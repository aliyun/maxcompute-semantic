# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Extract ``SET key=val`` statements into pyodps hints.

Called by the ``mcs sql`` verbs before the write guard and cost gate, so
``SET k=v; SELECT ...`` scripts run transparently (classified as the
remaining statement) instead of being rejected as a write or blocked by
the cost gate's ``execute_sql_cost`` (which, unlike ``run_sql``, does not
strip SET to hints).
"""

from __future__ import annotations


def split_set_hints(sql: str) -> tuple[str, dict[str, str]]:
    """Extract ``SET key=val`` statements into hints.

    Returns ``(sql_without_sets, hints)``. Uses the MaxCompute sqlglot
    tokenizer to split the SQL into verbatim statement segments at
    top-level semicolons (string- and comment-aware, so ``;`` inside a
    literal or ``--`` comment is not a separator). Each segment is parsed
    with ``parse_mc``; segments that parse to an assignment ``Set`` whose
    every ``SetItem`` is an ``EQ`` (not ``UNSET``/tag, not a bare flag)
    become hints and are dropped; all other segments (SELECT/DDL, and
    ``SET LABEL`` which parses as a ``Command``) are kept verbatim and
    rejoined.

    Non-SET SQL is preserved verbatim (no AST regeneration): the MaxCompute
    sqlglot generator rewrites functions (verified lossy: ``TO_CHAR(d,
    fmt)`` -> ``CAST(d AS STRING)`` losing the format string, ``SUBSTRING``
    -> ``SUBSTR``), which would change the user's SQL semantics. key/val
    are read from the ``Set`` AST (``SetItem.this.this`` is the key,
    ``.expression`` the value); boolean values normalize to
    ``TRUE``/``FALSE`` (MaxCompute is case-insensitive on these) while
    other literal forms round-trip.
    """
    # Lazy: sqlglot + the dialect package sit on the CLI startup chain.
    import sqlglot
    from sqlglot import exp
    from sqlglot.tokens import TokenType

    from maxcompute_semantic.dialect import MaxCompute, parse_mc

    hints: list[tuple[str, str]] = []
    kept: list[str] = []
    try:
        toks = MaxCompute.Tokenizer().tokenize(sql)
    except sqlglot.errors.TokenError:
        # Malformed SQL (e.g. unmatched quotes) cannot be tokenized; fall back
        # to the original SQL with no hints so the caller's write guard /
        # classifier can emit its friendly parse-error remediation instead of
        # crashing. Keeps this function total for every caller.
        return sql, {}
    semi_ends = [t.end for t in toks if t.token_type is TokenType.SEMICOLON]
    bounds = [-1, *semi_ends, len(sql)]
    for i in range(len(bounds) - 1):
        segment = sql[bounds[i] + 1 : bounds[i + 1]].strip()
        if not segment:
            continue
        try:
            stmts = parse_mc(segment, error_level=sqlglot.ErrorLevel.IGNORE)
        except Exception:  # noqa: BLE001 — keeps this function total for every caller
            stmts = []
        stmt = stmts[0] if stmts else None
        if (
            isinstance(stmt, exp.Set)
            and not stmt.args.get("unset")
            and not stmt.args.get("tag")
        ):
            items = stmt.args.get("expressions") or []
            eqs = [it for it in items if isinstance(it.this, exp.EQ)]
            if eqs and len(eqs) == len(items):
                for it in eqs:
                    eq = it.this
                    k = eq.this.sql(dialect="maxcompute")
                    v = eq.expression.sql(dialect="maxcompute")
                    hints.append((k, v))
                continue
        kept.append(segment)
    if not hints:
        # Nothing extracted -> return the ORIGINAL sql verbatim (newlines /
        # formatting between statements preserved), not a "; "-rejoined copy.
        return sql, {}
    return "; ".join(kept), dict(hints)
