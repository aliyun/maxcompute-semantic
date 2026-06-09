# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Schema rules — verify referenced tables and columns exist."""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    alias_to_table_in_select,
    cte_names,
    parse_statements,
    real_tables_in_select,
    resolve_source_for_table,
    source_keys_for_two_segment,
)
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext

# Maximum number of column names included in a "column-not-found" message
# before truncating with a "(+N more)" overflow marker. Wide tables (100+
# columns) blow up the message otherwise.
_MAX_AVAILABLE_COLS = 20

# Clause types that are evaluated AFTER the SELECT list and therefore
# may reference projection aliases by name in MaxCompute. WHERE is
# excluded — it evaluates before the projection so alias refs there
# are genuine errors and should still fire ``schema.column-not-found``.
_ALIAS_REF_CLAUSES: tuple[type[exp.Expression], ...] = (
    exp.Order,
    exp.Group,
    exp.Having,
    exp.Qualify,
)


def _alias_context_select(col: exp.Column) -> exp.Select | None:
    """Return the Select whose ORDER/GROUP/HAVING/QUALIFY clause
    directly contains *col*, or ``None`` when *col* lives outside any
    alias-permitting clause.

    Used by ``check_column_not_found`` to decide whether an unqualified
    column whose name matches a projection alias should be suppressed.
    The "directly contains" guard is the ``.find_ancestor(exp.Select)``
    check at the call site: we want to skip only when the alias-clause
    belongs to the same Select that declared the alias — a nested
    subquery's own columns must still resolve against the subquery's
    own FROM, not the outer Select's projection list.
    """
    clause = col.find_ancestor(*_ALIAS_REF_CLAUSES)
    if clause is None:
        return None
    return clause.find_ancestor(exp.Select)


def check_table_not_found(ctx: ReviewContext) -> list[Issue]:
    """For each table reference in the SQL, check it resolves to a source_key.

    CTE references (the `cte` in `WITH cte AS (...) SELECT * FROM cte`) are
    skipped because they're not real tables — sqlglot's `find_all(exp.Table)`
    yields a Table node for every CTE *reference* with bare name + empty
    catalog/db, which would otherwise fire this rule on every CTE-using SQL.
    """
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        ctes = cte_names(stmt)
        for table in stmt.find_all(exp.Table):
            # 3-segment FQN: project.schema.table
            if table.catalog:
                sk = ctx.db.lookup_source_key(table.catalog, table.db or "default", table.name)
                if sk is None:
                    issues.append(
                        Issue(
                            severity="error",
                            rule="schema.table-not-found",
                            message=(
                                f"Table `{table.catalog}.{table.db or 'default'}.{table.name}` "
                                f"is not in the profile's package; "
                                f"run `mcs build` or check the table name"
                            ),
                        )
                    )
                continue
            # 2-segment FQN: db.table — must honor the explicit db
            # segment. Try schema-form first (the standard 3-level
            # read shape where the project is implicit), then
            # project-form (the 2-level shape). Falling through to
            # the bare-name walk would let a same-named table in
            # an unrelated schema mask the missing reference (the
            # bug the Round 6 Codex review flagged).
            if table.db:
                if not source_keys_for_two_segment(ctx, table.db, table.name):
                    issues.append(
                        Issue(
                            severity="error",
                            rule="schema.table-not-found",
                            message=(
                                f"Table `{table.db}.{table.name}` is not in the "
                                f"profile's package; run `mcs build` or check "
                                f"the table name"
                            ),
                        )
                    )
                continue
            # Bare reference — skip CTE refs first; they're not real tables.
            if table.name.lower() in ctes:
                continue
            resolved = False
            for src in ctx.profile.sources:
                if ctx.db.lookup_source_key(src.project, src.schema, table.name):
                    resolved = True
                    break
            if not resolved:
                issues.append(
                    Issue(
                        severity="error",
                        rule="schema.table-not-found",
                        message=(
                            f"Table `{table.name}` is not in any source of the active "
                            f"profile; check the spelling or run `mcs build`"
                        ),
                    )
                )
    return issues


