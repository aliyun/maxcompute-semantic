# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Projection/import behavior; SQL is preserved, MEASURE AST and trust are checked."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.knowledge.model import KnowledgeError, content_hash
from maxcompute_semantic.knowledge.semanticspec import (
    import_package,
    import_semanticspec,
    project_semanticspec,
)
from maxcompute_semantic.remote.sync import section_integrity


def graph(*nodes, edges=()):
    return {
        "revision": 7,
        "kb_id": "private_kb",
        "nodes": list(nodes),
        "edges": list(edges),
        "evidence": [
            {
                "id": "proof",
                "source_kind": "user",
                "source_id": "review",
                "source_version": "1",
                "content_hash": content_hash("reviewed business definitions"),
                "excerpt": "reviewed business definitions",
            }
        ],
    }


def node(item_id, kind, name, native=None, *, status="confirmed", **properties):
    if native is not None:
        properties["server_native"] = native
    return {
        "id": item_id,
        "kind": kind,
        "name": name,
        "scope": "team",
        "status": status,
        "evidence_ids": ["proof"],
        "properties": properties,
    }


def snapshot(*, source="PUBLISHED", revision="r1"):
    return {
        "name": "sales",
        "endpoint": "https://gateway.invalid/mcp",
        "account_id": "42",
        "namespace": "42",
        "region": "cn-shanghai",
        "semantic_spec_id": "catalog-spec-42",
        "source": source,
        "revision_id": revision,
        "sections": {
            "dataReferences": [
                {
                    "project": "p",
                    "schema": "default",
                    "table": "orders",
                    "tableId": "remote_id",
                }
            ],
            "semanticModel": {
                "logicalTables": [
                    {
                        "name": "orders",
                        "baseTable": "p.default.orders",
                        "dimensions": [
                            {"name": "channel", "expr": "channel", "dataType": "STRING"}
                        ],
                    }
                ],
                "namedFilters": [],
                "approvedRelationships": [],
            },
            "metricDefinitions": [
                {
                    "name": "gmv",
                    "semanticType": "MEASURE",
                    "baseLogicalTable": "orders",
                    "expr": "amount",
                    "defaultAggregation": "SUM",
                    "dataType": "DOUBLE",
                }
            ],
            "glossary": [
                {
                    "term": "GMV",
                    "definition": "Paid order amount",
                    "alsoKnownAs": ["成交额"],
                    "linkedMetric": "gmv",
                }
            ],
            "verifiedQueries": [
                {
                    "name": "q",
                    "question": "What is GMV?",
                    "sql": "SELECT SUM(amount) FROM p.default.orders",
                    "trustLevel": "STRICT",
                    "trusted": False,
                }
            ],
        },
    }


def test_glossary_mapping_preserves_aliases_and_reports_unbound_metric():
    term = node("term", "term", "成交额", definition="已支付订单金额", aka=["GMV"])
    local_metric = node("local", "metric", "GMV", expression="SUM(amount)")
    report = project_semanticspec(graph(term, local_metric), ["term", "local"])
    assert report["sections"] == {
        "glossary": [
            {"term": "成交额", "definition": "已支付订单金额", "alsoKnownAs": ["GMV"]}
        ]
    }
    assert report["unmapped"][0]["id"] == "local"
    assert report["publishable"] is False
    assert report["source_revision"] == 7


def test_metric_dependency_closure_includes_physical_table_without_sql_rewrite():
    table = node(
        "table", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": "amount",
            "defaultAggregation": "SUM",
        },
    )
    report = project_semanticspec(graph(table, metric), ["metric"])
    assert report["publishable"] is True
    assert report["included_ids"] == ["metric", "table"]
    assert report["sections"]["dataReferences"] == [
        {"project": "p", "schema": "default", "table": "orders"}
    ]
    assert report["sections"]["metricDefinitions"][0]["expr"] == "amount"
    assert report["sections"]["metricDefinitions"][0]["defaultAggregation"] == "SUM"


