# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the annotation columns and index in PackageDB."""

import sqlite3
from pathlib import Path

import pytest

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.errors.annotate import (
    AnnotateNotFoundError,
    AnnotateValidationError,
)


def test_fresh_db_has_ai_context_column(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "test_annotation_schema.db")
    cols = _table_columns(db, "tables")
    assert "ai_context" in cols
    db.close()


def test_fresh_db_has_annotation_columns(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "test_annotation_schema_cols.db")
    cols = _table_columns(db, "columns")
    for expected in [
        "semantic_role",
        "dim_type",
        "agg",
        "id_type",
        "references_target",
        "semantic_description",
    ]:
        assert expected in cols
    db.close()


def test_fresh_db_has_idx_columns_role(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "test_annotation_schema_idx.db")
    indexes = _column_indexes(db, "columns")
    assert any("idx_columns_role" in i for i in indexes)
    db.close()


def test_upgrade_v3_db_adds_annotation_columns(tmp_path: Path) -> None:
    """Simulate a v3 DB (no annotation columns), then open with new binary."""
    path = tmp_path / "test_annotation_upgrade.db"
    # Create v3-shape DB without annotation columns
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL,
        name TEXT NOT NULL,
        schema_hash TEXT NOT NULL,
        last_built_at TEXT NOT NULL,
        errors_json TEXT,
        UNIQUE(source_key, name))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS columns (
        table_id INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
        name TEXT NOT NULL, type TEXT NOT NULL, comment TEXT,
        is_partition INTEGER NOT NULL DEFAULT 0,
        is_enum INTEGER NOT NULL DEFAULT 0,
        null_ratio REAL, distinct_count INTEGER,
        sample_values_json TEXT,
        PRIMARY KEY (table_id, name))"""
    )
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    # Open with new PackageDB — should add columns via ALTER
    db = PackageDB(path)
    cols = _table_columns(db, "tables")
    assert "ai_context" in cols
    cols = _table_columns(db, "columns")
    assert "semantic_role" in cols
    db.close()


def _table_columns(db: PackageDB, table_name: str) -> list[str]:
    rows = db._conn.execute(f"SELECT name FROM pragma_table_info('{table_name}')").fetchall()
    return [r[0] for r in rows]


def _column_indexes(db: PackageDB, table_name: str) -> list[str]:
    rows = db._conn.execute(f"SELECT name FROM pragma_index_list('{table_name}')").fetchall()
    return [r[0] for r in rows]


# --- Round-trip tests for set/get annotation methods ---


def test_set_get_table_ai_context_roundtrip(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    db.set_table_ai_context(
        "test_project__default", "orders", "Each row is one customer order event."
    )
    result = db.get_table_ai_context("test_project__default", "orders")
    assert result == "Each row is one customer order event."
    db.close()


def test_set_table_ai_context_empty_string_normalizes_to_none(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    db.set_table_ai_context("test_project__default", "orders", "")
    result = db.get_table_ai_context("test_project__default", "orders")
    assert result is None
    db.close()


def test_set_table_ai_context_none_clears(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    db.set_table_ai_context("test_project__default", "orders", "hello")
    db.set_table_ai_context("test_project__default", "orders", None)
    assert db.get_table_ai_context("test_project__default", "orders") is None
    db.close()


def test_set_get_column_semantics_dimension(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "order_status")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "order_status",
        role="dimension",
        dim_type="categorical",
    )
    result = db.get_column_semantics("test_project__default", "orders", "order_status")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    assert result["agg"] is None
    db.close()


def test_set_column_semantics_measure(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "amount")
    db.set_column_semantics("test_project__default", "orders", "amount", role="measure", agg="SUM")
    result = db.get_column_semantics("test_project__default", "orders", "amount")
    assert result["semantic_role"] == "measure"
    assert result["agg"] == "SUM"
    db.close()


def test_set_column_semantics_identifier_foreign(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "customer_id")
    # Also add the target table so references_target validation passes
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "customer_id",
        role="identifier",
        id_type="foreign",
        references_target="customers.id",
    )
    result = db.get_column_semantics("test_project__default", "orders", "customer_id")
    assert result["id_type"] == "foreign"
    assert result["references_target"] == "customers.id"
    db.close()


def test_set_column_semantics_idempotent(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    # Second call with same values is idempotent
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    result = db.get_column_semantics("test_project__default", "orders", "status")
    assert result["semantic_role"] == "dimension"
    db.close()


# --- Helpers ---


def _db_with_table(tmp_path: Path, table_name: str) -> PackageDB:
    db = PackageDB(tmp_path / "test.db")
    _insert_table(db, table_name)
    return db


def _db_with_table_and_column(tmp_path: Path, table_name: str, col_name: str) -> PackageDB:
    db = PackageDB(tmp_path / "test.db")
    tid = _insert_table(db, table_name)
    db._conn.execute(
        "INSERT INTO columns (table_id, name, type, comment, is_partition, is_enum) "
        "VALUES (?, ?, 'STRING', '', 0, 0)",
        (tid, col_name),
    )
    db._conn.commit()
    return db


def _insert_table(db: PackageDB, name: str) -> int:
    return db.upsert_table("test_project__default", name, "hash123")


def _insert_column(db: PackageDB, table_name: str, col_name: str) -> None:
    tid = db._conn.execute("SELECT id FROM tables WHERE name=?", (table_name,)).fetchone()[0]
    db._conn.execute(
        "INSERT INTO columns (table_id, name, type, comment, is_partition, is_enum) "
        "VALUES (?, ?, 'STRING', '', 0, 0)",
        (tid, col_name),
    )
    db._conn.commit()


# --- annotation_coverage and table_exists tests (§4 Task 4) ---


def test_annotation_coverage_empty_state(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    _insert_column(db, "orders", "col_a")
    coverage = db.annotation_coverage()
    assert coverage["tables_total"] == 1
    assert coverage["tables_with_ai_context"] == 0
    assert coverage["tables_with_any_column_role"] == 0
    assert coverage["columns_total"] == 1
    assert coverage["columns_with_role"] == 0
    db.close()


def test_annotation_coverage_partial_state(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_table_ai_context("test_project__default", "orders", "order events")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    coverage = db.annotation_coverage()
    assert coverage["tables_with_ai_context"] == 1
    assert coverage["columns_with_role"] == 1
    db.close()


def test_annotation_coverage_per_table_tristate(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_table_ai_context("test_project__default", "orders", "order events")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    coverage = db.annotation_coverage(per_table=True)
    pt = coverage["per_table"]["test_project__default"]["orders"]
    assert pt["has_ai_context"] is True
    assert pt["columns_annotated"] == 1
    db.close()


def test_annotation_coverage_per_table_tristate_no(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    _insert_column(db, "orders", "col_a")
    coverage = db.annotation_coverage(per_table=True)
    pt = coverage["per_table"]["test_project__default"]["orders"]
    assert pt["has_ai_context"] is False
    assert pt["tristate"] == "no"
    db.close()


def test_annotation_coverage_per_table_tristate_yes(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_table_ai_context("test_project__default", "orders", "order events")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
    )
    coverage = db.annotation_coverage(per_table=True)
    pt = coverage["per_table"]["test_project__default"]["orders"]
    assert pt["tristate"] == "yes"
    db.close()


def test_annotation_coverage_per_table_tristate_partial(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    _insert_column(db, "orders", "col_a")
    _insert_column(db, "orders", "col_b")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="dimension",
        dim_type="categorical",
    )
    coverage = db.annotation_coverage(per_table=True)
    pt = coverage["per_table"]["test_project__default"]["orders"]
    assert pt["tristate"] == "partial(1/2)"
    db.close()


def test_table_exists_returns_true(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    assert db.table_exists("test_project__default", "orders") is True
    db.close()


def test_table_exists_returns_false(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    assert db.table_exists("test_project__default", "no_such_table") is False
    db.close()


# --- §1 rule validation matrix (rules 1-9, 18+ parametrized cases) ---


@pytest.mark.parametrize(
    "role,expected_pass",
    [
        ("dimension", True),
        ("measure", True),
        ("identifier", True),
        ("attribute", True),
        ("invalid_role", False),
    ],
)
def test_rule_1_role_validation(tmp_path, role, expected_pass):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    if expected_pass:
        extra = {}
        if role == "dimension":
            extra = {"dim_type": "categorical"}
        elif role == "measure":
            extra = {"agg": "SUM"}
        elif role == "identifier":
            extra = {"id_type": "primary"}
        db.set_column_semantics("test_project__default", "orders", "col_a", role=role, **extra)
    else:
        with pytest.raises(AnnotateValidationError) as exc_info:
            db.set_column_semantics("test_project__default", "orders", "col_a", role=role)
        assert exc_info.value.code_subkey == "rule-1"
    db.close()


def test_rule_1_role_none_clears_annotation(tmp_path):
    """role=None is valid — it clears the annotation."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="dimension",
        dim_type="categorical",
    )
    db.set_column_semantics("test_project__default", "orders", "col_a", role=None)
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] is None
    db.close()


