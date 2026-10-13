# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cross-service evidence and exact-publication contracts with local service doubles."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from maxcompute_semantic.knowledge import KnowledgeError, KnowledgeStore
from maxcompute_semantic.knowledge.semanticspec import (
    import_semanticspec,
    project_semanticspec,
)
from maxcompute_semantic.remote.config import RemoteConnection
from maxcompute_semantic.remote.sync import (
    SECTIONS,
    SemanticPackageSync,
    SemanticSyncError,
)

_GET = "maxcompute_semanticspec_get"
_PUBLISH = "maxcompute_semanticspec_publish"


class InterleavingConnection(sqlite3.Connection):
    """An actual SQLite connection that can schedule a separate store's write."""

    after_edge_read: Callable[[], None] | None = None

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        return self.cursor(factory=InterleavingCursor).execute(sql, parameters)


class InterleavingCursor(sqlite3.Cursor):
    def fetchall(self) -> list[Any]:
        rows = super().fetchall()
        connection = self.connection
        if not isinstance(connection, InterleavingConnection):
            return rows
        callback = connection.after_edge_read
        columns = tuple(column[0] for column in self.description or ())
        if (
            callback
            and "payload" in columns
            and any(
                json.loads(row["payload"]).get("id") == "owner-edge" for row in rows
            )
        ):
            connection.after_edge_read = None
            callback()
        return rows


