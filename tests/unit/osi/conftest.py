"""Shared fixtures for the osi/ test suite.

The ``small_package_db`` fixture builds a minimal two-table PackageDB
(orders + customers in a ``warehouse`` source) with one foreign-key
JOIN, table-level ai_context on orders, and per-column OSI semantics
on the columns that drive the export/import round-trip tests. Tasks
7-11 reuse this fixture rather than re-staging the same schema in
each test module.
"""

from __future__ import annotations

import pytest
from maxcompute_semantic.build.storage import PackageDB


@pytest.fixture
def small_package_db(tmp_path):
    """A PackageDB with two related tables, FK join, and OSI annotations.

    Layout:
        warehouse.orders     (order_id PK, customer_id FK, order_date partition)
        warehouse.customers  (id PK, name)
        join: orders.customer_id -> customers.id (fk, many_to_one)
    """
    db = PackageDB(tmp_path / "small.db")

    # Create both tables first (customers must exist before the FK
    # annotation on orders.customer_id resolves under §1 rule 7).
    orders_id = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="hash1",
        table_type="MANAGED_TABLE",
    )
    cust_id = db.upsert_table(
        source_key="warehouse",
        name="customers",
        schema_hash="hash2",
        table_type="MANAGED_TABLE",
    )

    db.upsert_columns(
        orders_id,
        [
            {"name": "order_id", "type": "BIGINT", "comment": "PK", "is_partition": 0},
            {
                "name": "customer_id",
                "type": "BIGINT",
                "comment": "FK to customers",
                "is_partition": 0,
            },
            {"name": "order_date", "type": "DATE", "comment": "When placed", "is_partition": 1},
        ],
    )
    db.upsert_columns(
        cust_id,
        [
            {"name": "id", "type": "BIGINT", "comment": "PK", "is_partition": 0},
            {"name": "name", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )

    # Table-level OSI ai_context — orders only; customers stays None to
    # exercise the "table without ai_context" export path.
    db.set_table_ai_context("warehouse", "orders", "Order line items, one row per ordered SKU.")

    # Column semantics — customers.id PK must land before the FK on
    # orders.customer_id (§1 rule 7: references_target table must exist
    # in the same source_key, and the PK column must be resolvable).
    db.set_column_semantics(
        "warehouse",
        "customers",
        "id",
        role="identifier",
        id_type="primary",
    )
    db.set_column_semantics(
        "warehouse",
        "orders",
        "order_date",
        role="dimension",
        dim_type="time",
        semantic_description="Date the order was placed.",
    )
    db.set_column_semantics(
        "warehouse",
        "orders",
        "customer_id",
        role="identifier",
        id_type="foreign",
        references_target="customers.id",
    )

    db.upsert_join(
        left_source_key="warehouse",
        left_table="orders",
        left_col="customer_id",
        right_source_key="warehouse",
        right_table="customers",
        right_col="id",
        kind="fk",
        confidence=1.0,
        cardinality="many_to_one",
    )

    try:
        yield db
    finally:
        db.close()