def test_rule_2_dimension_requires_dim_type(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics("test_project__default", "orders", "col_a", role="dimension")
    assert exc_info.value.code_subkey == "rule-2"
    db.close()


def test_rule_2_dim_type_only_with_dimension(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="measure",
            agg="SUM",
            dim_type="categorical",
        )
    assert exc_info.value.code_subkey == "rule-2"
    db.close()


def test_rule_2_invalid_dim_type(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="dimension",
            dim_type="invalid",
        )
    assert exc_info.value.code_subkey == "rule-2"
    db.close()


def test_rule_2_dimension_with_valid_dim_type(tmp_path):
    """Pass: dimension + categorical is valid."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="dimension",
        dim_type="categorical",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    db.close()


def test_rule_3_measure_without_agg_demoted_to_attribute(tmp_path):
    """Soft-drop: ``role: measure`` without ``agg`` demotes to attribute.

    Mirrors the rule-5 soft-drop of ``id_type=foreign`` without a
    references target. The column's description/semantic_description
    still land; only the measure flag is lost.
    """
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="measure")
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "attribute"
    assert result["agg"] is None
    db.close()


def test_rule_3_invalid_agg(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="measure",
            agg="INVALID",
        )
    assert exc_info.value.code_subkey == "rule-3"
    db.close()


def test_rule_3_measure_with_valid_agg(tmp_path):
    """Pass: measure + SUM is valid."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="measure", agg="SUM")
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "measure"
    assert result["agg"] == "SUM"
    db.close()


@pytest.mark.parametrize(
    "alias,canonical_agg",
    [
        ("average", "AVG"),
        ("AVERAGE", "AVG"),
        ("mean", "AVG"),
        ("Mean", "AVG"),
        ("avg", "AVG"),
        ("total", "SUM"),
        ("TOTAL", "SUM"),
        ("sum_total", "SUM"),
        ("cnt", "COUNT"),
        ("n", "COUNT"),
        ("row_count", "COUNT"),
        ("total_count", "COUNT"),
        ("num", "COUNT"),
        ("minimum", "MIN"),
        ("min_value", "MIN"),
        ("maximum", "MAX"),
        ("max_value", "MAX"),
        ("count_distinct", "COUNT_DISTINCT"),
        ("distinct_count", "COUNT_DISTINCT"),
        ("unique_count", "COUNT_DISTINCT"),
        ("nunique", "COUNT_DISTINCT"),
        ("distinct", "COUNT_DISTINCT"),
    ],
)
def test_agg_alias_normalizes_to_canonical(tmp_path, alias, canonical_agg):
    """Natural-English aggregator names map to the canonical SQL
    verb before rule-3 validates. Covers the case where the agent
    writes ``subtype: average`` (which annotate.py routes to
    ``agg=AVERAGE``) — rule-3 would reject that as ``AVERAGE not in
    VALID_AGGS`` without this alias layer."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="measure", agg=alias)
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "measure"
    assert result["agg"] == canonical_agg
    db.close()


def test_agg_alias_unknown_still_fails_rule_3(tmp_path):
    """Truly garbage agg values (not in the alias map, not canonical)
    still raise rule-3 — the alias map is a forgiveness layer, not
    a silent acceptance of arbitrary tokens."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default", "orders", "col_a", role="measure", agg="BOGUS"
        )
    assert exc_info.value.code_subkey == "rule-3"
    db.close()


