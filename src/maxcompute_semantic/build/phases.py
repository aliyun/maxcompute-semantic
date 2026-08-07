# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Build pipeline phases 2-7.

PhaseResult is the unified return type for all phase functions. Each phase
produces a PhaseResult that the BuildPipeline orchestrator consumes.

Phase statuses:
  - "success": phase completed fully
  - "partial_failure": some items failed but the build can continue
  - "hard_error": unrecoverable; the entire build should abort

Each table-aware phase (``phase_list_tables`` / ``phase_describe_table`` /
``phase_column_sampling`` / ``phase_mine_history``) takes an explicit
``source: DataSource`` arg — the (project, schema) pair the phase operates
under. The orchestrator (``build/pipeline.py``) passes one source per phase
call; chain γ wraps phases 2-6 in ``for source in profile.sources`` so
multi-source profiles get every source built. Phase 7
(``phase_infer_joins_heuristic``) and the UDF discovery phase
(``phase_discover_udfs``) operate at the profile level — joins span
sources and UDFs are compute_project-scoped (single per profile).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from maxcompute_semantic._lib.acl_filter import should_drop_sql_for_acl
from maxcompute_semantic.build.info_schema import build_history_sql, detect_info_schema_source
from maxcompute_semantic.build.join_candidates import _apply_join_shape_adjustment
from maxcompute_semantic.build.schema_hash import schema_hash
from maxcompute_semantic.mc_client.errors import (
    McsError,
    PermissionDeniedError,
    TableNotFoundError,
)
from maxcompute_semantic.mc_client.hints import namespace_schema_hints
from maxcompute_semantic.mc_client.tier import get_tier

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import DataSource, Profile
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.mc_client.client import MaxComputeClient


@dataclass
class PhaseResult:
    """Unified result container for build pipeline phases."""

    status: str  # "success" | "partial_failure" | "hard_error"
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def resolve_active_source(profile: Profile) -> DataSource:
    """Return the first ``DataSource`` in ``profile.sources``, or a
    synthesized ``(compute_project, "default", "*")`` stand-in when the
    profile has no sources (a legal mid-wizard state where the editor
    hasn't added a source yet).

    The pipeline calls this once per source today (chain β: still
    single-source under the hood); chain γ replaces it with a
    ``for source in profile.sources`` outer loop.
    """
    from maxcompute_semantic.auth.schema import DataSource

    if profile.sources:
        return profile.sources[0]
    return DataSource(project=profile.compute_project, schema="default", tables="*")


# ── Phase 2: list_tables ─────────────────────────────────────────────────


def phase_list_tables(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
    source: DataSource,
) -> PhaseResult:
    """Discover all tables in ``source``'s ``(project, schema)`` pair.

    Inserts a placeholder row with ``schema_hash="pending"`` for any
    name not yet seen so subsequent phases can look the table up by
    ``(source_key, name)``. Tables already present keep their cached
    ``schema_hash``; otherwise an unconditional upsert here would clobber
    the cache and make ``_run_refresh`` reclassify every table as
    "changed" on every run.
    """
    try:
        table_names = client.list_tables(schema=source.schema, project=source.project)
    except McsError as exc:
        # pipeline.py reads errors[0]["exception"] on hard_error to re-raise
        # the original classified McsError; without it the remediation is lost.
        return PhaseResult(
            status="hard_error",
            errors=[
                {
                    "exception": exc,
                    "code": exc.code,
                    "message": exc.message,
                    "source_key": source.source_key(),
                }
            ],
        )

    # Apply the source's profile-side allowlist. ``tables: '*'`` (the
    # wildcard, ``is_wildcard()`` true) means "every table in the live
    # catalog"; an enumerated list narrows that to the named subset.
    # Names listed in the profile but absent from the live catalog
    # surface as warnings — that's the typo / table-dropped-on-source
    # signal the user wants visible without aborting the build.
    warnings: list[str] = []
    if not source.is_wildcard():
        allowed = set(source.table_names())
        live = set(table_names)
        missing = sorted(allowed - live)
        if missing:
            warnings.append(
                f"{source.project}.{source.schema}: "
                f"{len(missing)} allowlisted table(s) not found in live catalog: "
                f"{', '.join(missing)}"
            )
        # Preserve the live-catalog ordering so subsequent phases see a
        # stable iteration order.
        table_names = [t for t in table_names if t in allowed]

    sk = source.source_key()
    existing = {row["name"] for row in db.list_tables(source_key=sk)}
    for name in table_names:
        if name not in existing:
            db.upsert_table(sk, name, schema_hash="pending", errors_json=None)

    return PhaseResult(
        status="success",
        data={"table_names": table_names},
        warnings=warnings,
    )


# ── Phase 3: discover_udfs ──────────────────────────────────────────────


def phase_discover_udfs(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
) -> PhaseResult:
    """Discover UDFs in the AK's compute_project.

    UDFs are compute_project-scoped (one per profile, regardless of
    how many ``DataSource`` entries the profile has) — so this phase
    runs once per pipeline invocation, not per source.

    Soft-failure: build continues without UDF data when the client
    backend has no list-functions support, when the listing is
    denied / fails with a classified ``McsError``, or when any
    other exception fires. Classified failures forward code +
    remediation through to ``mcs status``.
    """
    try:
        udfs = client.list_functions()
    except NotImplementedError:
        return PhaseResult(
            status="partial_failure",
            warnings=["UDF discovery unavailable (client backend has no list_functions)"],
        )
    except McsError as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"UDF discovery failed: {exc.message}"],
            errors=[{"phase": "udf", "code": exc.code, "message": exc.message}],
        )
    except Exception as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"UDF discovery failed unexpectedly: {exc!r}"],
        )

    for udf in udfs:
        # udf is expected to be a dict with name, kind, signature, class_name, description
        db.upsert_udf(
            name=udf["name"] or "",
            kind=udf.get("kind") or "",
            signature=udf.get("signature"),
            class_name=udf.get("class_name"),
            description=udf.get("description"),
        )

    return PhaseResult(status="success", data={"udf_count": len(udfs)})


# ── Phase 4: describe_table ──────────────────────────────────────────────


def phase_describe_table(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
    source: DataSource,
    table_name: str,
) -> PhaseResult:
    """Describe a single table within ``source``: extract columns,
    compute schema_hash, upsert into db under ``source.source_key()``.

    Regular columns come from the ``schema`` key; partition columns from
    ``partition_columns``. Both are combined for the PackageDB upsert
    (partition columns marked with is_partition=1).

    Per-table permission and not-found errors —
    PermissionDeniedError and TableNotFoundError — are soft failures:
    the reason is recorded in the table's errors_json so downstream
    phases skip the table and ``mcs status --tables`` surfaces the
    per-table reason. All other McsError subclasses are hard errors
    that abort the per-table loop.
    """
    sk = source.source_key()
    try:
        result = client.describe_table(
            name=table_name,
            schema=source.schema,
            project=source.project,
        )
    except (
        PermissionDeniedError,
        TableNotFoundError,
    ) as exc:
        # Soft failure: record the error on the table row.
        error_info = {
            "phase": "describe",
            "code": exc.code,
            "message": exc.message,
        }
        db.upsert_table(
            sk,
            table_name,
            schema_hash="pending",
            errors_json=json.dumps(error_info),
        )
        return PhaseResult(
            status="partial_failure",
            errors=[{"table": table_name, "code": exc.code, "message": exc.message}],
        )
    except McsError as exc:
        return PhaseResult(
            status="hard_error",
            errors=[{"table": table_name, "code": exc.code, "message": exc.message}],
        )

    table_data = result["table"]
    raw_cols = table_data.get("schema", [])
    raw_part_cols = table_data.get("partition_columns", [])

    # Combine regular + partition columns for db upsert.
    # Partition columns may also appear in schema (MC returns them twice).
    # Dedup: skip regular-col entries whose name matches a partition column.
    part_names = {col["name"] for col in raw_part_cols}
    columns: list[dict[str, Any]] = []
    for col in raw_cols:
        if col["name"] in part_names:
            continue  # will be added below as partition column
        columns.append(
            {
                "name": col["name"],
                "type": col["type"],
                "comment": col.get("comment", ""),
                "is_partition": 0,
            }
        )
    for col in raw_part_cols:
        columns.append(
            {
                "name": col["name"],
                "type": col["type"],
                "comment": col.get("comment", ""),
                "is_partition": 1,
            }
        )

    # Compute schema_hash from name+type pairs only.
    hash_val = schema_hash(columns)

    # Extract pyodps Table.type.value from the client envelope. Used by
    # the pipeline to skip sampling/profiling on VIRTUAL_VIEW (re-executes
    # underlying SQL, may JOIN, times out the per-call cost gate) and on
    # OBJECT_TABLE (no row structure). MANAGED_TABLE, EXTERNAL_TABLE,
    # MATERIALIZED_VIEW are all treated as physical tables for profiling.
    table_type = table_data.get("type")

    # Upsert table with computed hash (clearing any prior errors_json).
    # NOTE: data_modified_at is deliberately NOT written here — describe
    # runs every build/refresh, and overwriting the stored baseline would
    # erase the "data as of last sample" reference the refresh diff uses
    # to detect data changes. The live value is surfaced in the result and
    # only persisted (via record_sampled) after the table is re-sampled.
    table_id = db.upsert_table(
        sk, table_name, schema_hash=hash_val, errors_json=None, table_type=table_type
    )

    # Upsert columns.
    db.upsert_columns(table_id, columns)

    return PhaseResult(
        status="success",
        data={
            "table_name": table_name,
            "column_count": len(columns),
            "partition_column_count": len(raw_part_cols),
            "schema_hash": hash_val,
            "data_modified_at": table_data.get("last_data_modified_time"),
        },
    )


