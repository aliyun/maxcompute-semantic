"""OSI export tests for multi-source qualified dataset names."""

from __future__ import annotations

from pathlib import Path

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.osi.export import to_osi_dict


def _build_multi_source(tmp_path: Path) -> PackageDB:
    db = PackageDB(tmp_path / "pkg.db")
    wh_orders = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    crm_orders = db.upsert_table(
        source_key="crm",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(
        wh_orders,
        [
            {"name": "id", "type": "BIGINT"},
            {"name": "amount", "type": "BIGINT"},
        ],
    )
    db.upsert_columns(
        crm_orders,
        [
            {"name": "id", "type": "BIGINT"},
            {"name": "customer_id", "type": "BIGINT"},
        ],
    )
    db.upsert_join(
        left_source_key="warehouse",
        left_table="orders",
        left_col="id",
        right_source_key="crm",
        right_table="orders",
        right_col="id",
        kind="inferred",
        confidence=0.9,
        cardinality="1:1",
    )
    return db


def test_osi_export_dataset_names_are_source_qualified(tmp_path: Path) -> None:
    db = _build_multi_source(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    names = {ds["name"] for ds in out["semantic_model"][0]["datasets"]}
    assert names == {"warehouse__orders", "crm__orders"}


def test_osi_export_relationships_reference_qualified_names(tmp_path: Path) -> None:
    db = _build_multi_source(tmp_path)
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    rels = out["semantic_model"][0]["relationships"]
    assert len(rels) == 1
    assert rels[0]["from"] == "warehouse__orders"
    assert rels[0]["to"] == "crm__orders"
    assert rels[0]["from_columns"] == ["id"]
    assert rels[0]["to_columns"] == ["id"]


def test_osi_export_single_source_also_qualified(tmp_path: Path) -> None:
    """Consistency: single-source profiles use the same prefix shape so
    the OSI YAML diff between 1-source and N-source stays structural."""
    db = PackageDB(tmp_path / "pkg.db")
    tid = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(
        tid,
        [{"name": "id", "type": "BIGINT"}],
    )
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    assert out["semantic_model"][0]["datasets"][0]["name"] == "warehouse__orders"


def test_osi_export_physical_source_for_project_schema_source_key(tmp_path: Path) -> None:
    """source_key is internally ``<project>__<schema>``; OSI ``source`` is
    the physical table reference, so it expands back to dotted form."""
    db = PackageDB(tmp_path / "pkg.db")
    tid = db.upsert_table(
        source_key="proj__default",
        name="orders",
        schema_hash="h",
        errors_json=None,
    )
    db.upsert_columns(tid, [{"name": "id", "type": "BIGINT"}])
    try:
        out = to_osi_dict(db, semantic_model_name="demo")
    finally:
        db.close()
    ds = out["semantic_model"][0]["datasets"][0]
    assert ds["name"] == "proj__default__orders"
    assert ds["source"] == "proj.default.orders"