@pytest.mark.parametrize(
    "expression",
    [
        "SUM(amount)",
        "COUNT(*)",
        "ROW_NUMBER() OVER (ORDER BY amount)",
        "(SELECT amount FROM orders)",
        "amount + (SELECT 1)",
        "amount; SELECT 1",
        "amount; SHOW TABLES",
        "amount, tax",
        "amount AS total",
        "amount FROM orders",
        "amount LIMIT 1",
        "*",
        "orders.*",
        "SHOW TABLES",
        "SUM(((",
    ],
)
def test_measure_projection_rejects_non_scalar_or_unparseable_expressions(expression):
    table = node(
        "table", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": expression,
            "defaultAggregation": "SUM",
        },
    )
    original = deepcopy(metric)

    report = project_semanticspec(graph(table, metric), ["metric"])

    assert report["publishable"] is False
    assert any(
        issue["code"] == "invalid_measure_expression"
        for issue in report["validation_issues"]
    )
    assert "metricDefinitions" not in report["sections"]
    assert metric == original


@pytest.mark.parametrize(
    "expression",
    [
        "amount + tax",
        "COALESCE(amount, 0)",
        "CASE WHEN status = 'SUM(amount); SELECT 1' THEN `MixedCase` ELSE 0 END",
        "amount /* SUM(x) OVER (ORDER BY x); SELECT 1 */ + tax",
        "`SUM` + `select`",
        "'COUNT(*); SELECT amount FROM orders'",
    ],
)
def test_measure_projection_checks_ast_and_preserves_scalar_expression_text(expression):
    table = node(
        "table", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": expression,
            "defaultAggregation": "COUNT",
        },
    )

    report = project_semanticspec(graph(table, metric), ["metric"])

    assert report["publishable"] is True
    assert report["sections"]["metricDefinitions"][0]["expr"] == expression


@pytest.mark.parametrize(
    "expression", ["CUSTOM_UDAF(amount)", "COALESCE(CUSTOM_UDF(amount), 0)"]
)
def test_measure_unknown_function_is_explicitly_unverified(expression):
    table = node(
        "table", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": expression,
            "defaultAggregation": "SUM",
        },
    )

    report = project_semanticspec(graph(table, metric), ["metric"])

    assert report["publishable"] is False
    assert report["validation_issues"][0]["code"] == "unverified_measure_function"
    assert metric["properties"]["server_native"]["expr"] == expression


def test_raw_metric_import_preserves_invalid_sql_but_projection_refuses_it():
    original = snapshot()
    original["sections"]["metricDefinitions"][0]["expr"] = "SUM((("
    imported = import_semanticspec(original)
    metric = next(item for item in imported["nodes"] if item["kind"] == "metric")

    report = project_semanticspec(imported, [metric["id"]])

    assert imported["source_sections"]["metricDefinitions"][0]["expr"] == "SUM((("
    assert metric["properties"]["server_native"]["expr"] == "SUM((("
    assert report["publishable"] is False
    assert any(
        issue["code"] == "invalid_measure_expression"
        for issue in report["validation_issues"]
    )


def test_calculated_formula_keeps_server_grammar_validation_boundary():
    table = node(
        "table", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    measure = node(
        "measure",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": "amount",
            "defaultAggregation": "SUM",
        },
    )
    derived = node(
        "derived",
        "metric",
        "gmv_percent",
        {
            "name": "gmv_percent",
            "semanticType": "CALCULATED_METRIC",
            "derivedMetric": {"formula": "gmv / 100", "refMetrics": ["gmv"]},
        },
    )

    report = project_semanticspec(graph(table, measure, derived), ["derived"])

    assert report["publishable"] is True
    assert report["sql_validation"]["validated_measure_ids"] == ["measure"]
    assert "calculated_formula_grammar" in report["sql_validation"]["unchecked"]
    native = next(
        item
        for item in report["sections"]["metricDefinitions"]
        if item["name"] == "gmv_percent"
    )
    assert native["derivedMetric"]["formula"] == "gmv / 100"


