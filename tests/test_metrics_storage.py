# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for v9→v10 migrator + metrics CRUD on PackageDB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from maxcompute_semantic.build.storage import (
    _SCHEMA_VERSION,
    PackageDB,
)
from maxcompute_semantic.errors.build import (
    MetricExistsError,
    MetricNotFoundError,
)


def _make_v9_db(path: Path) -> None:
    """Hand-rolled minimal v9 fixture: tables/columns + one
    semantic_role='metric' row + PRAGMA user_version=9.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE tables (
          id INTEGER PRIMARY KEY,
          source_key TEXT NOT NULL,
          name TEXT NOT NULL,
          schema_hash TEXT NOT NULL,
          last_built_at TEXT NOT NULL,
          errors_json TEXT,
          ai_context TEXT,
          table_type TEXT,
          UNIQUE(source_key, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE columns (
          table_id INTEGER REFERENCES tables(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          comment TEXT,
          is_partition INTEGER DEFAULT 0,
          sample_values_json TEXT,
          is_enum INTEGER DEFAULT 0,
          null_ratio REAL,
          distinct_count INTEGER,
          semantic_role TEXT,
          dim_type TEXT,
          agg TEXT,
          id_type TEXT,
          references_target TEXT,
          semantic_description TEXT,
          row_count INTEGER,
          approx_ndv INTEGER,
          uniqueness_ratio REAL,
          cast_rate REAL,
          profile_scope TEXT,
          profile_method TEXT,
          profile_confidence REAL,
          PRIMARY KEY (table_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE annotation_suggestions (
          id                  INTEGER PRIMARY KEY,
          source_key          TEXT NOT NULL,
          table_name          TEXT NOT NULL,
          column_name         TEXT NOT NULL,
          suggested_role      TEXT NOT NULL,
          suggested_subtype   TEXT DEFAULT NULL,
          confidence          REAL NOT NULL,
          evidence_json       TEXT NOT NULL,
          status              TEXT NOT NULL DEFAULT 'suggested',
          updated_at          TEXT NOT NULL,
          UNIQUE(source_key, table_name, column_name, suggested_role)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_annotation_suggestions_table
        ON annotation_suggestions(source_key, table_name)
        """
    )
    conn.execute(
        "INSERT INTO tables(id, source_key, name, schema_hash, last_built_at) "
        "VALUES (1, 'warehouse', 'orders', 'hash', '2026-05-26T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO columns(table_id, name, type, semantic_role, agg) "
        "VALUES (1, 'amount', 'BIGINT', 'metric', 'SUM')"
    )
    conn.execute(
        "INSERT INTO columns(table_id, name, type, semantic_role) "
        "VALUES (1, 'order_date', 'DATE', 'dimension')"
    )
    conn.execute(
        "INSERT INTO annotation_suggestions("
        "source_key, table_name, column_name, suggested_role, suggested_subtype, "
        "confidence, evidence_json, status, updated_at"
        ") VALUES ("
        "'warehouse', 'orders', 'amount', 'metric', 'SUM', "
        '0.91, \'[{"source":"history_sql","aggregate":"SUM"}]\', '
        "'suggested', '2026-05-26T00:00:00Z'"
        ")"
    )
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()


def test_migrate_v9_to_v10_rewrites_metric_role(tmp_path: Path) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)

    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        rows = conn.execute("SELECT name, semantic_role FROM columns ORDER BY name").fetchall()
        conn.close()
        assert ("amount", "measure") in rows
        assert ("order_date", "dimension") in rows
        # No row should still carry the v9 role.
        assert not any(r[1] == "metric" for r in rows)
    finally:
        db.close()


def test_migrate_v9_to_v10_rewrites_metric_annotation_suggestions(
    tmp_path: Path,
) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)

    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        rows = conn.execute(
            "SELECT column_name, suggested_role, suggested_subtype "
            "FROM annotation_suggestions ORDER BY column_name"
        ).fetchall()
        conn.close()
        assert rows == [("amount", "measure", "SUM")]
    finally:
        db.close()


def test_migrate_v9_to_v10_deduplicates_existing_measure_suggestion(
    tmp_path: Path,
) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)
    conn = sqlite3.connect(str(p))
    conn.execute(
        "INSERT INTO annotation_suggestions("
        "source_key, table_name, column_name, suggested_role, suggested_subtype, "
        "confidence, evidence_json, status, updated_at"
        ") VALUES ("
        "'warehouse', 'orders', 'amount', 'measure', 'SUM', "
        '0.95, \'[{"source":"rerun","aggregate":"SUM"}]\', '
        "'suggested', '2026-05-27T00:00:00Z'"
        ")"
    )
    conn.commit()
    conn.close()

    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        rows = conn.execute(
            "SELECT column_name, suggested_role, confidence "
            "FROM annotation_suggestions ORDER BY confidence DESC"
        ).fetchall()
        conn.close()
        assert rows == [("amount", "measure", 0.95)]
    finally:
        db.close()


