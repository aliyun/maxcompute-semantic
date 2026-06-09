"""Dialect rules — flag SQLite/Hive syntax MaxCompute rejects.

All 4 dialect rules walk the AST once per call (cheap); we trade a
tiny duplication of `sqlglot.parse` calls for rule independence,
which keeps each rule fixable without touching the others.
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot.tokens import Tokenizer, TokenType

from maxcompute_semantic.commands.sql_review.rules._common import parse_statements
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext


def _count_iif_calls(sql: str) -> int:
    """Count IIF(…) call sites in *sql* using sqlglot's tokenizer.

    Why tokens, not raw-text regex: a regex on ``ctx.sql`` would
    false-positive on string literals (``'IIF(x,1,2)'``) and SQL
    comments (``-- IIF(...)`` / ``/* IIF(...) */``) — and ``IIF`` is an
    ``error``-severity rule, so a single false positive can block a
    valid query. The tokenizer strips comments and quotes string
    contents, so an IIF inside either is invisible.

    Why a custom token walk, not the parser: sqlglot collapses both
    ``IIF(c, a, b)`` and the MaxCompute-valid ``IF(c, a, b)`` into the
    same three-arg ``exp.If`` node — the AST can't distinguish them.
    The tokenizer preserves the original identifier text, so an ``IIF``
    VAR token followed by ``L_PAREN`` is an unambiguous call site. A
    bare ``IIF`` identifier (column name in ``SELECT IIF FROM t``) is
    not followed by ``L_PAREN`` and is intentionally not flagged.

    The tokenizer is more permissive than the parser: unparseable SQL
    like ``SELECT IIF(>>>`` still tokenizes successfully, so the rule
    still fires when the agent's SQL is mid-edit — the IIF token is
    the dialect signal regardless of surrounding parse state.
    """
    try:
        tokens = Tokenizer().tokenize(sql)
    except Exception:
        return 0
    count = 0
    for i, tok in enumerate(tokens):
        if tok.token_type != TokenType.VAR or tok.text.upper() != "IIF":
            continue
        if i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN:
            count += 1
    return count


def check_sqlite_iif(ctx: ReviewContext) -> list[Issue]:
    n = _count_iif_calls(ctx.sql)
    if n == 0:
        return []
    return [
        Issue(
            severity="error",
            rule="dialect.sqlite-iif",
            message=(
                "IIF() is SQLite syntax; MaxCompute uses IF(cond, a, b) "
                "or CASE WHEN cond THEN a ELSE b END"
            ),
            fix_hint="CASE WHEN cond THEN a ELSE b END",
        )
        for _ in range(n)
    ]


# Identify named SQL functions by walking exp.Anonymous (sqlglot's catch-all
# for unrecognized function calls) and matching the upper-cased function
# name. On the default dialect (which `_parse` uses), STRFTIME and JULIANDAY
# both land in exp.Anonymous with `.this` carrying the upper-cased name —
# verified empirically against sqlglot at the version pinned for this repo.
# An earlier draft also walked exp.Func looking for dedicated subclass keys,
# but on default dialect there are no Strftime/Julianday subclasses (and the
# Anonymous nodes that loop would re-find carry key="anonymous", never the
# function name), so it was dead code and was removed.
def _calls_with_name(sql: str, fn_name_upper: str) -> list[exp.Expression]:
    matches: list[exp.Expression] = []
    for stmt in parse_statements(sql):
        for node in stmt.find_all(exp.Anonymous):
            if (node.this or "").upper() == fn_name_upper:
                matches.append(node)
    return matches


def check_sqlite_strftime(ctx: ReviewContext) -> list[Issue]:
    # sqlglot parses STRFTIME as exp.TimeToStr or exp.Anonymous depending on
    # the source dialect; on default dialect it's Anonymous.
    calls = _calls_with_name(ctx.sql, "STRFTIME")
    return [
        Issue(
            severity="error",
            rule="dialect.sqlite-strftime",
            message=(
                "STRFTIME() is SQLite syntax; MaxCompute uses DATE_FORMAT(dt, fmt) "
                "or TO_CHAR(dt, fmt)"
            ),
            fix_hint="DATE_FORMAT(dt, 'yyyy-MM-dd')",
        )
        for _ in calls
    ]


def check_sqlite_julianday(ctx: ReviewContext) -> list[Issue]:
    calls = _calls_with_name(ctx.sql, "JULIANDAY")
    return [
        Issue(
            severity="error",
            rule="dialect.sqlite-julianday",
            message=(
                "JULIANDAY() is SQLite syntax; MaxCompute uses "
                "DATEDIFF(end, start, 'dd') for day differences"
            ),
            fix_hint="DATEDIFF(end_dt, start_dt, 'dd')",
        )
        for _ in calls
    ]


def check_sqlite_substr_neg(ctx: ReviewContext) -> list[Issue]:
    """SUBSTR with negative second arg — SQLite indexes from end; MaxCompute does not."""
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        for node in stmt.find_all(exp.Substring):
            start = node.args.get("start")
            # Negative literal in start position
            if isinstance(start, exp.Neg):
                issues.append(
                    Issue(
                        severity="error",
                        rule="dialect.sqlite-substr-neg",
                        message=(
                            "SUBSTR(s, -N) uses SQLite's negative-index convention; "
                            "MaxCompute treats negative SUBSTR starts as an error. "
                            "Use SUBSTR(s, LENGTH(s) - N + 1, N) for the last-N-chars idiom."
                        ),
                        fix_hint="SUBSTR(s, LENGTH(s) - N + 1, N)",
                    )
                )
    return issues
