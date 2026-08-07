# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Sanity check that the small_package_db fixture builds without errors."""
import functools
import operator


def test_small_package_db_has_two_tables(small_package_db):
    tables = small_package_db.list_tables()
    assert {t["name"] for t in tables} == {"orders", "customers"}


def test_small_package_db_has_one_join(small_package_db):
    joins = small_package_db.list_joins()
    assert len(joins) == 1
    assert joins[0]["left_table"] == "orders"
    assert joins[0]["right_table"] == "customers"


def test_small_package_db_orders_has_ai_context(small_package_db):
    ctx = small_package_db.get_table_ai_context("warehouse", "orders")
    assert "Order line items" in ctx


def test_small_package_db_customers_id_is_identifier_primary(small_package_db):
    cols = small_package_db.get_columns_bulk(
        [t["id"] for t in small_package_db.list_tables() if t["name"] == "customers"]
    )
    all_cols = functools.reduce(operator.iadd, cols.values(), [])
    id_col = next(c for c in all_cols if c["name"] == "id")
    assert id_col["semantic_role"] == "identifier"
    assert id_col["id_type"] == "primary"
