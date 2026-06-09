"""Projection rule — flag a ranking key projected in a top-N query.

A purely structural over-projection signal: in a `... ORDER BY x DESC
LIMIT n` query, projecting `x` itself (when `x` is not also a filter /
group key the question pins) is usually wrong — `x` is the *ranking
key*, and "which row has the highest x" wants the row identifier, not
x. This is a universal SQL smell (independent of any dataset), so it is
a `warning`, not an `error`: the agent decides, but is nudged to drop
the ranking column when the question only asks for the entity.

Deliberately narrow to avoid false positives:
- only fires when the statement has BOTH `ORDER BY` and `LIMIT`
  (a top-N query, where projecting the sort key is the classic trap);
- only flags a projected column that appears in `ORDER BY` AND does NOT
  appear in `WHERE` / `GROUP BY` (a column the question explicitly
  filters/groups by is legitimately in the answer);
- aggregates in the projection are never flagged (they are the answer
  for "what is the highest/total X" questions).
"""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import parse_statements
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext


def _projected_plain_columns(select: exp.Select) -> set[str]:
    """Lower-cased names of bare/aliased plain columns in the SELECT list.

    Skips aggregates and any expression that isn't a plain column or an
    alias wrapping a plain column — those are computed answers, not
    over-projection candidates.
    """
    out: set[str] = set()
    for e in select.expressions:
        target = e.this if isinstance(e, exp.Alias) else e
        if isinstance(target, exp.Column):
            out.add(target.name.lower())
    return out


def _columns_in(node: exp.Expression | None) -> set[str]:
    if node is None:
        return set()
    return {c.name.lower() for c in node.find_all(exp.Column)}


def check_ranking_key_in_projection(ctx: ReviewContext) -> list[Issue]:
    """Flag a projected ranking key in an ORDER BY ... LIMIT query."""
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        for select in stmt.find_all(exp.Select):
            order = select.args.get("order")
            has_limit = select.args.get("limit") is not None
            if not order or not has_limit:
                continue
            order_cols = _columns_in(order)
            filter_cols = _columns_in(select.args.get("where")) | _columns_in(
                select.args.get("group")
            )
            projected = _projected_plain_columns(select)
            ranking_keys = sorted(
                c for c in projected if c in order_cols and c not in filter_cols
            )
            for col in ranking_keys:
                issues.append(
                    Issue(
                        severity="warning",
                        rule="projection.ranking-key-in-select",
                        message=(
                            f"Column `{col}` is in both SELECT and ORDER BY of a "
                            f"top-N (LIMIT) query — it looks like the ranking key. "
                            f"If the question asks which row ranks highest, project "
                            f"the row's identifier/name, not the sort key itself."
                        ),
                        fix_hint=(
                            f"drop `{col}` from SELECT unless the question explicitly "
                            f"asks for that value; keep it in ORDER BY only"
                        ),
                    )
                )
    return issues


def check_intermediate_values_in_projection(ctx: ReviewContext) -> list[Issue]:
    """Flag intermediate computation columns projected alongside the derived result.

    Pattern: SELECT raw_a, raw_b, raw_a - raw_b AS diff — the question
    only asks for the difference, but the agent projects both raw
    components as well. A universal SQL style issue (not dataset-specific):
    "What is the difference?" wants one scalar, not three columns.

    Detection: if the SELECT list contains a binary arithmetic expression
    (Add/Sub/Mul/Div) or CASE expression AND also projects one or more of
    the column operands of that expression as standalone columns, flag them.
    """
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        for select in stmt.find_all(exp.Select):
            derived_operand_cols: set[str] = set()
            for expr in select.expressions:
                target = expr.this if isinstance(expr, exp.Alias) else expr
                if isinstance(target, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Case)):
                    for col in target.find_all(exp.Column):
                        derived_operand_cols.add(col.name.lower())

            if not derived_operand_cols:
                continue

            projected = _projected_plain_columns(select)
            intermediates = sorted(projected & derived_operand_cols)
            if intermediates:
                issues.append(
                    Issue(
                        severity="warning",
                        rule="projection.intermediate-values",
                        message=(
                            f"Columns {intermediates} appear both as standalone "
                            f"projections AND as operands of a derived expression "
                            f"in the same SELECT. If the question asks for the "
                            f"derived value (difference/ratio/percentage), remove "
                            f"the raw components — they are intermediate values."
                        ),
                        fix_hint=(
                            f"drop {intermediates} from SELECT; keep only the "
                            f"derived expression"
                        ),
                    )
                )
    return issues
