# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""BuildPipeline orchestrator — runs all 8 build phases sequentially.

Phase 1 (resolve + tier) is done by the caller before BuildPipeline.run().
The orchestrator invokes phases 2-8 and returns a BuildSummary.

When opts.refresh=True, run() dispatches to _run_refresh() which does a
schema-hash diff against the existing PackageDB: unchanged tables skip the
expensive describe+sample phases; new tables get a full build; changed tables
are rebuilt; removed tables are deleted from DB and their markdown removed.

Multi-source iteration: ``_run_full`` and ``_run_refresh`` wrap the
table-aware phases (list / describe / sample / mine_history) in a
``for source in profile.sources`` loop, so a profile carrying
multiple ``(project, schema)`` source pairs gets each one built end-
to-end. The profile-level phases (``phase_discover_udfs``,
``phase_infer_joins_heuristic``, ``render_all``) run once at the
top, after every source has been described — UDFs are
compute_project-scoped (one set per profile) and joins / markdown
output spans all sources.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.build._logic_version import INFERENCE_LOGIC_VERSION
from maxcompute_semantic.build.cross_env import detect_cross_env_duplicate_sources
from maxcompute_semantic.build.errors import BuildPhaseError
from maxcompute_semantic.build.markdown import MarkdownRenderer, render_all
from maxcompute_semantic.build.phases import (
    PhaseResult,
    phase_column_profiling,
    phase_column_sampling,
    phase_describe_table,
    phase_discover_udfs,
    phase_infer_joins_heuristic,
    phase_list_tables,
    phase_mine_history,
)
from maxcompute_semantic.build.workload import WorkloadSummary, aggregate_workload_evidence
from maxcompute_semantic.memory.sample_sql import persist_sample_sqls

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import DataSource, Profile
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.mc_client.client import MaxComputeClient


Phase = Literal[
    "describe",
    "sampling",
    "profiling",
    "history",
    "udf",
    "joins",
    "join_candidates",
    "suggestions",
]


@dataclass
class BuildOptions:
    """Options controlling which build phases run and how."""

    no_history: bool = False
    no_sampling: bool = False
    no_joins: bool = False
    no_udf: bool = False
    refresh: bool = False
    fresh: bool = False
    tables_filter: list[str] | None = None
    profile_level: str = "light"
    profile_budget_cny: float = 3.0
    join_candidate_limit: int = 5
    include_views: bool = False
    parallel: int | None = None
    refresh_min_age_hours: float = 24.0


_AUTO_PARALLEL_CAP = 32


# Object types skipped by sampling/profiling unless include_views is set.
# VIRTUAL_VIEW: each query re-executes the underlying SQL (often JOINs);
#   may time out the per-call cost gate at 120s. Parameterized views in
#   this family fail outright on SELECT * because their definition
#   references @param / type-variable T.
# OBJECT_TABLE: non-row-oriented (typically OSS files); profile is meaningless.
# MATERIALIZED_VIEW, EXTERNAL_TABLE: NOT in this set — they have physical
# rows and behave like managed tables for profiling purposes.
_SKIPPED_TYPES_FOR_PROFILING = frozenset({"VIRTUAL_VIEW", "OBJECT_TABLE"})


_MAX_SUMMARY_ENTRIES = 200


