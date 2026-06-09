"""Mapping rules: dim_type, identifiers, descriptions, custom_extensions."""

import json

from maxcompute_semantic.osi import to_osi_dict
from maxcompute_semantic.osi.vocabulary import CUSTOM_EXTENSION_VENDOR


def _datasets(out):
    return {ds["name"]: ds for ds in out["semantic_model"][0]["datasets"]}


def _fields(ds):
    return {f["name"]: f for f in ds.get("fields", [])}


def test_ai_context_copied_to_dataset(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    assert orders["ai_context"].startswith("Order line items")


def test_dataset_source_identifies_physical_table(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    customers = _datasets(out)["warehouse__customers"]
    assert orders["source"] == "warehouse.orders"
    assert customers["source"] == "warehouse.customers"


def test_dataset_without_ai_context_omits_key(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    customers = _datasets(out)["warehouse__customers"]
    # OSI uses additionalProperties: false; omit absent values rather than null.
    assert "ai_context" not in customers


def test_semantic_description_becomes_field_description(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    f = _fields(orders)["order_date"]
    assert f["description"] == "Date the order was placed."


def test_dim_type_time_becomes_is_time_true(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    f = _fields(orders)["order_date"]
    assert f["dimension"] == {"is_time": True}


def test_non_dim_field_has_no_dimension_key(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    f = _fields(orders)["order_id"]
    assert "dimension" not in f


def test_primary_identifier_becomes_primary_key(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    customers = _datasets(out)["warehouse__customers"]
    assert customers["primary_key"] == ["id"]


def test_foreign_identifier_stashed_in_custom_extensions(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    f = _fields(orders)["customer_id"]
    ext = f["custom_extensions"][0]
    assert ext["vendor_name"] == CUSTOM_EXTENSION_VENDOR
    # OSI CustomExtension.data is a JSON string per schema; parse to inspect.
    data = json.loads(ext["data"])
    assert data["id_type"] == "foreign"
    assert data["references_target"] == "customers.id"
    assert data["semantic_role"] == "identifier"


def test_custom_extensions_omitted_when_no_mcs_only_data(small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    orders = _datasets(out)["warehouse__orders"]
    f = _fields(orders)["order_id"]
    # order_id is plain — no semantic_role, no id_type — so no extensions.
    assert "custom_extensions" not in f