def check_column_not_found(ctx: ReviewContext) -> list[Issue]:
    """For each (alias-resolved) column ref, check it exists on its declared table.

    When the originating `exp.Table` carries an explicit catalog/db (i.e. the
    user wrote a 3-segment FQN), we resolve the column against *that exact*
    source rather than walking `profile.sources` — otherwise same-named tables
    in different sources (cross-project profiles) silently resolve to the
    wrong one and we'd flag a real column as missing or miss a real typo
    because some other source happens to have the column.
    """
    issues: list[Issue] = []

    for stmt in parse_statements(ctx.sql):
        ctes = cte_names(stmt)
        # Per-Select alias / real-table maps: a nested subquery may
        # reuse an outer alias to mean a different table
        # (``SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM
        # other o WHERE o.x = 1)``); a statement-wide
        # ``alias_to_table`` collapsed both ``o`` entries to the
        # later-visited table and mis-resolved the outer ``o.id``
        # against ``other``. We compute the alias map per enclosing
        # Select, cached by ``id(select)`` so repeated columns in the
        # same Select don't re-walk its FROM/JOIN args.
        alias_maps: dict[int, dict[str, exp.Table]] = {}
        # Per-Select projection-alias set so ``SELECT COUNT(*) AS cnt
        # FROM orders ORDER BY cnt`` does not false-positive on ``cnt``
        # — MaxCompute (like standard SQL) lets ORDER BY / GROUP BY /
        # HAVING reference SELECT-list aliases by name. Pre-fix the
        # rule walked every ``exp.Column``, didn't recognize the alias,
        # resolved ``cnt`` against the single real table (``orders``),
        # and emitted ``schema.column-not-found`` for valid SQL.
        projection_aliases: dict[int, set[str]] = {}
        # Dedup key is FQN-aware: two tables with the same bare name from
        # different sources (e.g. ``proj_a.default.orders`` vs
        # ``proj_b.default.orders``) must each be column-checked
        # independently, since their schemas can diverge. A bare-name key
        # would collapse the second reference and silently skip checking
        # a real missing-column on it.
        seen: set[tuple[str, str, str, str]] = set()
        for col in stmt.find_all(exp.Column):
            parent_select = col.find_ancestor(exp.Select)
            if parent_select is None:
                continue
            sid = id(parent_select)
            if sid not in alias_maps:
                alias_maps[sid] = alias_to_table_in_select(parent_select)
                projection_aliases[sid] = {
                    e.alias.lower() for e in parent_select.expressions if e.alias
                }
            alias_map = alias_maps[sid]

            table_ref = col.table
            origin: exp.Table | None
            if table_ref:
                origin = alias_map.get(table_ref.lower())
                if origin is None:
                    # Unknown alias (e.g. CTE column qualifier we couldn't
                    # map back to a real table) — bail without flagging.
                    continue
            else:
                # Unqualified column — check projection-alias context
                # first: MaxCompute permits SELECT-list aliases in
                # ORDER BY / GROUP BY / HAVING / QUALIFY clauses, and
                # those aliases by definition do not exist on any base
                # table. ``_alias_context_select`` returns the Select
                # whose clause directly contains *col*; we suppress
                # the lookup only when that Select is the same as
                # the column's enclosing Select (so a nested subquery
                # inside an outer ORDER BY clause still resolves its
                # own columns normally).
                if (
                    col.name.lower() in projection_aliases[sid]
                    and _alias_context_select(col) is parent_select
                ):
                    continue
                # Resolve to the FROM/JOIN tables of the column's
                # *enclosing* Select only. ``stmt.find_all(
                # exp.Table)`` would walk the entire statement, picking up
                # base tables nested inside CTE bodies or sibling
                # subqueries even when the local SELECT only references
                # one of them. That caused
                # ``WITH ev AS (SELECT id AS user_id FROM events) SELECT
                # user_id FROM ev`` to mis-resolve the outer ``user_id``
                # against ``events`` (the lone non-CTE table the whole-
                # statement scan saw) and flag a valid CTE output alias
                # as column-not-found.
                real_tables = real_tables_in_select(parent_select, ctes)
                if len(real_tables) != 1:
                    continue
                origin = real_tables[0]

            table_name = origin.name
            # Skip CTE-qualified references — there's no schema to check.
            if not origin.catalog and table_name.lower() in ctes:
                continue

            key = (
                (origin.catalog or "").lower(),
                (origin.db or "default").lower(),
                table_name.lower(),
                col.name.lower(),
            )
            if key in seen:
                continue
            seen.add(key)

            # Resolve the source: prefer the FQN's explicit catalog/db when
            # present; otherwise walk profile.sources via to_source_key.
            sk = resolve_source_for_table(ctx, origin, table_name)
            if sk is None:
                continue  # table_not_found rule will flag this

            table_row = ctx.db.get_table(sk, table_name)
            if table_row is None:
                continue
            cols = ctx.db.get_columns(table_row["id"])
            col_names = {c["name"].lower() for c in cols}
            if col.name.lower() not in col_names:
                names = sorted(c["name"] for c in cols)
                shown = names[:_MAX_AVAILABLE_COLS]
                suffix = (
                    f" (+{len(names) - _MAX_AVAILABLE_COLS} more)"
                    if len(names) > _MAX_AVAILABLE_COLS
                    else ""
                )
                issues.append(
                    Issue(
                        severity="error",
                        rule="schema.column-not-found",
                        message=(
                            f"Column `{col.name}` does not exist on table `{table_name}`. "
                            f"Available: {', '.join(shown)}{suffix}"
                        ),
                    )
                )
    return issues