@pytest.mark.parametrize(
    "status", ["candidate", "stale", "conflict", "rejected", "retracted"]
)
def test_unconfirmed_dependency_blocks_projection(status):
    table = node(
        "table",
        "table",
        "orders",
        {"name": "orders", "baseTable": "p.default.orders"},
        status=status,
    )
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": "amount",
        },
    )
    report = project_semanticspec(graph(table, metric), ["metric"])
    assert report["publishable"] is False
    assert {issue["code"] for issue in report["validation_issues"]} == {
        "unconfirmed_item"
    }


def test_missing_and_ambiguous_dependencies_are_explicit():
    metric = node(
        "metric",
        "metric",
        "gmv",
        {
            "name": "gmv",
            "semanticType": "MEASURE",
            "baseLogicalTable": "orders",
            "expr": "amount",
        },
    )
    report = project_semanticspec(graph(metric), ["metric"])
    assert report["validation_issues"][0]["code"] == "missing_dependency"
    first = node(
        "one", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    second = node(
        "two", "table", "orders", {"name": "orders", "baseTable": "q.default.orders"}
    )
    report = project_semanticspec(graph(first, second, metric), ["metric"])
    assert report["validation_issues"][0]["code"] == "ambiguous_dependency"


def test_evidence_is_required_for_nodes_and_relation_claims():
    term = node("term", "term", "t", definition="d")
    term["evidence_ids"] = ["missing"]
    report = project_semanticspec(graph(term), ["term"])
    assert report["sections"] == {}
    assert report["validation_issues"][0]["code"] == "missing_evidence"


def test_only_explicit_join_relation_projects_and_other_business_edges_are_reported():
    left = node(
        "left", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    right = node(
        "right",
        "table",
        "customers",
        {"name": "customers", "baseTable": "p.default.customers"},
    )
    edge = {
        "id": "join",
        "subject": "left",
        "object": "right",
        "predicate": "joins_with",
        "status": "confirmed",
        "evidence_ids": ["proof"],
        "properties": {
            "server_section": "approvedRelationships",
            "server_native": {
                "name": "orders_customers",
                "leftTable": "orders",
                "rightTable": "customers",
                "relationshipColumns": [
                    {"leftColumn": "customer_id", "rightColumn": "id"}
                ],
                "source": "MANUAL",
            },
        },
    }
    report = project_semanticspec(graph(left, right, edges=[edge]), ["join"])
    assert report["publishable"] is True
    assert (
        report["sections"]["semanticModel"]["approvedRelationships"][0]["leftTable"]
        == "orders"
    )
    edge["predicate"] = "related_to"
    report = project_semanticspec(graph(left, right, edges=[edge]), ["join"])
    assert report["publishable"] is False
    assert report["unmapped"][0]["id"] == "join"


def test_local_verified_query_cannot_upgrade_server_trust():
    query = node(
        "query",
        "query",
        "q",
        {"name": "q", "question": "q", "sql": "SELECT 1", "trusted": True},
        memory_kind="verified_queries",
    )
    report = project_semanticspec(graph(query), ["query"])
    assert report["publishable"] is False
    assert "local verification" in report["unmapped"][0]["reason"]


def test_private_extensions_are_never_exported():
    term = node(
        "term",
        "term",
        "t",
        {
            "term": "t",
            "definition": "d",
            "custom_extensions": [{"vendor": "private", "secret": "not_for_server"}],
        },
    )
    report = project_semanticspec(graph(term), ["term"])
    assert report["sections"] == {}
    assert report["unmapped"]


def test_import_is_lossless_revision_pinned_namespaced_and_does_not_mutate_input():
    original = snapshot()
    before = deepcopy(original)
    imported = import_semanticspec(original)
    assert original == before
    assert imported["fragment"] is True
    assert "kb_id" not in imported
    assert imported["source_sections"] == original["sections"]
    assert all(item["status"] == "confirmed" for item in imported["nodes"])
    assert all(
        proof["source_kind"] == "semanticspec" and proof["source_version"] == "r1"
        for proof in imported["evidence"]
    )
    revised = import_semanticspec(snapshot(revision="r2"))
    assert {item["id"] for item in imported["nodes"]} == {
        item["id"] for item in revised["nodes"]
    }
    assert {proof["id"] for proof in imported["evidence"]}.isdisjoint(
        {proof["id"] for proof in revised["evidence"]}
    )
    draft = import_semanticspec(snapshot(source="USER_DRAFT"))
    assert all(item["status"] == "candidate" for item in draft["nodes"])
    query = next(item for item in imported["nodes"] if item["kind"] == "query")
    assert query["properties"]["server_native"]["trusted"] is False


def test_imported_metric_projection_carries_glossary_dependency_closure():
    imported = import_semanticspec(snapshot())
    term = next(item for item in imported["nodes"] if item["kind"] == "term")
    report = project_semanticspec(imported, [term["id"]])
    assert report["publishable"] is True
    assert report["sections"]["glossary"][0]["linkedMetric"] == "gmv"
    assert (
        report["sections"]["semanticModel"]["logicalTables"][0]["dimensions"][0]["name"]
        == "channel"
    )
    assert report["sections"]["metricDefinitions"][0]["name"] == "gmv"
    assert "tableId" not in report["sections"]["dataReferences"][0]


def test_snapshot_array_and_missing_content_boundaries():
    original = snapshot()
    original["sections"] = [
        {"section": key, "content": value, "content_hash": section_integrity(value)[1]}
        for key, value in original["sections"].items()
    ]
    assert import_semanticspec(original)["nodes"]
    with pytest.raises(KnowledgeError, match="section and content"):
        import_semanticspec({**original, "sections": [{"section": "glossary"}]})
    with pytest.raises(KnowledgeError, match="exact revision_id"):
        import_semanticspec({**original, "revision_id": ""})


def test_package_import_preserves_local_sql_without_promoting_trust_or_joins(tmp_path):
    db = PackageDB(tmp_path / "package.db")
    try:
        left = db.upsert_table("p__default", "orders", "schema_hash")
        db.upsert_table("p__default", "customers", "schema_hash2")
        db.upsert_columns(left, [{"name": "amount", "type": "DOUBLE"}])
        db.add_metric(name="gmv", expression="SUM(amount)")
        db.upsert_join(
            "p__default",
            "orders",
            "customer_id",
            "p__default",
            "customers",
            "id",
            "fk",
            1.0,
            "many_to_one",
        )
        sql = "SELECT 'do not tokenize SUM(abc)' AS note /* comment */"
        db.upsert_memory(
            "verified_queries",
            json.dumps({"question": "example", "sql": sql}),
            "example",
        )
        imported = import_package(
            db, {"name": "team", "auth": {"access_key_secret": "never_read"}}
        )
        assert "never_read" not in json.dumps(imported)
        query = next(item for item in imported["nodes"] if item["kind"] == "query")
        assert query["properties"]["package_record"]["payload"]["sql"] == sql
        assert query["properties"]["trusted"] is False
        join = next(
            edge for edge in imported["edges"] if edge["predicate"] == "joins_with"
        )
        assert join["status"] == "candidate"
        assert join["properties"]["approval"] == "not_confirmed"
        assert join["properties"]["confidence"] == 1.0
        assert project_semanticspec(imported, [query["id"]])["publishable"] is False
    finally:
        db.close()


def test_empty_selection_is_explicit_and_string_selection_is_rejected():
    assert (
        project_semanticspec(graph(), [])["validation_issues"][0]["code"]
        == "empty_selection"
    )
    with pytest.raises(KnowledgeError, match="sequence"):
        project_semanticspec(graph(), "term")


def test_calculated_metric_cycle_is_not_publishable():
    first = node(
        "one",
        "metric",
        "one",
        {
            "name": "one",
            "semanticType": "CALCULATED_METRIC",
            "derivedMetric": {"formula": "two", "refMetrics": ["two"]},
        },
    )
    second = node(
        "two",
        "metric",
        "two",
        {
            "name": "two",
            "semanticType": "CALCULATED_METRIC",
            "derivedMetric": {"formula": "one", "refMetrics": ["one"]},
        },
    )
    report = project_semanticspec(graph(first, second), ["one"])
    assert report["publishable"] is False
    assert "dependency_cycle" in {
        issue["code"] for issue in report["validation_issues"]
    }


def test_source_evidence_cannot_be_reused_to_upgrade_remote_query_trust():
    imported = import_semanticspec(snapshot())
    query = next(item for item in imported["nodes"] if item["kind"] == "query")
    query["properties"]["server_native"]["trusted"] = True
    report = project_semanticspec(imported, [query["id"]])
    assert report["publishable"] is False
    assert "query_trust_evidence" in {
        issue["code"] for issue in report["validation_issues"]
    }


def test_remote_native_query_trust_is_preserved_when_it_is_in_the_exact_snapshot():
    source = snapshot()
    source["sections"]["verifiedQueries"][0]["trusted"] = True
    imported = import_semanticspec(source)
    query = next(item for item in imported["nodes"] if item["kind"] == "query")
    report = project_semanticspec(imported, [query["id"]])
    assert report["publishable"] is True
    assert report["sections"]["verifiedQueries"][0]["trusted"] is True


def test_incomplete_evidence_record_cannot_back_a_projection():
    term = node("term", "term", "t", definition="d")
    source = graph(term)
    source["evidence"][0].pop("content_hash")
    report = project_semanticspec(source, ["term"])
    assert report["publishable"] is False
    assert "missing_evidence" in {
        issue["code"] for issue in report["validation_issues"]
    }


def test_real_gateway_envelope_and_section_wrappers_preserve_native_content():
    source = snapshot()
    sections = [
        {
            "section": key,
            "present": True,
            "size_bytes": section_integrity(value)[0],
            "content_hash": section_integrity(value)[1],
            "content": value,
        }
        for key, value in source["sections"].items()
    ]
    envelope = {
        "ok": True,
        "endpoint": source["endpoint"],
        "region": source["region"],
        "data": {
            "namespace": source["namespace"],
            "semantic_spec": {
                "name": source["name"],
                "semantic_spec_id": source["semantic_spec_id"],
            },
            "source": "PUBLISHED",
            "revision_id": "r1",
            "sections": sections,
        },
    }
    imported = import_semanticspec(envelope)
    assert imported["source_sections"] == source["sections"]
    assert imported["source_snapshots"] == sections
    wrapped = {
        **source,
        "sections": {
            key: {"present": True, "content": value}
            for key, value in source["sections"].items()
        },
    }
    assert import_semanticspec(wrapped)["source_id"] == imported["source_id"]


@pytest.mark.parametrize("field", ["content_hash", "size_bytes"])
def test_gateway_section_integrity_metadata_is_validated(field):
    source = snapshot()
    native = source["sections"]["glossary"]
    wrapped = {
        "present": True,
        "content": native,
        "content_hash": section_integrity(native)[1],
        "size_bytes": section_integrity(native)[0],
    }
    wrapped[field] = "sha256:not_the_body" if field == "content_hash" else 1
    source["sections"] = {"glossary": wrapped}
    with pytest.raises(KnowledgeError, match=field):
        import_semanticspec(source)


@pytest.mark.parametrize(
    "field", ["endpoint", "namespace", "region", "semantic_spec_id"]
)
def test_incomplete_source_identity_is_rejected(field):
    source = snapshot()
    source.pop("account_id")
    source.pop(field)
    with pytest.raises(KnowledgeError, match="source identity"):
        import_semanticspec(source)


def test_same_name_in_another_region_or_recreated_spec_has_distinct_identity():
    source = snapshot()
    first = import_semanticspec(source)
    for field, replacement in (
        ("region", "cn-hangzhou"),
        ("semantic_spec_id", "recreated-id"),
        ("namespace", "other-account"),
        ("endpoint", "https://other.invalid/mcp"),
    ):
        other = import_semanticspec({**source, field: replacement})
        assert first["source_id"] != other["source_id"]
        assert {item["id"] for item in first["nodes"]}.isdisjoint(
            {item["id"] for item in other["nodes"]}
        )


def test_absent_sections_are_not_imported_as_empty_placeholders():
    source = snapshot()
    source["sections"] = {
        "glossary": {"present": False, "size_bytes": 0, "content_hash": ""}
    }
    imported = import_semanticspec(source)
    assert imported["nodes"] == []
    assert imported["source_sections"] == {}
    source["sections"]["glossary"]["content"] = []
    with pytest.raises(KnowledgeError, match="absent"):
        import_semanticspec(source)


def test_direct_json_cannot_project_superseded_remote_evidence_as_confirmed():
    imported = import_semanticspec(snapshot())
    imported["fragment"] = False
    imported["kb_id"] = "kb"
    imported["sources"] = [
        {"id": imported["source_id"], "current_revision": "new_revision"}
    ]
    term = next(item for item in imported["nodes"] if item["kind"] == "term")
    assert term["status"] == "confirmed"
    report = project_semanticspec(imported, [term["id"]])
    assert report["publishable"] is False
    assert "stale_evidence" in {issue["code"] for issue in report["validation_issues"]}
    imported.pop("sources")
    report = project_semanticspec(imported, [term["id"]])
    assert "untracked_source" in {
        issue["code"] for issue in report["validation_issues"]
    }


def test_document_projection_checks_current_version_body_and_exact_evidence_span():
    term = node("term", "term", "GMV", definition="paid amount")
    text = "Title\nGMV is paid amount\n"
    source = graph(term)
    source["documents"] = [{"id": "doc", "current_version": "v1"}]
    source["document_versions"] = [
        {
            "id": "v1",
            "document_id": "doc",
            "content": text,
            "content_hash": content_hash(text),
        }
    ]
    source["evidence"] = [
        {
            "id": "proof",
            "source_kind": "document",
            "source_id": "doc",
            "source_version": "v1",
            "content_hash": content_hash("GMV is paid amount"),
            "start_line": 2,
            "end_line": 2,
            "excerpt": "GMV is paid amount",
        }
    ]
    assert project_semanticspec(source, ["term"])["publishable"] is True
    source["documents"][0]["current_version"] = "v2"
    assert "stale_evidence" in {
        issue["code"]
        for issue in project_semanticspec(source, ["term"])["validation_issues"]
    }
    source["documents"][0]["current_version"] = "v1"
    source["evidence"][0]["start_line"] = 1
    assert "invalid_document_evidence" in {
        issue["code"]
        for issue in project_semanticspec(source, ["term"])["validation_issues"]
    }
    source["evidence"][0]["start_line"] = 2
    source["document_versions"][0]["content"] = "changed source"
    assert "invalid_document_evidence" in {
        issue["code"]
        for issue in project_semanticspec(source, ["term"])["validation_issues"]
    }


def test_user_confirmation_requires_the_hash_of_its_declared_evidence():
    source = graph(node("term", "term", "t", definition="d"))
    source["evidence"][0]["excerpt"] = "changed after confirmation"
    report = project_semanticspec(source, ["term"])
    assert report["publishable"] is False
    assert "invalid_evidence_hash" in {
        issue["code"] for issue in report["validation_issues"]
    }


@pytest.mark.parametrize(
    "kind,native,properties",
    [
        ("table", None, {}),
        ("table", {"name": "t", "baseTable": "ambiguous_table"}, {}),
        ("metric", {"name": "m", "semanticType": "MEASURE", "expr": "amount"}, {}),
        (
            "metric",
            {
                "name": "m",
                "semanticType": "CALCULATED_METRIC",
                "expr": "SUM(amount)",
                "derivedMetric": {"formula": "1"},
            },
            {},
        ),
        ("term", None, {"definition": "d", "aka": "not_a_list"}),
        ("term", {"term": "t"}, {}),
        ("query", {"name": "q", "sql": "SELECT 1"}, {}),
        (
            "claim",
            {
                "name": "link",
                "leftTable": "a",
                "rightTable": "b",
                "relationshipColumns": [],
            },
            {"server_section": "approvedRelationships"},
        ),
    ],
)
def test_incomplete_typed_bindings_are_reported_without_fabricating_defaults(
    kind, native, properties
):
    report = project_semanticspec(
        graph(node("item", kind, "name", native, **properties)), ["item"]
    )
    assert report["publishable"] is False
    assert report["unmapped"][0]["id"] == "item"


def test_table_level_business_entity_requires_explicit_typed_physical_binding():
    entity = node(
        "entity",
        "business_entity",
        "Order",
        server_binding={"name": "orders", "baseTable": "p.default.orders"},
    )
    report = project_semanticspec(graph(entity), ["entity"])
    assert report["publishable"] is True
    assert report["sections"]["semanticModel"]["logicalTables"][0]["name"] == "orders"


def test_remote_relationship_preserves_provenance_and_resolves_physical_endpoints():
    source = snapshot()
    model = source["sections"]["semanticModel"]
    model["logicalTables"].append(
        {"name": "customers", "baseTable": "p.default.customers"}
    )
    relationship = {
        "name": "order_customer",
        "leftTable": "orders",
        "rightTable": "customers",
        "relationshipColumns": [{"leftColumn": "customer_id", "rightColumn": "id"}],
        "relationshipCardinality": "MANY_TO_ONE",
        "joinType": "LEFT",
        "source": "DATASCAN_SUGGESTED",
        "sourceDetail": {"type": "DATASCAN", "detectedSources": ["LINEAGE"]},
    }
    model["approvedRelationships"] = [relationship]
    imported = import_semanticspec(source)
    edge = next(item for item in imported["edges"] if item["predicate"] == "joins_with")
    report = project_semanticspec(imported, [edge["id"]])
    assert report["publishable"] is True
    assert report["sections"]["semanticModel"]["approvedRelationships"] == [
        relationship
    ]
    assert {ref["table"] for ref in report["sections"]["dataReferences"]} == {
        "orders",
        "customers",
    }
    model["logicalTables"].pop()
    unresolved = import_semanticspec(source)
    claim = next(
        item
        for item in unresolved["nodes"]
        if item["properties"].get("unresolved_endpoints")
    )
    assert project_semanticspec(unresolved, [claim["id"]])["publishable"] is False


def test_native_relationship_cannot_disagree_with_the_graph_endpoints():
    left = node(
        "left", "table", "orders", {"name": "orders", "baseTable": "p.default.orders"}
    )
    right = node(
        "right",
        "table",
        "customers",
        {"name": "customers", "baseTable": "p.default.customers"},
    )
    edge = {
        "id": "join",
        "subject": "left",
        "object": "right",
        "predicate": "joins_with",
        "status": "confirmed",
        "evidence_ids": ["proof"],
        "properties": {
            "server_section": "approvedRelationships",
            "server_native": {
                "name": "wrong_direction",
                "leftTable": "customers",
                "rightTable": "orders",
                "relationshipColumns": [
                    {"leftColumn": "id", "rightColumn": "customer_id"}
                ],
            },
        },
    }
    report = project_semanticspec(graph(left, right, edges=[edge]), ["join"])
    assert report["publishable"] is False
    assert "relationship_binding_mismatch" in {
        issue["code"] for issue in report["validation_issues"]
    }