def test_rule_3_fact_alias_without_agg_demoted_to_attribute(tmp_path):
    """The Kimball ``role: fact`` alias also goes through the
    ``measure``-without-agg soft-demote — the column lands as
    ``attribute`` rather than failing the whole batch."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="fact")
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "attribute"
    assert result["agg"] is None
    db.close()


def test_rule_4_identifier_without_id_type_landed_as_unspecified(tmp_path):
    """``role=identifier`` without ``id_type`` no longer hard-fails — it
    lands with ``id_type=None`` so the identifier signal still reaches
    SQL gen. Mirrors the rule-5 soft-drop of ``id_type=foreign``
    without references, and unblocks the ``entity_id`` alias path
    where the agent's intent ("this is the entity's key") doesn't
    disambiguate primary-vs-foreign-vs-unique.
    """
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="identifier")
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] is None
    db.close()


def test_rule_4_id_type_only_with_identifier(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="dimension",
            dim_type="categorical",
            id_type="primary",
        )
    assert exc_info.value.code_subkey == "rule-4"
    db.close()


def test_rule_4_identifier_with_valid_id_type(tmp_path):
    """Pass: identifier + primary is valid."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="identifier",
        id_type="primary",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] == "primary"
    db.close()


def test_rule_5_foreign_without_references_demotes_id_type(tmp_path):
    """``id_type=foreign`` without references no longer hard-fails — it
    demotes to ``id_type=NULL`` so the column still gets the
    identifier role marker. The agent's ``role: foreign_key`` alias
    path lands here when the FK target wasn't repeated in the YAML;
    we keep the partial annotation instead of dropping the whole row.
    """
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="identifier",
        id_type="foreign",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] is None
    assert result["references_target"] is None
    db.close()


def test_rule_5_references_only_with_foreign(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    _insert_table(db, "customers")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="identifier",
            id_type="primary",
            references_target="customers.id",
        )
    assert exc_info.value.code_subkey == "rule-5"
    db.close()


def test_rule_5_foreign_with_references(tmp_path):
    """Pass: foreign + references_target is valid (target table exists)."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="identifier",
        id_type="foreign",
        references_target="customers.id",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["id_type"] == "foreign"
    assert result["references_target"] == "customers.id"
    db.close()


def test_rule_6_references_format(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="identifier",
            id_type="foreign",
            references_target="just_a_name",
        )
    assert exc_info.value.code_subkey == "rule-6"
    db.close()


def test_rule_6_references_valid_format(tmp_path):
    """Pass: TABLE.COLUMN format is valid (target table exists)."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="identifier",
        id_type="foreign",
        references_target="customers.id",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["references_target"] == "customers.id"
    db.close()


