# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Wire model for the local evidence graph; relations are assertions, not proofs.

Nodes have stable IDs independent of names and source locations. An edge is a
reified claim: it has its own ID, review state and immutable evidence references.
Document hashes identify versions, not documents. Package projections may import
fragments (nodes/evidence/edges, with no kb_id); full exports preserve KB identity.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from maxcompute_semantic.errors.base import ErrorCode, McsError

GRAPH_KIND = "mcs.knowledge_graph"
SCHEMA_VERSION = 1
MAX_DEPTH = 10
MAX_LIMIT = 1000
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_GRAPH_ITEMS = 100_000
NODE_KINDS = frozenset(
    {
        "document",
        "term",
        "entity",
        "business_entity",
        "table",
        "column",
        "metric",
        "query",
        "claim",
    }
)
STATUSES = frozenset(
    {"candidate", "confirmed", "stale", "conflict", "rejected", "retracted"}
)
PREDICATES = frozenset(
    {
        "defines",
        "alias_of",
        "represents",
        "derived_from",
        "depends_on",
        "uses",
        "reads",
        "mentions",
        "supports",
        "contradicts",
        "part_of",
        "has_column",
        "maps_to",
        "joins_with",
        "implements",
        "documents",
        "related_to",
    }
)
# Subject depends on object. Impact follows these edges in reverse, never
# arbitrary mentions/support/alias links and never inferred transitive truth.
DEPENDENCY_PREDICATES = frozenset(
    {"derived_from", "depends_on", "uses", "reads", "implements"}
)
COLLECTIONS = (
    "nodes",
    "documents",
    "document_versions",
    "evidence",
    "edges",
    "proposals",
    "sources",
)
INTERPRETATION = "relations are recorded assertions; a graph path is not factual proof and does not establish new facts"