def test_migrated_metric_suggestion_promotes_to_applicable_measure_proposal(
    tmp_path: Path,
) -> None:
    from maxcompute_semantic.build.proposals import (
        apply_semantic_proposal,
        create_annotation_promotion_proposals,
        proposal_payload,
    )

    p = tmp_path / "pkg.db"
    _make_v9_db(p)

    db = PackageDB(p)
    try:
        result = create_annotation_promotion_proposals(db, min_confidence=0.9)

        assert result == {"created": 1, "updated": 0, "skipped": 0}
        rows = db.list_semantic_proposals(status="suggested")
        assert len(rows) == 1
        payload = proposal_payload(rows[0])
        assert payload["patch"]["role"] == "measure"
        assert payload["patch"]["agg"] == "SUM"

        applied = apply_semantic_proposal(
            db,
            rows[0]["id"],
            reviewed_by="tester",
        )

        assert applied["applied"] is True
        semantics = db.get_column_semantics("warehouse", "orders", "amount")
        assert semantics is not None
        assert semantics["semantic_role"] == "measure"
        assert semantics["agg"] == "SUM"
    finally:
        db.close()


def test_migrate_v9_to_v10_stamps_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)
    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == _SCHEMA_VERSION
    finally:
        db.close()


def test_migrate_v9_to_v10_creates_metrics_table(tmp_path: Path) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)
    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()}
        conn.close()
        assert cols == {
            "id",
            "name",
            "expression",
            "description",
            "ai_context",
            "created_at",
            "updated_at",
        }
    finally:
        db.close()


def test_migrate_v9_to_v10_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "pkg.db"
    _make_v9_db(p)

    # First open: runs the v9→v10 migrator.
    db = PackageDB(p)
    db.close()

    # Second open: migrator should be a no-op (CREATE TABLE IF NOT EXISTS
    # + idempotent UPDATE), schema version stays at the current version,
    # metrics table remains intact.
    db = PackageDB(p)
    try:
        conn = sqlite3.connect(str(p))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()}
        conn.close()
        assert version == _SCHEMA_VERSION
        assert "name" in cols and "expression" in cols
    finally:
        db.close()


def test_add_metric_happy(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        mid = db.add_metric(
            name="total_revenue",
            expression="SUM(orders.amount)",
            description="All-time gross",
        )
        assert mid > 0
        rows = db.list_metrics()
        assert len(rows) == 1
        assert rows[0]["name"] == "total_revenue"
        assert rows[0]["expression"] == "SUM(orders.amount)"
        assert rows[0]["description"] == "All-time gross"
        assert rows[0]["created_at"] == rows[0]["updated_at"]
    finally:
        db.close()


def test_add_metric_collision_raises(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        db.add_metric(name="m1", expression="SUM(x.y)")
        with pytest.raises(MetricExistsError):
            db.add_metric(name="m1", expression="SUM(a.b)")
    finally:
        db.close()


def test_get_metric_missing_returns_none(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        assert db.get_metric("nope") is None
    finally:
        db.close()


def test_update_metric_partial(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        db.add_metric(name="m1", expression="SUM(x.y)", description="old")
        original = db.get_metric("m1")
        assert original is not None
        # Force a different timestamp by reaching past second-resolution.
        import time

        time.sleep(0.01)
        db.update_metric("m1", expression="SUM(x.z)")
        updated = db.get_metric("m1")
        assert updated is not None
        assert updated["expression"] == "SUM(x.z)"
        assert updated["description"] == "old"  # unchanged
        assert updated["updated_at"] >= original["updated_at"]
    finally:
        db.close()


def test_update_metric_missing_raises(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        with pytest.raises(MetricNotFoundError):
            db.update_metric("nope", expression="SUM(x.y)")
    finally:
        db.close()


def test_update_metric_no_fields_is_noop(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        db.add_metric(name="m1", expression="SUM(x.y)", description="d")
        original = db.get_metric("m1")
        assert original is not None

        # Existing metric, no kwargs → silent no-op, row unchanged.
        result = db.update_metric("m1")
        assert result is None
        after = db.get_metric("m1")
        assert after == original

        # Nonexistent metric, no kwargs → still raises.
        with pytest.raises(MetricNotFoundError):
            db.update_metric("nope")
    finally:
        db.close()


def test_remove_metric_happy(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        db.add_metric(name="m1", expression="SUM(x.y)")
        db.remove_metric("m1")
        assert db.list_metrics() == []
    finally:
        db.close()


def test_remove_metric_missing_raises(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        with pytest.raises(MetricNotFoundError):
            db.remove_metric("nope")
    finally:
        db.close()


def test_list_metrics_sorted_by_name(tmp_path: Path) -> None:
    db = PackageDB(tmp_path / "pkg.db")
    try:
        for n in ("z_metric", "a_metric", "m_metric"):
            db.add_metric(name=n, expression="1")
        names = [r["name"] for r in db.list_metrics()]
        assert names == ["a_metric", "m_metric", "z_metric"]
    finally:
        db.close()


def test_role_alias_fact_resolves_to_measure() -> None:
    from maxcompute_semantic.build.storage import _ROLE_ALIASES

    assert _ROLE_ALIASES["fact"] == "measure"
    assert _ROLE_ALIASES["numeric"] == "measure"
    assert _ROLE_ALIASES["quantitative"] == "measure"


def test_canonical_roles_includes_measure_not_metric() -> None:
    from maxcompute_semantic.build.storage import _CANONICAL_ROLES

    assert "measure" in _CANONICAL_ROLES
    assert "metric" not in _CANONICAL_ROLES


def test_layer_mistake_role_raises_pointer_error() -> None:
    from maxcompute_semantic.build.storage import _resolve_role
    from maxcompute_semantic.errors.annotate import AnnotateValidationError

    with pytest.raises(AnnotateValidationError) as exc:
        _resolve_role("metric")
    assert "no longer a column-level annotation" in str(exc.value)
    assert "mcs metric add" in str(exc.value).lower() or "mcs metric add" in (
        exc.value.remediation or ""
    )
