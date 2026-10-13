# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Behavior contracts for local acquisition, review, provenance and analysis."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from maxcompute_semantic.knowledge import GRAPH_SCHEMA, KnowledgeError, KnowledgeStore
from maxcompute_semantic.knowledge.model import content_hash


@pytest.fixture
def store(tmp_path: Path) -> Iterator[KnowledgeStore]:
    with KnowledgeStore(tmp_path / "knowledge.db") as result:
        yield result


def _node(
    identifier: str, *, name: str | None = None, scope: str = "team", kind: str = "term"
) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": kind,
        "name": name or identifier,
        "scope": scope,
        "status": "confirmed",
        "properties": {},
    }


def _evidence(identifier: str = "receipt") -> dict[str, Any]:
    return {
        "id": identifier,
        "source_kind": "user",
        "source_id": "owner",
        "source_version": "v1",
        "content_hash": content_hash("user assertion"),
        "excerpt": "user assertion",
    }


def _edge(
    identifier: str,
    subject: str,
    object_: str,
    *,
    predicate: str = "derived_from",
    status: str = "confirmed",
    evidence: str = "receipt",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "status": status,
        "evidence_ids": [evidence],
        "properties": {},
    }


def _fragment(
    *,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "mcs.knowledge_graph",
        "schema_version": 1,
        "nodes": nodes or [],
        "edges": edges or [],
        "evidence": evidence or [],
    }


def _seed(store: KnowledgeStore) -> None:
    store.import_graph(
        _fragment(
            nodes=[
                _node("metric", kind="metric"),
                _node("column", kind="column"),
                _node("query", kind="query"),
                _node("doc", kind="document"),
            ],
            edges=[
                _edge("m-c", "metric", "column"),
                _edge("q-m", "query", "metric", predicate="uses"),
                _edge("d-c", "doc", "column", predicate="mentions"),
            ],
            evidence=[_evidence()],
        )
    )


def test_fragment_import_is_idempotent_and_search_works_without_a_profile(
    store: KnowledgeStore,
) -> None:
    fragment = _fragment(nodes=[_node("customer", name="有效客户")])
    first = store.import_graph(fragment)
    second = store.import_graph(fragment)
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["revision"] == first["revision"]
    assert store.search("有效")[0]["id"] == "customer"
    assert store.search("有效", scope="another") == []
    assert store.search('OR " *') == []


def test_same_name_does_not_merge_across_scopes(store: KnowledgeStore) -> None:
    store.import_graph(
        _fragment(
            nodes=[
                _node("sales", name="Revenue", scope="sales"),
                _node("finance", name="Revenue", scope="finance"),
            ]
        )
    )
    with pytest.raises(KnowledgeError, match="ambiguous"):
        store.show("Revenue")
    assert store.show("Revenue", scope="finance")["id"] == "finance"
    assert len(store.search("Revenue")) == 2


def test_graph_import_checks_all_references_before_mutation(
    store: KnowledgeStore,
) -> None:
    before = store.export_graph()
    fragment = _fragment(
        nodes=[_node("new")],
        edges=[_edge("bad", "new", "missing")],
        evidence=[_evidence()],
    )
    with pytest.raises(KnowledgeError, match="endpoint"):
        store.import_graph(fragment)
    assert store.export_graph() == before


@pytest.mark.parametrize("bad", ["nonsense", {}, [], None])
def test_malformed_node_state_is_a_classified_error_without_partial_import(
    store: KnowledgeStore, bad: Any
) -> None:
    node = _node("new")
    node["status"] = bad
    with pytest.raises(KnowledgeError):
        store.import_graph(_fragment(nodes=[node]))
    assert store.list() == []
    assert store.meta()["revision"] == 0


def test_immutable_evidence_collision_refuses_atomically(store: KnowledgeStore) -> None:
    store.import_graph(_fragment(evidence=[_evidence()]))
    before = store.export_graph()
    changed = _evidence()
    changed["source_version"] = "v2"
    with pytest.raises(KnowledgeError, match="immutable"):
        store.import_graph(_fragment(nodes=[_node("new")], evidence=[changed]))
    assert store.export_graph() == before


def test_confirmed_edge_requires_pinned_existing_evidence(
    store: KnowledgeStore,
) -> None:
    no_evidence = _edge("e", "a", "b")
    no_evidence["evidence_ids"] = []
    with pytest.raises(KnowledgeError, match="pinned"):
        store.import_graph(
            _fragment(nodes=[_node("a"), _node("b")], edges=[no_evidence])
        )
    with pytest.raises(KnowledgeError, match="evidence"):
        store.import_graph(
            _fragment(nodes=[_node("a"), _node("b")], edges=[_edge("e", "a", "b")])
        )
    assert store.list() == []


