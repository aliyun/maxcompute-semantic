# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Column ACL filter for history mining.

Spec: docs/superpowers/specs/2026-05-09-mining-acl-filter-design.md
"""

from __future__ import annotations

import re

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def should_drop_sql_for_acl(
    sql: str,
    table_name: str,
    *,
    all_cols: list[str],
    partition_cols: list[str],
    allowlist: list[str] | None,
) -> bool:
    """Return True if this SQL should be dropped from table_name's bucket
    because it would violate the user's column allowlist on that table.

    `allowlist=None` means no ACL constraint on this table → never drop.
    `allowlist=[]` is not produced by `_parse_columns_spec` (it raises on
    empty `--columns T=`); if a caller bypasses validation and passes [],
    every SQL touching a non-partition column is dropped, matching the
    literal "only these columns allowed" contract.

    Partition keys are always treated as allowed (auto-included in the
    `--columns` projection per existing sampling semantics).
    """
    if allowlist is None:
        return False
    if not _SAFE_IDENT.match(table_name):
        # Defensive: refuse to compile regex from non-identifier table name.
        return False
    # SELECT * against this table → would hit denied cols at runtime → drop.
    star_pattern = re.compile(
        rf"\bSELECT\s*\*\s*FROM\s+(\S+\.)?{re.escape(table_name)}\b",
        re.IGNORECASE,
    )
    if star_pattern.search(sql):
        return True
    # Mention of any denied column name → drop.
    allowed = {c.lower() for c in allowlist} | {c.lower() for c in partition_cols}
    denied = [c for c in all_cols if c.lower() not in allowed]
    for col in denied:
        if not _SAFE_IDENT.match(col):
            continue
        if re.search(rf"\b{re.escape(col)}\b", sql, re.IGNORECASE):
            return True
    return False