def test_rule_7_target_table_must_exist(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "col_a",
            role="identifier",
            id_type="foreign",
            references_target="nonexistent.id",
        )
    assert exc_info.value.code_subkey == "rule-7"
    db.close()


def test_rule_8_empty_string_normalizes_to_none(tmp_path):
    """Rule 8: empty-string values normalize to None (already tested elsewhere, but verify here)."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="dimension",
        dim_type="categorical",
        semantic_description="",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_description"] is None
    db.close()


def test_rule_9_attribute_no_extra_fields(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics("test_project__default", "orders", "col_a", role="attribute")
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "attribute"
    assert result["dim_type"] is None
    assert result["agg"] is None
    assert result["id_type"] is None
    db.close()


# --- AnnotateNotFoundError tests ---


def test_not_found_error_table(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "no_table",
            "col",
            role="dimension",
            dim_type="categorical",
        )
    assert exc_info.value.context.get("scope") == "table"
    db.close()


def test_not_found_error_column(tmp_path):
    db = _db_with_table(tmp_path, "orders")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "no_col",
            role="dimension",
            dim_type="categorical",
        )
    assert exc_info.value.context.get("scope") == "column"
    db.close()


# --- Fuzzy "did you mean" suggestions on AnnotateNotFoundError ---
#
# The remediation should list the closest existing names so a
# typo-prone agent (e.g. ``totl_amount`` for ``total_amount``)
# doesn't burn retry budget guessing the fix. Generic forgiveness
# layer; the suggestion is opportunistic — when nothing close enough
# exists, the remediation falls back to the plain "check spelling"
# form.


def test_not_found_column_remediation_includes_close_match(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "total_amount")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "totl_amount",
            role="dimension",
            dim_type="categorical",
        )
    remediation = exc_info.value.remediation
    assert "did you mean" in remediation
    assert "total_amount" in remediation
    db.close()


def test_not_found_column_no_suggestion_when_nothing_close(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "amount")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "zzzzzzz",
            role="dimension",
            dim_type="categorical",
        )
    remediation = exc_info.value.remediation
    assert "did you mean" not in remediation
    assert "check spelling" in remediation
    db.close()


def test_not_found_column_suggestion_is_case_insensitive(tmp_path):
    db = _db_with_table_and_column(tmp_path, "orders", "order_status")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "orders",
            "ORDER_STATU",
            role="dimension",
            dim_type="categorical",
        )
    remediation = exc_info.value.remediation
    assert "did you mean" in remediation
    assert "order_status" in remediation
    db.close()


def test_not_found_table_remediation_includes_close_match(tmp_path):
    db = _db_with_table(tmp_path, "customers")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "customer",
            "name",
            role="attribute",
        )
    remediation = exc_info.value.remediation
    assert "did you mean" in remediation
    assert "customers" in remediation
    db.close()


def test_not_found_table_no_suggestion_when_source_empty(tmp_path):
    db = PackageDB(tmp_path / "test.db")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "empty_source",
            "no_table",
            "col",
            role="attribute",
        )
    remediation = exc_info.value.remediation
    assert "did you mean" not in remediation
    db.close()


# --- Case-insensitive name matching (MaxCompute identifier semantics) ---
#
# MaxCompute identifiers are case-insensitive; the catalog canonicalizes
# to lowercase. Agents that copy column / table names from external
# schema docs (CSV imports, Hive migrations, training-data priors) often
# pass an upper- or mixed-case form. Storage-layer lookups must match
# regardless of case, otherwise the annotation silently lands nowhere
# (the smoking-gun case was ``bird_european_football_2.match`` losing
# 36/183 column annotations because the agent passed ``BWH`` /
# ``B1H`` / ``PSH`` for columns stored as ``bwh`` / ``b1h`` / ``psh``).


def test_set_column_semantics_accepts_mixed_case_column_name(tmp_path):
    """Agent annotates ``BWH`` but storage holds ``bwh`` (canonical
    MaxCompute case). The write must land on the existing row, and the
    read-back must succeed via either case form.
    """
    db = _db_with_table_and_column(tmp_path, "match", "bwh")
    db.set_column_semantics(
        "test_project__default",
        "match",
        "BWH",
        role="measure",
        agg="AVG",
        semantic_description="bet365 home win odds",
    )
    via_canonical = db.get_column_semantics("test_project__default", "match", "bwh")
    via_passed = db.get_column_semantics("test_project__default", "match", "BWH")
    assert via_canonical is not None
    assert via_canonical["semantic_role"] == "measure"
    assert via_canonical["agg"] == "AVG"
    assert via_canonical["semantic_description"] == "bet365 home win odds"
    assert via_passed == via_canonical
    db.close()


def test_set_column_semantics_accepts_mixed_case_table_name(tmp_path):
    """Same as above but for the table name (e.g. ``Match`` → ``match``)."""
    db = _db_with_table_and_column(tmp_path, "match", "bwh")
    db.set_column_semantics(
        "test_project__default",
        "Match",
        "BWH",
        role="measure",
        agg="AVG",
    )
    result = db.get_column_semantics("test_project__default", "match", "bwh")
    assert result is not None
    assert result["semantic_role"] == "measure"
    db.close()


def test_set_column_semantics_still_raises_for_truly_unknown_column(tmp_path):
    """Case-insensitive lookup must not silently match nothing — a
    genuinely-absent column still raises AnnotateNotFound."""
    db = _db_with_table_and_column(tmp_path, "match", "bwh")
    with pytest.raises(AnnotateNotFoundError) as exc_info:
        db.set_column_semantics(
            "test_project__default",
            "match",
            "no_such_column",
            role="dimension",
            dim_type="categorical",
        )
    assert exc_info.value.context.get("scope") == "column"
    db.close()


def test_find_table_by_name_is_case_insensitive(tmp_path):
    """Bare-name disambiguation in :mod:`commands/_table_resolve` calls
    :meth:`find_table_by_name`; agents passing ``MATCH`` should still
    hit the canonical ``match`` row.
    """
    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("proj__default", "match", "h1")
    rows_upper = db.find_table_by_name("MATCH")
    rows_canonical = db.find_table_by_name("match")
    assert len(rows_upper) == 1
    assert rows_upper[0]["name"] == "match"
    assert rows_upper == rows_canonical
    db.close()


def test_table_exists_is_case_insensitive(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("a__default", "match", "ha")
    assert db.table_exists("a__default", "MATCH") is True
    assert db.table_exists("a__default", "Match") is True
    assert db.table_exists("a__default", "match") is True
    # Different source — still doesn't exist.
    assert db.table_exists("b__default", "MATCH") is False
    db.close()


# --- Task 1: _resolve_table_id helper ---


def test_resolve_table_id_returns_id_for_matching_source_and_name(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    tid = db.upsert_table("proj__default", "orders", "h1")
    assert db._resolve_table_id("proj__default", "orders") == tid


def test_resolve_table_id_distinguishes_same_name_across_sources(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    tid_a = db.upsert_table("proj_a__default", "users", "ha")
    tid_b = db.upsert_table("proj_b__default", "users", "hb")
    assert db._resolve_table_id("proj_a__default", "users") == tid_a
    assert db._resolve_table_id("proj_b__default", "users") == tid_b
    assert tid_a != tid_b


def test_resolve_table_id_raises_annotate_not_found_when_missing(tmp_path):
    from maxcompute_semantic.errors.annotate import AnnotateNotFoundError

    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("proj__default", "orders", "h1")
    with pytest.raises(AnnotateNotFoundError) as ei:
        db._resolve_table_id("proj__default", "no_such")
    assert ei.value.scope == "table"
    with pytest.raises(AnnotateNotFoundError) as ei2:
        db._resolve_table_id("no_such_source", "orders")
    assert ei2.value.scope == "table"


# --- Task 2: source_key-aware ai_context + table_exists ---


def test_set_table_ai_context_isolates_same_name_across_sources(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("a__default", "users", "ha")
    db.upsert_table("b__default", "users", "hb")
    db.set_table_ai_context("a__default", "users", "context A")
    db.set_table_ai_context("b__default", "users", "context B")
    assert db.get_table_ai_context("a__default", "users") == "context A"
    assert db.get_table_ai_context("b__default", "users") == "context B"


def test_table_exists_is_source_scoped(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("a__default", "users", "ha")
    assert db.table_exists("a__default", "users") is True
    assert db.table_exists("b__default", "users") is False


# --- Task 3: source_key-aware column semantics ---


def test_set_column_semantics_isolates_same_name_across_sources(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    tid_a = db.upsert_table("a__default", "users", "ha")
    tid_b = db.upsert_table("b__default", "users", "hb")
    db.upsert_columns(tid_a, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.upsert_columns(tid_b, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.set_column_semantics("a__default", "users", "id", role="identifier", id_type="primary")
    db.set_column_semantics("b__default", "users", "id", role="attribute")
    a = db.get_column_semantics("a__default", "users", "id")
    b = db.get_column_semantics("b__default", "users", "id")
    assert a["semantic_role"] == "identifier"
    assert a["id_type"] == "primary"
    assert b["semantic_role"] == "attribute"
    assert b["id_type"] is None


def test_set_column_semantics_rule7_target_must_exist_in_same_source(tmp_path):
    """References target lookup is source-scoped — if proj_a has a
    ``users`` table but proj_b does not, ``id_type=foreign`` from a
    proj_b table referencing ``users.id`` must error rule-7.
    """
    from maxcompute_semantic.errors.annotate import AnnotateValidationError

    db = PackageDB(tmp_path / "package.db")
    tid_a = db.upsert_table("a__default", "users", "ha")
    tid_b = db.upsert_table("b__default", "orders", "hb")
    db.upsert_columns(tid_a, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.upsert_columns(
        tid_b,
        [{"name": "user_id", "type": "BIGINT", "comment": "", "is_partition": 0}],
    )
    # Within source a, users exists: fine.
    db.set_column_semantics("a__default", "users", "id", role="identifier", id_type="primary")
    # From source b, referencing users (which only exists in a) errors.
    with pytest.raises(AnnotateValidationError) as ei:
        db.set_column_semantics(
            "b__default",
            "orders",
            "user_id",
            role="identifier",
            id_type="foreign",
            references_target="users.id",
        )
    assert ei.value.context.get("code_subkey") == "rule-7"


# --- Task 4: nested per_table coverage ---


def test_annotation_coverage_per_table_nested_by_source(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    tid_a = db.upsert_table("a__default", "users", "ha")
    tid_b = db.upsert_table("b__default", "users", "hb")
    db.upsert_columns(tid_a, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.upsert_columns(tid_b, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.set_table_ai_context("a__default", "users", "context A")
    db.set_column_semantics("a__default", "users", "id", role="identifier", id_type="primary")
    # b__default.users left fully unannotated
    coverage = db.annotation_coverage(per_table=True)
    per_table = coverage["per_table"]
    assert set(per_table.keys()) == {"a__default", "b__default"}
    assert per_table["a__default"]["users"]["tristate"] == "yes"
    assert per_table["b__default"]["users"]["tristate"] == "no"
    # Top-level counters cover both
    assert coverage["tables_total"] == 2
    assert coverage["columns_total"] == 2
    assert coverage["columns_with_role"] == 1


def test_annotation_coverage_per_table_single_source_still_nested(tmp_path):
    """Even single-source profiles get the nested shape — consumers must
    not branch on the source-count to decide whether to index by name
    or by (source_key, name).
    """
    db = PackageDB(tmp_path / "package.db")
    db.upsert_table("proj__default", "orders", "h1")
    coverage = db.annotation_coverage(per_table=True)
    assert list(coverage["per_table"].keys()) == ["proj__default"]
    assert "orders" in coverage["per_table"]["proj__default"]


# --- Role / id_type shorthand-alias normalization ---


@pytest.mark.parametrize(
    "alias,canonical_role,canonical_id_type",
    [
        ("pk", "identifier", "primary"),
        ("PK", "identifier", "primary"),
        ("primary_key", "identifier", "primary"),
        ("fk", "identifier", "foreign"),
        ("foreign_key", "identifier", "foreign"),
        ("unique_key", "identifier", "unique"),
        ("id", "identifier", None),
    ],
)
def test_role_alias_identifier(tmp_path, alias, canonical_role, canonical_id_type):
    """Identifier shorthand resolves to ``role=identifier``; ``pk`` /
    ``fk`` / ``unique_key`` / ``primary_key`` / ``foreign_key`` also
    auto-fill ``id_type`` so the agent's first attempt sticks."""
    db = _db_with_table_and_column(tmp_path, "orders", "customer_id")
    _insert_table(db, "customers")
    kwargs: dict = {"role": alias}
    if alias.lower() == "id":
        kwargs["id_type"] = "primary"
    if canonical_id_type == "foreign":
        kwargs["references_target"] = "customers.id"
    db.set_column_semantics("test_project__default", "orders", "customer_id", **kwargs)
    result = db.get_column_semantics("test_project__default", "orders", "customer_id")
    assert result["semantic_role"] == canonical_role
    if canonical_id_type is not None:
        assert result["id_type"] == canonical_id_type
    db.close()


