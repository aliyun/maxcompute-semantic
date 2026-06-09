# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""to_osi_dict output must validate against the vendored OSI schema."""

from maxcompute_semantic.osi import OSI_SCHEMA_VERSION, to_osi_dict

from ._osi_validators import validate_all


def test_small_db_export_validates(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    errors = validate_all(out)
    assert errors == [], "OSI validation failed:\n" + "\n".join(errors)


def test_top_level_version_matches_pin(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    assert out["version"] == OSI_SCHEMA_VERSION


def test_semantic_model_is_a_list(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    assert isinstance(out["semantic_model"], list)
    assert len(out["semantic_model"]) == 1


def test_semantic_model_name_required(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    assert out["semantic_model"][0]["name"] == "demo"


def test_each_field_has_synthesized_ansi_sql_expression(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    for ds in out["semantic_model"][0]["datasets"]:
        for f in ds["fields"]:
            dialects = f["expression"]["dialects"]
            assert dialects[0]["dialect"] == "ANSI_SQL"
            assert dialects[0]["expression"] == f["name"]


def test_dataset_source_is_physical_table_reference(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    sources = {ds["source"] for ds in out["semantic_model"][0]["datasets"]}
    assert sources == {"warehouse.customers", "warehouse.orders"}
