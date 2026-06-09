"""Hint — aggregate function applied to a column annotated as dimension.

Fires when a SQL aggregate (``SUM``, ``AVG``, ``MIN``, ``MAX``, ``COUNT``,
etc. — anything ``sqlglot`` classifies as ``exp.AggFunc``) targets a
column whose ``semantic_role`` annotation is ``dimension``. Dimensions
are the GROUP-BY axis by definition; aggregating one is usually a
semantic bug (``SUM(status)`` over a categorical status code, ``AVG``
on a region label, etc.).

Confidence is ``medium`` rather than ``high`` because annotations can
be wrong — the agent may have misclassified a column as dimension when
it's really an ordinal numeric. The ``if_misleading`` payload directs
to the proposal workflow so the role can be corrected as a
maintenance follow-up, not during the query flow.

The generator skips:

- unqualified columns (the inner ``exp.Column`` has no ``.table``)
  — v1 behaviour, since multi-real-table inference here would be
  brittle without the type-rule's same-FROM single-table fallback;
- columns with no annotation row (``get_column_semantics`` returns
  ``None``) — the principle is "we don't know, so we don't fire";
- columns whose ``semantic_role`` is anything other than ``dimension``
  (case-insensitively).

Repeated ``(func, catalog, db, table, col)`` tuples in the same SQL
collapse to a single hint via the ``seen`` set so
``SELECT SUM(status), SUM(status)`` yields one hint, not two. The
dedup key is FQN-aware: two tables with the same bare name from
different sources (e.g. ``proj_a.default.orders.amount`` vs
``proj_b.default.orders.amount``) must each be checked independently,
since their annotations can diverge — a bare-triple key would collapse
the second aggregate and silently skip a real wrong-role hint.
"""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    alias_to_table_in_select,
    parse_statements,
    resolve_source_for_table,
)
from maxcompute_semantic.commands.sql_review.types import Hint, ReviewContext


def hint_dimension_aggregated(ctx: ReviewContext) -> list[Hint]:
    """Fire when an aggregate targets a column annotated as ``dimension``.

    Walks the AST directly (rather than reading ``ctx.evidence.aggregates``)
    so we can keep the column's originating ``exp.Table`` node and honor
    its FQN catalog/db when resolving the source. The evidence form
    drops to bare table names, which in a multi-source profile would
    silently pick whichever source happens to be listed first — and
    fire a hint against the wrong source's annotation.
    """
    hints: list[Hint] = []
    # Dedup key is FQN-aware: bare ``(func, table, col)`` would collapse
    # ``proj_a.default.orders.amount`` and ``proj_b.default.orders.amount``
    # to the same triple, so a hint that should fire against the second
    # source's annotation gets silently skipped after the first aggregate
    # marks the triple as seen. Mirror the schema/coverage Round 2 dedup
    # tuple: ``(catalog, db, name, ...)`` lower-cased.
    seen: set[tuple[str, str, str, str, str]] = set()
    for stmt in parse_statements(ctx.sql):
        # Per-Select alias map — see ``rules/schema.py:check_column_not_found``
        # for the alias-shadowing case a statement-wide ``alias_to_table``
        # mis-routes. Cached by ``id(select)`` so repeat aggregates in the
        # same Select don't re-walk its FROM/JOIN args.
        alias_maps: dict[int, dict[str, exp.Table]] = {}
        for agg in stmt.find_all(exp.AggFunc):
            inner = agg.this
            if not isinstance(inner, exp.Column) or not inner.table:
                # Unqualified column — v1 skips rather than infer the
                # single-real-table FROM target. Easier to relax later
                # than to revoke a too-eager hint after the fact.
                continue
            parent_select = inner.find_ancestor(exp.Select)
            if parent_select is None:
                continue
            sid = id(parent_select)
            if sid not in alias_maps:
                alias_maps[sid] = alias_to_table_in_select(parent_select)
            origin = alias_maps[sid].get(inner.table.lower())
            if origin is None:
                continue
            func = agg.key.upper()
            table = origin.name
            col = inner.name
            key = (
                func,
                (origin.catalog or "").lower(),
                (origin.db or "default").lower(),
                table.lower(),
                col.lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            sk = resolve_source_for_table(ctx, origin, table)
            if sk is None:
                continue
            semantics = ctx.db.get_column_semantics(sk, table, col)
            if semantics is None:
                continue
            if (semantics.get("semantic_role") or "").lower() != "dimension":
                continue
            hints.append(
                Hint(
                    kind="aggregation.dimension-aggregated",
                    message=(
                        f"`{func}({table}.{col})` aggregates a column "
                        f"annotated as `dimension`; dimensions are typically "
                        f"used in GROUP BY, not as the aggregate target"
                    ),
                    confidence="medium",
                    evidence={
                        "function": func,
                        "table": table,
                        "column": col,
                        "declared_role": "dimension",
                    },
                    if_misleading=(
                        f"if the role is wrong, correct it after the query: "
                        f"load `mcs skill get enrich` and propose the correct role "
                        f"for `{table}.{col}` via `mcs package propose --from-stdin`"
                    ),
                )
            )
    return hints
