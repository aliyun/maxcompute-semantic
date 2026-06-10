# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MaxCompute SQLGlot dialect — parse and generate MaxCompute SQL.

Public API:

    from maxcompute_semantic.dialect import parse_mc, parse_mc_one

    stmts = parse_mc("SELECT DATEADD(GETDATE(), 1, 'dd')")
    tree  = parse_mc_one("SELECT DATEADD(GETDATE(), 1, 'dd')")

The ``MaxCompute`` dialect is registered at import time via the SQLGlot
metaclass, so ``sqlglot.parse(sql, read="maxcompute")`` also works once
this module has been imported.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from maxcompute_semantic.dialect._dialect import MaxCompute  # noqa: F401 — triggers registration

_DIALECT = "maxcompute"


def parse_mc(
    sql: str,
    *,
    error_level: sqlglot.ErrorLevel = sqlglot.ErrorLevel.WARN,
) -> list[exp.Expression | None]:
    """Parse *sql* as MaxCompute SQL, returning a list of statements."""
    result: Any = sqlglot.parse(sql, read=_DIALECT, error_level=error_level)
    return result  # type: ignore[no-any-return]


def parse_mc_one(sql: str) -> exp.Expression:
    """Parse *sql* as a single MaxCompute statement."""
    result: Any = sqlglot.parse_one(sql, read=_DIALECT)
    return result  # type: ignore[no-any-return]
