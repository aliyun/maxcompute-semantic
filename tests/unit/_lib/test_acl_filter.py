# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for _lib/acl_filter.py — column ACL filter for history mining."""

from __future__ import annotations

from maxcompute_semantic._lib.acl_filter import should_drop_sql_for_acl


def test_no_allowlist_never_drops() -> None:
    """allowlist=None → never drop any SQL."""
    assert (
        should_drop_sql_for_acl(
            "SELECT * FROM orders",
            "orders",
            all_cols=["id", "secret_col"],
            partition_cols=[],
            allowlist=None,
        )
        is False
    )


def test_star_select_drops_when_allowlist_set() -> None:
    """SELECT * against a table with allowlist → drop (hits denied cols)."""
    assert (
        should_drop_sql_for_acl(
            "SELECT * FROM orders",
            "orders",
            all_cols=["id", "secret_col"],
            partition_cols=["ds"],
            allowlist=["id"],
        )
        is True
    )


def test_allowed_cols_not_dropped() -> None:
    """SQL that only mentions allowed columns is not dropped."""
    assert (
        should_drop_sql_for_acl(
            "SELECT id FROM orders",
            "orders",
            all_cols=["id", "secret_col"],
            partition_cols=[],
            allowlist=["id"],
        )
        is False
    )


def test_denied_col_drops() -> None:
    """SQL that mentions a denied column is dropped."""
    assert (
        should_drop_sql_for_acl(
            "SELECT secret_col FROM orders",
            "orders",
            all_cols=["id", "secret_col"],
            partition_cols=[],
            allowlist=["id"],
        )
        is True
    )


def test_partition_cols_always_allowed() -> None:
    """Partition columns are always treated as allowed."""
    assert (
        should_drop_sql_for_acl(
            "SELECT id, ds FROM orders WHERE ds = '20260101'",
            "orders",
            all_cols=["id", "secret_col"],
            partition_cols=["ds"],
            allowlist=["id"],
        )
        is False
    )


def test_unsafe_table_name_never_drops() -> None:
    """Non-identifier table name → defensive return False."""
    assert (
        should_drop_sql_for_acl(
            "SELECT * FROM 1bad",
            "1bad",
            all_cols=["x"],
            partition_cols=[],
            allowlist=["x"],
        )
        is False
    )


def test_unsafe_column_name_not_checked() -> None:
    """Denied column with non-safe-identifier name is skipped in regex check."""
    assert (
        should_drop_sql_for_acl(
            "SELECT col'inject FROM orders",
            "orders",
            all_cols=["id", "col'inject"],
            partition_cols=[],
            allowlist=["id"],
        )
        is False
    )
