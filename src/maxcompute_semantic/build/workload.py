# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Structured SQL workload evidence extraction and aggregation.

Extends ``SqlPattern`` (from ``memory/sql_pattern.py``) which already
extracts tables, join_edges, and where_predicates. This module adds
group_by_columns and aggregates, plus aggregation across multiple
SQL strings to produce a ``WorkloadSummary`` for downstream phases.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from maxcompute_semantic.memory.sql_pattern import analyze_sql_pattern

# Matches table-qualified column refs (``<table>.<col>``) inside the
# normalized join-edge string emitted by ``_join_edges`` — used by
# ``_edge_tables`` to find all tables an edge references when the edge
# carries multiple equality clauses (``t1.a=t2.b and t1.c=?``) or an
# OR-disjunction that a naive ``split("=")`` can't parse.
_TABLE_QUAL_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\.[a-z_][a-z0-9_]*", re.IGNORECASE)


@dataclass(frozen=True)
class SqlWorkloadEvidence:
    """Structured evidence extracted from one SQL string."""

    tables: tuple[str, ...]
    join_edges: tuple[str, ...]
    where_columns: tuple[tuple[str, str], ...]
    group_by_columns: tuple[tuple[str, str], ...]
    aggregates: tuple[tuple[str, str, str], ...]
    shape_key: str = ""
    parse_error: str | None = None


def _resolve_table_alias(tree: exp.Expr) -> dict[str, str]:
    """Build alias→table_name map from FROM/JOIN clauses."""
    alias_map: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        alias = table.alias
        if alias:
            alias_map[alias] = table.name
        else:
            alias_map[table.name] = table.name
    return alias_map


