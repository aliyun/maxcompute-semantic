# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Test the helpers themselves with hand-built good/bad fixtures."""

from ._osi_validators import (
    load_schema,
    validate_all,
    validate_references,
    validate_schema,
    validate_unique_names,
)


def _minimal_valid() -> dict:
    """A minimal OSI document that satisfies the vendored schema.

    ``semantic_model`` is an **array** at the top level (per OSI spec
    + the schema in ``tests/fixtures/osi/osi-schema.json``).
    """
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "demo",
                "datasets": [
                    {
                        "name": "t1",
                        "source": "src",
                        "fields": [
                            {
                                "name": "c1",
                                "expression": {
                                    "dialects": [{"dialect": "ANSI_SQL", "expression": "c1"}]
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_minimal_valid_passes_all_checks():
    assert validate_all(_minimal_valid()) == []


def test_schema_rejects_missing_top_level_required():
    bad = {"semantic_model": [{"name": "x", "datasets": [{"name": "t", "source": "s"}]}]}
    errs = validate_schema(bad, load_schema())
    assert any("version" in e for e in errs)


def test_schema_rejects_empty_datasets():
    bad = _minimal_valid()
    bad["semantic_model"][0]["datasets"] = []
    errs = validate_schema(bad, load_schema())
    assert any("minItems" in e or "datasets" in e for e in errs)


def test_unique_names_flags_duplicate_dataset():
    bad = _minimal_valid()
    bad["semantic_model"][0]["datasets"].append(bad["semantic_model"][0]["datasets"][0])
    errs = validate_unique_names(bad)
    assert any("duplicate dataset" in e for e in errs)


def test_references_flags_dangling_relationship():
    bad = _minimal_valid()
    bad["semantic_model"][0]["relationships"] = [
        {
            "name": "r1",
            "from": "t1",
            "to": "ghost",
            "from_columns": ["c1"],
            "to_columns": ["c1"],
        }
    ]
    errs = validate_references(bad)
    assert any("ghost" in e for e in errs)