class KnowledgeError(McsError):
    """Classified local graph error, with no backend/profile dependency."""

    code = ErrorCode.KNOWLEDGE

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message, code="KnowledgeError", exit_code=2, **context)


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise KnowledgeError("graph values must be finite JSON values") from exc


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    """Namespaced deterministic ID for source-bound versions/claims/projections."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_json(list(parts))))


def require_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{key} must be a non-empty string")
    return value


def normalize_node(data: Mapping[str, Any]) -> dict[str, Any]:
    node = dict(data)
    for key in ("id", "kind", "name"):
        require_string(node, key)
    if node["kind"] not in NODE_KINDS:
        raise KnowledgeError(f"unsupported node kind: {node['kind']}")
    node.setdefault("scope", "")
    node.setdefault("status", "confirmed")
    node.setdefault("properties", {})
    if not isinstance(node["scope"], str) or not isinstance(node["properties"], dict):
        raise KnowledgeError("node scope must be text and properties must be an object")
    if not isinstance(node["status"], str) or node["status"] not in STATUSES:
        raise KnowledgeError(f"unsupported node status: {node['status']}")
    canonical_json(node)
    return node


def normalize_edge(data: Mapping[str, Any]) -> dict[str, Any]:
    edge = dict(data)
    for key in ("id", "subject", "predicate", "object"):
        require_string(edge, key)
    if edge["predicate"] not in PREDICATES:
        raise KnowledgeError(f"unsupported relation: {edge['predicate']}")
    edge.setdefault("status", "candidate")
    edge.setdefault("evidence_ids", [])
    edge.setdefault("properties", {})
    if not isinstance(edge["status"], str) or edge["status"] not in STATUSES:
        raise KnowledgeError(f"unsupported edge status: {edge['status']}")
    if not isinstance(edge["evidence_ids"], list) or any(
        not isinstance(item, str) or not item for item in edge["evidence_ids"]
    ):
        raise KnowledgeError("edge evidence_ids must be a list of IDs")
    edge["evidence_ids"] = sorted(set(edge["evidence_ids"]))
    if not isinstance(edge["properties"], dict):
        raise KnowledgeError("edge properties must be an object")
    if edge["status"] == "confirmed" and not edge["evidence_ids"]:
        raise KnowledgeError("confirmed relations require pinned evidence")
    canonical_json(edge)
    return edge


def normalize_evidence(data: Mapping[str, Any]) -> dict[str, Any]:
    evidence = dict(data)
    for key in ("id", "source_kind", "source_id", "source_version", "content_hash"):
        require_string(evidence, key)
    if evidence["source_kind"] not in {"document", "package", "user", "semanticspec"}:
        raise KnowledgeError("unsupported evidence source_kind")
    evidence.setdefault("start_line", None)
    evidence.setdefault("end_line", None)
    evidence.setdefault("excerpt", "")
    evidence.setdefault("properties", {})
    if not isinstance(evidence["excerpt"], str) or not isinstance(
        evidence["properties"], dict
    ):
        raise KnowledgeError(
            "evidence excerpt must be text and properties must be an object"
        )
    if evidence["source_kind"] == "document":
        start, end = evidence["start_line"], evidence["end_line"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise KnowledgeError(
                "document evidence requires a valid inclusive line span"
            )
    canonical_json(evidence)
    return evidence


def validate_bounds(*, max_depth: int = 1, limit: int = 50) -> None:
    if type(max_depth) is not int or not 1 <= max_depth <= MAX_DEPTH:
        raise KnowledgeError(f"depth must be between 1 and {MAX_DEPTH}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise KnowledgeError(f"limit must be between 1 and {MAX_LIMIT}")


_ID_SCHEMA = {"type": "string", "minLength": 1}
_RECORD_SCHEMAS: dict[str, Any] = {
    "nodes": {
        "type": "object",
        "required": ["id", "kind", "name", "scope", "status", "properties"],
        "properties": {
            "id": _ID_SCHEMA,
            "kind": {"enum": sorted(NODE_KINDS)},
            "name": _ID_SCHEMA,
            "scope": {"type": "string"},
            "status": {"enum": sorted(STATUSES)},
            "properties": {"type": "object"},
            "evidence_ids": {"type": "array", "items": _ID_SCHEMA, "uniqueItems": True},
        },
    },
    "edges": {
        "type": "object",
        "required": [
            "id",
            "subject",
            "predicate",
            "object",
            "status",
            "evidence_ids",
            "properties",
        ],
        "properties": {
            "id": _ID_SCHEMA,
            "subject": _ID_SCHEMA,
            "object": _ID_SCHEMA,
            "predicate": {"enum": sorted(PREDICATES)},
            "status": {"enum": sorted(STATUSES)},
            "evidence_ids": {"type": "array", "items": _ID_SCHEMA, "uniqueItems": True},
            "properties": {"type": "object"},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "confirmed"}}},
                "then": {"properties": {"evidence_ids": {"minItems": 1}}},
            }
        ],
    },
    "evidence": {
        "type": "object",
        "required": [
            "id",
            "source_kind",
            "source_id",
            "source_version",
            "content_hash",
            "start_line",
            "end_line",
            "excerpt",
            "properties",
        ],
        "properties": {
            "id": _ID_SCHEMA,
            "source_kind": {"enum": ["document", "package", "user", "semanticspec"]},
            "source_id": _ID_SCHEMA,
            "source_version": _ID_SCHEMA,
            "content_hash": _ID_SCHEMA,
            "start_line": {"type": ["integer", "null"], "minimum": 1},
            "end_line": {"type": ["integer", "null"], "minimum": 1},
            "excerpt": {"type": "string"},
            "properties": {"type": "object"},
        },
        "allOf": [
            {
                "if": {"properties": {"source_kind": {"const": "document"}}},
                "then": {
                    "properties": {
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    }
                },
            }
        ],
    },
    "documents": {
        "type": "object",
        "required": ["id", "locator", "current_version"],
        "properties": {
            "id": _ID_SCHEMA,
            "locator": _ID_SCHEMA,
            "current_version": _ID_SCHEMA,
        },
    },
    "document_versions": {
        "type": "object",
        "required": ["id", "document_id", "content_hash", "content"],
        "properties": {
            "id": _ID_SCHEMA,
            "document_id": _ID_SCHEMA,
            "content_hash": _ID_SCHEMA,
            "content": {"type": "string"},
        },
    },
    "proposals": {
        "type": "object",
        "required": [
            "id",
            "status",
            "patch",
            "created_by",
            "base_revision",
            "expected_revision",
            "before_value",
        ],
        "properties": {
            "id": _ID_SCHEMA,
            "status": {"enum": ["candidate", "conflict", "applied", "rejected"]},
            "patch": {"type": "object"},
            "created_by": _ID_SCHEMA,
            "base_revision": {"type": "integer", "minimum": 0},
            "expected_revision": {"type": "integer", "minimum": 1},
        },
    },
    "sources": {
        "type": "object",
        "required": ["id", "current_revision", "known_revisions"],
        "properties": {
            "id": _ID_SCHEMA,
            "current_revision": _ID_SCHEMA,
            "known_revisions": {
                "type": "array",
                "minItems": 1,
                "items": _ID_SCHEMA,
                "uniqueItems": True,
            },
            "snapshot_hash": _ID_SCHEMA,
            "source": _ID_SCHEMA,
            "source_snapshot": {"type": "object"},
            "source_sections": {"type": "object"},
            "source_snapshots": {"type": ["object", "array"]},
            "snapshots": {"type": "object", "additionalProperties": {"type": "object"}},
        },
    },
}

GRAPH_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "mcs local evidence graph",
    "type": "object",
    "required": ["kind", "schema_version", "kb_id", "revision", *COLLECTIONS],
    "properties": {
        "kind": {"const": GRAPH_KIND},
        "schema_version": {"const": SCHEMA_VERSION},
        "kb_id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 0},
        **{
            name: {
                "type": "array",
                "maxItems": MAX_GRAPH_ITEMS,
                "items": _RECORD_SCHEMAS[name],
            }
            for name in COLLECTIONS
        },
    },
}
