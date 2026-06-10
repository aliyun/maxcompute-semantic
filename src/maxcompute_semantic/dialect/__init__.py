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

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from maxcompute_semantic.dialect._dialect import MaxCompute  # noqa: F401 — triggers registration

if TYPE_CHECKING:
    pass

_DIALECT = "maxcompute"


def parse_mc(
    sql: str,
    *,
    error_level: sqlglot.ErrorLevel = sqlglot.ErrorLevel.WARN,
) -> list[exp.Expression | None]:
    """Parse *sql* as MaxCompute SQL, returning a list of statements."""
    return sqlglot.parse(sql, read=_DIALECT, error_level=error_level)


def parse_mc_one(sql: str) -> exp.Expression:
    """Parse *sql* as a single MaxCompute statement."""
    return sqlglot.parse_one(sql, read=_DIALECT)