class PublicationGateway:
    """An external service whose publication can expose a changed physical identity."""

    def __init__(self) -> None:
        self.connection = RemoteConnection(
            "test", "http://127.0.0.1:18099/mcp", "cn-test"
        )
        self.publication: str | None = None
        self.calls: list[str] = []
        self.draft: dict[str, Any] = {
            "dataReferences": [
                {
                    "project": "warehouse",
                    "schema": "default",
                    "table": "orders",
                    "tableId": "physical-reviewed",
                }
            ],
            "glossary": [{"term": "Revenue", "definition": "Paid order amount"}],
        }
        self.published: dict[str, Any] = {}

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        identity = {"name": "sales", "semantic_spec_id": "immutable-sales"}
        if tool == _PUBLISH:
            assert args["source"] == "USER_DRAFT"
            assert args["expected_revision_id"] == "reviewed-draft"
            self.publication = "published-2"
            self.published = copy.deepcopy(self.draft)
            self.published["dataReferences"][0]["tableId"] = "physical-unreviewed"
            return {
                "ok": True,
                "data": {
                    "namespace": "account-1",
                    **identity,
                    "published_revision_id": self.publication,
                },
            }
        assert tool == _GET
        if "source" not in args:
            return {
                "ok": True,
                "data": {
                    "namespace": "account-1",
                    "semantic_spec": {
                        **identity,
                        "user_draft_revision_id": "reviewed-draft",
                        "published_revision_id": self.publication,
                        "system_suggestions_revision_id": None,
                    },
                },
            }
        published = args["source"] == "PUBLISHED"
        if published:
            assert args["revision_id"] == self.publication
        bodies = self.published if published else self.draft
        rows = []
        for section in args["sections"]:
            if section not in bodies:
                rows.append(
                    {
                        "section": section,
                        "present": False,
                        "size_bytes": 0,
                        "content_hash": "",
                    }
                )
                continue
            body = copy.deepcopy(bodies[section])
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            rows.append(
                {
                    "section": section,
                    "present": True,
                    "content": body,
                    "size_bytes": len(raw),
                    "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                }
            )
        spec = {
            **identity,
            "source": args["source"],
            "revision_id": self.publication if published else "reviewed-draft",
            "sections": rows,
        }
        if published:
            spec.update(
                source_revision_id="reviewed-draft", publication_source="USER_DRAFT"
            )
        return {"ok": True, "data": {"namespace": "account-1", "semantic_spec": spec}}


def test_publication_readback_rejects_changed_read_only_physical_identity(
    tmp_path: Path,
) -> None:
    gateway = PublicationGateway()
    sync = SemanticPackageSync(tmp_path / "semantic", gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", SECTIONS)
    before = sync.state_path.read_bytes()

    with pytest.raises(SemanticSyncError):
        sync.publish("reviewed-draft", confirm=True)

    assert gateway.calls.count(_PUBLISH) == 1
    assert gateway.published["dataReferences"][0]["tableId"] == "physical-unreviewed"
    assert sync.state_path.read_bytes() == before


def test_publication_refuses_unreviewed_draft_physical_identity_before_write(
    tmp_path: Path,
) -> None:
    gateway = PublicationGateway()
    sync = SemanticPackageSync(tmp_path / "semantic", gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", SECTIONS)
    before = sync.state_path.read_bytes()
    gateway.draft["dataReferences"][0]["tableId"] = "physical-unreviewed"

    with pytest.raises(SemanticSyncError):
        sync.publish("reviewed-draft", confirm=True)

    assert _PUBLISH not in gateway.calls
    assert sync.state_path.read_bytes() == before


def test_source_document_update_invalidates_confirmed_term_projection(
    tmp_path: Path,
) -> None:
    document = tmp_path / "definition.md"
    document.write_text(
        "# Definition\nRevenue means gross paid amount.\n", encoding="utf-8"
    )
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(
            {
                "kind": "mcs.knowledge_graph",
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "revenue",
                        "kind": "term",
                        "name": "Revenue",
                        "scope": "sales",
                        "status": "candidate",
                        "properties": {"definition": "Gross paid amount"},
                    }
                ],
            }
        )
        source = store.ingest_markdown(document, scope="sales")
        mention = store.neighbors(source["id"], statuses=("candidate",))["edges"][0]
        term = store.show("revenue")
        node = {
            key: value
            for key, value in term.items()
            if key not in {"collection", "document"}
        }
        node["evidence_ids"] = mention["evidence_ids"]
        proposal = store.propose({"kind": "upsert_node", "node": node})
        store.apply(proposal["proposal"]["id"], reviewed_by="definition-owner")
        assert (
            project_semanticspec(store.export_graph(), ["revenue"])["publishable"]
            is True
        )

        old_evidence = store.show(mention["evidence_ids"][0])
        document.write_text(
            "# Definition\nRevenue means net paid amount.\n", encoding="utf-8"
        )
        changed = store.ingest_markdown(document, scope="sales")
        assert changed["version"] != source["version"]
        assert store.show(mention["evidence_ids"][0]) == old_evidence
        report = project_semanticspec(store.export_graph(), ["revenue"])

        assert report["publishable"] is False
        assert report["validation_issues"]


def test_node_review_cannot_commit_dangling_evidence(tmp_path: Path) -> None:
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        proposal = store.propose(
            {
                "kind": "upsert_node",
                "node": {
                    "id": "revenue",
                    "kind": "term",
                    "name": "Revenue",
                    "evidence_ids": ["missing-proof"],
                    "properties": {"definition": "Paid amount"},
                },
            }
        )
        before = store.export_graph()

        with pytest.raises(KnowledgeError):
            store.apply(proposal["proposal"]["id"], reviewed_by="definition-owner")

        assert store.export_graph() == before


def test_draft_relation_stays_candidate_through_store_paths_and_projection(
    tmp_path: Path,
) -> None:
    snapshot = {
        "name": "sales",
        "endpoint": "http://127.0.0.1:18099/mcp",
        "account_id": "account-1",
        "namespace": "account-1",
        "region": "cn-test",
        "semantic_spec_id": "immutable-sales",
        "source": "USER_DRAFT",
        "revision_id": "draft-unreviewed",
        "sections": {
            "semanticModel": {
                "logicalTables": [
                    {"name": "orders", "baseTable": "warehouse.default.orders"},
                    {"name": "customers", "baseTable": "warehouse.default.customers"},
                ],
                "namedFilters": [],
                "approvedRelationships": [
                    {
                        "name": "order_customer",
                        "leftTable": "orders",
                        "rightTable": "customers",
                        "relationshipCardinality": "MANY_TO_ONE",
                        "joinType": "LEFT",
                        "relationshipColumns": [
                            {"leftColumn": "customer_id", "rightColumn": "customer_id"}
                        ],
                        "source": "IMPORTED",
                    }
                ],
            }
        },
    }
    fragment = import_semanticspec(snapshot)
    relation = next(
        edge for edge in fragment["edges"] if edge["predicate"] == "joins_with"
    )
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(fragment)
        assert store.path(relation["subject"], relation["object"])["found"] is False
        candidate = store.path(
            relation["subject"], relation["object"], statuses=("candidate",)
        )
        assert candidate["found"] is True
        assert all(edge["status"] == "candidate" for edge in candidate["edges"])
        report = project_semanticspec(store.export_graph(), [relation["id"]])

        assert report["publishable"] is False
        assert "approvedRelationships" not in report["sections"].get(
            "semanticModel", {}
        )


def test_path_revision_and_claims_share_one_snapshot_during_retraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "knowledge.db"
    with KnowledgeStore(path) as writer:
        writer.import_graph(
            {
                "kind": "mcs.knowledge_graph",
                "schema_version": 1,
                "nodes": [
                    {"id": "orders", "name": "Orders", "kind": "entity"},
                    {"id": "customers", "name": "Customers", "kind": "entity"},
                ],
                "evidence": [
                    {
                        "id": "proof",
                        "source_kind": "user",
                        "source_id": "owner",
                        "source_version": "review-1",
                        "content_hash": "sha256:"
                        + hashlib.sha256(b"review").hexdigest(),
                        "excerpt": "review",
                    }
                ],
                "edges": [
                    {
                        "id": "owner-edge",
                        "subject": "orders",
                        "predicate": "maps_to",
                        "object": "customers",
                        "status": "confirmed",
                        "evidence_ids": ["proof"],
                    }
                ],
            }
        )
        before_revision = writer.meta()["revision"]
        original_connect = sqlite3.connect
        with original_connect(path) as settings:
            settings.execute("PRAGMA journal_mode=WAL")
        connections: list[InterleavingConnection] = []

        def reader_connect(*args: Any, **kwargs: Any) -> InterleavingConnection:
            kwargs["factory"] = InterleavingConnection
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        monkeypatch.setattr(sqlite3, "connect", reader_connect)
        with KnowledgeStore(path, create=False) as reader:
            connections[0].after_edge_read = lambda: writer.retract(
                "owner-edge", reason="source definition changed"
            )
            result = reader.path("orders", "customers")

        assert writer.show("owner-edge")["status"] == "retracted"
        assert writer.meta()["revision"] > before_revision
        assert result["found"] is True
        assert result["edges"][0]["status"] == "confirmed"
        assert result["revision"] == before_revision


def _source_snapshot() -> dict[str, Any]:
    return {
        "name": "sales",
        "endpoint": "https://gateway.invalid/mcp",
        "namespace": "account-1",
        "region": "cn-test",
        "semantic_spec_id": "immutable-sales",
        "source": "USER_DRAFT",
        "revision_id": "source-1",
        "sections": {
            "semanticModel": {
                "logicalTables": [
                    {"name": "orders", "baseTable": "warehouse.default.orders"}
                ],
                "namedFilters": [],
                "approvedRelationships": [],
                "futureNativeField": {"origin": "native-unindexed-v1"},
            },
            "glossary": [{"term": "Revenue", "definition": "Paid amount"}],
        },
    }


def test_reimporting_identical_source_preserves_local_review(tmp_path: Path) -> None:
    original = import_semanticspec(_source_snapshot())
    term = next(node for node in original["nodes"] if node["kind"] == "term")
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(original)
        reviewed = copy.deepcopy(term)
        reviewed["name"] = "Reviewed Revenue"
        proposal = store.propose({"kind": "upsert_node", "node": reviewed})
        store.apply(proposal["proposal"]["id"], reviewed_by="definition-owner")
        before = store.export_graph()

        result = store.import_graph(original)

        assert result["changed"] is False
        assert store.show(term["id"])["name"] == "Reviewed Revenue"
        assert store.show(term["id"])["status"] == "confirmed"
        assert store.export_graph() == before


def test_remote_native_snapshot_and_identity_survive_graph_store_export(
    tmp_path: Path,
) -> None:
    snapshot = _source_snapshot()
    fragment = import_semanticspec(snapshot)
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(fragment)
        exported = store.export_graph()

    source = next(
        item for item in exported["sources"] if item["id"] == fragment["source_id"]
    )
    assert source["source_snapshot"] == {
        key: value for key, value in snapshot.items() if key != "sections"
    }
    assert source["source_sections"] == snapshot["sections"]
    assert source["source_snapshots"] == snapshot["sections"]


def test_source_revision_cannot_be_reused_with_changed_unindexed_native_fields(
    tmp_path: Path,
) -> None:
    snapshot = _source_snapshot()
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(import_semanticspec(snapshot))
        before = store.export_graph()
        changed = copy.deepcopy(snapshot)
        changed["sections"]["semanticModel"]["futureNativeField"]["origin"] = (
            "changed-v2"
        )

        with pytest.raises(KnowledgeError):
            store.import_graph(import_semanticspec(changed))

        assert store.export_graph() == before


def test_small_dependency_cycle_is_blocking_even_beyond_traversal_depth(
    tmp_path: Path,
) -> None:
    count = 12
    fragment = {
        "kind": "mcs.knowledge_graph",
        "schema_version": 1,
        "nodes": [
            {"id": f"n{i}", "name": f"Node {i}", "kind": "entity"} for i in range(count)
        ],
        "evidence": [
            {
                "id": "proof",
                "source_kind": "user",
                "source_id": "owner",
                "source_version": "review-1",
                "content_hash": "sha256:" + hashlib.sha256(b"review").hexdigest(),
                "excerpt": "review",
            }
        ],
        "edges": [
            {
                "id": f"edge-{i}",
                "subject": f"n{i}",
                "predicate": "depends_on",
                "object": f"n{(i + 1) % count}",
                "status": "confirmed",
                "evidence_ids": ["proof"],
            }
            for i in range(count)
        ],
    }
    with KnowledgeStore(tmp_path / "knowledge.db") as store:
        store.import_graph(fragment)
        report = store.check()

    assert report["ok"] is False
    assert any(issue["kind"] == "dependency_cycle" for issue in report["issues"])