@dataclass
class BuildSummary:
    """Summary of a completed build pipeline run."""

    tables_built: int = 0
    tables_skipped: int = 0
    tables_new: int = 0
    tables_changed: int = 0
    tables_removed: int = 0
    tables_unchanged: int = 0
    tables_resumed: int = 0
    memory_count: int = 0
    vector_count: int = 0
    elapsed_seconds: float = 0.0
    parallel_workers: int = 1
    phases_skipped: list[Phase] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BuildPipeline:
    """Orchestrates the 8-phase build pipeline.

    Phase 1 (resolve + tier) is done by the caller before calling run().
    Phases 2-8 are executed sequentially by this orchestrator.

    A ``progress`` callback (``Callable[[str], None]``) gets invoked
    at the start of each phase with a short human-readable message;
    the CLI's ``build_cmd`` wires this to a stderr printer in plain
    mode so users see "what's happening now" rather than just the
    final summary dict. Pass ``None`` to silence (used in tests).
    """

    def __init__(
        self,
        client: MaxComputeClient,
        db: PackageDB,
        profile: Profile,
        opts: BuildOptions,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._db = db
        self._profile = profile
        self._opts = opts
        self._summary = BuildSummary()
        self._progress = progress or (lambda _msg: None)
        self._progress_lock = threading.Lock()

    def run(self) -> BuildSummary:
        """Execute the build pipeline — full or refresh based on opts."""
        sources = self._iter_sources()
        if self._opts.refresh:
            return self._run_refresh(sources)
        return self._run_full(sources)

    def _absorb_phase_result(
        self,
        result: PhaseResult,
        table: str,
        *,
        phase: Phase,
    ) -> None:
        """Surface PhaseResult warnings/errors into BuildSummary.

        Non-success results used to be silently discarded for the
        sampling/profiling phases — the orchestrator threw away the
        return value and the build summary reported ``errors: []`` even
        when every table's sampling SQL had failed. Funnelling them here
        is what makes ``mcs status`` distinguish real-vs-fake successes.

        Per-list cap at :data:`_MAX_SUMMARY_ENTRIES` so a profile with
        thousands of failing tables can't grow the in-memory summary
        unboundedly (and JSON-serialize gigabytes).
        """
        if result.status == "success":
            return
        for warning in result.warnings:
            self._append_capped_warning(f"{phase}/{table}: {warning}")
        for err in result.errors:
            self._append_capped_error({"table": table, "phase": phase, **err})

    def _append_capped_warning(self, msg: str) -> None:
        lst = self._summary.warnings
        if len(lst) < _MAX_SUMMARY_ENTRIES:
            lst.append(msg)
        elif len(lst) == _MAX_SUMMARY_ENTRIES:
            lst.append(f"… additional warnings truncated at {_MAX_SUMMARY_ENTRIES}")

    def _append_capped_error(self, err: dict[str, Any]) -> None:
        lst = self._summary.errors
        if len(lst) < _MAX_SUMMARY_ENTRIES:
            lst.append(err)
        elif len(lst) == _MAX_SUMMARY_ENTRIES:
            lst.append({"phase": "_truncated", "message": f"capped at {_MAX_SUMMARY_ENTRIES}"})

    def _absorb_describe_failure(self, result: PhaseResult, table: str) -> bool:
        """Skip-and-record a describe failure. Returns True if skipped."""
        if result.status not in ("partial_failure", "hard_error"):
            return False
        self._summary.tables_skipped += 1
        self._absorb_phase_result(result, table, phase="describe")
        return True

    def _detect_and_warn_cross_env_duplicates(self) -> frozenset[frozenset[str]]:
        """Detect cross-env duplicate sources from the current DB state,
        emit a per-pair progress + summary warning, and return the set
        of suppressed source-key pairs to pass into
        :func:`phase_infer_joins_heuristic`.

        Run after every per-source loop completes so both the full and
        refresh paths see the same picture. Returns an empty set when
        the profile has only one source or when no pair clears the
        overlap threshold.
        """
        tables_by_source: dict[str, set[str]] = {}
        for row in self._db.list_tables():
            tables_by_source.setdefault(row["source_key"], set()).add(row["name"])
        pairs = detect_cross_env_duplicate_sources(tables_by_source)
        if not pairs:
            return frozenset()
        suppressed: set[frozenset[str]] = set()
        for p in pairs:
            msg = (
                f"cross-env duplicate sources: {p.source_a!r} and {p.source_b!r} "
                f"share {p.shared_count} of {p.smaller_size} tables "
                f"({p.overlap_ratio:.0%} overlap) — likely dev/prod or "
                "staging/prod copies of the same schema; JOIN inference "
                "between them suppressed"
            )
            self._progress(f"      ⚠ {msg}")
            self._append_capped_warning(f"cross_env/{p.source_a}+{p.source_b}: {msg}")
            suppressed.add(frozenset({p.source_a, p.source_b}))
        return frozenset(suppressed)

    def _sample_profile_and_record_refresh(
        self,
        source: DataSource,
        name: str,
        data_modified_at: str | None,
        workload_columns: set[str],
    ) -> None:
        """Refresh-path sampling/profile wrapper with truthful freshness writes."""
        phase_results = self._sample_and_profile_one(source, name, workload_columns, 1, 1)
        for phase_name, result in phase_results:
            self._absorb_phase_result(result, name, phase=phase_name)
        sk = source.source_key()
        if not phase_results:
            self._db.mark_build_complete(sk, [name])
        elif self._phase_results_succeeded(phase_results):
            self._db.record_sampled(sk, name, data_modified_at)

    def _skips_sampling_profile(self, source_key: str, table_name: str) -> bool:
        """Return True when table type should not be row-scanned by default."""
        table_row = self._db.get_table(source_key, table_name)
        table_type = table_row.get("table_type") if table_row else None
        return table_type in _SKIPPED_TYPES_FOR_PROFILING and not self._opts.include_views

    @staticmethod
    def _phase_results_succeeded(results: list[tuple[Phase, PhaseResult]]) -> bool:
        """True when at least one sampled/profiled phase ran and all succeeded."""
        return bool(results) and all(result.status == "success" for _, result in results)

    def _iter_sources(self) -> list[DataSource]:
        """Return the list of sources the pipeline will iterate, or
        raise ``BuildPhaseError`` when the profile has none.

        A profile with ``sources=()`` is the freshly-created mid-
        wizard state; running ``mcs build`` against it would have
        nothing to do, and silently returning a zero-table summary
        is more confusing than a hard error pointing at the right
        next-step verb.
        """
        sources = list(self._profile.sources)
        if not sources:
            raise BuildPhaseError(
                f"profile {self._profile.name!r} has no data sources to build "
                "— add at least one (project, schema) pair via "
                "`mcs profile update " + self._profile.name + "` and re-run "
                "`mcs build`",
            )
        return sources

    def _prime_client_for_parallel(self) -> None:
        """Pre-populate every lazily-initialized field on ``MaxComputeClient``
        so worker threads in the sampling/profiling pool never race on first
        access.

        Touches ``_odps`` (via ``_ensure_odps()``), ``_tier`` (already
        primed by the caller before construction but defensive here),
        and the per-project entry of ``_project_tier_cache`` for every
        distinct ``DataSource.project`` the profile references.
        """
        from maxcompute_semantic.mc_client.tier import get_tier

        self._client._ensure_odps()
        if self._client._tier is None:
            self._client._tier = get_tier(
                self._profile, self._profile.compute_project, client=self._client
            )
        seen: set[str] = set()
        for src in self._profile.sources:
            if src.project in seen:
                continue
            seen.add(src.project)
            self._client.get_project_tier(src.project)

    def _run_phase_7c(
        self,
        sources: list[DataSource],
        workload_summary_jsonable: dict[str, Any],
    ) -> None:
        """Run Phase 7c (semantic suggestions) across every (source, table).

        Pure DB-reader: no MaxCompute calls. Clears any prior
        ``annotation_suggestions`` for each source, then upserts the
        fresh suggestion set produced by
        :func:`suggest_column_semantics`. Shared by the full-build path
        and the refresh-path force-re-derive branch (the latter fires
        when the on-disk ``inference_logic_version`` is behind the
        CLI's :data:`INFERENCE_LOGIC_VERSION`).
        """
        from collections import defaultdict

        from maxcompute_semantic.build.markdown import _date_format_hint
        from maxcompute_semantic.build.semantic_suggestions import suggest_column_semantics

        cands_by_table: dict[str, list[dict]] = defaultdict(list)
        for cand in self._db.list_join_candidates():
            lt = cand.get("left_table")
            rt = cand.get("right_table")
            if lt:
                cands_by_table[lt].append(cand)
            if rt and rt != lt:
                cands_by_table[rt].append(cand)
        for source in sources:
            sk = source.source_key()
            self._db.clear_annotation_suggestions(source_key=sk)
            src_tables = self._db.list_tables(source_key=sk)
            src_cols_by_tid = self._db.get_columns_bulk([t["id"] for t in src_tables])
            for tbl_row in src_tables:
                tbl = tbl_row["name"]
                cols = src_cols_by_tid.get(tbl_row["id"], [])
                col_dict = {
                    c["name"]: {
                        "type": c.get("type", ""),
                        "uniqueness_ratio": c.get("uniqueness_ratio"),
                        "approx_ndv": c.get("approx_ndv"),
                        "row_count": c.get("row_count"),
                        "cast_rate": c.get("cast_rate"),
                        "is_partition": c.get("is_partition", 0),
                        "format_hint": _date_format_hint(c),
                    }
                    for c in cols
                }
                sug_list = suggest_column_semantics(
                    table_name=tbl,
                    columns=col_dict,
                    workload_summary=workload_summary_jsonable,
                    join_candidates=cands_by_table.get(tbl, []),
                )
                for s in sug_list:
                    self._db.upsert_annotation_suggestion(
                        source_key=sk,
                        table_name=tbl,
                        column_name=s.column_name,
                        suggested_role=s.suggested_role,
                        suggested_subtype=s.suggested_subtype,
                        confidence=s.confidence,
                        evidence=s.evidence,
                    )

    def _reconstruct_workload_from_db(self, sources: list[DataSource]) -> WorkloadSummary:
        """Re-aggregate workload evidence from persisted sample_sqls.

        The full-build path computes ``workload_total`` from freshly-
        mined SQLs and never persists the merged blob. The refresh-
        path force-re-derive branch needs that same evidence to feed
        Phase 7c offline, so we walk
        :meth:`PackageDB.list_sample_sqls` per source, gather every
        SQL (representative + alternates), and run the same
        :func:`aggregate_workload_evidence` we used during the
        original build. ``min_shape_frequency=2`` and the per-source
        ``allowed_tables`` scoping are kept identical so the
        suggestions remain consistent.
        """
        import json

        total = WorkloadSummary()
        for source in sources:
            sk = source.source_key()
            table_names = [t["name"] for t in self._db.list_tables(source_key=sk)]
            allowed_tables_lc = {n.lower() for n in table_names}
            sqls: list[str] = []
            for row in self._db.list_sample_sqls(source_key=sk):
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                # ``representative_sqls`` carries up to 3 variants per
                # shape; the single ``sql`` field is the first of those.
                # Aggregating the full representative list preserves the
                # frequency signal at shape granularity better than
                # using the canonical SQL alone.
                reps = payload.get("representative_sqls") or []
                if reps:
                    sqls.extend(s for s in reps if isinstance(s, str))
                elif isinstance(payload.get("sql"), str):
                    sqls.append(payload["sql"])
            if not sqls:
                continue
            ws = aggregate_workload_evidence(
                sqls,
                min_shape_frequency=2,
                allowed_tables=allowed_tables_lc,
            )
            total.merge(ws)
        return total

    def _sample_and_profile_one(
        self,
        source: DataSource,
        table: str,
        workload_columns: set[str],
        idx: int,
        total: int,
    ) -> list[tuple[Phase, PhaseResult]]:
        """Run sampling + profiling for a single table on a worker thread.

        Returns the list of ``(phase, result)`` pairs that the caller
        feeds into ``_absorb_phase_result``. Any unexpected exception
        is wrapped into a hard_error PhaseResult so a single bad table
        cannot abort the rest of the parallel batch.
        """
        from maxcompute_semantic.mc_client.errors import McsError

        results: list[tuple[Phase, PhaseResult]] = []

        if not self._opts.no_sampling:
            try:
                sample_result = phase_column_sampling(
                    self._client,
                    self._db,
                    self._profile,
                    source,
                    table,
                )
            except McsError as exc:
                sample_result = PhaseResult(
                    status="hard_error",
                    errors=[{"table": table, "code": exc.code, "message": exc.message}],
                )
            except Exception as exc:
                sample_result = PhaseResult(
                    status="hard_error",
                    errors=[{"table": table, "code": "UnknownError", "message": str(exc)}],
                )
            results.append(("sampling", sample_result))

        if self._opts.profile_level != "none" and not self._opts.no_sampling:
            try:
                profile_result = phase_column_profiling(
                    self._client,
                    self._db,
                    self._profile,
                    source,
                    table,
                    workload_columns=workload_columns,
                )
            except McsError as exc:
                profile_result = PhaseResult(
                    status="hard_error",
                    errors=[{"table": table, "code": exc.code, "message": exc.message}],
                )
            except Exception as exc:
                profile_result = PhaseResult(
                    status="hard_error",
                    errors=[{"table": table, "code": "UnknownError", "message": str(exc)}],
                )
            results.append(("profiling", profile_result))

        return results

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds into a human-readable duration string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        if minutes < 60:
            return f"{minutes}m{secs:02d}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h{mins:02d}m{secs:02d}s"

    def _resolve_parallel(self, work_item_count: int) -> int:
        """Resolve effective parallel worker count from opts."""
        if self._opts.parallel is None:
            return max(1, min(work_item_count, _AUTO_PARALLEL_CAP))
        return max(1, self._opts.parallel)

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        """Parse an ISO-8601 timestamp into an aware datetime, or None."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _data_changed_needs_resample(
        self,
        prior_data_modified: str | None,
        live_data_modified: str | None,
        last_sampled_at: str | None,
    ) -> bool:
        """Decide whether a schema-unchanged table should be re-sampled
        because its DATA changed since the last sample.

        True when (a) the live modification time exists and either no
        baseline exists yet (pre-v12 bootstrap) or the live modification
        time is strictly newer than the baseline, and (b) for established
        baselines, the throttle window (``refresh_min_age_hours``) has
        elapsed since the last sample — so a constantly-changing hot table
        is not re-sampled on every refresh.
        """
        live = self._parse_iso(live_data_modified)
        if live is None:
            return False
        prior = self._parse_iso(prior_data_modified)
        if prior is None:
            # Pre-v12 packages have no baseline. Re-sample once so the
            # stored stats truthfully match the data_modified_at value we
            # are about to persist.
            return True
        if live <= prior:
            return False
        min_age = self._opts.refresh_min_age_hours
        if min_age and min_age > 0:
            sampled = self._parse_iso(last_sampled_at)
            if sampled is not None:
                age_hours = (datetime.now(timezone.utc) - sampled).total_seconds() / 3600
                if age_hours < min_age:
                    return False
        return True

    def _emit_sampling_progress(
        self,
        done: int,
        total: int,
        t0: float,
        parallel: int,
        last_table: str,
    ) -> None:
        elapsed = time.monotonic() - t0
        if done > 0 and done < total:
            avg = elapsed / done
            remaining = (total - done) * avg / max(parallel, 1)
            eta = f", ~{self._format_duration(remaining)} remaining"
        else:
            eta = ""
        self._progress(f"[4/7] sampling + profiling ({done}/{total} done{eta}): {last_table}")

    def _run_full(self, sources: list[DataSource]) -> BuildSummary:
        """Execute the full build pipeline (no incremental diff)."""
        t0 = time.monotonic()
        self._prime_client_for_parallel()
        # Phase 1: resolve + tier already done by caller

        # Per-source loop: phases 2 (list_tables), 4 (describe_table),
        # 5 (column_sampling), 6 (mine_history) run once per
        # ``DataSource`` so a multi-(project, schema) profile gets
        # every (source, table) pair into PackageDB.
        n_sources = len(sources)
        history_skipped = self._opts.no_history
        tables_with_sample_sqls = 0
        info_schema_source = "tenant"
        total_table_count = 0
        workload_total = WorkloadSummary()

        for src_idx, source in enumerate(sources, 1):
            self._progress(
                f"[1/7] enumerating tables in source ({src_idx}/{n_sources}): "
                f"{source.project}.{source.schema}..."
            )
            list_result = phase_list_tables(self._client, self._db, self._profile, source)
            if list_result.status == "hard_error":
                if list_result.errors and "exception" in list_result.errors[0]:
                    raise list_result.errors[0]["exception"]
                raise BuildPhaseError(
                    f"list_tables failed for source {source.project}.{source.schema}"
                )
            for w in list_result.warnings:
                self._progress(f"      ⚠ {w}")
                self._append_capped_warning(f"list_tables/{source.source_key()}: {w}")
            table_names = list_result.data.get("table_names", [])
            if self._opts.tables_filter:
                table_names = [t for t in table_names if t in self._opts.tables_filter]
            self._progress(f"      found {len(table_names)} table(s) in this source")

            # Phase 2b: clean up tables removed from the source since the last build.
            sk = source.source_key()
            existing_rows = self._db.list_tables(source_key=sk)
            existing_names = {row["name"] for row in existing_rows}
            # Resume support: a prior (possibly interrupted) build may have
            # left some tables fully built. Capture their pre-describe state
            # so the describe loop can skip re-sampling unchanged, already-
            # complete tables. ``--fresh`` disables this (rebuild from
            # scratch). Pre-v11 rows lack build_complete → treated complete.
            prior_complete = {row["name"]: row.get("build_complete", 1) for row in existing_rows}
            prior_hash = {row["name"]: row["schema_hash"] for row in existing_rows}
            prior_data_modified = {
                row["name"]: row.get("data_modified_at") for row in existing_rows
            }
            prior_sampled_at = {row["name"]: row.get("last_sampled_at") for row in existing_rows}
            removed_tables = existing_names - set(table_names)
            markdown_dir = profile_data_dir(self._profile)
            for removed_name in removed_tables:
                self._db.delete_table(sk, removed_name)
                md_file = markdown_dir / sk / f"{removed_name}.md"
                md_file.unlink(missing_ok=True)
            self._summary.tables_removed += len(removed_tables)
            if removed_tables:
                self._progress(f"      -{len(removed_tables)} removed table(s) from cache")

            total_table_count += len(table_names)

            # Phase 4: describe every table first. Sampling and profiling
            # are deferred until after history mining so the profiler can
            # see workload-derived column hints from THIS source's
            # verified queries — running profile before mine_history left
            # source[0] with an empty workload_columns set and source[1+]
            # cross-contaminated by prior sources' columns.
            n = len(table_names)
            described_tables: list[str] = []
            # Tables that were already fully built in a prior run with an
            # unchanged schema AND unchanged data — skipped from re-sampling
            # so an interrupted build resumes instead of redoing finished
            # work. A schema-unchanged table whose DATA changed (per its
            # last_data_modified_time) is NOT skipped, so re-running picks up
            # fresh data; the throttle in _data_changed_needs_resample keeps
            # hot tables from re-sampling on every run.
            resume_skip: set[str] = set()
            # Live last_data_modified_time per table, captured at describe
            # and persisted (via record_sampled) only after re-sampling.
            live_dm_by_table: dict[str, str | None] = {}
            for i, table in enumerate(table_names, 1):
                self._progress(f"[3/7] describing table ({i}/{n}): {table}")
                desc_result = phase_describe_table(
                    self._client, self._db, self._profile, source, table
                )
                if self._absorb_describe_failure(desc_result, table):
                    continue
                self._summary.tables_built += 1
                described_tables.append(table)
                new_hash = desc_result.data.get("schema_hash")
                live_dm_by_table[table] = desc_result.data.get("data_modified_at")
                if (
                    not self._opts.fresh
                    and prior_complete.get(table) == 1
                    and new_hash is not None
                    and prior_hash.get(table) == new_hash
                    and not self._data_changed_needs_resample(
                        prior_data_modified.get(table),
                        live_dm_by_table[table],
                        prior_sampled_at.get(table),
                    )
                ):
                    resume_skip.add(table)

            # Phase 6: mine history for THIS source. Workload columns
            # are scoped per-source so a column name from source A
            # can't trigger profiling of an unrelated column with the
            # same name in source B.
            workload_columns_this_source: set[str] = set()
            if not self._opts.no_history:
                self._progress(
                    f"[5/7] mining INFORMATION_SCHEMA.TASKS_HISTORY for "
                    f"{source.project}.{source.schema}..."
                )
                hist_result = phase_mine_history(self._client, self._db, self._profile, source)
                self._absorb_phase_result(hist_result, source.source_key(), phase="history")
                info_schema_source = (
                    hist_result.data.get("info_schema_source") or info_schema_source
                )
                if hist_result.data.get("history_skipped"):
                    history_skipped = True
                    self._progress("      no INFORMATION_SCHEMA access — skipped")
                else:
                    sample_sql_candidates = (
                        hist_result.data.get("sample_sql_candidates")
                        or hist_result.data.get("verified_queries")
                        or {}
                    )
                    src_with_sqls = sum(1 for v in sample_sql_candidates.values() if v)
                    tables_with_sample_sqls += src_with_sqls
                    persist_result = persist_sample_sqls(
                        self._db, sample_sql_candidates, source.source_key()
                    )
                    sql_count = persist_result.created
                    self._progress(
                        f"      found sample SQLs for {src_with_sqls} table(s) "
                        f"({sql_count} queries persisted)"
                    )

                    if self._opts.profile_level != "none":
                        all_sqls = [sql for sqls in sample_sql_candidates.values() for sql in sqls]
                        # min_shape_frequency=2 drops one-shot mined SQL
                        # so a single ad-hoc query can't single-handedly
                        # drive ``where_counts`` / ``group_by_counts``
                        # past the dim/metric classification gates.
                        # Repeating shapes still carry full weight.
                        # allowed_tables scopes evidence to THIS source's
                        # tables — mined SQL that JOINs an in-source
                        # table with an out-of-source one is still kept
                        # as a sample, but its cross-source joins /
                        # columns don't count toward suggestion ranking.
                        allowed_tables_lc = {n.lower() for n in table_names}
                        ws = aggregate_workload_evidence(
                            all_sqls,
                            min_shape_frequency=2,
                            allowed_tables=allowed_tables_lc,
                        )
                        workload_total.merge(ws)
                        for col_key in ws.where_counts:
                            workload_columns_this_source.add(col_key.split(".")[-1])
                        for col_key in ws.group_by_counts:
                            workload_columns_this_source.add(col_key.split(".")[-1])

            # Phase 5+5b: sample and profile each successfully-described
            # table, using THIS source's workload column set. VIRTUAL_VIEW
            # and OBJECT_TABLE objects are skipped by default — their
            # row-level scans either re-execute the underlying SQL (views)
            # or have no row structure (object tables). ``include_views``
            # is the opt-in escape hatch (``mcs build --include-views``).
            # NULL table_type (legacy pre-v9 rows) is treated as a table
            # so an upgrade does not silently change what gets profiled.
            n_described = len(described_tables)
            skipped_views: list[str] = []
            work_items: list[tuple[int, str]] = []
            for i, table in enumerate(described_tables, 1):
                if table in resume_skip:
                    continue
                table_row = self._db.get_table(source.source_key(), table)
                table_type = table_row.get("table_type") if table_row else None
                if table_type in _SKIPPED_TYPES_FOR_PROFILING and not self._opts.include_views:
                    skipped_views.append(table)
                    continue
                work_items.append((i, table))

            if resume_skip:
                self._summary.tables_resumed += len(resume_skip)
                self._progress(
                    f"      ↻ resuming: {len(resume_skip)} table(s) already built "
                    f"(unchanged), sampling {len(work_items)} remaining"
                )

            parallel = self._resolve_parallel(len(work_items))
            self._summary.parallel_workers = max(self._summary.parallel_workers, parallel)
            sk_complete = source.source_key()
            if work_items and not self._opts.no_sampling:
                self._db.mark_build_incomplete(sk_complete, [table for _idx, table in work_items])
            if parallel == 1 or len(work_items) <= 1:
                sample_t0 = time.monotonic()
                for done_i, (idx, table) in enumerate(work_items):
                    phase_results = self._sample_and_profile_one(
                        source,
                        table,
                        workload_columns_this_source,
                        idx,
                        n_described,
                    )
                    for phase_name, result in phase_results:
                        self._absorb_phase_result(result, table, phase=phase_name)
                    if not phase_results:
                        self._db.mark_build_complete(sk_complete, [table])
                    elif self._phase_results_succeeded(phase_results):
                        # Mark complete + advance the data-change baseline
                        # only after this table's sampling + profiling
                        # succeeded. Failures leave build_complete=0 so the
                        # next run retries instead of treating stale stats as
                        # fresh.
                        self._db.record_sampled(sk_complete, table, live_dm_by_table.get(table))
                    self._emit_sampling_progress(
                        done_i + 1,
                        len(work_items),
                        sample_t0,
                        parallel,
                        table,
                    )
            else:
                sample_t0 = time.monotonic()
                completed_count = 0
                with ThreadPoolExecutor(
                    max_workers=parallel,
                    thread_name_prefix="mcs-build-sample",
                ) as pool:
                    future_to_table = {
                        pool.submit(
                            self._sample_and_profile_one,
                            source,
                            table,
                            workload_columns_this_source,
                            idx,
                            n_described,
                        ): table
                        for idx, table in work_items
                    }
                    for fut in as_completed(future_to_table):
                        table = future_to_table[fut]
                        phase_results = fut.result()
                        for phase_name, result in phase_results:
                            self._absorb_phase_result(result, table, phase=phase_name)
                        if not phase_results:
                            self._db.mark_build_complete(sk_complete, [table])
                        elif self._phase_results_succeeded(phase_results):
                            self._db.record_sampled(sk_complete, table, live_dm_by_table.get(table))
                        completed_count += 1
                        with self._progress_lock:
                            self._emit_sampling_progress(
                                completed_count,
                                len(work_items),
                                sample_t0,
                                parallel,
                                table,
                            )

            if skipped_views:
                # Views/object tables are never sampled — mark them complete
                # so --refresh does not mistake them for resume candidates.
                self._db.mark_build_complete(sk_complete, skipped_views)
                self._progress(
                    f"[4/7] skipped {len(skipped_views)} view/object table(s) "
                    f"in sampling/profiling (use --include-views to override): "
                    f"{', '.join(skipped_views)}"
                )

        if self._opts.no_sampling:
            self._summary.phases_skipped.append("sampling")
        if self._opts.no_history:
            self._progress("[5/7] history mining skipped (--no-history / MCS_NO_HISTORY)")
            self._summary.phases_skipped.append("history")

        # Phase 3: discover UDFs (profile-global, run once)
        if not self._opts.no_udf:
            self._progress("[2/7] discovering UDFs...")
            phase_discover_udfs(self._client, self._db, self._profile)
        else:
            self._progress("[2/7] UDF discovery skipped (--no-udf)")
            self._summary.phases_skipped.append("udf")

        # Phase 7: infer joins (profile-global, spans all sources)
        if not self._opts.no_joins:
            self._progress("[6/7] inferring join relationships from column-name heuristics...")
            suppressed_pairs = self._detect_and_warn_cross_env_duplicates()
            phase_infer_joins_heuristic(
                self._db, self._profile, suppressed_source_pairs=suppressed_pairs
            )
        else:
            self._progress("[6/7] join inference skipped (--no-joins)")
            self._summary.phases_skipped.append("joins")

        # Phase 7b: rank join candidates from workload + stats + names.
        if self._opts.profile_level != "none" and not self._opts.no_joins:
            from maxcompute_semantic.build.join_candidates import rank_join_candidates

            self._progress("[6b/7] ranking join candidates...")
            name_edges = [dict(j) for j in self._db.list_joins()]
            # Build column stats map from DB (bulk-fetch columns).
            all_tables = self._db.list_tables()
            cols_by_tid = self._db.get_columns_bulk([t["id"] for t in all_tables])
            col_stats_map: dict[tuple[str, str], dict[str, dict]] = {}
            for tbl_row in all_tables:
                col_stats_map[(tbl_row["source_key"], tbl_row["name"])] = {
                    c["name"]: {
                        "uniqueness_ratio": c.get("uniqueness_ratio"),
                        "approx_ndv": c.get("approx_ndv"),
                    }
                    for c in cols_by_tid.get(tbl_row["id"], [])
                }
            merged_workload = workload_total.to_jsonable()
            ranked = rank_join_candidates(
                tables=col_stats_map,
                workload_summary=merged_workload,
                name_edges=name_edges,
                limit_per_table=self._opts.join_candidate_limit,
            )
            self._db.clear_join_candidates()
            for jc in ranked:
                self._db.upsert_join_candidate(
                    left_source_key=jc.left_source_key,
                    left_table=jc.left_table,
                    left_col=jc.left_col,
                    right_source_key=jc.right_source_key,
                    right_table=jc.right_table,
                    right_col=jc.right_col,
                    confidence=jc.confidence,
                    evidence=jc.evidence,
                    conflict_group=jc.conflict_group,
                    coverage_ratio=jc.coverage_ratio,
                    right_uniqueness_ratio=jc.right_uniqueness_ratio,
                    cardinality=jc.cardinality,
                    status=jc.status,
                )
        elif self._opts.profile_level == "none":
            self._summary.phases_skipped.append("join_candidates")

        # Phase 7b-deep: value-overlap validation for top join candidates
        # (deep profile only). Runs cost-gated LEFT JOIN COUNT queries to
        # compute coverage_ratio; promotes to "confirmed" at ≥0.95.
        if self._opts.profile_level == "deep" and not self._opts.no_joins:
            self._progress("[6b-deep/7] validating top join candidates (deep)...")
            from maxcompute_semantic.build.join_candidates import build_overlap_validation_sql
            from maxcompute_semantic.mc_client.errors import McsError as _DeepMcsError

            budget = self._opts.profile_budget_cny
            validated = 0
            skipped_cost = 0
            skipped_err = 0
            fallback_src = sources[0] if sources else None

            def _resolve(
                source_key: str | None,
                table: str,
            ) -> tuple[DataSource, str] | None:
                src = self._profile.source_by_key(source_key) if source_key else fallback_src
                if src is None:
                    return None
                tier = self._client.get_project_tier(src.project)
                return src, src.qualified_for_tier(table, tier)

            for jc in ranked:
                if jc.status == "conflicting" or budget <= 0:
                    continue

                left = _resolve(jc.left_source_key, jc.left_table)
                right = _resolve(jc.right_source_key, jc.right_table)
                if left is None or right is None:
                    skipped_err += 1
                    continue
                left_src, left_fq = left
                right_src, right_fq = right

                overlap_sql = build_overlap_validation_sql(
                    left_fq_name=left_fq,
                    left_col=jc.left_col,
                    right_fq_name=right_fq,
                    right_col=jc.right_col,
                    left_where_clause="",
                    right_where_clause="",
                )

                fq_projects = [left_src.project, right_src.project]

                try:
                    cost = self._client.cost_estimate_fq(overlap_sql, projects=fq_projects)
                except _DeepMcsError:
                    skipped_err += 1
                    continue

                est_cny = cost.get("estimated_cost_cny", 0.0)
                if cost["verdict"] == "blocked" or est_cny > budget:
                    skipped_cost += 1
                    continue

                try:
                    # assume_yes=True: build already cost-estimated and
                    # gated on `budget` above; the per-call cost gate
                    # would re-estimate and (in non-TTY contexts like CI
                    # or eval) refuse on a confirm verdict. The build's
                    # own budget check is the authoritative gate here.
                    envelope = self._client.execute_fq_sql(
                        overlap_sql, projects=fq_projects, assume_yes=True
                    )
                except _DeepMcsError:
                    skipped_err += 1
                    continue

                budget -= est_cny

                rows = envelope.data.get("rows", [])
                coverage: float = 0.0
                if rows and len(rows) > 0:
                    left_nn = int(rows[0].get("left_non_null", 0))
                    matched = int(rows[0].get("matched_rows", 0))
                    coverage = matched / left_nn if left_nn > 0 else 0.0

                new_status = "confirmed" if coverage >= 0.95 else "suggested"
                new_evidence = list(jc.evidence) + [
                    {"source": "overlap_validation", "coverage_ratio": coverage},
                ]
                self._db.upsert_join_candidate(
                    left_source_key=jc.left_source_key,
                    left_table=jc.left_table,
                    left_col=jc.left_col,
                    right_source_key=jc.right_source_key,
                    right_table=jc.right_table,
                    right_col=jc.right_col,
                    confidence=min(jc.confidence + 0.1, 0.98)
                    if new_status == "confirmed"
                    else jc.confidence,
                    evidence=new_evidence,
                    conflict_group=jc.conflict_group,
                    coverage_ratio=coverage,
                    right_uniqueness_ratio=jc.right_uniqueness_ratio,
                    cardinality=jc.cardinality,
                    status=new_status,
                )
                validated += 1

            self._summary.warnings.append(
                f"deep validation: {validated} validated, "
                f"{skipped_cost} cost-skipped, {skipped_err} error-skipped, "
                f"{budget:.2f} CNY remaining"
            )

        # Phase 7c: semantic suggestions.
        if self._opts.profile_level != "none":
            self._progress("[6c/7] generating semantic suggestions...")
            self._run_phase_7c(sources, workload_total.to_jsonable())
        elif self._opts.profile_level == "none":
            self._summary.phases_skipped.append("suggestions")

        # Phase 8: render markdown (profile-global)
        self._progress("[7/7] rendering markdown bundle (per-source dirs + _overview / _joins)...")
        render_all(
            self._db,
            self._profile,
            history_skipped=history_skipped,
            tables_with_sample_sqls=tables_with_sample_sqls,
            info_schema_source=info_schema_source,
        )
        # Stamp the inference-logic version we just derived under.
        # ``mcs build --refresh`` reads this back and force-re-derives
        # offline when the stored value is behind the CLI's current
        # constant — i.e. after a CLI upgrade that touched Phase 7c
        # / markdown / naming heuristics.
        self._db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)
        elapsed = time.monotonic() - t0
        self._summary.elapsed_seconds = round(elapsed, 1)
        self._progress(f"✓ build complete ({self._format_duration(elapsed)})")

        return self._summary

    def _run_refresh(self, sources: list[DataSource]) -> BuildSummary:
        """Incremental rebuild: schema-hash diff + selective rebuild,
        per source.

        For each ``DataSource`` in the profile:
          1. Get live table list from the source's MC project/schema.
          2. For each live table, compute a new schema_hash via
             ``phase_describe_table`` and compare with the existing
             hash in PackageDB (filtered to this source's rows).
          3. Classify per-source: new / changed / unchanged / removed.
          4. Only rebuild describe+sample for new + changed tables.
          5. Delete removed tables from DB + remove their markdown.
        Then UDFs / joins / markdown render once at the top.

        If the on-disk ``inference_logic_version`` stamp is behind the
        CLI's current :data:`INFERENCE_LOGIC_VERSION`, this path
        additionally force-runs Phase 7c against every (source, table)
        and re-renders every per-table markdown file — purely from
        already-cached DB state, no MC round-trips. This is the
        recovery flow after a ``mcs update`` that changed the
        inference layer.
        """
        t0 = time.monotonic()
        n_sources = len(sources)
        history_skipped = self._opts.no_history
        tables_with_sample_sqls = 0
        info_schema_source = "tenant"
        rebuild_names_per_source: dict[str, set[str]] = {}

        stored_logic_version = self._db.get_inference_logic_version()
        force_rederive = stored_logic_version < INFERENCE_LOGIC_VERSION
        if force_rederive:
            self._progress(
                f"      inference-logic stamp v{stored_logic_version} < "
                f"current v{INFERENCE_LOGIC_VERSION}; will re-derive "
                "suggestions + re-render every table after the diff."
            )

        for src_idx, source in enumerate(sources, 1):
            sk = source.source_key()
            self._progress(
                f"[1/7] enumerating tables (refresh) for source ({src_idx}/{n_sources}): "
                f"{source.project}.{source.schema}..."
            )
            list_result = phase_list_tables(self._client, self._db, self._profile, source)
            if list_result.status == "hard_error":
                if list_result.errors and "exception" in list_result.errors[0]:
                    raise list_result.errors[0]["exception"]
                raise BuildPhaseError(
                    f"list_tables failed for source {source.project}.{source.schema}"
                )
            for w in list_result.warnings:
                self._progress(f"      ⚠ {w}")
                self._append_capped_warning(f"list_tables/{source.source_key()}: {w}")
            live_tables = list_result.data.get("table_names", [])
            if self._opts.tables_filter:
                live_tables = [t for t in live_tables if t in self._opts.tables_filter]

            # Load existing rows for this source only — cross-source
            # tables don't get classified as "removed" here.
            existing_rows = self._db.list_tables(source_key=sk)
            existing_names = {row["name"] for row in existing_rows}
            existing_hashes = {row["name"]: row["schema_hash"] for row in existing_rows}
            # build_complete=0 marks a table that was described but never
            # finished sampling (a prior build was interrupted). Pre-v11
            # rows lack the column entirely → treat as complete (1) so an
            # upgrade does not re-sample everything. See _migrate_v10_to_v11.
            existing_complete = {row["name"]: row.get("build_complete", 1) for row in existing_rows}
            existing_data_modified = {
                row["name"]: row.get("data_modified_at") for row in existing_rows
            }
            existing_sampled_at = {row["name"]: row.get("last_sampled_at") for row in existing_rows}

            self._progress(
                f"      classifying {len(live_tables)} live "
                f"vs {len(existing_names)} cached table(s)..."
            )
            new_tables: list[str] = []
            changed_tables: list[str] = []
            unchanged_tables: list[str] = []
            # Live last_data_modified_time per table, captured during the
            # classification describe; persisted via record_sampled only
            # after a table is (re-)sampled.
            live_dm_by_table: dict[str, str | None] = {}

            for name in live_tables:
                if name not in existing_names:
                    new_tables.append(name)
                    continue
                old_hash = existing_hashes.get(name, "pending")
                desc_result = phase_describe_table(
                    self._client, self._db, self._profile, source, name
                )
                new_hash = None
                if desc_result.status == "success":
                    new_hash = desc_result.data.get("schema_hash")
                    live_dm_by_table[name] = desc_result.data.get("data_modified_at")
                elif self._absorb_describe_failure(desc_result, name):
                    continue

                if new_hash and old_hash != new_hash:
                    changed_tables.append(name)
                elif new_hash and old_hash == new_hash:
                    unchanged_tables.append(name)

            removed_tables = existing_names - set(live_tables)

            # Delete removed tables from DB + remove their markdown.
            # Per-source subdir layout from chain δ: each source's .md
            # files live under ``<markdown_dir>/<source_key>/``.
            markdown_dir = profile_data_dir(self._profile)
            for removed_name in removed_tables:
                self._db.delete_table(sk, removed_name)
                md_file = markdown_dir / sk / f"{removed_name}.md"
                md_file.unlink(missing_ok=True)

            # Resume: tables whose schema is unchanged but that never
            # finished sampling (build_complete=0 from an interrupted
            # build) must be re-sampled, not skipped.
            resumed_tables = [
                name for name in unchanged_tables if not existing_complete.get(name, 1)
            ]
            resumed_set = set(resumed_tables)
            # Data-aware refresh: schema-unchanged, already-complete tables
            # whose DATA changed since the last sample (throttled by
            # refresh_min_age_hours) are re-sampled so stats stay fresh.
            data_changed_tables = [
                name
                for name in unchanged_tables
                if name not in resumed_set
                and self._data_changed_needs_resample(
                    existing_data_modified.get(name),
                    live_dm_by_table.get(name),
                    existing_sampled_at.get(name),
                )
            ]
            data_changed_set = set(data_changed_tables)
            truly_unchanged = len(unchanged_tables) - len(resumed_tables) - len(data_changed_tables)

            self._summary.tables_removed += len(removed_tables)
            self._summary.tables_unchanged += truly_unchanged
            diff_msg = (
                f"      diff: +{len(new_tables)} new, "
                f"~{len(changed_tables)} changed, "
                f"={truly_unchanged} unchanged, "
                f"-{len(removed_tables)} removed"
            )
            if resumed_tables:
                diff_msg += f", ↻{len(resumed_tables)} resumed (incomplete)"
            if data_changed_tables:
                diff_msg += f", ⟳{len(data_changed_tables)} data-changed"
            self._progress(diff_msg)

            # New tables are not present in the DB during the
            # classification pass. Describe them before history mining:
            # phase_mine_history attributes SQL from PackageDB's current
            # source table set, and profiling needs those workload hints.
            described_new_tables: list[str] = []
            for name in new_tables:
                desc_result = phase_describe_table(
                    self._client, self._db, self._profile, source, name
                )
                if self._absorb_describe_failure(desc_result, name):
                    continue
                live_dm_by_table[name] = desc_result.data.get("data_modified_at")
                described_new_tables.append(name)
                self._summary.tables_built += 1
                self._summary.tables_new += 1

            # Mine history before profiling so refresh has the same
            # source-scoped workload-column hints as the full build path.
            workload_columns_this_source: set[str] = set()
            history_touched_tables_this_source: set[str] = set()
            skipped_views_this_source: list[str] = []
            if not self._opts.no_history:
                hist_result = phase_mine_history(self._client, self._db, self._profile, source)
                self._absorb_phase_result(hist_result, source.source_key(), phase="history")
                info_schema_source = (
                    hist_result.data.get("info_schema_source") or info_schema_source
                )
                if hist_result.data.get("history_skipped"):
                    history_skipped = True
                else:
                    sample_sql_candidates = (
                        hist_result.data.get("sample_sql_candidates")
                        or hist_result.data.get("verified_queries")
                        or {}
                    )
                    tables_with_sample_sqls += sum(1 for v in sample_sql_candidates.values() if v)
                    persist_result = persist_sample_sqls(
                        self._db, sample_sql_candidates, source.source_key()
                    )
                    history_touched_tables_this_source = persist_result.touched_tables

                    if self._opts.profile_level != "none":
                        all_sqls = [sql for sqls in sample_sql_candidates.values() for sql in sqls]
                        allowed_tables_lc = {n.lower() for n in live_tables}
                        ws = aggregate_workload_evidence(
                            all_sqls,
                            min_shape_frequency=2,
                            allowed_tables=allowed_tables_lc,
                        )
                        for col_key in ws.where_counts:
                            workload_columns_this_source.add(col_key.split(".")[-1])
                        for col_key in ws.group_by_counts:
                            workload_columns_this_source.add(col_key.split(".")[-1])

            # Phase 5: sampling/profiling for new + changed tables.
            for name in described_new_tables:
                if self._skips_sampling_profile(sk, name):
                    skipped_views_this_source.append(name)
                    self._db.mark_build_complete(sk, [name])
                    continue
                self._sample_profile_and_record_refresh(
                    source,
                    name,
                    live_dm_by_table.get(name),
                    workload_columns_this_source,
                )

            for name in changed_tables:
                # Describe already ran during classification. Reset the
                # complete flag before re-sampling so an interrupt mid-
                # resample is resumed on the next refresh.
                self._summary.tables_built += 1
                self._summary.tables_changed += 1
                if self._skips_sampling_profile(sk, name):
                    skipped_views_this_source.append(name)
                    self._db.mark_build_complete(sk, [name])
                    continue
                self._db.mark_build_incomplete(sk, [name])
                self._sample_profile_and_record_refresh(
                    source,
                    name,
                    live_dm_by_table.get(name),
                    workload_columns_this_source,
                )

            for name in resumed_tables:
                self._progress(
                    f"      ↻ resuming sampling/profiling for {name} (incomplete from prior build)"
                )
                self._summary.tables_built += 1
                if self._skips_sampling_profile(sk, name):
                    skipped_views_this_source.append(name)
                    self._db.mark_build_complete(sk, [name])
                    continue
                self._sample_profile_and_record_refresh(
                    source,
                    name,
                    live_dm_by_table.get(name),
                    workload_columns_this_source,
                )

            for name in data_changed_tables:
                self._progress(
                    f"      ⟳ re-sampling/re-profiling {name} (data changed since last build)"
                )
                self._summary.tables_built += 1
                if self._skips_sampling_profile(sk, name):
                    skipped_views_this_source.append(name)
                    self._db.mark_build_complete(sk, [name])
                    continue
                self._db.mark_build_incomplete(sk, [name])
                self._sample_profile_and_record_refresh(
                    source,
                    name,
                    live_dm_by_table.get(name),
                    workload_columns_this_source,
                )

            if skipped_views_this_source:
                self._progress(
                    f"      skipped {len(skipped_views_this_source)} view/object table(s) "
                    f"in refresh sampling/profiling (use --include-views to override): "
                    f"{', '.join(skipped_views_this_source)}"
                )

            rebuild_names_per_source[sk] = (
                set(described_new_tables)
                | set(changed_tables)
                | resumed_set
                | data_changed_set
                | history_touched_tables_this_source
            )

        if self._opts.no_history:
            self._summary.phases_skipped.append("history")

        # Phase 3: discover UDFs (profile-global).
        if not self._opts.no_udf:
            self._progress("[2/7] discovering UDFs...")
            phase_discover_udfs(self._client, self._db, self._profile)
        else:
            self._summary.phases_skipped.append("udf")

        # Phase 7: infer joins (profile-global, re-validate cross-source).
        if not self._opts.no_joins:
            self._progress("[6/7] inferring join relationships...")
            suppressed_pairs = self._detect_and_warn_cross_env_duplicates()
            phase_infer_joins_heuristic(
                self._db, self._profile, suppressed_source_pairs=suppressed_pairs
            )
        else:
            self._summary.phases_skipped.append("joins")

        # Force-rederive (post-CLI-upgrade): re-run Phase 7c across
        # all sources from reconstructed workload evidence, then fall
        # through to a render_all instead of the selective per-table
        # render below.
        if force_rederive and self._opts.profile_level != "none":
            self._progress(
                "[6c/7] re-deriving semantic suggestions (offline, "
                "inference-logic version mismatch)..."
            )
            reconstructed_workload = self._reconstruct_workload_from_db(sources)
            self._run_phase_7c(sources, reconstructed_workload.to_jsonable())

        # Phase 8: render markdown — only rebuild changed/new per-table
        # files; always re-render overview / joins / udfs (they reflect
        # the full table set across all sources). Force-rederive widens
        # the per-table render to every table so the new inference
        # layer reaches the on-disk .md surface.
        all_rebuild = {
            (sk, name) for sk, names in rebuild_names_per_source.items() for name in names
        }
        output_dir = profile_data_dir(self._profile)
        renderer = MarkdownRenderer(
            self._db,
            self._profile,
            output_dir,
            history_skipped=history_skipped,
            tables_with_sample_sqls=tables_with_sample_sqls,
            info_schema_source=info_schema_source,
        )

        if force_rederive:
            self._progress(
                "[7/7] re-rendering full markdown bundle (inference-logic version bump)..."
            )
            render_all(
                self._db,
                self._profile,
                history_skipped=history_skipped,
                tables_with_sample_sqls=tables_with_sample_sqls,
                info_schema_source=info_schema_source,
            )
        else:
            renderer.render_overview()
            renderer.render_joins()
            renderer.render_udfs()
            if all_rebuild:
                self._progress(f"[7/7] rendering markdown for {len(all_rebuild)} table(s)...")
                for src_sk, name in all_rebuild:
                    renderer.render_table(src_sk, name)
            else:
                self._progress(
                    "[7/7] no rebuilt table files — refreshed overview / joins / udfs / state"
                )

        # Stamp only after the rederive + render_all both succeed.
        # If anything above raised, the stamp stays at the prior
        # value so the next refresh retries the rederive.
        if force_rederive:
            self._db.set_inference_logic_version(INFERENCE_LOGIC_VERSION)
        elapsed = time.monotonic() - t0
        self._summary.elapsed_seconds = round(elapsed, 1)
        self._progress(f"✓ refresh complete ({self._format_duration(elapsed)})")

        return self._summary
