# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs joins -> OSI relationships translation."""

from maxcompute_semantic.osi import to_osi_dict


def _relationships(out):
    return out["semantic_model"][0].get("relationships", [])


def test_join_produces_relationship(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    rels = _relationships(out)
    assert len(rels) == 1


def test_relationship_has_required_fields(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    rel = _relationships(out)[0]
    for required in ("name", "from", "to", "from_columns", "to_columns"):
        assert required in rel, f"missing {required}: {rel}"


def test_relationship_from_to_are_dataset_names(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    rel = _relationships(out)[0]
    dataset_names = {ds["name"] for ds in out["semantic_model"][0]["datasets"]}
    assert rel["from"] in dataset_names
    assert rel["to"] in dataset_names


def test_relationship_columns_are_lists(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    rel = _relationships(out)[0]
    assert rel["from_columns"] == ["customer_id"]
    assert rel["to_columns"] == ["id"]


def test_relationship_name_is_deterministic(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    rel = _relationships(out)[0]
    # Convention: <from_dataset>__<from_col>__to__<to_dataset>__<to_col>
    assert rel["name"] == "warehouse__orders__customer_id__to__warehouse__customers__id"


def test_no_joins_means_no_relationships_key(tmp_path):
    from maxcompute_semantic.build.storage import PackageDB

    db = PackageDB(tmp_path / "no_joins.db")
    try:
        tid = db.upsert_table(
            source_key="src",
            name="lonely",
            schema_hash="h",
            table_type="MANAGED_TABLE",
        )
        db.upsert_columns(tid, [{"name": "x", "type": "BIGINT", "comment": "", "is_partition": 0}])
        out = to_osi_dict(db, semantic_model_name="lonely-model")
        assert "relationships" not in out["semantic_model"][0]
    finally:
        db.close()