# ── Phase 5: column_sampling ──────────────────────────────────────────────


def phase_column_sampling(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
    source: DataSource,
    table_name: str,
) -> PhaseResult:
    """Sample column data for a table within ``source`` to compute
    null_ratio, distinct_count, is_enum.

    Executes ``SELECT * FROM <fq_name> [WHERE partition] LIMIT 20`` via
    client.execute_sql().  For partitioned tables, adds a WHERE clause for
    the latest partition.

    All errors during sampling are soft failures — the build continues with
    existing column descriptions intact.
    """
    sk = source.source_key()
    table_row = db.get_table(sk, table_name)
    if table_row is None:
        return PhaseResult(
            status="hard_error",
            errors=[
                {
                    "table": table_name,
                    "code": "TableNotFound",
                    "message": f"Table {table_name} not in db",
                }
            ],
        )

    table_id = table_row["id"]
    existing_cols = db.get_columns(table_id)

    # SQL form depends on the *connection's* tier (``compute_project``)
    # and whether the source is cross-project. ``qualified_for_connection``
    # encodes the full cross-tier matrix the 2026-05-22 probes
    # confirmed: 3-level connections accept 3-segment form universally
    # (with ``odps.namespace.schema=true``, injected by ``build_hints``
    # via ``client._tier``); 2-level connections reject any 3-segment
    # form and need the 2-segment ``<src.proj>.<table>`` shape for
    # cross-project reads.
    conn_tier = get_tier(profile, profile.compute_project, client=client)
    fq_name = source.qualified_for_connection(
        table_name, conn_tier=conn_tier, compute_project=profile.compute_project
    )
    extra_hints = source.connection_hints(conn_tier=conn_tier)

    # Check for partition columns and build WHERE clause.
    partition_cols = [c for c in existing_cols if c["is_partition"] == 1]
    where_clause = ""
    if partition_cols:
        try:
            part_info = client.list_partitions(
                table_name,
                schema=source.schema,
                project=source.project,
            )
            latest = part_info.get("latest_partition")
            if latest:
                where_parts = []
                for segment in latest.split("/"):
                    if "=" in segment:
                        col, val = segment.split("=", 1)
                        where_parts.append(f"{col} = '{val}'")
                if where_parts:
                    where_clause = "WHERE " + " AND ".join(where_parts)
        except Exception:
            # Can't determine partition filter; try without filter.
            where_clause = ""

    sql = f"SELECT * FROM {fq_name} {where_clause} LIMIT 20"

    try:
        # schema= triggers build_hints to inject odps.namespace.schema=true
        # on 3-level projects so the 3-part fq_name parses.
        # assume_yes=True: build phases own their cost budgeting
        # (profile_budget_cny in BuildOptions) and run non-interactively, so
        # the per-call cost gate should not prompt — the blocked-ceiling
        # still fires and is caught as McsError below.
        envelope = client.execute_sql(
            sql, schema=source.schema, hints=extra_hints, assume_yes=True, timeout=120,
        )
        rows = envelope.data.get("rows", []) if envelope.data else []
    except McsError as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"Sampling failed for {table_name}: {exc.message}"],
            errors=[{"table": table_name, "code": exc.code, "message": exc.message}],
        )
    except Exception as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"Sampling failed for {table_name}: {exc}"],
        )

    if not rows:
        return PhaseResult(
            status="success",
            data={"table_name": table_name, "sampled_rows": 0},
        )

    total_rows = len(rows)
    updated_cols = list(existing_cols)
    for col in existing_cols:
        col_name = col["name"]
        if col["is_partition"]:
            continue

        values = [row.get(col_name) for row in rows]
        non_null = [v for v in values if v is not None]
        null_ratio = (total_rows - len(non_null)) / total_rows
        distinct_vals = set(str(v) for v in non_null)
        distinct_count = len(distinct_vals)
        max_len = max((len(str(v)) for v in non_null), default=0)
        # All-NULL columns have distinct_count=0; without an observed value
        # we have no evidence the column is an enum. Marking it as one
        # produces an internally inconsistent row (is_enum=1, sample_values=[])
        # that downstream surfaces render as a bogus "enum with no values".
        is_enum = 1 <= distinct_count <= 30 and max_len <= 80

        # Find the matching column in updated_cols and set stats.
        for uc in updated_cols:
            if uc["name"] == col_name:
                uc["null_ratio"] = null_ratio
                uc["distinct_count"] = distinct_count
                uc["is_enum"] = 1 if is_enum else 0
                if is_enum:
                    sample_vals = sorted(distinct_vals)[:30]
                    uc["sample_values_json"] = json.dumps(sample_vals)
                else:
                    # Non-enum STRING columns still keep a small set of
                    # format examples — that's how ``_date_format_hint``
                    # detects ``str-date`` / ``str-datetime`` columns at
                    # high NDV (a STRING ``signup_date`` column with
                    # 50k distinct ISO-date values cannot be an enum but
                    # the format-wrap hint is the difference between an
                    # agent writing ``YEAR(col)`` (silently NULLs out on
                    # STRING in MaxCompute) versus ``SUBSTR(col, 1, 4)``).
                    # Same payload field as the enum branch but the
                    # ``is_enum`` flag tells the renderer which key to
                    # emit (``sample_values`` vs ``format_examples``).
                    # Length cap matches the enum gate so a TEXT blob
                    # column doesn't spam 5×N bytes per render.
                    col_type_str = (uc.get("type") or "").upper()
                    is_string_type = any(t in col_type_str for t in ("STRING", "VARCHAR", "CHAR"))
                    if is_string_type and distinct_count >= 2 and max_len <= 80:
                        sample_vals = sorted(distinct_vals)[:5]
                        uc["sample_values_json"] = json.dumps(sample_vals)
                    else:
                        # A re-sample may flip a column from is_enum=True
                        # to False (cardinality grew, or the sample is
                        # now 100% NULL). Clear any prior
                        # sample_values_json so downstream surfaces don't
                        # keep advertising stale enum values for a column
                        # the current sample no longer supports.
                        uc["sample_values_json"] = None
                break

    db.upsert_columns(table_id, updated_cols)

    return PhaseResult(
        status="success",
        data={"table_name": table_name, "sampled_rows": total_rows},
    )


# ── Phase 5b: column_profiling ─────────────────────────────────────────


