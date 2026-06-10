# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Compute model_coverage envelope field — annotation completeness for the SQL.

The coverage payload tells the agent how much of the SQL's surface is
backed by annotations:

- ``tables_with_ai_context`` / ``tables_referenced`` — narrative
  table-level coverage
- ``columns_with_semantic_role`` / ``columns_referenced`` — column-level
  coverage across all column references (SELECT, WHERE, JOIN ON,
  GROUP BY, ORDER BY, HAVING)
- ``joins_declared`` / ``joins_used_in_sql`` — JOIN side, ``declared``
  is restricted to joins touching at least one referenced table

``coverage_pct`` is the simple arithmetic mean of the table and column
percentages, rounded to int. This v1 weighting (tables and columns
count equally) is a deliberate simplification — a future revision may
revisit it once we have signal on what the agent actually keys off.
Joins are reported separately for visibility but do not contribute to
``coverage_pct``.
"""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    alias_to_table_in_select,
    cte_names,
    parse_statements,
    real_tables_in_select,
    resolve_source_for_table,
)
from maxcompute_semantic.commands.sql_review.types import ReviewContext


def _real_tables_and_ctes(stmt: exp.Expression) -> tuple[list[exp.Table], set[str]]:
    """Split FROM/JOIN tables into real tables vs CTE refs for *stmt*.

    The CTE filter mirrors ``rules/schema.py:check_column_not_found`` so
    coverage and schema rules agree on what counts as a "real" table.
    """
    ctes = cte_names(stmt)
    real = [t for t in stmt.find_all(exp.Table) if t.name.lower() not in ctes]
    return real, ctes


def _referenced_columns(sql: str) -> list[tuple[exp.Table, str, str]]:
    """Extract (origin_table, table_name, column) triples for every
    column reference in *sql*.

    The first element is the originating ``exp.Table`` so callers can
    honor FQN catalog/db when resolving the source; references that
    can't resolve to a real table (CTE aliases, ambiguous unqualified
    columns) are skipped. The second is the bare table name (alias
    resolved).

    Walks every ``exp.Column`` node — deliberately broader than just
    the SELECT projection list. Coverage measures annotation
    completeness against the full set of columns the SQL touches
    (SELECT, WHERE, JOIN ON, GROUP BY, ORDER BY, HAVING); a WHERE-only
    column with no ``semantic_role`` is just as much "uncovered" as a
    projected one.

    Unqualified columns are resolved to the FROM table when the
    statement has exactly one real (non-CTE) table — matching how
    ``rules/schema.py:check_column_not_found`` resolves them.
    """
    triples: list[tuple[exp.Table, str, str]] = []
    # FQN-aware dedup: two same-bare-name tables from different sources
    # contribute independent column references. A bare ``(name, col)``
    # key would collapse them and under-count ``columns_referenced`` —
    # one source's ``orders.amount`` would mask the other's.
    seen: set[tuple[str, str, str, str]] = set()
    for stmt in parse_statements(sql):
        ctes = cte_names(stmt)
        # Per-Select alias / real-table maps — see
        # ``rules/schema.py:check_column_not_found`` for the alias-
        # shadowing case that motivates this. The cache is keyed on
        # ``id(select)`` so multiple columns in the same Select don't
        # re-walk its FROM/JOIN args.
        alias_maps: dict[int, dict[str, exp.Table]] = {}
        single_origins: dict[int, exp.Table | None] = {}
        for col in stmt.find_all(exp.Column):
            parent_select = col.find_ancestor(exp.Select)
            if parent_select is None:
                continue
            sid = id(parent_select)
            if sid not in alias_maps:
                alias_maps[sid] = alias_to_table_in_select(parent_select)
                local_real = real_tables_in_select(parent_select, ctes)
                single_origins[sid] = local_real[0] if len(local_real) == 1 else None
            alias_map = alias_maps[sid]
            origin: exp.Table | None
            if col.table:
                # CTE-qualified column refs (e.g. `ev.id` where `ev` is a
                # CTE) have no underlying schema to count against.
                if col.table.lower() in ctes:
                    continue
                origin = alias_map.get(col.table.lower())
                if origin is None:
                    continue
            else:
                origin = single_origins[sid]
                if origin is None:
                    # Genuinely ambiguous for this Select — leave it out
                    # rather than guess; the schema rule does the same.
                    continue
            key = (
                (origin.catalog or "").lower(),
                (origin.db or "default").lower(),
                origin.name.lower(),
                col.name.lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            triples.append((origin, origin.name, col.name))
    return triples


def _referenced_tables(sql: str) -> list[exp.Table]:
    """Real (non-CTE) ``exp.Table`` nodes referenced by *sql*.

    Returns nodes (not bare names) so callers can honor FQN catalog/db
    when resolving the source. Computed by walking statements ourselves
    rather than reading ``ctx.evidence.tables`` because the latter
    comes from ``find_all(exp.Table)`` and includes CTE references.

    De-duplicates on the FQN tuple ``(catalog, db, name)`` so two
    same-bare-name tables from different sources both contribute to the
    referenced-tables count and each get their annotation status checked
    — collapsing on bare name would silently let one source's annotated
    ``orders`` mask another source's unannotated ``orders`` and report
    100% coverage on a half-annotated SQL.
    """
    out: list[exp.Table] = []
    seen: set[tuple[str, str, str]] = set()
    for stmt in parse_statements(sql):
        real_tables, _ = _real_tables_and_ctes(stmt)
        for t in real_tables:
            key = (
                (t.catalog or "").lower(),
                (t.db or "default").lower(),
                t.name.lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
    return out


def compute_model_coverage(ctx: ReviewContext) -> dict[str, int]:
    """Build the ``model_coverage`` envelope dict for *ctx*."""
    if ctx.db is None:
        return {
            "tables_referenced": 0,
            "tables_with_ai_context": 0,
            "columns_referenced": 0,
            "columns_with_semantic_role": 0,
            "joins_used_in_sql": 0,
            "joins_declared": 0,
            "coverage_pct": 0,
        }
    referenced_tables = _referenced_tables(ctx.sql)
    # ``list_joins()`` rows key on bare table name (no catalog/db
    # disambiguator), so the joins-declared check below must compare
    # against bare names; the coverage-percent denominators use the full
    # FQN-deduped table list so the percentage reflects per-FQN counts.
    referenced_table_names = {t.name for t in referenced_tables}
    annotated_tables = 0
    for origin in referenced_tables:
        sk = resolve_source_for_table(ctx, origin, origin.name)
        if sk is None:
            continue
        row = ctx.db.get_table(sk, origin.name)
        if row and row.get("ai_context"):
            annotated_tables += 1

    referenced_columns = _referenced_columns(ctx.sql)
    annotated_columns = 0
    for origin, table, col in referenced_columns:
        sk = resolve_source_for_table(ctx, origin, table)
        if sk is None:
            continue
        s = ctx.db.get_column_semantics(sk, table, col)
        if s and s.get("semantic_role"):
            annotated_columns += 1

    joins_declared = 0
    for j in ctx.db.list_joins():
        if j["left_table"] in referenced_table_names or j["right_table"] in referenced_table_names:
            joins_declared += 1
    joins_used = len(ctx.evidence.join_edges)

    def _pct(num: int, denom: int) -> float:
        return 0.0 if denom == 0 else (num / denom) * 100.0

    table_pct = _pct(annotated_tables, len(referenced_tables))
    col_pct = _pct(annotated_columns, len(referenced_columns))

    return {
        "tables_referenced": len(referenced_tables),
        "tables_with_ai_context": annotated_tables,
        "columns_referenced": len(referenced_columns),
        "columns_with_semantic_role": annotated_columns,
        "joins_used_in_sql": joins_used,
        "joins_declared": joins_declared,
        # Equal weighting of table and column coverage — see module docstring.
        "coverage_pct": round((table_pct + col_pct) / 2),
    }
