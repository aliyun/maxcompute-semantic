# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""JIT next-step phrasing for `mcs sql execute` success envelopes."""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql import _classify_sql
from maxcompute_semantic.commands.sql_review.rules._common import (
    cte_names,
    parse_statements,
)


def _qualified_name(table: exp.Table) -> str:
    """Return the FQN-aware string form of *table*.

    Sqlglot's :class:`exp.Table` exposes ``catalog`` / ``db`` / ``name``
    as separate slots. Callers writing ``mcs memory verify --tables ...``
    in multi-source profiles need the qualifiers preserved so ambiguous
    bare names don't collapse to the wrong source — see CLAUDE.md's
    ``mcs memory verify`` guidance.
    """
    if table.catalog:
        return f"{table.catalog}.{table.db}.{table.name}"
    if table.db:
        return f"{table.db}.{table.name}"
    return table.name


def next_step_for_sql(sql: str) -> str:
    """Return a one-line next-step suggestion for the response body.

    Empty string when no useful suggestion fits (writes, table-less SELECTs).
    CTE references (``WITH cte AS (...) SELECT FROM cte``) are filtered
    out per-statement so they don't leak into ``--tables``; FQN qualifiers
    on real tables are preserved verbatim.
    """
    if _classify_sql(sql) != "read":
        return ""
    tables: list[str] = []
    for stmt in parse_statements(sql):
        ctes = cte_names(stmt)
        for table in stmt.find_all(exp.Table):
            if table.name and table.name.lower() in ctes and not table.db:
                continue
            tables.append(_qualified_name(table))
    if not tables:
        return ""
    table_list = ",".join(sorted(set(tables)))
    return (
        f"If the result matches intent, teach it: "
        f"mcs memory verify --tables {table_list} "
        f"--question '<NL question>' --sql '<this SQL>'"
    )
