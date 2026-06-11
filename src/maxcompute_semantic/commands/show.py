# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs show`` — display the semantic-package data for a profile.

The single agent-facing entry point for "what's in this profile's
package?". Reads the on-disk markdown projections that ``mcs build``
writes to ``profile_data_dir(profile)`` (or ``profile.package_path``
override). No live MaxCompute calls.

The agent's typical NL → SQL workflow is:

  1. ``mcs show``                       → tables / joins / UDFs overview
  2. ``mcs show --table T``             → single-table column hints + samples
  3. ``mcs show --tables T1,T2,T3``     → batch fetch for related tables
  4. ``mcs sql cost 'SQL'``             → cost gate
  5. ``mcs sql execute 'SQL'``          → run

Profile resolution follows the standard auto-resolution chain
(``--profile`` → ``MCS_PROFILE`` → cwd link →
env-var fallback). The agent usually does not need to specify
``--project`` or ``--schema`` — the cwd-bound profile carries both.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.build.markdown import (
    build_role_groups,
    compact_column_entry,
    trim_annotation_suggestion,
    trim_join_candidate,
)
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.mc_client.errors import (
    AmbiguousTableError,
    McsError,
    PackageNotBuiltError,
    TableNotFoundError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.build.storage import PackageDB


def _extract_sample_sqls(db: Any, source_key: str, table: str) -> list[str]:
    """Extract user-verified sample SQL strings for a (source, table) pair.

    The literal ``sample_sqls`` list is what the agent treats as a
    quotable example — only ``user_verified`` entries qualify. Mined
    patterns (even high-frequency ones) carry question-specific
    SELECT lists; surfacing them as quotable examples is the
    ``answers-the-wrong-question`` trap. Mined patterns still show
    up in ``sample_sql_patterns`` with their projections redacted.
    """
    entries = db.list_sample_sqls(source_key=source_key, table=table, limit=5)
    result: list[str] = []
    for entry in entries:
        try:
            payload = json.loads(entry["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        sql = payload.get("sql")
        if not isinstance(sql, str) or not sql:
            continue
        confidence = payload.get("confidence", "mined_low")
        if confidence != "user_verified":
            continue
        result.append(sql)
    return result


def _extract_sample_sql_patterns(db: Any, source_key: str, table: str) -> list[dict[str, Any]]:
    """Return ranked sample-SQL patterns with confidence/frequency metadata.

    Only ``user_verified`` patterns are emitted. Mined patterns —
    even with projection / JOIN-key redaction — proved to be a strong
    template attractor for the agent (a smoke run's with-history
    arm regressed three cases by structurally copying singleton mined
    patterns from per-table markdown files).
    See the comment in ``build/markdown.py`` next to the same logic
    for the full history. Verified queries are explicit endorsements
    (``mcs memory verify``), so they're safe to surface.
    """
    entries = db.list_sample_sqls(source_key=source_key, table=table, limit=5)
    patterns: list[dict[str, Any]] = []
    for entry in entries:
        try:
            payload = json.loads(entry["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        confidence = payload.get("confidence", "mined_low")
        if confidence != "user_verified":
            continue
        sql = payload.get("sql") or ""
        entry_dict: dict[str, Any] = {
            "canonical_sql": payload.get("canonical_sql", ""),
            "shape_key": payload.get("shape_key", ""),
            "normalizer_version": int(payload.get("normalizer_version") or 0),
            "frequency": int(payload.get("frequency") or 1),
            "verified_count": int(payload.get("verified_count") or 0),
            "confidence": confidence,
            "provenance": payload.get("provenance", "user_verified"),
            "where_predicates": payload.get("where_predicates") or [],
            "join_edges": payload.get("join_edges") or [],
        }
        if isinstance(sql, str) and sql:
            entry_dict["sql"] = sql
        patterns.append(entry_dict)
    return sorted(
        patterns,
        key=lambda item: (
            -item["verified_count"],
            -item["frequency"],
            item["shape_key"],
        ),
    )


def _err_no_package(profile: Profile) -> PackageNotBuiltError:
    return PackageNotBuiltError(
        f"no semantic package for profile {profile.name!r}; "
        f"use `mcs meta list-tables` / `describe-table <T>` "
        f"to discover schema directly from MaxCompute"
    )


def _err_table_not_found(profile: Profile, table: str) -> TableNotFoundError:
    return TableNotFoundError(
        f"table {table!r} not found in profile {profile.name!r}; "
        f"run `mcs meta list-tables` to see available tables"
    )


def _resolve_source_key(
    profile: Profile, table: str, db: PackageDB | None
) -> tuple[str | None, McsError | None]:
    """Resolve which source_key owns this table name.

    Single-source profiles return the sole source's key without
    touching the PackageDB. Multi-source profiles query the DB's
    per-source ownership index; an :class:`AmbiguousTableError`
    surfaces when the same table name lives in more than one source.

    Returns (source_key, None) on success or (None, err). Callers in
    single-table mode re-raise / hand err to the renderer; multi-table
    mode inlines err into the per-table result entry.
    """
    if len(profile.sources) == 1:
        return profile.sources[0].source_key(), None
    if db is None:
        return None, _err_no_package(profile)
    rows = db.find_table_by_name(table)
    if not rows:
        return None, _err_table_not_found(profile, table)
    if len(rows) > 1:
        candidates = [f"{r['source_key']}.{r['name']}" for r in rows]
        return None, AmbiguousTableError(
            f"table {table!r} exists in {len(rows)} sources "
            f"({', '.join(candidates)}); "
            f"use the FQN form 'project.schema.{table}'"
        )
    return rows[0]["source_key"], None


def _build_table_json(db: PackageDB, sk: str, table: str, profile: Any = None) -> dict[str, Any]:
    """Assemble the per-table JSON payload (annotations / joins / cols).

    Key ordering matters: Claude Code persists outputs above ~5KB and
    only shows the agent a small preview before linking to the saved
    file (which agents reliably forget to read). The semantic-layer
    fields the agent needs to gate its SQL — ``ai_context``,
    ``dimensions`` / ``metrics`` / ``identifiers``, ``join_candidates``,
    ``annotation_suggestions``, ``sample_sql_patterns`` — are emitted
    BEFORE the bulk ``columns`` array, so even a wide table (e.g. a
    74-column ``cards`` table) lands its load-bearing signal inside the
    preview window. Empty fields are dropped to claim more preview
    space for what's actually present.

    The on-disk markdown body is *not* re-emitted here when the DB is
    present — the structured fields above carry the same signal more
    compactly, and surfacing both was the dominant size cost
    (~30 KB duplicated YAML per wide table). The DB-absent code path
    in :func:`_show_single_table` still emits ``{markdown: ...}`` as
    the only available signal source.
    """
    tbl = db.get_table(sk, table)
    tid = tbl["id"] if tbl else None
    cols = db.get_columns(tid) if tid else []

    dimensions, metrics, identifiers = build_role_groups(cols)

    from maxcompute_semantic.commands._sql_name import sql_name as _sql_name

    payload: dict[str, Any] = {
        "source_key": sk,
        "table": table,
    }
    if profile is not None:
        payload["sql_name"] = _sql_name(table, sk, profile)
    if tbl is not None and tbl.get("ai_context"):
        payload["ai_context"] = tbl["ai_context"]
    if dimensions:
        payload["dimensions"] = dimensions
    if metrics:
        payload["metrics"] = metrics
    if identifiers:
        payload["identifiers"] = identifiers
    partition_columns = [c["name"] for c in cols if c.get("is_partition")]
    if partition_columns:
        payload["partition_columns"] = partition_columns
    join_candidates = [
        trim_join_candidate(jc, owner_source_key=sk)
        for jc in db.list_join_candidates(left_source_key=sk, left_table=table)
    ]
    if join_candidates:
        payload["join_candidates"] = join_candidates
    # ``annotated_cols`` mirrors the equivalent set in
    # ``build/markdown.py``'s per-table renderer: when a column is
    # already confirmed in the dimensions/metrics/identifiers block,
    # ``trim_annotation_suggestion`` strips ``where_count`` from its
    # ``history_sql`` evidence (and drops the row if no other
    # evidence remains) to suppress the over-filter bias that
    # surfaces when both surfaces show the same column twice.
    annotated_cols: set[str] = (
        {d["name"] for d in dimensions}
        | {m["name"] for m in metrics}
        | {i["name"] for i in identifiers}
    )
    annotation_suggestions: list[dict[str, Any]] = []
    for s in db.list_annotation_suggestions(source_key=sk, table_name=table):
        trimmed = trim_annotation_suggestion(
            s,
            owner_source_key=sk,
            strip_filter_evidence=s["column_name"] in annotated_cols,
        )
        if trimmed is not None:
            annotation_suggestions.append(trimmed)
    if annotation_suggestions:
        payload["annotation_suggestions"] = annotation_suggestions
    sample_sqls = _extract_sample_sqls(db, sk, table)
    if sample_sqls:
        payload["sample_sqls"] = sample_sqls
    sample_sql_patterns = _extract_sample_sql_patterns(db, sk, table)
    if sample_sql_patterns:
        payload["sample_sql_patterns"] = sample_sql_patterns
    payload["columns"] = [compact_column_entry(c) for c in cols]
    return payload


_group = click.Group()  # private container for profile_command registration


@profile_command(_group, "show", accepts_schema=False)
@click.option(
    "--table",
    default=None,
    help="show one table's column hints + sample SQL (omit for overview)",
)
@click.option(
    "--tables",
    default=None,
    help=("comma-separated table names for batch view (mutually exclusive with --table)"),
)
def show_cmd(
    pctx: ProfileContext,
    table: str | None,
    tables: str | None,
) -> None:
    """Display the semantic-package data for a profile.

    Without ``--table`` / ``--tables``: prints the overview (table
    list, JOIN graph, UDFs). With ``--table T``: prints column
    hints, partition info, enum samples, and verified queries for
    one table. With ``--tables T1,T2,T3``: same as ``--table`` but
    batched — fetches column hints + sample SQL for several tables
    in one call, with inline per-table error entries rather than
    failing the whole batch on the first miss.
    """
    if table and tables:
        raise click.UsageError("--table and --tables are mutually exclusive")

    r = pctx.renderer
    p = pctx.profile

    pdir = profile_data_dir(p)

    if tables:
        names = [t.strip() for t in tables.split(",") if t.strip()]
        if not names:
            raise click.UsageError("--tables must contain at least one table name")
        _show_multi_tables(r, p, pdir, names)
        return

    if table:
        _show_single_table(r, p, pdir, table)
        return

    _show_overview(r, p, pdir)


def _show_single_table(r: Renderer, p: Profile, pdir: Path, table: str) -> None:
    """Render one table; preserves the original error contract."""
    from maxcompute_semantic.build.storage import PackageDB

    db_path = pdir / "package.db"

    if len(p.sources) == 1:
        sk: str = p.sources[0].source_key()
    else:
        if not db_path.exists():
            err_pkg: McsError = _err_no_package(p)
            r.error(err_pkg)
            sys.exit(err_pkg.exit_code)
        db = PackageDB(db_path)
        try:
            resolved, err = _resolve_source_key(p, table, db)
        finally:
            db.close()
        if err is not None:
            r.error(err)
            sys.exit(err.exit_code)
        assert resolved is not None
        sk = resolved

    md_path = pdir / sk / f"{table}.md"
    if not md_path.exists():
        err_miss: McsError = _err_table_not_found(p, table)
        r.error(err_miss)
        sys.exit(err_miss.exit_code)

    content = md_path.read_text(encoding="utf-8")

    if r.is_envelope:
        if not db_path.exists():
            r.success({"profile": p.name, "table": table, "markdown": content})
            return
        db = PackageDB(db_path)
        try:
            structured = _build_table_json(db, sk, table, profile=p)
            structured["profile"] = p.name
            # Back-compat for agent scripts that generalized the
            # ``show --tables`` batch shape to single-table output.
            # Canonical consumers should keep using top-level
            # ``data.columns``; the alias is deliberately appended
            # after the bulk columns array so it does not push the
            # semantic gates out of Claude Code's preview window.
            table_entry = dict(structured)
            table_entry.pop("profile", None)
            table_entry["status"] = "ok"
            table_entry["name"] = table_entry["table"]
            table_entry["columns_index"] = [
                c["name"] for c in table_entry.get("columns", []) if c.get("name")
            ]
            structured["tables"] = [table_entry]
            r.success(structured)
        finally:
            db.close()
    else:
        click.echo(content, nl=False)


def _show_multi_tables(r: Renderer, p: Profile, pdir: Path, names: list[str]) -> None:
    """Render several tables in one call with inline per-table errors.

    Opens the PackageDB at most once (multi-source resolution or
    JSON assembly both need it). Each table produces either an
    ``"ok"`` entry with its full payload or an ``"error"`` entry
    carrying ``code`` + ``message``; the command exits 0 when **any**
    entry succeeded so the caller can act on partial results in a
    single round-trip. When every entry failed the command exits 5
    (resource-not-found, mirroring the single-table miss path) so
    callers can distinguish "nothing usable" from "got something."
    """
    from maxcompute_semantic.build.storage import PackageDB

    db_path = pdir / "package.db"
    is_envelope = r.is_envelope
    needs_db = is_envelope or len(p.sources) != 1

    db: PackageDB | None = None
    if needs_db:
        if not db_path.exists():
            err_pkg: McsError = _err_no_package(p)
            r.error(err_pkg)
            sys.exit(err_pkg.exit_code)
        db = PackageDB(db_path)

    error_count = 0
    try:
        if is_envelope:
            assert db is not None
            entries: list[dict[str, Any]] = []
            for name in names:
                sk, err = _resolve_source_key(p, name, db)
                if err is not None:
                    entries.append(
                        {
                            "table": name,
                            "status": "error",
                            "error": {"code": err.code, "message": err.message},
                        }
                    )
                    error_count += 1
                    continue
                assert sk is not None
                md_path = pdir / sk / f"{name}.md"
                if not md_path.exists():
                    miss = _err_table_not_found(p, name)
                    entries.append(
                        {
                            "table": name,
                            "source_key": sk,
                            "status": "error",
                            "error": {"code": miss.code, "message": miss.message},
                        }
                    )
                    error_count += 1
                    continue
                entry = _build_table_json(db, sk, name, profile=p)
                entry["status"] = "ok"
                entries.append(entry)
            payload = {"profile": p.name, "tables": entries}
            if error_count == len(names):
                # All entries failed — emit an error envelope (with the
                # per-table breakdown attached) instead of a success
                # envelope, so machine callers see status=error.
                r.error(
                    TableNotFoundError(
                        f"none of the requested tables resolved in profile {p.name!r}",
                        tables=entries,
                    )
                )
            else:
                r.success(payload)
        else:
            parts: list[str] = []
            for name in names:
                sk, err = _resolve_source_key(p, name, db)
                if err is not None:
                    parts.append(f"## {name}\nERROR: {err.message}\n")
                    error_count += 1
                    continue
                assert sk is not None
                md_path = pdir / sk / f"{name}.md"
                if not md_path.exists():
                    parts.append(
                        f"## {sk}.{name}\nERROR: table {name!r} not found in profile {p.name!r}\n"
                    )
                    error_count += 1
                    continue
                content = md_path.read_text(encoding="utf-8")
                parts.append(f"## {sk}.{name}\n{content}")
            click.echo("\n---\n\n".join(parts), nl=False)
    finally:
        if db is not None:
            db.close()

    if error_count == len(names):
        sys.exit(5)


def _show_overview(r: Renderer, p: Profile, pdir: Path) -> None:
    """Render the profile-level overview (tables / joins / UDFs)."""
    from maxcompute_semantic.build.storage import PackageDB

    md_path = pdir / "_overview.md"
    # Recommend live metadata verbs, not `mcs build` — `build` is a
    # multi-minute scan and would block the agent mid-query.
    if not md_path.exists():
        err = _err_no_package(p)
        r.error(err)
        sys.exit(err.exit_code)

    content = md_path.read_text(encoding="utf-8")

    if not r.is_envelope:
        click.echo(content, nl=False)
        return

    db_path = pdir / "package.db"
    if not db_path.exists():
        r.success({"profile": p.name, "table": None, "markdown": content})
        return

    sources_state = _read_sources_state(pdir)
    db = PackageDB(db_path)
    try:
        tables_rows = db.list_tables()
        sources_info: list[dict[str, Any]] = []
        for src in p.sources:
            sk_src = src.source_key()
            src_tables = [t for t in tables_rows if t.get("source_key") == sk_src]
            sources_info.append(
                {
                    "source_key": sk_src,
                    "project": src.project,
                    "schema": src.schema,
                    "tier": sources_state.get(sk_src, {}).get("tier", ""),
                    "tables": len(src_tables),
                }
            )
        primary_tier = sources_info[0]["tier"] if sources_info else ""
        existing_for_joins = {(t["source_key"], t["name"]) for t in tables_rows}
        joins_visible = sum(
            1
            for j in db.list_joins()
            if (j.get("left_source_key", ""), j["left_table"]) in existing_for_joins
            and (j.get("right_source_key", ""), j["right_table"]) in existing_for_joins
        )
        r.success(
            {
                "profile": p.name,
                "compute_project": p.compute_project,
                "tier": primary_tier,
                "sources": sources_info,
                "total_tables": len(tables_rows),
                "joins_count": joins_visible,
                "udfs_count": len(db.list_udfs()),
                "markdown": content,
            }
        )
    finally:
        db.close()


def _read_sources_state(pdir: Path) -> dict[str, dict[str, Any]]:
    """Read per-source state (tier, tables_count, …) from ``_state.json``.

    Returns an empty dict if the file is missing or malformed — the
    overview JSON envelope is best-effort about tier reporting and
    shouldn't crash on a partial build.
    """
    state_path = pdir / "_state.json"
    if not state_path.exists():
        return {}
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    sources = raw.get("sources") or {}
    return sources if isinstance(sources, dict) else {}
