# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Low-cost column profiling: select candidate columns, build aggregate
SQL, and normalize profiling results.

Profiling runs one bounded aggregate query per table (APPROX_DISTINCT
for NDV + null counts + row count + STRING numeric-cast count) and stores
results via ``PackageDB.update_column_profile()``. It is a lightweight
complement to the existing ``phase_column_sampling()`` which uses
``LIMIT 20``.
"""

from __future__ import annotations

from typing import Any

from maxcompute_semantic.build._naming import ID_NAME_RE, METRIC_NAME_RE

# Default column cap for the profile aggregate. Bounded only because
# ODPS imposes practical limits on SQL text size; the aggregate is a
# single full-table scan regardless of column count, so the per-column
# marginal cost is essentially free. 200 covers wide entity-catalog
# tables (widest is ~75 cols) plus typical wide warehouse tables (~150 cols).
DEFAULT_PROFILE_LIMIT = 200


def select_profile_columns(
    columns: list[dict[str, Any]],
    *,
    workload_columns: set[str] | None = None,
    limit: int = DEFAULT_PROFILE_LIMIT,
) -> list[str]:
    """Return high-value non-partition column names for profile aggregation.

    Priority order: workload-referenced columns first, then identifier-like
    columns, then metric-like columns, then remaining non-partition columns.
    Partition columns (``ds``, ``pt``, etc.) are always excluded.
    """
    workload = workload_columns or set()
    non_partition = [c for c in columns if not c.get("is_partition", 0)]
    names = [c["name"] for c in non_partition]

    ranked: list[str] = []
    # Tier 1: workload-referenced columns
    for n in names:
        if n in workload and n not in ranked:
            ranked.append(n)
    # Tier 2: identifier-like columns
    for n in names:
        if ID_NAME_RE.search(n) and n not in ranked:
            ranked.append(n)
    # Tier 3: metric-like columns
    for n in names:
        if METRIC_NAME_RE.search(n) and n not in ranked:
            ranked.append(n)
    # Tier 4: remaining non-partition columns
    for n in names:
        if n not in ranked:
            ranked.append(n)

    return ranked[:limit]


def _is_string_type(col_type: str) -> bool:
    """STRING / VARCHAR / CHAR all benefit from the dirty-numeric probe."""
    upper = col_type.upper()
    return "STRING" in upper or "VARCHAR" in upper or "CHAR" in upper


def build_column_profile_sql(
    *,
    fq_name: str,
    columns: list[str],
    where_clause: str,
    column_types: dict[str, str] | None = None,
) -> str:
    """Return one aggregate SQL statement with row_count, nulls, APPROX_DISTINCT,
    and (for STRING columns) numeric-cast count.

    Each selected column gets ``{col}__approx_ndv`` and ``{col}__nulls``.
    STRING-typed columns additionally get ``{col}__numeric_count`` —
    a permissive ``COUNT(CAST(... AS DOUBLE))`` aggregate that surfaces
    dirty-numeric strings (e.g. a clinical measurement column with
    "negative" / "trace" mixed in among the numeric values) without
    needing a separate scan. MC's ``CAST(... AS DOUBLE)`` returns NULL
    on parse failure rather than raising, so the COUNT covers only the
    values that genuinely look like numbers.

    A global ``row_count`` is included.
    """
    # MaxCompute treats double quotes as string literals — ``"id"`` parses
    # as the constant ``'id'`` and Fuxi rejects the aggregate with
    # ``Illegal constant val type: String: "id"``. Identifiers must be
    # backtick-quoted. Aliases stay unquoted because double-quoting the
    # alias causes ODPS to canonicalize it to an auto-generated name
    # (``_c0``, etc.), which breaks column-name matching in
    # apply_profile_result.
    types = column_types or {}
    parts = ["COUNT(1) AS row_count"]
    for col in columns:
        safe = col.replace("`", "``")
        parts.append(f"APPROX_DISTINCT(`{safe}`) AS {safe}__approx_ndv")
        parts.append(f"SUM(CASE WHEN `{safe}` IS NULL THEN 1 ELSE 0 END) AS {safe}__nulls")
        if _is_string_type(types.get(col, "")):
            parts.append(f"COUNT(CAST(`{safe}` AS DOUBLE)) AS {safe}__numeric_count")

    select_clause = ", ".join(parts)
    from_clause = f"FROM {fq_name}"
    where = f" {where_clause}" if where_clause else ""
    return f"SELECT {select_clause} {from_clause}{where}"


def apply_profile_result(
    columns: list[dict[str, Any]],
    *,
    selected_columns: list[str],
    row: dict[str, Any],
    scope: str,
    method: str,
) -> list[dict[str, Any]]:
    """Return updated column dicts carrying row_count, approx_ndv, ratios,
    and cast_rate.

    ``row`` is the dict from the profiling query result. For each selected
    column, ``row[f"{col}__approx_ndv"]`` and ``row[f"{col}__nulls"]``
    provide the aggregate stats. ``row["row_count"]`` is the global row count.
    For STRING columns, ``row[f"{col}__numeric_count"]`` (if present)
    drives ``cast_rate`` = numeric_count / (row_count - nulls).
    """
    row_count = row.get("row_count", 0)
    updated = []
    for col in columns:
        entry = dict(col)
        if col["name"] in selected_columns:
            ndv = row.get(f"{col['name']}__approx_ndv")
            nulls = row.get(f"{col['name']}__nulls", 0)
            entry["row_count"] = row_count
            if ndv is not None:
                entry["approx_ndv"] = ndv
            entry["null_ratio"] = nulls / row_count if row_count else None
            # APPROX_DISTINCT can overshoot the true row count for columns
            # with near-zero duplicates (HyperLogLog standard error ~1.6% at
            # the MaxCompute default precision). An unclamped ratio above
            # 1.0 surfaces in the markdown evidence as e.g.
            # `uniqueness_ratio: 1.0493` which is mathematically impossible
            # and trips downstream consumers that assume the ratio is a
            # well-formed probability. Clamp at the source so every reader
            # (semantic_suggestions, annotation markdown, JSON exports)
            # sees a valid [0, 1] value without each having to defend.
            if row_count and ndv:
                entry["uniqueness_ratio"] = min(1.0, ndv / row_count)
            else:
                entry["uniqueness_ratio"] = None
            entry["is_enum"] = 1 if ndv and ndv <= 30 and row_count > 0 else 0

            # cast_rate is computed only when the profile SQL emitted
            # ``{col}__numeric_count`` (STRING-typed columns). Denominator
            # is non-null count; an all-NULL column has no meaningful
            # cast_rate.
            numeric_count = row.get(f"{col['name']}__numeric_count")
            if numeric_count is not None:
                non_null = (row_count or 0) - (nulls or 0)
                entry["cast_rate"] = min(1.0, numeric_count / non_null) if non_null > 0 else None
            entry["profile_scope"] = scope
            entry["profile_method"] = method
            entry["profile_confidence"] = (
                0.9 if scope in {"full_table", "latest_partition"} else 0.5
            )
        updated.append(entry)
    return updated