@pytest.mark.parametrize(
    "alias,canonical_role",
    [
        ("dim", "dimension"),
        ("DIM", "dimension"),
        ("measure", "measure"),
        ("MEASURE", "measure"),
        ("fact", "measure"),
        ("attr", "attribute"),
        # General data-modeling vocabulary the agent reaches for
        # before the OSI canonical names.
        ("numeric", "measure"),
        ("numeric_measurable", "measure"),
        ("measurable", "measure"),
        ("quantitative", "measure"),
        ("free_text", "attribute"),
        ("text", "attribute"),
        ("string", "attribute"),
        # Kimball star-schema vocabulary.
        ("context", "attribute"),
        # Column-name-as-role shorthand the agent uses when a column
        # literally has that name (``role: name`` / ``role: url`` /
        # ``role: description`` / ``role: location``) — all payload
        # strings with no analytic role.
        ("name", "attribute"),
        ("url", "attribute"),
        ("description", "attribute"),
        ("location", "attribute"),
        ("const", "attribute"),
        ("constant", "attribute"),
        ("value", "attribute"),
        # ``numerical`` is a sibling of the existing ``numeric``
        # alias.
        ("numerical", "measure"),
        # ML / data-science vocabulary the agent reaches for when
        # the table looks like a training dataset. All payload
        # columns (the model's input or output values), none of
        # which carry a dimension/measure/identifier role —
        # ``attribute`` is the right canonical landing slot.
        ("target", "attribute"),
        ("label", "attribute"),
        ("outcome", "attribute"),
        ("response", "attribute"),
        ("feature", "attribute"),
        ("predictor", "attribute"),
        ("dependent", "attribute"),
        ("independent", "attribute"),
        ("TARGET", "attribute"),
        ("Label", "attribute"),
    ],
)
def test_role_alias_non_identifier(tmp_path, alias, canonical_role):
    """``dim`` / ``measure`` / ``fact`` / ``attr`` plus
    ``numeric`` / ``measurable`` / ``free_text`` / ``text`` /
    ``string`` resolve to their canonical role names. Sub-flags
    still required by the rule-2/3 validators (alias normalization
    doesn't synthesize unrelated metadata)."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    kwargs: dict = {"role": alias}
    if canonical_role == "dimension":
        kwargs["dim_type"] = "categorical"
    elif canonical_role == "measure":
        kwargs["agg"] = "SUM"
    db.set_column_semantics("test_project__default", "orders", "col_a", **kwargs)
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == canonical_role
    db.close()


def test_role_alias_categorical_auto_fills_dim_type(tmp_path):
    """``role: categorical`` (an agent's natural SQL vocabulary)
    resolves to ``role=dimension`` AND auto-fills
    ``dim_type=categorical``, so rule-2 passes without the caller
    having to specify dim_type explicitly."""
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_column_semantics("test_project__default", "orders", "status", role="categorical")
    result = db.get_column_semantics("test_project__default", "orders", "status")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    db.close()


def test_role_alias_category_auto_fills_dim_type(tmp_path):
    """``role: category`` (singular/short of ``categorical``) is
    the form agents reach for most often (24 obs in 0.9.2 smoke vs
    38 for ``categorical``). Should resolve identically: dimension
    + auto-fill dim_type=categorical."""
    db = _db_with_table_and_column(tmp_path, "orders", "segment")
    db.set_column_semantics("test_project__default", "orders", "segment", role="category")
    result = db.get_column_semantics("test_project__default", "orders", "segment")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    db.close()


def test_role_alias_status_auto_fills_dim_type(tmp_path):
    """``role: status`` resolves to dimension + auto-fill
    dim_type=categorical. Status columns are universally
    categorical filters/groupers (order status, account status,
    transaction status) regardless of project."""
    db = _db_with_table_and_column(tmp_path, "orders", "order_status")
    db.set_column_semantics("test_project__default", "orders", "order_status", role="status")
    result = db.get_column_semantics("test_project__default", "orders", "order_status")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    db.close()


@pytest.mark.parametrize(
    "alias",
    [
        "code",
        "type",
        "enum",
        "flag",
        "boolean",
        "bool",
        "CODE",
        "Type",
    ],
)
def test_role_alias_code_family_auto_fills_dim_type(tmp_path, alias):
    """``role: code`` / ``role: type`` / ``role: enum`` / ``role: flag``
    / ``role: boolean`` (and the case variants) resolve to
    ``role=dimension`` AND auto-fill ``dim_type=categorical``. These
    are universal data-modeling shorthand for "short categorical
    identifier" columns — country code, currency code, type code,
    enum-style typeid, boolean flag — regardless of project. The
    agent reaches for these instead of the canonical
    ``dimension`` + explicit ``dim_type: categorical`` pair, and the
    alias forgiveness layer lets the first attempt stick instead of
    forcing a retry that often drops the ``description`` field."""
    db = _db_with_table_and_column(tmp_path, "orders", "type_id")
    db.set_column_semantics("test_project__default", "orders", "type_id", role=alias)
    result = db.get_column_semantics("test_project__default", "orders", "type_id")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    db.close()


def test_role_alias_entity_id_lands_as_identifier_without_id_type(tmp_path):
    """``role: entity_id`` resolves to ``role=identifier`` with no
    id_type auto-fill (rule-4 soft-drop absorbs the missing slot).
    An ``entity_id`` column could be either a local PK or a
    polymorphic FK depending on the schema, so we don't guess —
    leaving id_type=None preserves the join_candidates layer's
    independence to infer it from data co-occurrence."""
    db = _db_with_table_and_column(tmp_path, "orders", "account_id")
    db.set_column_semantics("test_project__default", "orders", "account_id", role="entity_id")
    result = db.get_column_semantics("test_project__default", "orders", "account_id")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] is None
    db.close()


def test_rule_3_agg_on_identifier_is_soft_dropped(tmp_path):
    """``role: identifier, agg: COUNT`` (agent annotating "we count
    rows by this id") drops ``agg`` instead of raising — the
    identifier role + id_type metadata still lands. Mirrors the
    rule-5 soft-drop precedent for ``id_type=foreign without
    references``."""
    db = _db_with_table_and_column(tmp_path, "orders", "order_id")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "order_id",
        role="identifier",
        id_type="primary",
        agg="COUNT",
    )
    result = db.get_column_semantics("test_project__default", "orders", "order_id")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] == "primary"
    assert result["agg"] is None
    db.close()


def test_rule_3_agg_on_attribute_is_soft_dropped(tmp_path):
    """``role: attribute, agg: COUNT_DISTINCT`` (agent annotating
    "we count distinct values of this string field") drops ``agg``
    instead of raising."""
    db = _db_with_table_and_column(tmp_path, "orders", "notes")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "notes",
        role="attribute",
        agg="COUNT_DISTINCT",
    )
    result = db.get_column_semantics("test_project__default", "orders", "notes")
    assert result["semantic_role"] == "attribute"
    assert result["agg"] is None
    db.close()


def test_rule_3_agg_on_dimension_is_soft_dropped(tmp_path):
    """``role: dimension, dim_type: categorical, agg: COUNT`` drops
    ``agg`` instead of raising; dimension annotation still lands."""
    db = _db_with_table_and_column(tmp_path, "orders", "status")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "status",
        role="dimension",
        dim_type="categorical",
        agg="COUNT",
    )
    result = db.get_column_semantics("test_project__default", "orders", "status")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "categorical"
    assert result["agg"] is None
    db.close()


def test_role_alias_explicit_id_type_wins(tmp_path):
    """An explicit ``id_type`` overrides the implicit one from a role
    alias. ``role=pk`` implies primary, but if the caller also passes
    ``id_type=unique`` we honour their explicit value."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="pk",
        id_type="unique",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] == "unique"
    db.close()


def test_role_alias_canonical_lowercased(tmp_path):
    """Uppercase canonical role names (``Dimension``, ``METRIC``) are
    lowercased before validation."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="DIMENSION",
        dim_type="categorical",
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["semantic_role"] == "dimension"
    db.close()


def test_role_alias_unknown_still_fails_rule_1(tmp_path):
    """Aliases shouldn't silently swallow garbage — values not in the
    alias dict still go through rule-1 validation and fail."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    with pytest.raises(AnnotateValidationError) as exc_info:
        db.set_column_semantics("test_project__default", "orders", "col_a", role="bogus_role")
    assert exc_info.value.code_subkey == "rule-1"
    db.close()


def test_id_type_alias_pk_fk(tmp_path):
    """``id_type=pk`` / ``id_type=fk`` resolve to the canonical
    ``primary`` / ``foreign`` even when ``role`` is the canonical
    ``identifier`` form."""
    db = _db_with_table_and_column(tmp_path, "orders", "customer_id")
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "customer_id",
        role="identifier",
        id_type="fk",
        references_target="customers.id",
    )
    result = db.get_column_semantics("test_project__default", "orders", "customer_id")
    assert result["id_type"] == "foreign"
    db.close()