def phase_column_profiling(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
    source: DataSource,
    table_name: str,
    *,
    workload_columns: set[str] | None = None,
    max_columns: int | None = None,
) -> PhaseResult:
    """Profile high-value columns using bounded aggregate SQL.

    Runs one ``APPROX_DISTINCT + null count + row count`` query per table,
    scoped to the latest partition when applicable. Results are stored via
    ``db.update_column_profile()`` — separate from the schema-level
    ``upsert_columns()``.
    """
    from maxcompute_semantic.build.profiling import (
        DEFAULT_PROFILE_LIMIT,
        apply_profile_result,
        build_column_profile_sql,
        select_profile_columns,
    )

    sk = source.source_key()
    table_row = db.get_table(sk, table_name)
    if table_row is None:
        return PhaseResult(
            status="hard_error",
            errors=[{"table": table_name, "code": "TableNotFound"}],
        )

    table_id = table_row["id"]
    columns = db.get_columns(table_id)
    limit = max_columns if max_columns is not None else DEFAULT_PROFILE_LIMIT
    selected = select_profile_columns(
        columns,
        workload_columns=workload_columns,
        limit=limit,
    )
    if not selected:
        return PhaseResult(
            status="success",
            data={"table_name": table_name, "profiled_columns": 0},
        )

    # See ``phase_column_sampling`` — SQL form is governed by the
    # connection's tier and same-project-ness via
    # ``qualified_for_connection``.
    conn_tier = get_tier(profile, profile.compute_project, client=client)
    fq_name = source.qualified_for_connection(
        table_name, conn_tier=conn_tier, compute_project=profile.compute_project
    )
    extra_hints = source.connection_hints(conn_tier=conn_tier)

    partition_cols = [c for c in columns if c["is_partition"] == 1]
    where_clause = ""
    if partition_cols:
        try:
            part_info = client.list_partitions(
                table_name,
                schema=source.schema,
                project=source.project,
            )
            latest = part_info.get("latest_partition")
            if latest:
                where_parts = []
                for segment in latest.split("/"):
                    if "=" in segment:
                        col, val = segment.split("=", 1)
                        where_parts.append(f"{col} = '{val}'")
                if where_parts:
                    where_clause = "WHERE " + " AND ".join(where_parts)
        except Exception:
            where_clause = ""

    column_types = {c["name"]: c.get("type", "") for c in columns}
    sql = build_column_profile_sql(
        fq_name=fq_name,
        columns=selected,
        where_clause=where_clause,
        column_types=column_types,
    )

    try:
        # assume_yes=True: see sampling phase comment — build owns its
        # own cost budget; per-call cost gate should not prompt.
        envelope = client.execute_sql(
            sql, schema=source.schema, hints=extra_hints, assume_yes=True, timeout=120,
        )
    except McsError as exc:
        return PhaseResult(
            status="partial_failure",
            errors=[{"table": table_name, "code": exc.code, "message": exc.message}],
            data={"table_name": table_name, "profiled_columns": 0},
        )

    rows = envelope.data.get("rows", []) if envelope.data else []
    if not rows:
        return PhaseResult(
            status="success",
            data={"table_name": table_name, "profiled_columns": 0},
        )

    row = rows[0]
    scope = "latest_partition" if where_clause else "full_table"
    updated = apply_profile_result(
        columns,
        selected_columns=selected,
        row=row,
        scope=scope,
        method="approx_ndv",
    )

    profile_fields = [
        {
            "name": c["name"],
            "row_count": c.get("row_count"),
            "approx_ndv": c.get("approx_ndv"),
            "uniqueness_ratio": c.get("uniqueness_ratio"),
            "is_enum": c.get("is_enum"),
            "null_ratio": c.get("null_ratio"),
            "cast_rate": c.get("cast_rate"),
            "profile_scope": c.get("profile_scope"),
            "profile_method": c.get("profile_method"),
            "profile_confidence": c.get("profile_confidence"),
        }
        for c in updated
        if c["name"] in selected
    ]

    db.update_columns_profile_batch(sk, table_name, profile_fields)

    return PhaseResult(
        status="success",
        data={"table_name": table_name, "profiled_columns": len(selected)},
    )


# ── Phase 6: mine_history ─────────────────────────────────────────────────


def _normalize_signature(sql: str) -> str:
    """Normalize SQL for dedup: lowercase, collapse whitespace, strip comments."""
    # Remove single-line comments.
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    # Remove multi-line comments.
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Collapse whitespace.
    sql = re.sub(r"\s+", " ", sql).strip().lower()
    return sql


def phase_mine_history(
    client: MaxComputeClient,
    db: PackageDB,
    profile: Profile,
    source: DataSource,
) -> PhaseResult:
    """Mine ``<source.project>.information_schema.tasks_history`` for
    verified SQL queries that reference tables in this source.

    Soft failure: if history mining fails (source=none or query error),
    the build continues without verified queries.
    """
    cache_dir = db._path.parent

    try:
        info_source = detect_info_schema_source(profile, client, cache_dir)
    except Exception as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"Info-schema detection failed: {exc}"],
        )

    if info_source == "none":
        return PhaseResult(
            status="success",
            data={"history_skipped": True, "info_schema_source": "none"},
        )

    sql = build_history_sql(source.project, info_source, lookback_days=14, limit=2000)

    hints = namespace_schema_hints(info_source == "tenant")

    try:
        # assume_yes=True: see sampling phase comment — build owns its
        # own cost budget; per-call cost gate should not prompt.
        envelope = client.execute_sql(sql, hints=hints, assume_yes=True, timeout=120)
        rows = envelope.data.get("rows", []) if envelope.data else []
    except McsError as exc:
        return PhaseResult(
            status="partial_failure",
            warnings=[f"History mining query failed: {exc.message}"],
            errors=[{"code": exc.code, "message": exc.message}],
        )

    # Restrict matching to *this source's* tables — verified queries
    # get attributed to the (source, table) they actually reference.
    # Cross-source attribution lands later if it becomes useful; for
    # now keeping mining scoped per-source matches the build pipeline's
    # per-source iteration.
    sk = source.source_key()
    table_rows = db.list_tables(source_key=sk)
    table_names_in_db = [t["name"] for t in table_rows]

    if not table_names_in_db:
        return PhaseResult(
            status="success",
            data={
                "sample_sql_candidates": {},
                "verified_queries": {},
                "info_schema_source": info_source,
            },
        )

    # Build the source-table set (lowercase, for case-insensitive
    # comparison against sqlglot-extracted bare table names) plus a
    # case-preserving lookup so attribution keys match what the rest
    # of PackageDB stores. Also build a regex fallback used only when
    # sqlglot fails to parse an SQL string (sort longest-first so
    # multi-word table names match first).
    allowed_lower = {name.lower() for name in table_names_in_db}
    canonical_by_lower = {name.lower(): name for name in table_names_in_db}
    table_pattern = "|".join(sorted(table_names_in_db, key=len, reverse=True))
    table_re = re.compile(rf"\b({table_pattern})\b", re.IGNORECASE)

    # Extract mined sample-SQL candidates. These are successful historical
    # SQL tasks, not user-confirmed verified queries. Keep literal variants
    # so the persistence layer can aggregate frequency by normalized shape.
    sample_sql_candidates: dict[str, list[str]] = {}
    seen_exact_by_table: dict[str, set[str]] = {}

    for row in rows:
        operation_text = row.get("operation_text", "")
        if not operation_text:
            continue

        # sqlglot-based attribution: only count true ``exp.Table`` refs,
        # not string literals / comments / column names that happen to
        # collide with a source table name. On parse error, fall back
        # to the regex (MaxCompute has non-standard syntax sqlglot
        # occasionally rejects — losing those SQLs entirely would
        # silently shrink mining coverage).
        # Lazy: sql_pattern pulls sqlglot + the dialect package, which
        # must stay off the CLI startup chain.
        from maxcompute_semantic.memory.sql_pattern import analyze_sql_pattern

        pattern = analyze_sql_pattern(operation_text)
        if pattern.parse_error:
            matched_tables = list(set(table_re.findall(operation_text)))
        else:
            matched_lower = set(pattern.tables) & allowed_lower
            matched_tables = [canonical_by_lower[lc] for lc in matched_lower]

        for table in matched_tables:
            table_row = db.get_table(sk, table)
            if table_row is None:
                continue
            # Column-level ACL filter is a guaranteed no-op today
            # (``allowlist=None`` short-circuits in `_lib.acl_filter`),
            # but the call stays wired so future Profile.allow_columns
            # support drops in without touching the mining phase.
            if should_drop_sql_for_acl(
                operation_text,
                table,
                all_cols=[],
                partition_cols=[],
                allowlist=None,
            ):
                continue

            exact_sig = _normalize_signature(operation_text)
            seen_for_table = seen_exact_by_table.setdefault(table, set())
            if exact_sig in seen_for_table:
                continue
            seen_for_table.add(exact_sig)

            sample_sql_candidates.setdefault(table, []).append(operation_text)

    return PhaseResult(
        status="success",
        data={
            "sample_sql_candidates": sample_sql_candidates,
            "verified_queries": sample_sql_candidates,
            "info_schema_source": info_source,
        },
    )


# ── Phase 7: infer_joins_heuristic ────────────────────────────────────────


def _find_matching_table(base_name: str, table_names: set[str]) -> str | None:
    """Find a table matching the base name of an ``_id`` column.

    Tries exact match first, then simple plural (base + ``s``).
    """
    if base_name in table_names:
        return base_name
    plural = base_name + "s"
    if plural in table_names:
        return plural
    return None


