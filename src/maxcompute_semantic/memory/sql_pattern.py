# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""SQL pattern normalization for mined and verified query memory.

The analyzer intentionally normalizes literal values but keeps table names,
column names, joins, projection shape, grouping, ordering, and limit shape.
That lets ``WHERE id = 10`` and ``WHERE id = 20`` count as the same usage
pattern without hiding meaningful structural differences.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlglot import errors, exp

from maxcompute_semantic.dialect import parse_mc_one


@dataclass(frozen=True)
class SqlPattern:
    raw_sql: str
    canonical_sql: str
    shape_key: str
    frequency_key: str
    normalizer_version: int
    tables: tuple[str, ...]
    where_predicates: tuple[str, ...]
    join_edges: tuple[str, ...]
    parse_error: str | None = None


NORMALIZER_VERSION = 1


_COMPARISON_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
    exp.In,
    exp.Is,
)


def analyze_sql_pattern(sql: str) -> SqlPattern:
    """Return a literal-insensitive SQL pattern.

    ``shape_key`` is stable for equivalent SQL shapes. ``frequency_key`` is
    currently the same value and exists so future storage code can use a more
    granular key without changing every caller.
    """
    try:
        parsed = parse_mc_one(sql)
        normalized = parsed.copy()
        normalized = normalized.transform(
            lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node
        )
        _sort_top_level_and_predicates(normalized)
        canonical_sql = normalized.sql()
        shape_key = _hash(canonical_sql)
        return SqlPattern(
            raw_sql=sql,
            canonical_sql=canonical_sql,
            shape_key=shape_key,
            frequency_key=shape_key,
            normalizer_version=NORMALIZER_VERSION,
            tables=_tables(normalized),
            where_predicates=_where_predicates(normalized),
            join_edges=_join_edges(normalized),
        )
    except errors.SqlglotError as exc:
        canonical_sql = _fallback_normalize(sql)
        shape_key = _hash(canonical_sql)
        return SqlPattern(
            raw_sql=sql,
            canonical_sql=canonical_sql,
            shape_key=shape_key,
            frequency_key=shape_key,
            normalizer_version=NORMALIZER_VERSION,
            tables=(),
            where_predicates=(),
            join_edges=(),
            parse_error=str(exc),
        )


def _sort_top_level_and_predicates(tree: exp.Expression) -> None:
    where = tree.args.get("where")
    if not isinstance(where, exp.Where):
        return
    root = where.this
    if root is None:
        return
    predicates = list(root.flatten()) if isinstance(root, exp.And) else [root]
    if len(predicates) < 2:
        return
    predicates = sorted(predicates, key=lambda item: item.sql().lower())
    where.set("this", exp.and_(*predicates, copy=False))


def _tables(tree: exp.Expression) -> tuple[str, ...]:
    names = {
        table.name.lower()
        for table in tree.find_all(exp.Table)
        if isinstance(table.name, str) and table.name
    }
    return tuple(sorted(names))


def _where_predicates(tree: exp.Expression) -> tuple[str, ...]:
    where = tree.args.get("where")
    if not isinstance(where, exp.Where) or where.this is None:
        return ()
    if isinstance(where.this, exp.And):
        flattened: list[exp.Expression] = list(where.this.flatten())  # type: ignore[arg-type]
        predicates = flattened
    else:
        predicates = [where.this]
    return tuple(sorted(_predicate_key(predicate) for predicate in predicates))


def _join_edges(tree: exp.Expression) -> tuple[str, ...]:
    """Extract join edge keys with table aliases resolved to real names.

    sqlglot leaves ``ON`` expressions referencing the alias the SQL
    used (``ON u.id = o.user_id`` for ``FROM users u JOIN orders o``);
    if we ship the alias-shaped string downstream, the candidate
    ranker's ``tables`` lookup misses (no entry for bare ``u``) and
    the workload edge silently never enriches with profile stats. Walk
    the FROM/JOIN clause to build an alias map, then rewrite columns
    inside the ON expression before normalizing.
    """
    alias_map: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if table.alias:
            alias_map[table.alias] = table.name

    edges: list[str] = []
    for join in tree.find_all(exp.Join):
        on_expr = join.args.get("on")
        if on_expr is None:
            continue
        if alias_map:
            on_expr = on_expr.copy()
            for column in on_expr.find_all(exp.Column):
                tref = column.table
                if tref and tref in alias_map:
                    column.set("table", exp.Identifier(this=alias_map[tref], quoted=False))
        edges.append(_normalize_expr_sql(on_expr))
    return tuple(sorted(edges))


def _predicate_key(predicate: exp.Expression) -> str:
    if isinstance(predicate, _COMPARISON_TYPES):
        return _normalize_expr_sql(predicate)
    return _normalize_expr_sql(predicate)


def _normalize_expr_sql(expression: exp.Expression) -> str:
    normalized = expression.copy().transform(
        lambda node: exp.Placeholder() if isinstance(node, exp.Literal) else node
    )
    return re.sub(r"\s+", " ", normalized.sql()).strip().lower()