def test_role_alias_descriptive(tmp_path):
    """``role: descriptive`` is a common synonym for ``attribute``
    (free-form descriptive metadata, not used for filtering or
    aggregation)."""
    db = _db_with_table_and_column(tmp_path, "orders", "notes")
    db.set_column_semantics("test_project__default", "orders", "notes", role="descriptive")
    result = db.get_column_semantics("test_project__default", "orders", "notes")
    assert result["semantic_role"] == "attribute"
    db.close()


def test_role_alias_reference_implies_foreign(tmp_path):
    """``role: reference`` maps to ``identifier`` and auto-fills
    ``id_type=foreign`` (a reference is by definition a pointer to
    another table). Caller still must provide ``references_target``."""
    db = _db_with_table_and_column(tmp_path, "orders", "customer_id")
    _insert_table(db, "customers")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "customer_id",
        role="reference",
        references_target="customers.id",
    )
    result = db.get_column_semantics("test_project__default", "orders", "customer_id")
    assert result["semantic_role"] == "identifier"
    assert result["id_type"] == "foreign"
    db.close()


@pytest.mark.parametrize("alias", ["date", "time", "timestamp", "datetime", "temporal"])
def test_role_alias_temporal_implies_dim_time(tmp_path, alias):
    """Temporal-shorthand role values map to ``dimension`` and
    auto-fill ``dim_type=time``. Catches the common confusion where
    the agent puts the column TYPE in the role slot."""
    db = _db_with_table_and_column(tmp_path, "orders", "created_at")
    db.set_column_semantics("test_project__default", "orders", "created_at", role=alias)
    result = db.get_column_semantics("test_project__default", "orders", "created_at")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "time"
    db.close()


def test_role_alias_temporal_explicit_dim_type_wins(tmp_path):
    """An explicit ``dim_type`` overrides the implicit one from a
    temporal role alias."""
    db = _db_with_table_and_column(tmp_path, "orders", "fiscal_year")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "fiscal_year",
        role="date",
        dim_type="ordinal",
    )
    result = db.get_column_semantics("test_project__default", "orders", "fiscal_year")
    assert result["semantic_role"] == "dimension"
    assert result["dim_type"] == "ordinal"
    db.close()


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("cat", "categorical"),
        ("category", "categorical"),
        ("date", "time"),
        ("datetime", "time"),
        ("timestamp", "time"),
    ],
)
def test_dim_type_alias_normalization(tmp_path, alias, canonical):
    """``dim_type`` shorthand (e.g. ``cat`` for ``categorical``,
    ``date`` for ``time``) normalizes to the canonical value."""
    db = _db_with_table_and_column(tmp_path, "orders", "col_a")
    db.set_column_semantics(
        "test_project__default",
        "orders",
        "col_a",
        role="dimension",
        dim_type=alias,
    )
    result = db.get_column_semantics("test_project__default", "orders", "col_a")
    assert result["dim_type"] == canonical
    db.close()