def _extract_group_by_columns(
    tree: exp.Expr,
    alias_map: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    """Extract (table, column) pairs from GROUP BY columns."""
    group = tree.args.get("group")
    if not isinstance(group, exp.Group):
        return ()
    result: list[tuple[str, str]] = []
    for col_expr in group.expressions:
        if isinstance(col_expr, exp.Column):
            table_ref = col_expr.table
            table_name = alias_map.get(table_ref, table_ref) if table_ref else ""
            result.append((table_name, col_expr.name))
    return tuple(sorted(result))


def _extract_aggregates(
    tree: exp.Expr,
    alias_map: dict[str, str],
) -> tuple[tuple[str, str, str], ...]:
    """Extract (func_name, table, column) triples from aggregate expressions."""
    result: list[tuple[str, str, str]] = []
    for agg in tree.find_all(exp.AggFunc):
        func_name = agg.key.upper()
        inner = agg.this
        if isinstance(inner, exp.Column):
            table_ref = inner.table
            table_name = alias_map.get(table_ref, table_ref) if table_ref else ""
            result.append((func_name, table_name, inner.name))
    return tuple(sorted(result))


def _extract_where_columns(
    tree: exp.Expr,
    alias_map: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    """Extract (table, column) pairs from WHERE clause column refs."""
    where = tree.args.get("where")
    if not isinstance(where, exp.Where):
        return ()
    result: list[tuple[str, str]] = []
    for col in where.find_all(exp.Column):
        table_ref = col.table
        table_name = alias_map.get(table_ref, table_ref) if table_ref else ""
        result.append((table_name, col.name))
    return tuple(sorted(set(result)))


def extract_sql_evidence(sql: str) -> SqlWorkloadEvidence:
    """Parse one SQL string and extract structured workload evidence."""
    pattern = analyze_sql_pattern(sql)
    try:
        parsed = sqlglot.parse_one(sql)
    except sqlglot.errors.SqlglotError:
        return SqlWorkloadEvidence(
            tables=pattern.tables,
            join_edges=pattern.join_edges,
            where_columns=(),
            group_by_columns=(),
            aggregates=(),
            shape_key=pattern.shape_key,
            parse_error=pattern.parse_error,
        )

    alias_map = _resolve_table_alias(parsed)
    return SqlWorkloadEvidence(
        tables=pattern.tables,
        join_edges=pattern.join_edges,
        where_columns=_extract_where_columns(parsed, alias_map),
        group_by_columns=_extract_group_by_columns(parsed, alias_map),
        aggregates=_extract_aggregates(parsed, alias_map),
        shape_key=pattern.shape_key,
        parse_error=pattern.parse_error,
    )


@dataclass
class WorkloadSummary:
    """Aggregated evidence counts across multiple SQL strings."""

    join_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    where_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    group_by_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    aggregate_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    table_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    parse_errors: int = 0

    def to_jsonable(self) -> dict:
        """Return a JSON-friendly dict for PhaseResult.data."""
        return {
            "join_counts": dict(self.join_counts),
            "where_counts": dict(self.where_counts),
            "group_by_counts": dict(self.group_by_counts),
            "aggregate_counts": dict(self.aggregate_counts),
            "table_counts": dict(self.table_counts),
            "parse_errors": self.parse_errors,
        }

    def merge(self, other: WorkloadSummary) -> None:
        """Sum *other*'s counts into self in-place.

        The pipeline runs ``aggregate_workload_evidence`` once per source
        and folds the per-source summaries into a single profile-wide
        accumulator for the join-ranker and semantic-suggestions phases.
        Keys collide on bare ``table.column`` form when two sources name
        a table identically — by design, those tables genuinely share the
        downstream-workload signal (a JOIN that names the bare table
        applies to whichever source resolves it).
        """
        for key, val in other.join_counts.items():
            self.join_counts[key] += val
        for key, val in other.where_counts.items():
            self.where_counts[key] += val
        for key, val in other.group_by_counts.items():
            self.group_by_counts[key] += val
        for key, val in other.aggregate_counts.items():
            self.aggregate_counts[key] += val
        for key, val in other.table_counts.items():
            self.table_counts[key] += val
        self.parse_errors += other.parse_errors


def _edge_tables(edge: str) -> set[str] | None:
    """Return the set of distinct table names referenced in a join edge.

    The edge string comes from ``analyze_sql_pattern._join_edges`` —
    a normalized SQL expression like ``t1.a = t2.b`` or
    ``t1.a = t2.b and t1.c = ?`` (multi-clause ON) or even an
    OR-disjunction. Naively splitting on ``=`` mis-parses the latter
    two, so we regex-extract every table-qualified column reference
    instead. Returns ``None`` when no qualified ref is present (the
    edge is unattributable; ``allowed_tables`` filter should drop it).
    """
    tables = {m.lower() for m in _TABLE_QUAL_RE.findall(edge)}
    return tables or None


def aggregate_workload_evidence(
    sqls: list[str],
    *,
    min_shape_frequency: int = 1,
    allowed_tables: set[str] | None = None,
) -> WorkloadSummary:
    """Aggregate evidence counts across accepted SQL strings.

    ``min_shape_frequency`` drops SQLs whose normalized shape appears
    fewer than N times across the input. The default of 1 preserves
    legacy behavior. Callers mining unverified history (where a
    single-occurrence SQL is one-shot exploration, not a repeating
    workload pattern) pass ``min_shape_frequency=2`` so per-shape
    noise can't drive column classification on its own — a single
    ``GROUP BY foo`` from one ad-hoc query used to contribute +0.45
    dimension confidence in ``semantic_suggestions`` and was the
    smoking gun behind the benchmark with-history arm regressing
    against the no-history arm. Parse-error SQLs are still counted
    in ``parse_errors``; they just don't contribute keyed evidence.

    ``allowed_tables`` (lowercase set, optional) scopes evidence to a
    source's table set. A mined SQL that JOINs an in-source table with
    one outside the source still gets attributed and persisted as a
    sample for the in-source side (the agent benefits from seeing real
    usage), but the cross-source ``join_counts`` / ``where_counts`` /
    ``group_by_counts`` / ``aggregate_counts`` / ``table_counts`` keys
    referencing the out-of-source table are dropped here so they
    can't pollute join-candidate ranking or column classification in
    ``semantic_suggestions``. When ``None`` (default), no filtering
    is applied — legacy behavior. Edges or column refs that lack a
    table qualifier are also dropped under filtering, since they can't
    be source-attributed unambiguously.
    """
    extracted = [(sql, extract_sql_evidence(sql)) for sql in sqls]

    if min_shape_frequency > 1:
        shape_counts: dict[str, int] = defaultdict(int)
        for _, evidence in extracted:
            if not evidence.parse_error and evidence.shape_key:
                shape_counts[evidence.shape_key] += 1

    summary = WorkloadSummary()
    for _sql, evidence in extracted:
        if evidence.parse_error:
            summary.parse_errors += 1
            continue
        if (
            min_shape_frequency > 1
            and shape_counts.get(evidence.shape_key, 0) < min_shape_frequency
        ):
            continue
        # ``allowed_tables`` filter policy: a key referencing an
        # explicitly out-of-source table is dropped (cross-source
        # noise). Unqualified column refs (``table == ""``) pass
        # through — they typically come from single-FROM queries
        # where the column unambiguously belongs to the in-source
        # table; dropping them would shrink the
        # ``workload_columns_this_source`` seed set that drives
        # which columns get sampled / profiled.
        for table in evidence.tables:
            if allowed_tables is not None and table.lower() not in allowed_tables:
                continue
            summary.table_counts[table] += 1
        for edge in evidence.join_edges:
            if allowed_tables is not None:
                edge_tables = _edge_tables(edge)
                if edge_tables is None:
                    continue
                if not edge_tables.issubset(allowed_tables):
                    continue
            summary.join_counts[edge] += 1
        for table, col in evidence.where_columns:
            if allowed_tables is not None and table and table.lower() not in allowed_tables:
                continue
            key = f"{table}.{col}" if table else col
            summary.where_counts[key] += 1
        for table, col in evidence.group_by_columns:
            if allowed_tables is not None and table and table.lower() not in allowed_tables:
                continue
            key = f"{table}.{col}" if table else col
            summary.group_by_counts[key] += 1
        for func, table, col in evidence.aggregates:
            if allowed_tables is not None and table and table.lower() not in allowed_tables:
                continue
            key = f"{func}({table}.{col})" if table else f"{func}({col})"
            summary.aggregate_counts[key] += 1
    return summary