def _redact_projection_inplace(parsed: exp.Expr) -> None:
    placeholder = exp.Column(this=exp.Identifier(this="<col>", quoted=False))
    for select in parsed.find_all(exp.Select):
        new_expressions: list[exp.Expression] = []
        for expr in select.expressions:
            underlying = expr.unalias() if isinstance(expr, exp.Alias) else expr
            if isinstance(underlying, (exp.Column, exp.Star)):
                new_expressions.append(placeholder.copy())
            else:
                new_expressions.append(expr)
        select.set("expressions", new_expressions)


def _redact_join_keys_inplace(parsed: exp.Expr) -> None:
    placeholder_col = exp.Column(this=exp.Identifier(this="<col>", quoted=False))
    for join in parsed.find_all(exp.Join):
        on_expr = join.args.get("on")
        if on_expr is not None:
            for column in list(on_expr.find_all(exp.Column)):
                column.replace(placeholder_col.copy())
        using_expr = join.args.get("using")
        if using_expr:
            join.set("using", [exp.Identifier(this="<col>", quoted=False)])


def redact_projection_columns(canonical_sql: str) -> str:
    """Replace bare column references in SELECT projections with a placeholder.

    Mined SQL patterns expose access patterns (FROM / WHERE / JOIN /
    GROUP BY) that are reusable across questions, but the SELECT
    projection list is typically question-specific. Agents that copy
    a mined SELECT list verbatim end up answering the wrong question
    — they hit the right tables with the right predicates but project
    columns no-one asked for.

    Bare column references (``SELECT name, id``) and column aliases
    (``SELECT name AS n``) get replaced with ``<col>``. Aggregate /
    function / CASE / arithmetic expressions stay intact — those carry
    *shape* signal (``COUNT(*)``, ``SUM(amount)``, ``AVG(score)``) that
    tells the agent which aggregates this table is typically queried
    with, which IS reusable.

    SELECT * and star expressions are also redacted, since the agent
    must commit to a specific projection.
    """
    try:
        parsed = parse_mc_one(canonical_sql)
    except errors.SqlglotError:
        return canonical_sql
    _redact_projection_inplace(parsed)
    return parsed.sql()


def redact_join_keys(canonical_sql: str) -> str:
    """Replace column references inside JOIN ``ON`` clauses with ``<col>``.

    The same hazard as ``redact_projection_columns`` plays out on join
    edges. Mined patterns capture whatever join the historical query
    used — including queries that ran successfully but returned 0 rows
    because the join key was wrong. (Wrong-key joins don't raise an
    error in MaxCompute; they just produce empty result sets that the
    miner still counts as ``executed``.) When the agent reads such a
    pattern, it copies ``ON c.id = l.id`` verbatim into the new
    question even when the authoritative ``join_candidates`` (built
    from data profiling — uniqueness ratios + value-overlap evidence)
    says the real FK is ``c.uuid = l.uuid``.

    Redacting the ON-clause column references to ``<col>`` preserves
    the relationship signal (``cards JOIN legalities``, the join's
    cardinality, the join type) while forcing the agent to consult
    ``join_candidates`` for the actual column names. The join's
    table / alias / type / cardinality are all kept intact — only the
    EQ left/right column references and any ``AND``-combined extra
    predicates inside ON get their column refs replaced.

    For ``USING (col1, col2)`` joins, the entire USING clause is
    rewritten to ``USING (<col>)`` — the column list is question-
    specific.

    Cross joins / joins without an ON clause pass through unchanged.
    """
    try:
        parsed = parse_mc_one(canonical_sql)
    except errors.SqlglotError:
        return canonical_sql
    _redact_join_keys_inplace(parsed)
    return parsed.sql()


def redact_for_display(canonical_sql: str) -> str:
    """Apply both projection and join-key redactions in a single AST pass.

    Composing :func:`redact_projection_columns` and :func:`redact_join_keys`
    by chaining their string outputs silently fails: the first redaction
    emits ``<col>`` placeholders, sqlglot then refuses to re-parse the
    result because ``<col>`` looks like a less-than / greater-than token
    pair instead of an identifier, the second redaction catches the
    :class:`sqlglot.errors.SqlglotError` and returns its input unchanged,
    and the JOIN keys survive un-redacted into the agent's view.
    Witnessed in a smoke with-history snapshot where a child table's
    markdown surfaced ``ON c.id = l.id`` despite the markdown
    renderer "redacting" it, leading the agent to copy that wrong
    join into downstream cases. The unified single-pass
    function parses once, applies both transforms in-place on the AST,
    and serializes once — no intermediate ``<col>`` round-trip through
    the parser.
    """
    try:
        parsed = parse_mc_one(canonical_sql)
    except errors.SqlglotError:
        return canonical_sql
    _redact_projection_inplace(parsed)
    _redact_join_keys_inplace(parsed)
    return parsed.sql()


def _fallback_normalize(sql: str) -> str:
    text = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    text = re.sub(r"/\*.*", "", text, flags=re.DOTALL)
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
