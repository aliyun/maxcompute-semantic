# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for sql_review rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from maxcompute_semantic.dialect import parse_mc

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import DataSource
    from maxcompute_semantic.commands.sql_review.types import ReviewContext


def parse_statements(sql: str) -> list[exp.Expression]:
    """Parse SQL into non-None statements; empty on parse failure.

    ``sqlglot.parse()`` is typed as returning ``list[Expr | None]``
    (``Expr`` is sqlglot's structural alias), but at runtime every
    non-None entry is an ``exp.Expression``. The ``isinstance`` filter
    narrows the type for mypy without changing the runtime behaviour
    of the prior ``is not None`` comprehension.
    """
    try:
        parsed = parse_mc(sql, error_level=sqlglot.ErrorLevel.IGNORE)
    except sqlglot.errors.SqlglotError:
        return []
    return [s for s in parsed if isinstance(s, exp.Expression)]


def cte_names(stmt: exp.Expression) -> set[str]:
    """Names introduced by `WITH cte AS (...)` clauses on *stmt*.

    Names are lower-cased to match `lookup_source_key`'s case-insensitive
    semantics — CTE references with mismatched case still resolve to the
    same definition at execution time.
    """
    return {(c.alias_or_name or "").lower() for c in stmt.find_all(exp.CTE) if c.alias_or_name}


def alias_to_table_in_select(select: exp.Select) -> dict[str, exp.Table]:
    """Build alias → exp.Table map for the direct FROM/JOIN tables of one Select.

    Scopes to *select*'s own ``from_`` and ``joins`` args — does NOT
    recurse into nested subqueries or CTE bodies. This is what column
    resolution needs: in
    ``SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM other o WHERE o.x = 1)``
    the inner ``o`` aliases ``other`` and the outer ``o`` aliases
    ``orders``. A statement-wide ``find_all(exp.Table)`` collapses both
    ``o`` keys to whichever Table iteration order visits last, so the
    outer ``o.id`` mis-resolves against ``other`` and the schema /
    type / coverage / aggregation passes fire false hits against the
    wrong table.

    sqlglot 30.x stores the immediate FROM clause under the ``from_``
    arg key (not ``from``) and JOINs under ``joins``. Both accessors
    are non-recursive — they return only the Select's own clauses —
    which is exactly the scoping this helper needs. Each caller computes
    the per-col enclosing Select via ``col.find_ancestor(exp.Select)``
    and looks up (or caches) the map for that Select.

    Keys are lower-cased so ``SELECT o.x FROM orders O`` resolves
    ``o.x`` against the alias ``O``. MaxCompute identifiers are
    case-insensitive at the engine, so a case-sensitive lookup would
    miss legitimate references and silently suppress the
    ``schema.column-not-found`` / type / aggregation hints they should
    fire. Callers must lower-case the lookup key.
    """
    out: dict[str, exp.Table] = {}
    from_clause = select.args.get("from_")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        t = from_clause.this
        out[(t.alias or t.name).lower()] = t
    for join in select.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            t = join.this
            out[(t.alias or t.name).lower()] = t
    return out


def real_tables_in_select(select: exp.Select, ctes: set[str]) -> list[exp.Table]:
    """Real (non-CTE) FROM/JOIN tables of *select* — scoped to this Select.

    Sibling to ``alias_to_table_in_select`` for the unqualified-column
    fallback used by the schema / type / coverage passes:
    ``SELECT x FROM other`` inside a subquery must resolve ``x``
    against ``other`` regardless of what the outer Select's FROM is.
    A statement-wide ``find_all(exp.Table)`` walks both scopes and the
    single-real-table check picks an unrelated outer table or bails on
    len != 1.
    """
    out: list[exp.Table] = []
    from_clause = select.args.get("from_")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        t = from_clause.this
        if t.name.lower() not in ctes:
            out.append(t)
    for join in select.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            t = join.this
            if t.name.lower() not in ctes:
                out.append(t)
    return out


def resolve_source_for_table(
    ctx: ReviewContext,
    origin: exp.Table | None,
    table_name: str,
) -> str | None:
    """Resolve the source_key for *table_name*, honoring an FQN origin.

    Three-step contract:

    1. **3-segment ``proj.schema.table``** (``origin.catalog`` set):
       look up that exact ``(catalog, db, name)`` triple — no walking
       other sources.
    2. **2-segment ``db.table``** (``origin.db`` set, ``origin.catalog``
       empty): the user has named one segment explicitly, so we must
       not silently fall through to a different source. Try the two
       valid interpretations in order:

       - *schema-form* — walk ``profile.sources`` whose ``schema``
         matches ``origin.db`` (case-insensitively) and look the
         table up against ``(src.project, src.schema, name)``. This
         is the standard 3-level read where the project is implicit
         (the session's compute project) and the SQL names the
         schema explicitly.
       - *project-form* — walk ``profile.sources`` whose ``project``
         matches ``origin.db`` and look the table up against
         ``(src.project, src.schema, name)``. This is the 2-level
         shape MaxCompute accepts (``proj.table`` with implicit
         ``default`` schema).

       Returns ``None`` when neither interpretation finds the table
       — the explicit two-segment form must not silently resolve
       through ``ctx.to_source_key``, which would walk *every* source
       and let a same-named table in an unrelated schema mask the
       missing reference (the bug the Round 6 Codex review flagged:
       ``SELECT amount FROM schema_b.orders`` silently resolved to
       ``default.orders`` and let a missing-column escape detection).
    3. **Bare name** (no catalog, no db): fall back to
       ``ctx.to_source_key(table_name)`` — first matching source wins.

    Returns ``None`` when no source contains the table.
    """
    if ctx.db is None:
        return None
    if origin is not None and origin.catalog:
        sk: str | None = ctx.db.lookup_source_key(
            origin.catalog, origin.db or "default", table_name
        )
        return sk
    if origin is not None and origin.db:
        keys = source_keys_for_two_segment(ctx, origin.db, table_name)
        return keys[0] if len(keys) == 1 else None
    return ctx.to_source_key(table_name)


def _keys_for_sources(
    ctx: ReviewContext,
    sources: list[DataSource],
    table_name: str,
) -> list[str]:
    out: list[str] = []
    if ctx.db is None:
        return out
    for src in sources:
        sk: str | None = ctx.db.lookup_source_key(src.project, src.schema, table_name)
        if sk:
            out.append(sk)
    return out


def source_keys_for_two_segment(
    ctx: ReviewContext,
    db_segment: str,
    table_name: str,
) -> list[str]:
    """Resolve a two-segment ``db.table`` reference.

    ``db`` can mean schema (3-level ``schema.table``) or project
    (2-level ``project.table``). Schema-form wins. If the active
    target project has that schema, only that project's source is
    eligible; a miss there must not fall through to another project's
    same-named schema. Project-form is restricted to the default
    schema because ``project.table`` is the 2-level shape.
    """
    segment = db_segment.lower()
    preferred_project = (ctx.project or ctx.profile.compute_project or "").lower()

    schema_sources = [src for src in ctx.profile.sources if (src.schema or "").lower() == segment]
    if schema_sources:
        preferred_sources = [
            src
            for src in schema_sources
            if preferred_project and src.project.lower() == preferred_project
        ]
        if preferred_sources:
            return _keys_for_sources(ctx, preferred_sources, table_name)
        return _keys_for_sources(ctx, schema_sources, table_name)

    project_sources = [
        src
        for src in ctx.profile.sources
        if src.project.lower() == segment and (src.schema or "default").lower() == "default"
    ]
    return _keys_for_sources(ctx, project_sources, table_name)
