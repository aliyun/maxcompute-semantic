# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sqlglot-based metric expression validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.metric_validator import (
    validate_metric_expression,
)


@pytest.fixture
def single_source_db(tmp_path: Path) -> PackageDB:
    db = PackageDB(tmp_path / "pkg.db")
    orders_id = db.upsert_table(
        source_key="warehouse", name="orders", schema_hash="h", errors_json=None
    )
    customers_id = db.upsert_table(
        source_key="warehouse", name="customers", schema_hash="h", errors_json=None
    )
    db.upsert_columns(
        orders_id,
        columns=[
            {"name": "id", "type": "BIGINT"},
            {"name": "amount", "type": "BIGINT"},
            {"name": "customer_id", "type": "BIGINT"},
        ],
    )
    db.upsert_columns(
        customers_id,
        columns=[
            {"name": "id", "type": "BIGINT"},
            {"name": "name", "type": "VARCHAR"},
        ],
    )
    return db


@pytest.fixture
def multi_source_db(tmp_path: Path) -> PackageDB:
    db = PackageDB(tmp_path / "pkg.db")
    orders_id = db.upsert_table(
        source_key="warehouse", name="orders", schema_hash="h", errors_json=None
    )
    refunds_id = db.upsert_table(
        source_key="crm", name="refunds", schema_hash="h", errors_json=None
    )
    db.upsert_columns(
        orders_id,
        columns=[{"name": "amount", "type": "BIGINT"}],
    )
    db.upsert_columns(
        refunds_id,
        columns=[{"name": "amount", "type": "BIGINT"}],
    )
    return db


def test_validates_qualified_column_no_warnings(single_source_db: PackageDB) -> None:
    res = validate_metric_expression("SUM(orders.amount)", single_source_db)
    assert res.ok is True
    assert res.warnings == []


def test_unparseable_expression_returns_error(single_source_db: PackageDB) -> None:
    res = validate_metric_expression("SUM(((", single_source_db)
    assert res.ok is False
    assert "parse" in res.error.lower()


def test_warns_on_missing_table(single_source_db: PackageDB) -> None:
    res = validate_metric_expression("SUM(refunds.amount)", single_source_db)
    assert res.ok is True
    joined = " ".join(res.warnings).lower()
    assert "refunds" in joined and "not in the current profile" in joined


def test_warns_on_bare_column_in_multi_source_profile(multi_source_db: PackageDB) -> None:
    res = validate_metric_expression("SUM(amount)", multi_source_db)
    assert res.ok is True
    joined = " ".join(res.warnings).lower()
    assert "qualify" in joined or "ambiguous" in joined


def test_bare_column_in_single_source_no_warning(single_source_db: PackageDB) -> None:
    res = validate_metric_expression("SUM(amount)", single_source_db)
    assert res.ok is True
    # ``amount`` exists only in orders within the warehouse source, so
    # bare reference is unambiguous.
    assert res.warnings == []


def test_warns_on_ambiguous_bare_column(single_source_db: PackageDB) -> None:
    # ``id`` exists in both orders and customers.
    res = validate_metric_expression("COUNT(id)", single_source_db)
    assert res.ok is True
    joined = " ".join(res.warnings).lower()
    assert "ambiguous" in joined


def test_three_part_reference(multi_source_db: PackageDB) -> None:
    res = validate_metric_expression(
        "SUM(warehouse.orders.amount) + SUM(crm.refunds.amount)", multi_source_db
    )
    assert res.ok is True
    assert res.warnings == []
