"""Tests for build/schema_hash.py — SHA-256 column hash for refresh diff."""

from __future__ import annotations

from maxcompute_semantic.build.schema_hash import schema_hash


def test_schema_hash_deterministic() -> None:
    cols = [{"name": "id", "type": "STRING"}, {"name": "val", "type": "INT"}]
    assert schema_hash(cols) == schema_hash(cols)


def test_schema_hash_order_independent() -> None:
    cols_a = [{"name": "id", "type": "STRING"}, {"name": "val", "type": "INT"}]
    cols_b = [{"name": "val", "type": "INT"}, {"name": "id", "type": "STRING"}]
    assert schema_hash(cols_a) == schema_hash(cols_b)


def test_schema_hash_case_normalized() -> None:
    cols_a = [{"name": "id", "type": "string"}]
    cols_b = [{"name": "id", "type": "STRING"}]
    assert schema_hash(cols_a) == schema_hash(cols_b)


def test_schema_hash_changes_with_type() -> None:
    cols_a = [{"name": "id", "type": "STRING"}]
    cols_b = [{"name": "id", "type": "INT"}]
    assert schema_hash(cols_a) != schema_hash(cols_b)


def test_schema_hash_ignores_comment() -> None:
    cols_a = [{"name": "id", "type": "STRING", "comment": "old"}]
    cols_b = [{"name": "id", "type": "STRING", "comment": "new"}]
    assert schema_hash(cols_a) == schema_hash(cols_b)


def test_schema_hash_empty_list() -> None:
    assert schema_hash([]) != ""


def test_schema_hash_single_column() -> None:
    assert len(schema_hash([{"name": "x", "type": "BIGINT"}])) == 64
