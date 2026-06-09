"""Type rule — STRING column compared to date-shaped literal."""

from __future__ import annotations

import re

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    alias_to_table_in_select,
    cte_names,
    parse_statements,
    real_tables_in_select,
    resolve_source_for_table,
)
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext

# `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`
_DATE_LITERAL = re.compile(r"^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}:\d{2})?$")

# Map sqlglot's lowercase node `key` for the binary comparison ops to
# the SQL token the agent should paste back into a fix. Keeps fix_hint
# free of placeholder text like ``<op>``.
_OP_TO_SQL = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _looks_like_date(literal: exp.Expression) -> bool:
    if not isinstance(literal, exp.Literal) or not literal.is_string:
        return False
    return bool(_DATE_LITERAL.match(literal.this))


def check_string_date_compare(ctx: ReviewContext) -> list[Issue]:
    """Flag STRING columns compared with `=`/`<`/`<=`/`>`/`>=` against a
    date-shaped literal — lexicographic order is not always the same as
    chronological order (e.g. ``'2026-1-2'`` sorts AFTER ``'2026-12-01'``
    because ``-`` is ``0x2D`` and ``1`` is ``0x31``), and missing zero-
    padding can also slip past naive range filters.
    """
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        ctes = cte_names(stmt)
        # Per-Select alias map — see ``rules/schema.py:check_column_not_found``
        # for why a statement-wide ``alias_to_table`` routes columns incorrectly
        # when a nested subquery reuses an outer alias.
        alias_maps: dict[int, dict[str, exp.Table]] = {}
        # v1: scope limited to binary comparisons; BETWEEN/IN/NOT IN deferred.
        for cmp in stmt.find_all(exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE):
            left, right = cmp.this, cmp.expression
            col: exp.Column | None = None
            literal: exp.Expression | None = None
            if isinstance(left, exp.Column) and _looks_like_date(right):
                col, literal = left, right
            elif isinstance(right, exp.Column) and _looks_like_date(left):
                col, literal = right, left
            if col is None or literal is None:
                continue
            parent_select = col.find_ancestor(exp.Select)
            if parent_select is None:
                continue
            sid = id(parent_select)
            if sid not in alias_maps:
                alias_maps[sid] = alias_to_table_in_select(parent_select)
            alias_map = alias_maps[sid]
            origin: exp.Table | None
            if col.table:
                origin = alias_map.get(col.table.lower())
                if origin is None:
                    # Unknown alias (likely a CTE column qualifier) —
                    # leave it for MaxCompute to surface.
                    continue
                table_name = origin.name
            else:
                # Unqualified column — single-real-table fallback scoped
                # to the enclosing Select, matching the schema rule.
                # A statement-wide ``find_all(exp.Table)`` would walk
                # into sibling subqueries and either pick the wrong
                # base table or bail on len != 1 when the outer Select
                # has its own FROM.
                real_tables = real_tables_in_select(parent_select, ctes)
                if len(real_tables) != 1:
                    continue
                origin = real_tables[0]
                table_name = origin.name
            # Skip CTE-qualified references that survived alias resolution
            # — there's no schema to type-check against.
            if not origin.catalog and table_name.lower() in ctes:
                continue
            sk = resolve_source_for_table(ctx, origin, table_name)
            if sk is None:
                continue
            tbl = ctx.db.get_table(sk, table_name)
            if tbl is None:
                continue
            cols = {c["name"].lower(): c for c in ctx.db.get_columns(tbl["id"])}
            col_row = cols.get(col.name.lower())
            if col_row is None:
                continue
            col_type = (col_row.get("type") or "").upper()
            if (
                col_type.startswith("STRING")
                or col_type.startswith("VARCHAR")
                or col_type.startswith("CHAR")
            ):
                op_sql = _OP_TO_SQL.get(cmp.key, "=")
                issues.append(
                    Issue(
                        severity="warning",
                        rule="type.string-date-compare",
                        message=(
                            f"Column `{table_name}.{col.name}` is STRING but "
                            f"compared to a date-shaped literal "
                            f"`{literal.this}`; lexicographic compare may "
                            f"not match the intended chronological order"
                        ),
                        fix_hint=(f"CAST({col.name} AS DATETIME) {op_sql} '{literal.this}'"),
                    )
                )
    return issues