def test_node_evidence_is_preserved_and_must_resolve(store: KnowledgeStore) -> None:
    node = {**_node("a"), "evidence_ids": ["receipt"]}
    with pytest.raises(KnowledgeError, match="node evidence"):
        store.import_graph(_fragment(nodes=[node]))
    store.import_graph(_fragment(nodes=[node], evidence=[_evidence()]))
    assert store.show("a")["evidence_ids"] == ["receipt"]


def test_snapshot_roundtrip_preserves_ids_revision_and_evidence(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    _seed(store)
    snapshot = store.export_graph()
    assert list(Draft202012Validator(GRAPH_SCHEMA).iter_errors(snapshot)) == []
    with KnowledgeStore(tmp_path / "copy.db") as other:
        imported = other.import_graph(snapshot)
        assert imported["kb_id"] == snapshot["kb_id"]
        assert other.export_graph() == snapshot
        assert other.import_graph(snapshot)["changed"] is False
        assert other.show("m-c")["evidence"][0]["source_version"] == "v1"


def test_import_different_identity_or_replace_cannot_overwrite_local_work(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    store.import_graph(_fragment(nodes=[_node("local")]))
    with KnowledgeStore(tmp_path / "other.db") as other:
        other.import_graph(_fragment(nodes=[_node("remote")]))
        snapshot = other.export_graph()
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="identity"):
        store.import_graph(snapshot)
    with pytest.raises(KnowledgeError, match="replacement"):
        store.import_graph(snapshot, replace=True)
    assert store.export_graph() == before


def test_markdown_acquisition_is_idempotent_and_does_not_auto_confirm(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    doc = tmp_path / "rules.md"
    doc.write_text(
        "---\nmcs:\n  scope: team\n  nodes:\n    - {id: customer, kind: term, name: Customer}\n    - {id: table, kind: table, name: Customers}\n  relations:\n    - {subject: customer, predicate: maps_to, object: table}\n---\n# Active customers\nCustomer means someone registered. [Customer](kb://customer)\n",
        encoding="utf-8",
    )
    first = store.ingest_markdown(doc)
    snapshot = store.export_graph()
    assert first["candidates"] >= 2
    assert all(edge["status"] == "candidate" for edge in snapshot["edges"])
    assert store.neighbors("customer")["edges"] == []
    assert store.search("registered")[0]["id"] == first["id"]
    assert store.ingest_markdown(doc)["changed"] is False
    assert store.export_graph() == snapshot
    for evidence in snapshot["evidence"]:
        assert evidence["source_version"] == first["version"]
        assert content_hash(evidence["excerpt"]) == evidence["content_hash"]


def test_document_update_stales_reviewed_relation_and_preserves_old_evidence(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    store.import_graph(_fragment(nodes=[_node("customer", name="Customer")]))
    doc = tmp_path / "rules.md"
    doc.write_text("# Rules\nCustomer is active.\n", encoding="utf-8")
    first = store.ingest_markdown(doc, scope="team")
    edge = store.neighbors(first["id"], statuses=("candidate",))["edges"][0]
    proposal = store.propose({"kind": "upsert_edge", "edge": edge})
    applied = store.apply(proposal["proposal"]["id"], reviewed_by="owner")
    assert applied["applied"] is True
    assert store.show(edge["id"])["status"] == "confirmed"
    old_evidence = store.show(edge["evidence_ids"][0])
    doc.write_text("# Rules\nCustomer is retired.\n", encoding="utf-8")
    changed = store.ingest_markdown(doc, scope="team")
    assert changed["id"] == first["id"]
    assert changed["version"] != first["version"]
    assert store.show(edge["id"])["status"] == "stale"
    assert store.show(edge["evidence_ids"][0]) == old_evidence
    assert store.neighbors(first["id"])["edges"] == []
    assert any(issue["kind"] == "stale" for issue in store.check()["issues"])


def test_local_document_move_preserves_document_identity(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    old = tmp_path / "old.md"
    old.write_text("# Same document\nWords.\n", encoding="utf-8")
    before = store.ingest_markdown(old)
    new = tmp_path / "new.md"
    old.rename(new)
    after = store.ingest_markdown(new)
    assert after["id"] == before["id"]
    assert after["version"] == before["version"]
    assert len(store.export_graph()["documents"]) == 1


def test_identical_documents_that_both_exist_remain_separate(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    for doc in (a, b):
        doc.write_text("# Same\nSame body.\n", encoding="utf-8")
    assert store.ingest_markdown(a)["id"] != store.ingest_markdown(b)["id"]


def test_frontmatter_cannot_claim_review_acceptance(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    doc = tmp_path / "untrusted.md"
    doc.write_text(
        "---\nmcs:\n  nodes:\n    - {id: a, kind: term, name: A, status: confirmed}\n    - {id: b, kind: term, name: B}\n  relations:\n    - {subject: a, predicate: alias_of, object: b, status: confirmed}\n---\n# Aliases\n",
        encoding="utf-8",
    )
    store.ingest_markdown(doc)
    assert store.show("a")["status"] == "candidate"
    assert store.neighbors("a")["edges"] == []


def test_invalid_frontmatter_relation_rolls_back_document_and_nodes(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    doc = tmp_path / "bad.md"
    doc.write_text(
        "---\nmcs:\n  nodes: [{id: a, kind: term, name: A}]\n  relations: [{subject: a, predicate: maps_to, object: missing}]\n---\n# Broken\n",
        encoding="utf-8",
    )
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="endpoint"):
        store.ingest_markdown(doc)
    assert store.export_graph() == before


def test_tampered_document_evidence_is_refused_on_import(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    store.import_graph(_fragment(nodes=[_node("term")]))
    doc = tmp_path / "rules.md"
    doc.write_text("# Rules\nterm definition\n", encoding="utf-8")
    store.ingest_markdown(doc, scope="team")
    snapshot = store.export_graph()
    snapshot["evidence"][0]["excerpt"] = "fabricated"
    snapshot["evidence"][0]["content_hash"] = content_hash("fabricated")
    with KnowledgeStore(tmp_path / "new.db") as other:
        with pytest.raises(KnowledgeError, match="span/hash"):
            other.import_graph(snapshot)
        assert other.list() == []


def test_proposal_applies_atomically_and_revision_conflict_retains_review(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_fragment(nodes=[_node("a")]))
    proposal = store.propose({"kind": "upsert_node", "node": _node("a", name="Better")})
    result = store.apply(proposal["proposal"]["id"])
    assert result["applied"] is True
    assert store.show("a")["name"] == "Better"
    proposal = store.propose(
        {"kind": "upsert_node", "node": _node("a", name="Old plan")}
    )
    store.import_graph(_fragment(nodes=[_node("unrelated")]))
    with pytest.raises(KnowledgeError, match="conflicts"):
        store.apply(proposal["proposal"]["id"])
    assert store.show("a")["name"] == "Better"
    assert store.show(proposal["proposal"]["id"])["status"] == "conflict"


def test_proposal_before_value_null_means_target_must_be_absent(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_fragment(nodes=[_node("a")]))
    with pytest.raises(KnowledgeError, match="before_value"):
        store.propose({"kind": "upsert_node", "node": _node("a")}, before_value=None)
    proposal = store.propose(
        {"kind": "upsert_node", "node": _node("b")}, before_value=None
    )
    store.apply(proposal["proposal"]["id"])
    assert store.show("b")["id"] == "b"


def test_invalid_apply_rolls_back_mutation_proposal_and_revision(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_fragment(nodes=[_node("a"), _node("b")]))
    proposal = store.propose(
        {"kind": "upsert_edge", "edge": _edge("bad", "a", "b", evidence="absent")}
    )
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="evidence"):
        store.apply(proposal["proposal"]["id"])
    assert store.export_graph() == before


def test_reject_and_retract_are_retained_and_excluded_by_default(
    store: KnowledgeStore,
) -> None:
    _seed(store)
    proposal = store.propose({"kind": "upsert_node", "node": _node("unused")})
    store.reject(proposal["proposal"]["id"], reason="wrong concept")
    assert store.show(proposal["proposal"]["id"])["reason"] == "wrong concept"
    with pytest.raises(KnowledgeError):
        store.apply(proposal["proposal"]["id"])
    store.retract("m-c", reason="replaced")
    assert store.show("m-c")["status"] == "retracted"
    assert store.show("m-c")["evidence"]
    assert store.path("metric", "column")["found"] is False
    assert store.retract("m-c")["changed"] is False


def test_typed_path_is_directed_bounded_and_returns_each_claim(
    store: KnowledgeStore,
) -> None:
    _seed(store)
    result = store.path("query", "column", max_depth=2)
    assert result["found"] is True
    assert [edge["predicate"] for edge in result["edges"]] == ["uses", "derived_from"]
    assert "not factual proof" in result["interpretation"]
    assert store.path("column", "query")["found"] is False
    assert store.path("query", "column", max_depth=1)["found"] is False
    assert store.path("metric", "column", limit=1)["found"] is True


def test_impact_follows_dependencies_in_reverse_not_mentions(
    store: KnowledgeStore,
) -> None:
    _seed(store)
    impacted = store.impact("column")
    assert {node["id"] for node in impacted["nodes"]} == {"metric", "query"}
    assert {edge["id"] for edge in impacted["edges"]} == {"m-c", "q-m"}
    assert store.impact("query")["nodes"] == []


def test_review_conflicts_are_explicit_and_not_silently_accepted(
    store: KnowledgeStore,
) -> None:
    first, second = _edge("e1", "a", "b"), _edge("e2", "a", "c")
    for edge in (first, second):
        edge["properties"] = {"conflict_key": "a.definition"}
    store.import_graph(
        _fragment(
            nodes=[_node("a"), _node("b"), _node("c")],
            evidence=[_evidence()],
            edges=[first, second],
        )
    )
    assert store.show("e1")["status"] == "conflict"
    assert store.show("e2")["status"] == "conflict"
    assert store.neighbors("a")["edges"] == []
    assert store.check()["ok"] is False


def test_dependency_cycles_and_isolated_nodes_are_reported(
    store: KnowledgeStore,
) -> None:
    store.import_graph(
        _fragment(
            nodes=[_node("a"), _node("b"), _node("isolated")],
            evidence=[_evidence()],
            edges=[_edge("e1", "a", "b"), _edge("e2", "b", "a")],
        )
    )
    issues = store.check()["issues"]
    assert any(issue["kind"] == "dependency_cycle" for issue in issues)
    assert {issue["id"] for issue in issues if issue["kind"] == "isolated_node"} == {
        "isolated"
    }


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("path", {"max_depth": 0}),
        ("path", {"max_depth": 11}),
        ("path", {"limit": 1001}),
        ("impact", {"max_depth": -1}),
        ("neighbors", {"limit": 0}),
    ],
)
def test_unbounded_traversal_requests_are_rejected(
    store: KnowledgeStore, method: str, kwargs: dict[str, Any]
) -> None:
    _seed(store)
    args = ("metric", "column") if method == "path" else ("metric",)
    with pytest.raises(KnowledgeError):
        getattr(store, method)(*args, **kwargs)


def test_read_of_missing_store_never_creates_database(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "knowledge.db"
    with pytest.raises(KnowledgeError, match="does not exist"):
        KnowledgeStore(path, create=False)
    assert not path.parent.exists()


def test_future_schema_and_unknown_relation_are_rejected_without_writes(
    store: KnowledgeStore,
) -> None:
    future = _fragment(nodes=[_node("a")])
    future["schema_version"] = 99
    with pytest.raises(KnowledgeError, match="schema_version"):
        store.import_graph(future)
    unknown = _edge("e", "a", "b")
    unknown["predicate"] = "automatically_proves"
    with pytest.raises(KnowledgeError, match="unsupported relation"):
        store.import_graph(_fragment(nodes=[_node("a"), _node("b")], edges=[unknown]))
    assert store.meta()["revision"] == 0


def test_read_operations_do_not_change_the_export(store: KnowledgeStore) -> None:
    _seed(store)
    before = deepcopy(store.export_graph())
    store.search("metric")
    store.list(kind="metric")
    store.show("m-c")
    store.path("query", "column")
    store.neighbors("column")
    store.impact("column")
    store.check()
    assert store.export_graph() == before


def test_empty_snapshot_preserves_kb_identity(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    snapshot = store.export_graph()
    with KnowledgeStore(tmp_path / "other.db") as other:
        other.import_graph(snapshot)
        assert other.export_graph() == snapshot


def test_existing_sqlite_package_is_not_modified_by_init(tmp_path: Path) -> None:
    path = tmp_path / "package.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tables (name TEXT)")
        conn.execute("INSERT INTO tables VALUES ('private_table')")
    before = path.read_bytes()
    with pytest.raises(KnowledgeError, match="not a knowledge"):
        KnowledgeStore(path)
    assert path.read_bytes() == before


def test_candidate_edge_can_be_reviewed_but_node_with_missing_evidence_cannot(
    store: KnowledgeStore,
) -> None:
    proposal = store.propose(
        {"kind": "upsert_node", "node": {**_node("bad"), "evidence_ids": ["missing"]}}
    )
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="node evidence"):
        store.apply(proposal["proposal"]["id"])
    assert store.export_graph() == before


def _source_fragment(revision: str) -> dict[str, Any]:
    evidence = {
        **_evidence("receipt-" + revision),
        "source_kind": "package",
        "source_id": "package:team",
        "source_version": revision,
    }
    return {
        **_fragment(
            nodes=[{**_node("source-node"), "evidence_ids": [evidence["id"]]}],
            evidence=[evidence],
        ),
        "fragment": True,
        "source_id": "package:team",
        "source_revision": revision,
    }


def test_new_source_revision_stales_old_local_claims_and_forbids_old_reimport(
    store: KnowledgeStore,
) -> None:
    first = _source_fragment("r1")
    store.import_graph(first)
    store.import_graph(
        _fragment(
            nodes=[_node("local")],
            edges=[
                _edge("local-dependency", "local", "source-node", evidence="receipt-r1")
            ],
        )
    )
    assert store.import_graph(first)["changed"] is False
    second = _source_fragment("r2")
    store.import_graph(second)
    assert store.show("local-dependency")["status"] == "stale"
    assert store.show("source-node")["status"] == "confirmed"
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="superseded"):
        store.import_graph(first)
    assert store.export_graph() == before
    assert store.import_graph(second)["changed"] is False


def test_same_source_revision_cannot_be_reused_for_different_contents(
    store: KnowledgeStore,
) -> None:
    source = _source_fragment("r1")
    store.import_graph(source)
    source["nodes"][0]["name"] = "Different contents"
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="reused"):
        store.import_graph(source)
    assert store.export_graph() == before


def test_source_revision_removal_marks_old_nodes_stale_and_cannot_be_reviewed_current(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_source_fragment("r1"))
    store.import_graph(
        {**_fragment(), "source_id": "package:team", "source_revision": "r2"}
    )
    assert store.show("source-node")["status"] == "stale"
    node = store.export_graph()["nodes"][0]
    proposal = store.propose({"kind": "upsert_node", "node": node})
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="stale"):
        store.apply(proposal["proposal"]["id"])
    assert store.export_graph() == before


def test_reimport_conflicting_fragment_is_idempotent(store: KnowledgeStore) -> None:
    a, b = _edge("a-b", "a", "b"), _edge("a-c", "a", "c")
    for edge in (a, b):
        edge["properties"]["conflict_key"] = "definition"
    source = _fragment(
        nodes=[_node("a"), _node("b"), _node("c")], evidence=[_evidence()], edges=[a, b]
    )
    store.import_graph(source)
    before = store.export_graph()
    assert store.import_graph(source)["changed"] is False
    assert store.export_graph() == before


@pytest.mark.parametrize(
    "body",
    [
        "---\nmcs: {}",
        "---\n[bad: yaml\n---\n",
        "---\n[one, two]\n---\n",
        "---\nmcs: nope\n---\n",
        "---\nmcs: {nodes: nope}\n---\n",
    ],
)
def test_malformed_markdown_is_rejected_without_a_partial_document(
    store: KnowledgeStore, tmp_path: Path, body: str
) -> None:
    doc = tmp_path / "invalid.md"
    doc.write_text(body, encoding="utf-8")
    before = store.export_graph()
    with pytest.raises(KnowledgeError):
        store.ingest_markdown(doc)
    assert store.export_graph() == before


def test_binary_document_and_missing_document_have_classified_read_errors(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    doc = tmp_path / "binary.md"
    doc.write_bytes(b"\xff\x00")
    for target in (doc, tmp_path / "missing.md"):
        with pytest.raises(KnowledgeError, match="cannot read"):
            store.ingest_markdown(target)
    assert store.list() == []


def test_traversal_reports_truncation_and_filters_direction(
    store: KnowledgeStore,
) -> None:
    store.import_graph(
        _fragment(
            nodes=[_node("a"), _node("b"), _node("c"), _node("target")],
            evidence=[_evidence()],
            edges=[
                _edge("1", "a", "b"),
                _edge("2", "a", "c"),
                _edge("3", "c", "target"),
            ],
        )
    )
    assert store.neighbors("a", direction="out", limit=1)["truncated"] is True
    assert store.neighbors("a", direction="in")["edges"] == []
    assert store.path("a", "target", limit=1)["truncated"] is True
    assert store.impact("target", limit=1)["truncated"] is True


@pytest.mark.parametrize(
    "kind", ["documents", "document_versions", "proposals", "sources"]
)
def test_malformed_imported_source_metadata_is_rejected_atomically(
    store: KnowledgeStore, kind: str
) -> None:
    payload = _fragment(nodes=[_node("new")])
    payload[kind] = [{"id": "bad"}]
    with pytest.raises(KnowledgeError):
        store.import_graph(payload)
    assert store.list() == []


@pytest.mark.parametrize(
    "collection,item",
    [
        ("nodes", {"id": "a", "kind": "not_supported", "name": "A"}),
        ("nodes", {"id": "a", "kind": "term", "name": "A", "scope": []}),
        (
            "nodes",
            {"id": "a", "kind": "term", "name": "A", "properties": "not an object"},
        ),
        (
            "nodes",
            {
                "id": "a",
                "kind": "term",
                "name": "A",
                "properties": {"value": float("nan")},
            },
        ),
        (
            "edges",
            {
                "id": "a",
                "subject": "b",
                "object": "c",
                "predicate": "uses",
                "status": {},
            },
        ),
        (
            "edges",
            {
                "id": "a",
                "subject": "b",
                "object": "c",
                "predicate": "uses",
                "evidence_ids": 1,
            },
        ),
        (
            "edges",
            {
                "id": "a",
                "subject": "b",
                "object": "c",
                "predicate": "uses",
                "evidence_ids": [1],
            },
        ),
        (
            "edges",
            {
                "id": "a",
                "subject": "b",
                "object": "c",
                "predicate": "uses",
                "properties": [],
            },
        ),
        (
            "evidence",
            {
                "id": "a",
                "source_kind": "remote_guess",
                "source_id": "source",
                "source_version": "1",
                "content_hash": "hash",
            },
        ),
        (
            "evidence",
            {
                "id": "a",
                "source_kind": "user",
                "source_id": "source",
                "source_version": "1",
                "content_hash": "hash",
                "excerpt": [],
            },
        ),
        (
            "evidence",
            {
                "id": "a",
                "source_kind": "document",
                "source_id": "source",
                "source_version": "1",
                "content_hash": "hash",
                "start_line": 0,
                "end_line": 1,
            },
        ),
    ],
)
def test_untrusted_graph_field_shapes_are_rejected_without_writes(
    store: KnowledgeStore, collection: str, item: dict[str, Any]
) -> None:
    fragment = _fragment()
    fragment[collection] = [item]
    before = store.export_graph()
    with pytest.raises(KnowledgeError):
        store.import_graph(fragment)
    assert store.export_graph() == before


def test_unknown_proposal_and_wrong_base_revision_cannot_create_reviews(
    store: KnowledgeStore,
) -> None:
    with pytest.raises(KnowledgeError, match="unsupported proposal"):
        store.propose({"kind": "execute_sql", "sql": "SELECT secret"})
    with pytest.raises(KnowledgeError, match="base revision"):
        store.propose(
            {"kind": "upsert_node", "node": _node("new")}, expected_revision=10
        )
    assert store.export_graph()["proposals"] == []


def test_retraction_proposal_keeps_evidence_and_review(store: KnowledgeStore) -> None:
    _seed(store)
    proposal = store.propose(
        {"kind": "retract_edge", "id": "m-c", "reason": "superseded"}
    )
    store.apply(proposal["proposal"]["id"])
    edge = store.show("m-c")
    assert edge["status"] == "retracted"
    assert edge["properties"]["retraction_reason"] == "superseded"
    assert edge["evidence"][0]["source_version"] == "v1"


def test_node_identity_change_is_refused_on_review(store: KnowledgeStore) -> None:
    store.import_graph(_fragment(nodes=[_node("a", scope="one")]))
    proposal = store.propose({"kind": "upsert_node", "node": _node("a", scope="two")})
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="identity"):
        store.apply(proposal["proposal"]["id"])
    assert store.export_graph() == before


def test_same_node_path_and_missing_relation_are_classified(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_fragment(nodes=[_node("a")]))
    assert store.path("a", "a")["found"] is True
    assert store.path("a", "a")["edges"] == []
    with pytest.raises(KnowledgeError, match="relation not found"):
        store.retract("missing")
    with pytest.raises(KnowledgeError, match="direction"):
        store.neighbors("a", direction="sideways")


def test_local_package_projection_import_is_offline_and_revision_pinned(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.knowledge.semanticspec import import_package

    db = PackageDB(tmp_path / "package.db")
    try:
        table_id = db.upsert_table("warehouse__default", "orders", "h1")
        db.upsert_columns(table_id, [{"name": "amount", "type": "BIGINT"}])
        fragment = import_package(db, "sales")
        assert store.import_graph(fragment)["changed"] is True
        assert store.import_graph(fragment)["changed"] is False
        snapshot = store.export_graph()
        assert snapshot["sources"][0]["current_revision"] == fragment["source_revision"]
        assert snapshot["evidence"][0]["source_kind"] == "package"
        assert db.list_tables()[0]["name"] == "orders"
    finally:
        db.close()


def test_semanticspec_projection_import_preserves_exact_source_and_kb_identity(
    store: KnowledgeStore,
) -> None:
    from maxcompute_semantic.knowledge.semanticspec import import_semanticspec

    snapshot = {
        "name": "sales",
        "endpoint": "https://gateway.invalid/mcp",
        "namespace": "42",
        "region": "cn-shanghai",
        "semantic_spec_id": "catalog-42",
        "source": "PUBLISHED",
        "revision_id": "published-r1",
        "sections": {"glossary": [{"term": "GMV", "definition": "Paid amount"}]},
    }
    kb_id = store.meta()["kb_id"]
    fragment = import_semanticspec(snapshot)
    store.import_graph(fragment)
    assert store.meta()["kb_id"] == kb_id
    term = store.list(kind="term")[0]
    assert term["status"] == "confirmed"
    evidence = store.show(term["evidence_ids"][0])
    assert evidence["source_version"] == "published-r1"
    assert evidence["source_id"] == fragment["source_id"]
    assert store.import_graph(fragment)["changed"] is False


def test_standard_document_links_are_candidate_mentions_and_do_not_fetch(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text(
        "# A\n[B](b.md#definition) [Remote](https://example.invalid/private)\n",
        encoding="utf-8",
    )
    second.write_text("# B\nDefinition.\n", encoding="utf-8")
    a = store.ingest_markdown(first, scope="team")
    refs = store.neighbors(a["id"], statuses=("candidate",))["nodes"]
    b_placeholder = next(
        node for node in refs if node["properties"]["locator"] == str(second.resolve())
    )
    assert all(node["status"] == "candidate" for node in refs)
    b = store.ingest_markdown(second, scope="team")
    assert b["id"] == b_placeholder["id"]
    assert len(store.export_graph()["documents"]) == 2
    assert store.neighbors(a["id"])["edges"] == []


def test_document_version_hash_preserves_source_newline_bytes(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    doc = tmp_path / "windows.md"
    body = "# Definition\r\nTwo lines.\r\n"
    doc.write_bytes(body.encode("utf-8"))
    imported = store.ingest_markdown(doc)
    version = store.show(imported["version"])
    assert version["content"] == body
    assert version["content_hash"] == content_hash(body)


def test_identical_source_import_preserves_accepted_local_edits(
    store: KnowledgeStore,
) -> None:
    fragment = _source_fragment("r1")
    fragment["nodes"][0]["status"] = "candidate"
    store.import_graph(fragment)
    edited = store.export_graph()["nodes"][0]
    edited["name"] = "Reviewed local name"
    proposal = store.propose({"kind": "upsert_node", "node": edited})
    store.apply(proposal["proposal"]["id"])
    before = store.export_graph()
    assert store.import_graph(fragment)["changed"] is False
    assert store.export_graph() == before
    assert store.show("source-node")["name"] == "Reviewed local name"
    assert store.show("source-node")["status"] == "confirmed"


def test_new_source_preserves_local_review_as_stale_with_explicit_candidate(
    store: KnowledgeStore,
) -> None:
    store.import_graph(_source_fragment("r1"))
    local = store.export_graph()["nodes"][0]
    local["name"] = "Local reviewed value"
    proposal = store.propose({"kind": "upsert_node", "node": local})
    store.apply(proposal["proposal"]["id"])
    updated = _source_fragment("r2")
    updated["nodes"][0]["name"] = "New source value"
    store.import_graph(updated)
    stored = store.show("source-node")
    assert stored["name"] == "Local reviewed value"
    assert stored["status"] == "stale"
    assert stored["evidence_ids"] == ["receipt-r1"]
    assert stored["source_candidate"]["name"] == "New source value"
    assert stored["local_review"]["proposal_id"] == proposal["proposal"]["id"]
    recheck = store.propose({"kind": "upsert_node", "node": stored["source_candidate"]})
    store.apply(recheck["proposal"]["id"])
    assert store.show("source-node")["name"] == "New source value"
    assert store.show("source-node")["status"] == "confirmed"
    assert "source_candidate" not in store.show("source-node")


def _native_snapshot(revision: str) -> dict[str, Any]:
    return {
        "name": "sales",
        "endpoint": "https://gateway.invalid/mcp",
        "namespace": "42",
        "region": "cn-shanghai",
        "semantic_spec_id": "catalog-42",
        "source": "USER_DRAFT",
        "revision_id": revision,
        "sections": {
            "glossary": [{"term": "Revenue", "definition": "Paid amount"}],
            "semanticModel": {
                "logicalTables": [],
                "namedFilters": [],
                "approvedRelationships": [],
                "futureNativeField": {"origin": "v1"},
            },
        },
    }


def test_entire_source_snapshot_is_pinned_and_survives_export_import(
    store: KnowledgeStore, tmp_path: Path
) -> None:
    from maxcompute_semantic.knowledge.semanticspec import import_semanticspec

    fragment = import_semanticspec(_native_snapshot("r1"))
    store.import_graph(fragment)
    exported = store.export_graph()
    source = exported["sources"][0]
    for key in ("source_snapshot", "source_sections", "source_snapshots"):
        assert source[key] == fragment[key]
        assert source["snapshots"]["r1"][key] == fragment[key]
    with KnowledgeStore(tmp_path / "restored.db") as other:
        other.import_graph(exported)
        assert other.export_graph() == exported
    changed = _native_snapshot("r1")
    changed["sections"]["semanticModel"]["futureNativeField"]["origin"] = (
        "changed but not indexed"
    )
    with pytest.raises(KnowledgeError, match="revision was reused"):
        store.import_graph(import_semanticspec(changed))
    assert store.export_graph() == exported


def test_old_source_snapshots_remain_addressable_after_new_source_revision(
    store: KnowledgeStore,
) -> None:
    from maxcompute_semantic.knowledge.semanticspec import import_semanticspec

    first, second = _native_snapshot("r1"), _native_snapshot("r2")
    second["sections"]["semanticModel"]["futureNativeField"]["origin"] = "v2"
    store.import_graph(import_semanticspec(first))
    store.import_graph(import_semanticspec(second))
    source = store.export_graph()["sources"][0]
    assert (
        source["snapshots"]["r1"]["source_sections"]["semanticModel"][
            "futureNativeField"
        ]["origin"]
        == "v1"
    )
    assert (
        source["snapshots"]["r2"]["source_sections"]["semanticModel"][
            "futureNativeField"
        ]["origin"]
        == "v2"
    )
    assert source["current_revision"] == "r2"


@pytest.mark.parametrize("cycle", [True, False])
def test_integrity_checks_whole_dependency_graph_beyond_traversal_depth(
    store: KnowledgeStore, cycle: bool
) -> None:
    count = 12
    nodes = [_node(f"n{i}") for i in range(count)]
    edges = [
        _edge(f"e{i}", f"n{i}", f"n{i + 1}", predicate="depends_on")
        for i in range(count - 1)
    ]
    if cycle:
        edges.append(_edge("close", "n11", "n0", predicate="depends_on"))
    store.import_graph(_fragment(nodes=nodes, edges=edges, evidence=[_evidence()]))
    report = store.check()
    assert report["complete"] is True
    assert report["ok"] is not cycle
    assert (
        any(issue["kind"] == "dependency_cycle" for issue in report["issues"]) is cycle
    )


def test_repeated_structured_dependency_refs_import_as_one_pinned_relation(
    store: KnowledgeStore,
) -> None:
    from maxcompute_semantic.knowledge.semanticspec import import_semanticspec

    snapshot = _native_snapshot("r1")
    snapshot["sections"]["semanticModel"]["logicalTables"] = [
        {"name": "orders", "baseTable": "p.default.orders"}
    ]
    snapshot["sections"]["glossary"] = [
        {
            "term": "Paid orders",
            "definition": "Orders with completed payment",
            "applicableTables": ["orders"],
            "linkedDimension": "orders.state",
        }
    ]
    fragment = import_semanticspec(snapshot)
    store.import_graph(fragment)
    exported = store.export_graph()
    dependencies = [
        edge for edge in exported["edges"] if edge["predicate"] == "depends_on"
    ]
    assert len(dependencies) == 1
    assert dependencies[0]["status"] == "candidate"
    assert dependencies[0]["evidence_ids"]
    assert exported["sources"][0]["source_sections"] == snapshot["sections"]
    assert store.import_graph(fragment)["changed"] is False


def test_duplicate_ids_with_conflicting_payloads_are_not_last_writer_wins(
    store: KnowledgeStore,
) -> None:
    first, second = _node("term", name="First"), _node("term", name="Different")
    before = store.export_graph()
    with pytest.raises(KnowledgeError, match="duplicate IDs"):
        store.import_graph(_fragment(nodes=[first, second]))
    assert store.export_graph() == before