def _infer_cardinality(left_col: dict, right_col: dict | None) -> str:
    """Infer join cardinality from PK-likeness of both sides.

    Uses ``_looks_like_primary_key`` (uniqueness_ratio ≥ 0.95 when
    available, else name == "id") so the cardinality label survives
    cases where the actual PK is named something other than ``id``
    (``customer_id`` in a ``customers`` table, ``uuid`` in
    ``cards``). When ``right_col`` is ``None`` (e.g. the loose_id
    pattern has no real right-hand side) both sides default to
    non-PK and we return ``n:m``.

    - left is PK, right is PK -> 1:1
    - left is PK, right is not -> 1:n
    - right is PK, left is not -> n:1
    - neither -> n:m

    Concrete bug this prevents: when two tables share a non-``id``
    natural-key column (e.g. ``uuid``) and the FK-side table also has
    its own ``id`` column, the old name-only check would mis-orient
    the relationship as ``n:1`` instead of the actual ``1:n``
    (PK-side uniqueness rules, not column-name).
    """
    left_is_pk = _looks_like_primary_key(left_col)
    right_is_pk = _looks_like_primary_key(right_col) if right_col else False
    if left_is_pk and right_is_pk:
        return "1:1"
    if left_is_pk:
        return "1:n"
    if right_is_pk:
        return "n:1"
    return "n:m"


# Threshold above which a column's ``uniqueness_ratio`` qualifies it as
# "PK-like" for the purposes of suppressing coincidental same-name joins.
# Two tables that both have a near-unique column with the same name (e.g.
# both have ``id`` as their PK, both have ``uuid`` as their natural PK)
# are almost never joined on that column — real cross-table joins follow
# the FK pattern where one side is PK-like and the other is not.
PK_LIKE_UNIQUENESS_THRESHOLD = 0.95


def _looks_like_fk_name(col_name: str, name_index: dict[str, list]) -> bool:
    """Return True when a column name credibly signals a foreign key.

    Recognizes both common naming conventions:

    - **Rails/Django**: ``_id`` suffix, e.g. ``customer_id``,
      ``team_api_id``. Always treated as FK-shaped — the underscore
      makes the suffix unambiguous.
    - **SQLite-style**: no-underscore ``id`` suffix, e.g.
      ``customerid``, ``accountid``, ``scorecardid``. Only accepted when
      the prefix matches a table name (singular or plural) elsewhere
      in the profile, so false friends like ``bid``, ``void``,
      ``uuid`` aren't mistaken for FKs.

    Bare ``id`` is **not** FK-shaped (that's the PK).
    """
    if col_name == "id":
        return False
    if col_name.endswith("_id"):
        return True
    if not col_name.endswith("id") or len(col_name) <= 2:
        return False
    base = col_name[:-2]
    return base in name_index or (base + "s") in name_index


def _fk_suffix_form(col_name: str) -> tuple[str | None, str | None]:
    """Decompose an FK-shaped column name into ``(base, suffix_kind)``.

    Recognizes two suffix conventions:

    - ``"_id"`` — Rails/Django style, always FK-shaped. ``suffix_kind``
      is ``"_id"``. Underscore-split trailing-word recovery is enabled
      by callers (see pattern 1 in the heuristic).
    - ``"id"`` (no underscore) — SQLite/StackExchange style.
      ``suffix_kind`` is ``"id"``. Only signaled when ``len(col_name)
      >= 5`` so the base has at least 3 characters — this rules out
      ``bid``, ``aid``, ``paid``, ``void``, ``uuid`` that share the
      trailing ``id`` but aren't foreign keys.

    Bare ``id`` returns ``(None, None)`` — that's the PK.
    """
    if col_name == "id":
        return None, None
    if col_name.endswith("_id"):
        return col_name[:-3], "_id"
    if col_name.endswith("id") and len(col_name) >= 5:
        return col_name[:-2], "id"
    return None, None


# Generic identifier names that, when shared between two PK-like
# columns, are almost always coincidental rather than a real join.
# Distinctive PK names (e.g. ``cdscode``, ``account_number``, ``ssn``)
# pass this filter and become 1:1 entity-split joins; generic ones
# stay dropped.
_GENERIC_ID_NAMES = frozenset({"id", "uuid", "pk", "key", "code", "rid", "gid", "uid", "num", "no"})


def _is_generic_id_name(name: str) -> bool:
    """Return True when *name* is a generic identifier — too plain to
    distinguish coincidental PK name collisions from real entity-split
    1:1 joins.

    Two heuristics combined:
    1. ≤3 characters: ``id``, ``pk``, ``no`` are inherently generic.
    2. Membership in the small ``_GENERIC_ID_NAMES`` lexicon: catches
       4-char generic identifiers (``uuid``, ``code``) that don't
       trip the length guard but are still domain-agnostic.

    Distinctive PK names (``cdscode``, ``account_number``, ``ssn``,
    ``isbn``) fail both checks and pass through the gate as legitimate
    entity-split candidates.
    """
    n = name.lower()
    return len(n) <= 3 or n in _GENERIC_ID_NAMES


def _looks_like_primary_key(col: dict) -> bool:
    """Return True when a column is plausibly the table's primary key.

    When ``uniqueness_ratio`` is populated, trust it: a column with low
    uniqueness is not a PK no matter what it's named. Only fall back
    to name-based detection (column named ``id``) when stats are
    missing — typical for tables that didn't reach the profiling
    phase, or for the small handful of fixtures that exercise the
    join phase directly without going through ``phase_column_profiling``.

    Concrete failure mode this guard prevents: when a child table's
    ``id`` column is the FK to the parent's ``id`` column (rather than
    the child's own PK), it will have low ``uniqueness_ratio`` (e.g.
    ≈0.02 if it repeats ~50× per parent). A name-only check would
    classify it as PK and drop the legitimate ``parent.id = child.id``
    same_name edge as a coincidental PK↔PK collision, which makes
    downstream queries predict the wrong join.
    """
    ratio = col.get("uniqueness_ratio")
    if ratio is not None:
        return float(ratio) >= PK_LIKE_UNIQUENESS_THRESHOLD
    return col.get("name") == "id"


# Penalty applied to cross-source join confidence — within-source joins
# are baseline (1.0× the heuristic's pattern confidence), cross-source
# joins multiply by this factor. A cross-source ``link_to`` (pattern
# confidence 0.9) lands at 0.72; a cross-source ``loose_id`` (0.3) lands
# at 0.24, near the noise floor. Tuned conservatively to keep cross-source
# matches from drowning out within-source ones in the joins table.
CROSS_SOURCE_CONFIDENCE_PENALTY = 0.8


# Same regex shape as ``markdown._DATE_RE`` (kept in sync — STRING column
# is "date-shaped" when its sample values mostly start with a YYYY-MM-DD
# / YYYY/MM/DD prefix). Duplicated rather than imported to keep
# ``markdown`` from being pulled in at join-inference time.
_DATE_VALUE_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?"
    r"|^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}",
)
_NATIVE_TEMPORAL_TYPES = frozenset({"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ"})


