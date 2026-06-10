# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""SQL read/write classifier and default write guard.

The guard lives in ``mc_client`` so every caller of ``MaxComputeClient``
gets the same fail-closed default, not just the ``mcs sql`` CLI entrypoint.
"""

from __future__ import annotations

import sqlglot

from maxcompute_semantic.dialect import parse_mc
from maxcompute_semantic.mc_client.errors import WriteOpRejectedError

_READ_EXPR_TYPES: tuple[type[sqlglot.exp.Expression], ...] = (
    sqlglot.exp.Select,
    sqlglot.exp.Union,
    sqlglot.exp.Intersect,
    sqlglot.exp.Except,
    sqlglot.exp.Subquery,
    sqlglot.exp.Describe,
    sqlglot.exp.Use,
)

# SET mutates session state (e.g. odps.sql.allow.fullscan), so it requires
# an explicit write allowance.
_WRITE_SESSION_EXPR_TYPES: tuple[type[sqlglot.exp.Expression], ...] = (sqlglot.exp.Set,)

_WRITE_EXPR_TYPES: tuple[type[sqlglot.exp.Expression], ...] = (
    sqlglot.exp.Insert,
    sqlglot.exp.Update,
    sqlglot.exp.Delete,
    sqlglot.exp.Merge,
    sqlglot.exp.Create,
    sqlglot.exp.Drop,
    sqlglot.exp.Alter,
    sqlglot.exp.TruncateTable,
    *(
        t
        for t in (
            getattr(sqlglot.exp, "Grant", None),
            getattr(sqlglot.exp, "Revoke", None),
        )
        if t is not None
    ),
)

_READ_COMMAND_KEYWORDS: frozenset[str] = frozenset({"SHOW", "DESC", "DESCRIBE", "EXPLAIN"})
_WRITE_COMMAND_KEYWORDS: frozenset[str] = frozenset({"GRANT", "REVOKE"})


class _LastParseError:
    error: str = ""


_last_parse_error = _LastParseError()


def classify_sql(sql: str) -> str:
    """Return ``"read"``, ``"write"``, or ``"unparseable"`` for *sql*."""
    _last_parse_error.error = ""
    try:
        statements = parse_mc(sql, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:
        _last_parse_error.error = str(exc)
        return "unparseable"
    if not statements or any(s is None for s in statements):
        return "unparseable"
    for stmt in statements:
        if isinstance(stmt, (*_WRITE_EXPR_TYPES, *_WRITE_SESSION_EXPR_TYPES)):
            return "write"
        if isinstance(stmt, _READ_EXPR_TYPES):
            continue
        if isinstance(stmt, sqlglot.exp.Command):
            keyword = (stmt.name or "").upper()
            if keyword in _WRITE_COMMAND_KEYWORDS:
                return "write"
            if keyword in _READ_COMMAND_KEYWORDS:
                continue
            return "unparseable"
        return "unparseable"
    return "read"


def last_parse_error() -> str:
    """Return the parse detail captured by the previous ``classify_sql`` call."""
    return _last_parse_error.error


def ensure_sql_write_allowed(sql: str, *, allow_write: bool = False) -> None:
    """Raise ``WriteOpRejectedError`` unless the SQL is read-only or allowed."""
    if allow_write:
        return
    verdict = classify_sql(sql)
    if verdict == "read":
        return
    if verdict == "write":
        raise WriteOpRejectedError(
            "SQL is a DML/DDL write; MaxComputeClient refuses writes by default",
            remediation=(
                "pass allow_write=True to confirm the write intent, or use a "
                "dedicated managed write path that sets allow_write explicitly"
            ),
            sql=sql,
        )

    parse_detail = last_parse_error()
    hint = f" Parse error: {parse_detail}." if parse_detail else ""
    raise WriteOpRejectedError(
        f"SQL could not be parsed as a known read shape; MaxComputeClient fails closed.{hint}",
        remediation=(
            "fix the SQL syntax error described in the message field, "
            "or double embedded single quotes in string literals "
            "(for example 'O''Reilly', not backslash escaping), "
            "or pass allow_write=True if the statement is intentional "
            "(typical for MaxCompute-specific DDL like ADD JAR or SET LABEL)"
        ),
        sql=sql,
    )