def _looks_like_temporal_column(col: dict) -> bool:
    """Return True when *col* holds a date / time value rather than an
    entity identifier.

    Temporal columns frequently land on the wrong side of the same_name
    pattern's PK-like gate: a ``creationdate`` timestamp at sub-second
    precision has ``uniqueness_ratio`` ≈ 1.0, which makes the heuristic
    accept it as PK-like and emit a phantom
    ``T1.creationdate = T2.creationdate`` edge between unrelated tables.
    The intuition: a date column's uniqueness comes from the time
    dimension (every event happens at a slightly different instant),
    not from being an entity identifier. Treating it as a PK creates
    Cartesian-shaped phantom joins across every table that records a
    timestamp.

    Concrete failure mode this guard prevents: in any schema where
    several event-like tables (e.g. ``comments``, ``posts``, ``votes``)
    all carry a ``created_at`` / ``creationdate`` column with sub-second
    precision, each side will have uniqueness ≈ 1.0; the pre-fix
    same_name pattern emitted many ``via creationdate [1:1]`` /
    ``[1:n]`` edges across the table soup, polluting ``_overview.md``
    with fake join paths the agent then tried to use.

    Detection (any one is sufficient):
    - Native temporal type — ``DATE`` / ``DATETIME`` / ``TIMESTAMP`` /
      ``TIMESTAMP_NTZ``.
    - Confirmed time-dimension annotation — ``dim_type == 'time'``.
    - STRING/VARCHAR/CHAR column whose sample values mostly match the
      ``YYYY-MM-DD``-prefix regex (50%-majority threshold, same shape
      as ``markdown._date_format_hint``).

    The check is symmetric and called once per side in the same_name
    loop — both sides have to be temporal for the edge to be dropped,
    so a legitimate ``orders.created_at = order_status.changed_at``
    pattern survives only when one side is the entity column and the
    name-match itself collapses to ``id``-style.
    """
    col_type = (col.get("type") or "").upper()
    head = col_type.split("(", 1)[0].split("<", 1)[0].strip()
    if head in _NATIVE_TEMPORAL_TYPES:
        return True
    if col.get("dim_type") == "time":
        return True
    if head not in ("STRING", "VARCHAR", "CHAR"):
        return False
    raw = col.get("sample_values_json")
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, list) or not parsed:
        return False
    matches = sum(1 for v in parsed if isinstance(v, str) and _DATE_VALUE_RE.match(v.strip()))
    return matches >= max(1, len(parsed) // 2)


# Match http:// and https:// prefixes — the only reliable shape signal for
# "this STRING column holds a URL, not an identifier". File:// / ftp:// /
# data: URIs aren't worth catching; in practice they never collide as same_name
# across multiple tables in a real schema.
_URL_VALUE_RE = re.compile(r"^https?://", re.IGNORECASE)


def _looks_like_url_column(col: dict) -> bool:
    """Return True when *col* holds a URL value rather than an entity
    identifier.

    URL columns trip the same same_name PK-like gate as temporal columns:
    each table's Wikipedia/profile/image URL is unique per row
    (``uniqueness_ratio`` ≈ 1.0), so the heuristic accepts the column as
    PK-like and emits a phantom ``T1.url = T2.url`` edge between unrelated
    entity tables. The intuition: a URL is a content URI that identifies
    one specific entity; sharing the column name across tables means each
    table has its OWN URLs (each entity table has its own per-row URLs),
    not that the URLs reference each other.

    Concrete failure mode this guard prevents: schemas where several
    independent entity tables each carry a ``url`` STRING column with
    Wikipedia / homepage / profile URLs. Pre-fix same_name pattern
    emitted many ``via url`` edges across the table soup, polluting
    ``_overview.md`` with fake join paths.

    Detection: STRING/VARCHAR/CHAR column whose sample values mostly
    start with ``http://`` or ``https://`` (50%-majority threshold, same
    shape as ``_looks_like_temporal_column``).

    The check is symmetric and called once per side in the same_name
    loop — both sides have to be URL-shaped for the edge to be dropped,
    so a legitimate ``page.url = url_lookup.url`` cross-reference where
    only one side is the URL bag survives.
    """
    col_type = (col.get("type") or "").upper()
    head = col_type.split("(", 1)[0].split("<", 1)[0].strip()
    if head not in ("STRING", "VARCHAR", "CHAR"):
        return False
    raw = col.get("sample_values_json")
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, list) or not parsed:
        return False
    matches = sum(1 for v in parsed if isinstance(v, str) and _URL_VALUE_RE.match(v.strip()))
    return matches >= max(1, len(parsed) // 2)


# NUMERIC type names that classify a column as holding a value (amount,
# score, weight, count, year, ...) rather than a string identifier. The
# match runs against the leading word of the type string so parameterized
# forms like ``DECIMAL(10,2)`` still resolve.
_NUMERIC_VALUE_TYPES = frozenset(
    {
        "BIGINT",
        "INT",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "DECIMAL",
        "NUMERIC",
        "FLOAT",
        "DOUBLE",
        "REAL",
    }
)


def _looks_like_numeric_value_column(col: dict) -> bool:
    """Return True when *col* holds a numeric value (amount, score, weight,
    count, year, ...) rather than an entity identifier.

    Numeric value columns trip the same_name PK-like gate when one side
    happens to have high uniqueness — e.g. a monetary ``amount`` column
    on a low-volume table where each row carries a distinct dollar value
    can have uniqueness ≈ 0.97, which pushes it above the
    ``PK_LIKE_UNIQUENESS_THRESHOLD``; the heuristic then accepts it as
    PK-like and emits phantom ``T1.amount = T2.amount`` edges between
    unrelated tables, even though monetary amounts are values, not
    entity keys.

    The intuition: a numeric column whose name has no identity-suggesting
    suffix is a metric/measure (``amount``, ``score``, ``height``,
    ``year``, ``count``, ``balance``, ``duration``, ``payments``); its
    uniqueness comes from continuous value distribution, not from being
    a foreign key. Two unrelated tables sharing such a column name are
    almost never legitimately joinable through it.

    Concrete failure mode this guard prevents: when three transactional
    tables (e.g. ``loan``, ``order``, ``trans``) all carry an ``amount``
    numeric column with high-uniqueness amounts on one side and lower-
    uniqueness amounts on the others, the pre-fix same_name pattern
    emits phantom ``via amount`` edges across each pair, polluting
    ``_overview.md`` and propagating as ``amount:int [fk]`` markers in
    the columns_index.

    Detection:
    - NUMERIC type (``BIGINT`` / ``INT`` / ``DECIMAL`` / ``DOUBLE`` /
      ``FLOAT`` / ...).
    - Name does NOT end with an identity-suggesting suffix (``id``,
      ``_id``, ``key``, ``_key``, ``code``, ``_code``, ``num``, ``_num``,
      ``no``, ``_no``, ``ref``, ``_ref``).

    The check is symmetric and called once per side in the same_name
    loop — both sides have to be numeric value columns for the edge to
    be dropped, so a legitimate ``fact.year_id = dim_date.year`` survives
    via the FK-shape branch (left side is ``_id``-suffixed), and a
    legitimate ``account.balance_at_close = ledger.balance_at_close``
    where ``balance_at_close`` is a true natural key (one side fully
    unique on the value, but encoded as STRING/UUID) survives because
    the type doesn't match the NUMERIC gate.
    """
    col_type = (col.get("type") or "").upper()
    head = col_type.split("(", 1)[0].split("<", 1)[0].strip()
    if head not in _NUMERIC_VALUE_TYPES:
        return False
    name = (col.get("name") or "").lower()
    if not name:
        return False
    if name == "id":
        return False
    # Bare-suffix length guard mirrors ``_fk_suffix_form`` — avoid
    # mis-classifying short tokens (``id``, ``no``) when they ARE the
    # whole name. ``id`` is already filtered above; ``no`` / ``ref``
    # etc. as the entire name are rare and likely metric-like, but
    # conservatism keeps them out.
    id_suffixes = (
        "_id",
        "id",
        "_key",
        "key",
        "_code",
        "code",
        "_num",
        "num",
        "_no",
        "no",
        "_ref",
        "ref",
    )
    for suffix in id_suffixes:
        if name.endswith(suffix) and len(name) >= max(len(suffix) + 2, 4):
            return False
    return True


# STRING-like type heads that hold textual values (labels, descriptions,
# free-form notes). VARCHAR/CHAR with parameter lists like ``VARCHAR(255)``
# pre-strip the ``(...)`` segment via the same head-of-type split used
# elsewhere in this module.
_STRING_TYPE_HEADS = frozenset({"STRING", "VARCHAR", "CHAR", "TEXT"})


# Column names that are almost-always entity labels rather than entity
# keys — display fields whose uniqueness comes from each entity having
# a distinct human-readable name, not from being a foreign key. The
# match is exact (not suffix-based): a column literally NAMED
# ``name`` / ``title`` / etc. is a label; a column named
# ``user_name`` / ``product_title`` is unaffected and continues through
# the regular FK-shape branch (it would be filtered separately if it
# matches the ``_id``/``_code`` suffix pattern handled elsewhere).
_LABEL_COLUMN_NAMES = frozenset(
    {
        "name",
        "title",
        "label",
        "caption",
        "description",
        "comment",
        "notes",
        "summary",
        "subject",
    }
)


def _looks_like_label_column(col: dict) -> bool:
    """Return True when *col* holds a textual entity label (display name,
    title, description) rather than a join key.

    Label columns trip the same_name PK-like gate when both sides are
    high-uniqueness — each entity carries a distinct name/title, so
    uniqueness_ratio is near 1.0 on both sides; the heuristic accepts
    the unique-on-one-side check and emits a phantom
    ``T1.name = T2.name`` edge, even though display-name value spaces
    for unrelated entity types don't overlap.

    Detection:
    - STRING-like type (``STRING`` / ``VARCHAR`` / ``CHAR`` /
      ``TEXT``).
    - Name is EXACTLY one of the label keywords (``name``, ``title``,
      ``description``, ``label``, ``caption``, ``comment``,
      ``notes``, ``summary``, ``subject``). The exact-match keeps
      legitimate FK-like suffixed forms (``user_name``,
      ``product_title``, ``category_label``) flowing through the
      regular ``_fk_suffix_form`` path — those still get evaluated as
      potential FKs.

    Symmetric guard: both sides must be label columns for the edge to
    be dropped. This preserves legitimate joins like
    ``dim_country.name = fact.country_name`` (different column names —
    same_name wouldn't fire) and natural-key textual joins where one
    side uses a non-keyword column name.
    """
    col_type = (col.get("type") or "").upper()
    head = col_type.split("(", 1)[0].split("<", 1)[0].strip()
    if head not in _STRING_TYPE_HEADS:
        return False
    name = (col.get("name") or "").lower()
    return name in _LABEL_COLUMN_NAMES


def phase_infer_joins_heuristic(
    db: PackageDB,
    profile: Profile,
    *,
    suppressed_source_pairs: frozenset[frozenset[str]] = frozenset(),
) -> PhaseResult:
    """Infer join relationships from column naming patterns across all
    sources in the profile.

    Five heuristic patterns (applied in order; each FK-shaped column
    matches at most one pattern):

    0. ``link_to`` (Airtable/Notion): column ``link_to_{table}`` ->
       join to ``{table}.id`` where ``{table}`` (or ``{table}s``)
       exists. Confidence 0.9 for exact match, 0.8 for trailing-word
       recovery.
    1. ``link_to`` (Rails/SQLite): column ``{table}_id`` or
       no-underscore ``{table}id`` -> join to ``{table}.id`` where
       ``{table}`` (or ``{table}s``) exists. The no-underscore form
       requires ``len(name) >= 5`` so the base has at least 3 chars
       (rules out ``bid``, ``aid``, ``paid``, ``uuid``). Confidence
       0.9 for exact match, 0.8 for trailing-word recovery
       (``_id`` form only — no-underscore base has no underscore-split
       structure to walk).
    2. ``xxx_id``: FK-shaped column whose base appears as a substring
       of some table name (forward), or whose base contains a table
       name's singular form (reverse, ``len(table) >= 3`` guard so
       1-2 char tables don't generate noise). Reverse catches
       qualifier-prefixed StackExchange/Reddit FKs like
       ``owneruserid`` -> ``users`` via singular ``user`` in
       ``owneruser``. Confidence 0.7.
    3. ``same_name``: two tables share a column with the same name
       (not an ``_id`` / ``<X>id`` suffix). Confidence 0.5 plus
       uniqueness-shape boost.
    4. ``loose_id``: ``_id`` column (strict form only — no-underscore
       form skipped to avoid phantom markers from FK-shaped false
       friends) with no matching table at all. Confidence 0.3.

    Cross-source pairs (left and right under different ``source_key``
    values) get their pattern confidence multiplied by
    ``CROSS_SOURCE_CONFIDENCE_PENALTY`` (0.8) so within-source joins surface
    above their cross-source equivalents in the ranked output.
    Pattern 4 (``loose_id``) doesn't have a right-hand source to
    cross to and is always recorded under the left's source_key.

    ``suppressed_source_pairs`` is a set of unordered source-key
    pairs (each pair as a ``frozenset({a, b})``) for which JOIN
    inference must be skipped entirely — typically the dev/prod
    duplicate sources surfaced by
    :func:`build.cross_env.detect_cross_env_duplicate_sources`. Edges
    whose left and right tables fall under a suppressed pair are
    dropped before they reach the joins table.
    """
    all_rows = db.list_tables()
    if not all_rows:
        return PhaseResult(status="success", data={"joins_count": 0})

    # Index every table by ``(source_key, name)`` and gather their
    # columns. ``col_map[(sk, name)]`` is the column list; the global
    # name index maps ``name`` to the list of ``(sk, name)`` keys that
    # share that name (cross-source name collisions become legit
    # duplicate hits in the heuristic).
    col_map: dict[tuple[str, str], list[dict]] = {}
    name_index: dict[str, list[tuple[str, str]]] = {}
    for row in all_rows:
        key = (row["source_key"], row["name"])
        col_map[key] = db.get_columns(row["id"])
        name_index.setdefault(row["name"], []).append(key)

    joins_count = 0

    # Track which ``(source_key, table, col)`` triples have already
    # matched a higher-priority pattern so they don't double-count.
    id_cols_matched: set[tuple[str, str, str]] = set()

    # Map each FK column to the set of dimension-table targets it points
    # at, accumulated as patterns 0/1/2 emit ``link_to`` / ``xxx_id``
    # edges. Pattern 3's shared-dimension guard consults this map to
    # suppress fact↔fact ``same_name`` edges whose two sides both
    # already FK the same dimension.
    fk_targets: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}

    def _emit(
        left_key: tuple[str, str],
        left_col: str,
        right_key: tuple[str, str],
        right_col: str,
        kind: str,
        base_confidence: float,
        cardinality: str,
    ) -> None:
        nonlocal joins_count
        cross = left_key[0] != right_key[0]
        if cross and frozenset({left_key[0], right_key[0]}) in suppressed_source_pairs:
            return
        confidence = base_confidence * CROSS_SOURCE_CONFIDENCE_PENALTY if cross else base_confidence
        db.upsert_join(
            left_source_key=left_key[0],
            left_table=left_key[1],
            left_col=left_col,
            right_source_key=right_key[0],
            right_table=right_key[1],
            right_col=right_col,
            kind=kind,
            confidence=confidence,
            cardinality=cardinality,
        )
        joins_count += 1
        if kind in ("link_to", "xxx_id"):
            fk_targets.setdefault((left_key[0], left_key[1], left_col), set()).add(
                (right_key[0], right_key[1], right_col)
            )

    def _resolve_right_col(
        ref_cols: list[dict], fk_col_name: str, right_table_name: str
    ) -> tuple[str | None, dict | None]:
        """Pick the verified right-side column for a FK→PK edge.

        Resolution chain (first match wins):

        1. Same-name match — a column on the right table whose name
           matches the FK column verbatim (the
           ``orders.customer_id → customers.customer_id`` convention
           plus the external-id convention where both tables carry
           the same non-surrogate identifier, e.g.
           ``team_attributes.team_api_id → team.team_api_id``).
           Same-name is the strongest signal we have: when an FK
           column has a distinctive name and the right table happens
           to carry that exact name, the schema author almost always
           meant those two columns to join — even when a bare ``id``
           PK also exists.
        2. Universal ``id`` — the conventional bare PK fallback for
           the common case where the right side has only a synthetic
           ``id`` (the previous default; still wins whenever no
           same-name column exists on the right table).
        3. Right table's natural PK — ``<table>id`` / ``<table>_id``
           with a singular variant (``users.userid``,
           ``articles.articleid``, ``event.event_id``). Catches
           the StackExchange-style ``posts.owneruserid → users.userid``
           and Airtable-style ``attendance.link_to_event → event.event_id``
           cases where neither (1) nor (2) resolve.

        Returns ``(None, None)`` when none of these resolve — the edge
        can't be reliably resolved and the caller must skip emission
        rather than fabricate a non-existent column. Skipping a
        false-positive guard here previously emitted edges like
        ``child.childdetailid → parent.childdetailid`` even when
        ``parent`` had no ``childdetailid`` column (the reverse-
        substring matcher fired on ``parent in child`` but the
        fallback column-name didn't actually exist on the right
        side).
        """
        same_name_col = next((c for c in ref_cols if c["name"] == fk_col_name), None)
        if same_name_col is not None:
            return fk_col_name, same_name_col
        right_id_col = next((c for c in ref_cols if c["name"] == "id"), None)
        if right_id_col is not None:
            return "id", right_id_col
        table_lower = right_table_name.lower()
        singular = table_lower[:-1] if table_lower.endswith("s") else table_lower
        pk_candidates = {
            f"{table_lower}id",
            f"{table_lower}_id",
            f"{singular}id",
            f"{singular}_id",
        }
        for c in ref_cols:
            if c["name"].lower() in pk_candidates:
                return c["name"], c
        return None, None

    def _is_table_own_pk(col_name: str, table_name: str) -> bool:
        """True when ``col_name`` looks like ``table_name``'s own PK.

        Recognizes the universal bare ``id``, the natural-PK
        conventions ``<table>id`` / ``<table>_id``, and their singular
        variants (``users.userid``, ``posts.postid``,
        ``parents.parentid``). Used in pattern 2 to skip emitting a
        bogus FK edge for what is actually the table's own primary
        key — concrete failure mode this guards against:
        ``child.childdetailid`` reverse-substring-matching another
        table by name prefix and emitting an ``xxx_id`` edge in the
        wrong direction (PK→sibling rather than child→PK).
        """
        n = col_name.lower()
        if n == "id":
            return True
        t = table_name.lower()
        s = t[:-1] if t.endswith("s") else t
        return n in {f"{t}id", f"{t}_id", f"{s}id", f"{s}_id"}

    def _try_link_match(
        left_key: tuple[str, str],
        col: dict,
        base_name: str,
        include_trailing_split: bool,
    ) -> bool:
        """Resolve ``base_name`` against ``name_index`` and emit a
        ``link_to`` edge if a target table exists.

        Tries the exact base name (and its ``+s`` plural) at 0.9
        confidence; when ``include_trailing_split`` is True, also
        walks progressively shorter trailing-word splits of the base
        at 0.8 (for underscored bases only — no-underscore bases have
        no split structure to walk).

        Returns True iff at least one edge was emitted, and marks
        the column in ``id_cols_matched`` so downstream patterns
        skip it.
        """
        candidates: list[tuple[str, float]] = [(base_name, 0.9)]
        if include_trailing_split:
            parts = base_name.split("_")
            if len(parts) > 1:
                for i in range(1, len(parts)):
                    candidates.append(("_".join(parts[i:]), 0.8))

        matched_any = False
        for candidate, confidence in candidates:
            if matched_any:
                break
            for plural in (candidate, candidate + "s"):
                if plural in name_index:
                    for right_key in name_index[plural]:
                        if right_key == left_key:
                            continue
                        ref_cols = col_map[right_key]
                        right_col, right_col_dict = _resolve_right_col(
                            ref_cols, col["name"], right_key[1]
                        )
                        if right_col is None:
                            continue
                        cardinality = _infer_cardinality(col, right_col_dict)
                        _emit(
                            left_key,
                            col["name"],
                            right_key,
                            right_col,
                            "link_to",
                            confidence,
                            cardinality,
                        )
                        id_cols_matched.add((left_key[0], left_key[1], col["name"]))
                        matched_any = True
                    break  # found a match in this base form, don't double-count plural
        return matched_any

    # Pattern 0: link_to_<table> — Airtable/Notion FK convention,
    # e.g. ``attendance.link_to_event`` -> ``event.id``. The literal
    # ``link_to_`` prefix is a strong domain signal that the column
    # is a foreign key, so we surface it before pattern 1 even
    # though it doesn't end in ``_id``. Trailing-word recovery on
    # the base name handles compound targets the same way pattern
    # 1's split does (``link_to_eye_colour`` -> ``colour``).
    for left_key, cols in col_map.items():
        for col in cols:
            if col["is_partition"]:
                continue
            if not col["name"].startswith("link_to_"):
                continue
            base_name = col["name"][len("link_to_") :]
            if not base_name:
                continue
            _try_link_match(left_key, col, base_name, include_trailing_split=True)

    # Pattern 1: ``{X}_id`` (Rails/Django) or ``{X}id`` (no-underscore,
    # SQLite/StackExchange) -> any matching ``X`` (or ``Xs``) anywhere
    # in the profile. The exact base name is tried first; if no
    # match, walk progressively shorter trailing-word splits of the
    # base (``_id`` form only — the no-underscore base has no
    # underscore-split structure). Trailing-word recovery handles
    # the common ``{qualifier}_{table}_id`` FK convention:
    #
    # - ``eye_colour_id`` -> ``colour`` (gold expects
    #   ``entity.eye_colour_id = colour.id``)
    # - ``customer_account_id`` -> ``account``
    # - ``payment_method_id`` -> ``method``
    #
    # Trailing-word matches land at confidence 0.8 — strictly below
    # exact-form 0.9 so the agent still prefers the unambiguous
    # name when both exist, and strictly above the broader
    # substring-form 0.7 of pattern 2 below.
    for left_key, cols in col_map.items():
        for col in cols:
            if col["is_partition"]:
                continue
            base_name, suffix_kind = _fk_suffix_form(col["name"])
            if base_name is None:
                continue
            # Skip if pattern 0 already matched (a ``link_to_user_id``
            # column would match both pattern 0 via the ``link_to_``
            # prefix and pattern 1 via the ``_id`` suffix).
            if (left_key[0], left_key[1], col["name"]) in id_cols_matched:
                continue
            include_split = suffix_kind == "_id"
            _try_link_match(left_key, col, base_name, include_trailing_split=include_split)

    # Pattern 2: ``xxx_id`` / ``xxxid`` — FK-shaped column whose base
    # relates to a table name by substring (skipped if pattern 0 or
    # 1 already matched this column). Two substring directions:
    #
    # - **Forward** (``base in table_name``): e.g. ``user_id`` ->
    #   ``users``, the existing recovery for plural targets that the
    #   exact ``+s`` lookup in pattern 1 missed.
    # - **Reverse** (``singular(table_name) in base``, ``len >= 3``):
    #   catches qualifier-prefixed FKs like ``owneruserid`` ->
    #   ``users`` (singular ``user`` is a substring of ``owneruser``)
    #   and ``lasteditoruserid`` -> ``users``. The minimum-length
    #   guard rules out 1-2 char tables drowning the noise floor.
    for left_key, cols in col_map.items():
        for col in cols:
            if col["is_partition"]:
                continue
            base_name, _suffix_kind = _fk_suffix_form(col["name"])
            if base_name is None:
                continue
            if (left_key[0], left_key[1], col["name"]) in id_cols_matched:
                continue
            # Skip when the FK-shaped column is actually the left
            # table's OWN primary key. Pattern 2's substring matcher
            # otherwise emits bogus reverse-direction edges where the
            # parent's PK is mis-interpreted as a child-pointing FK.
            # Concrete failure mode: ``parents.parentid`` (the
            # parent's PK) forward-substring-matches a child whose
            # name embeds the parent's name (``parents.parentid`` →
            # ``parent_details``) and would emit an ``xxx_id`` edge
            # in the wrong direction. The legitimate child→parent
            # edge is already covered by pattern 1's link_to.
            if _is_table_own_pk(col["name"], left_key[1]):
                continue
            base_lower = base_name.lower()
            matched = False
            for table_name, candidate_keys in name_index.items():
                if matched:
                    break
                tn_lower = table_name.lower()
                forward = base_lower in tn_lower
                # Strip an optional trailing ``s`` to handle the
                # singular form of plural table names; the guard is
                # on the resulting singular length, not the original
                # plural, so 3-char plurals collapsing to 2 chars
                # ("ids") still skip the reverse branch.
                singular = tn_lower[:-1] if tn_lower.endswith("s") else tn_lower
                reverse = len(singular) >= 3 and singular in base_lower
                if not (forward or reverse):
                    continue
                for right_key in candidate_keys:
                    if right_key == left_key:
                        continue
                    ref_cols = col_map[right_key]
                    right_col, right_col_dict = _resolve_right_col(
                        ref_cols, col["name"], right_key[1]
                    )
                    if right_col is None:
                        continue
                    cardinality = _infer_cardinality(col, right_col_dict)
                    _emit(
                        left_key,
                        col["name"],
                        right_key,
                        right_col,
                        "xxx_id",
                        0.7,
                        cardinality,
                    )
                    id_cols_matched.add((left_key[0], left_key[1], col["name"]))
                    matched = True
                    break

    # Pattern 3: ``same_name`` — column shared between two tables
    # (within or across sources). Walk every ordered pair of tables
    # once. Includes ``_id`` columns: a shared ``customer_id`` between
    # ``orders`` and ``payments`` is a legitimate FK↔FK join, and a
    # shared ``team_api_id`` between ``team`` and ``team_attributes``
    # (parent table absent) is recoverable here even though pattern 1
    # found no parent.
    #
    # We suppress the PK↔PK case: when both sides of the shared column
    # look like primary keys (column named ``id`` or near-unique by
    # ``uniqueness_ratio``), the match is almost always a coincidental
    # name collision rather than a real join. Concrete failure mode:
    # when several entity tables (e.g. ``customers``, ``products``,
    # ``orders``) each carry an ``id`` PK, the same_name pattern would
    # emit a 1:1 edge for every pair, drowning the legitimate FK
    # signal carried by a different shared natural-key column
    # (e.g. ``uuid``). Real PK↔FK joins still surface — one side
    # PK-like, the other not — and pattern 1 (``link_to``) still
    # captures the explicit ``{X}_id`` form.
    regular_cols: dict[tuple[str, str], dict[str, dict]] = {}
    for key, cols in col_map.items():
        regular_cols[key] = {c["name"]: c for c in cols if not c["is_partition"]}
    keys_list = list(col_map.keys())
    for i in range(len(keys_list)):
        for j in range(i + 1, len(keys_list)):
            left_key = keys_list[i]
            right_key = keys_list[j]
            shared = set(regular_cols[left_key]) & set(regular_cols[right_key])
            for col_name in shared:
                left_col = regular_cols[left_key][col_name]
                right_col_data = regular_cols[right_key][col_name]
                # Dedup against patterns 0/1/2: when ``link_to`` /
                # ``xxx_id`` already emitted an edge for this exact
                # (left_table, col_name) → (right_table, col_name) pair
                # (in either orientation), don't re-emit a ``same_name``
                # edge with a different cardinality. Concrete failure
                # mode: ``child.parentid``→ ``parent.parentid`` was
                # rendered as both ``n:m via link_to`` and ``n:1 via
                # same_name``, showing the agent two contradictory
                # cardinalities for the same edge.
                left_targets_set = fk_targets.get((left_key[0], left_key[1], col_name), set())
                right_targets_set = fk_targets.get((right_key[0], right_key[1], col_name), set())
                if (right_key[0], right_key[1], col_name) in left_targets_set or (
                    left_key[0],
                    left_key[1],
                    col_name,
                ) in right_targets_set:
                    continue
                # Drop temporal-only collisions: when both sides are date
                # / time columns, the high uniqueness comes from
                # sub-second precision, not from being a real identifier.
                # See ``_looks_like_temporal_column`` for the full
                # rationale (event-table ``creationdate`` /
                # ``created_at`` collisions are the motivating case).
                if _looks_like_temporal_column(left_col) and _looks_like_temporal_column(right_col_data):
                    continue
                # Drop URL-only collisions: each table's URL bag is a per-
                # entity content URI, not a foreign-key reference. See
                # ``_looks_like_url_column`` — multi-entity schemas
                # where each table carries its own ``url`` column
                # motivated this.
                if _looks_like_url_column(left_col) and _looks_like_url_column(right_col_data):
                    continue
                # Drop numeric-value collisions: a shared NUMERIC column
                # without an identity-suggesting suffix (``amount``,
                # ``score``, ``balance``, ``year``, ``count``, ...) is a
                # metric, not a foreign key. See
                # ``_looks_like_numeric_value_column`` — when one side
                # of a shared monetary ``amount`` column has uniqueness
                # ≈ 0.97 it trips the PK-like gate even though monetary
                # amounts are values, not entity keys.
                if _looks_like_numeric_value_column(left_col) and _looks_like_numeric_value_column(
                    right_col_data
                ):
                    continue
                # Drop label-column collisions: a shared STRING column
                # named ``name`` / ``title`` / ``description`` / etc. is
                # a display label, not a foreign key. See
                # ``_looks_like_label_column`` — each entity carries a
                # distinct display name, so both sides have high
                # uniqueness and trip the PK-like gate even though the
                # value spaces don't overlap.
                if _looks_like_label_column(left_col) and _looks_like_label_column(right_col_data):
                    continue
                if _looks_like_primary_key(left_col) and _looks_like_primary_key(right_col_data):
                    # Two PK-like columns sharing a name. When the name
                    # is a generic identifier (``id``, ``uuid``, ``pk``,
                    # ``key``, ``code``, ≤3 chars), this is almost always
                    # coincidental: multiple entity tables each carrying
                    # their own ``id`` PK that happens to spell the same
                    # word. The full set of n*(n-1)/2 ``id↔id`` edges
                    # would mislead the agent into joining unrelated
                    # entities.
                    #
                    # When the name is distinctive (``cdscode``,
                    # ``account_number``, ``ssn`` — long, multi-word, or
                    # domain-specific), two tables sharing it as a PK
                    # almost always means a 1:1 entity-split: the same
                    # entity decomposed across tables (e.g. a school's
                    # meal-program record in one table and its directory
                    # row in another, both keyed by the same CDS code).
                    # Emit those as a 1:1 ``same_name`` edge at reduced
                    # confidence so the agent has a join hint to work
                    # from instead of guessing.
                    #
                    # Concrete failure mode this opens up: california_schools
                    # has frpm.cdscode (PK) and schools.cdscode (PK)
                    # both at 100% uniqueness — the upstream gate
                    # dropped both candidates, leaving _joins.md with
                    # ``relationships: []`` and the agent generating
                    # wrong JOIN structures across 10/16 cases.
                    if _is_generic_id_name(col_name):
                        continue
                    _emit(
                        left_key,
                        col_name,
                        right_key,
                        col_name,
                        "same_name",
                        0.4,
                        "1:1",
                    )
                    continue
                # Suppress attribute-name collisions: a shared column
                # that isn't FK-shaped and has no PK-like side is
                # almost always a coincidental attribute match, not a
                # real join. Observed noise that this filter removes:
                # ``account.date = loan.date``, ``card.type =
                # disp.type``, ``cards.name = sets.name``,
                # ``order.k_symbol = trans.k_symbol`` — pure n:m@0.5
                # edges that mislead the agent into Cartesian-style
                # joins. A credible same_name edge needs at least one
                # signal:
                #   - FK-shaped name (``_id`` suffix or no-underscore
                #     ``id`` suffix backstopped by a parent table), OR
                #   - one side PK-like (uniqueness ≥ threshold), so
                #     the shape is FK→PK rather than attr=attr.
                # The PK↔PK branch above filters two-PK coincidences;
                # this closes the symmetric attr↔attr case. Shared
                # ``_id`` columns survive via the FK-shape branch (the
                # ``team.team_api_id = team_attributes.team_api_id``
                # absent-parent case), and FK→PK via uniqueness.
                fk_shaped = _looks_like_fk_name(col_name, name_index)
                left_ratio = left_col.get("uniqueness_ratio")
                right_ratio = right_col_data.get("uniqueness_ratio")
                one_side_pk_like = (
                    left_ratio is not None and left_ratio >= PK_LIKE_UNIQUENESS_THRESHOLD
                ) or (right_ratio is not None and right_ratio >= PK_LIKE_UNIQUENESS_THRESHOLD)
                if not fk_shaped and not one_side_pk_like:
                    continue
                # Shared-dimension guard: when both sides of a non-PK
                # ``same_name`` match already FK the same dimension via
                # patterns 0/1/2, the direct fact↔fact edge is redundant
                # noise — the agent should join through the shared
                # dimension instead. Concrete failure mode this filter
                # prevents: a schema where seven fact tables each carry
                # ``topicid`` FKed to ``topics.topicid`` (recovered by
                # pattern 1 as ``link_to`` at 0.9). Without this guard,
                # pattern 3 also emits n*(n-1)/2 = 21 fact↔fact
                # ``topicid`` n:m@0.5 edges that all collapse to "join
                # via topics". With this guard, only the legitimate
                # ``fact.topicid <-> topics.topicid`` edges (FK→PK,
                # ``link_to``/``xxx_id`` 0.7+ plus the surviving
                # PK-asymmetric ``same_name`` 0.7) reach the agent. The
                # PK↔PK branch above handles the symmetric ``T1.id <->
                # T2.id`` case; this closes the fact↔fact ``T1.col <->
                # T2.col`` case where neither side is PK-like but both
                # refer the same dimension.
                if not one_side_pk_like:
                    left_targets = fk_targets.get((left_key[0], left_key[1], col_name))
                    right_targets = fk_targets.get((right_key[0], right_key[1], col_name))
                    if left_targets and right_targets and left_targets & right_targets:
                        continue
                cardinality = _infer_cardinality(left_col, right_col_data)
                # Boost FK→PK shapes above the 0.5 floor when uniqueness
                # stats are available — a column that's unique on one side
                # and non-unique on the other is a much stronger join
                # signal than two coincidentally-shared non-unique columns
                # (e.g. ``fact_a.points`` vs ``fact_b.points``).
                # PK↔PK is already
                # filtered upstream; the realistic effect of the
                # adjustment here is:
                #   fk-pk / pk-fk → base + uniqueness_cap * unique_side
                #   fk-fk        → base unchanged
                #   unknown      → base unchanged (no stats)
                _shape, adjusted_conf = _apply_join_shape_adjustment(
                    base_conf=0.5,
                    left_uniqueness=left_col.get("uniqueness_ratio"),
                    right_uniqueness=right_col_data.get("uniqueness_ratio"),
                )
                _emit(
                    left_key,
                    col_name,
                    right_key,
                    col_name,
                    "same_name",
                    adjusted_conf,
                    cardinality,
                )

    # Pattern 4: ``loose_id`` — ``_id`` column with no matching table
    # anywhere. Recorded as a "would join here" marker against the
    # unmatched base name; right_source_key matches left's so
    # ``loose_id`` rows are always within-source.
    for left_key, cols in col_map.items():
        for col in cols:
            if not (col["name"].endswith("_id") and not col["is_partition"]):
                continue
            if (left_key[0], left_key[1], col["name"]) in id_cols_matched:
                continue
            base_name = col["name"][:-3]
            # Skip self-loops where base_name == own table: right_col
            # would be the literal ``"id"`` placeholder, not a real FK.
            # Drops legitimate tree-structure self-FKs (e.g.
            # ``employee.manager_id`` on a flat ``employee`` table) too,
            # but those are rare in current corpora and can be
            # re-attached as confirmed annotations.
            if base_name == left_key[1]:
                continue
            _emit(
                left_key,
                col["name"],
                (left_key[0], base_name),
                "id",
                "loose_id",
                0.3,
                "n:m",
            )

    return PhaseResult(status="success", data={"joins_count": joins_count})
