# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""SQLite evidence graph with transactional review and bounded traversal.

The store owns local source acquisition and provenance, not SQL parsing or truth
evaluation. Confirmed means an explicitly accepted assertion. Paths carry their
typed edges and evidence; reachability does not entail a new factual statement.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import sqlite3
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar
from urllib.parse import unquote, urlsplit

from maxcompute_semantic.knowledge.markdown import parse_markdown
from maxcompute_semantic.knowledge.model import (
    COLLECTIONS,
    DEPENDENCY_PREDICATES,
    GRAPH_KIND,
    INTERPRETATION,
    MAX_DOCUMENT_BYTES,
    MAX_GRAPH_ITEMS,
    SCHEMA_VERSION,
    STATUSES,
    KnowledgeError,
    canonical_json,
    content_hash,
    normalize_edge,
    normalize_evidence,
    normalize_node,
    require_string,
    stable_id,
    validate_bounds,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, scope TEXT NOT NULL,
    status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS nodes_name_scope ON nodes(name, scope);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_versions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES nodes(id),
    predicate TEXT NOT NULL, object TEXT NOT NULL REFERENCES nodes(id),
    status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edges_subject ON edges(subject, status);
CREATE INDEX IF NOT EXISTS edges_object ON edges(object, status);
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS graph_fts USING fts5(id UNINDEXED, text, tokenize='unicode61');
"""
_UNSET = object()
_SOURCE_METADATA_FIELDS = (
    "source",
    "source_snapshot",
    "source_sections",
    "source_snapshots",
)
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _read_snapshot(
    function: Callable[Concatenate[KnowledgeStore, _P], _R],
) -> Callable[Concatenate[KnowledgeStore, _P], _R]:
    @wraps(function)
    def wrapped(store: KnowledgeStore, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with store._read():
            return function(store, *args, **kwargs)

    return wrapped


class KnowledgeStore:
    """One local KB; the filename has no MaxCompute profile/auth meaning."""

    def __init__(self, path: Path, *, create: bool = True) -> None:
        self.db_path = Path(path)
        if not self.db_path.exists() and not create:
            raise KnowledgeError(
                "knowledge store does not exist", path=str(self.db_path)
            )
        try:
            new_database = not self.db_path.exists() or self.db_path.stat().st_size == 0
            if create:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            if create:
                self._conn = sqlite3.connect(self.db_path, timeout=5)
            else:
                self._conn = sqlite3.connect(
                    f"{self.db_path.resolve().as_uri()}?mode=rw", uri=True, timeout=5
                )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            if create and new_database:
                self._conn.execute("PRAGMA journal_mode=WAL")
            tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables and "meta" not in tables:
                raise KnowledgeError(
                    "file is not a knowledge database", path=str(self.db_path)
                )
            if tables and self.meta()["schema_version"] != SCHEMA_VERSION:
                raise KnowledgeError("unsupported knowledge store schema version")
            if create:
                self._conn.executescript(_SCHEMA)
                with self._conn:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO meta VALUES ('kb_id', ?)",
                        (str(uuid.uuid4()),),
                    )
                    self._conn.execute(
                        "INSERT OR IGNORE INTO meta VALUES ('revision', '0')"
                    )
            if self.meta()["schema_version"] != SCHEMA_VERSION:
                raise KnowledgeError("unsupported knowledge store schema version")
        except (OSError, sqlite3.Error, ValueError) as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise KnowledgeError(
                "cannot open local knowledge store", path=str(self.db_path)
            ) from exc
        except KnowledgeError:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise

    def __enter__(self) -> KnowledgeStore:  # noqa: PYI034 -- Python 3.10 compatibility
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def meta(self) -> dict[str, Any]:
        values = {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM meta")
        }
        if not {"kb_id", "revision", "schema_version"} <= values.keys():
            raise KnowledgeError("invalid knowledge store metadata")
        return {
            "kb_id": values["kb_id"],
            "revision": int(values["revision"]),
            "schema_version": int(values["schema_version"]),
        }

    @contextlib.contextmanager
    def _read(self) -> Iterator[None]:
        owned = not self._conn.in_transaction
        try:
            if owned:
                self._conn.execute("BEGIN")
            yield
        except sqlite3.Error as exc:
            raise KnowledgeError("local knowledge read failed") from exc
        finally:
            if owned:
                self._conn.rollback()

    @contextlib.contextmanager
    def _write(self) -> Iterator[None]:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise KnowledgeError("local knowledge transaction failed") from exc
        except BaseException:
            self._conn.rollback()
            raise

    def _bump(self) -> int:
        revision = self.meta()["revision"] + 1
        self._conn.execute(
            "UPDATE meta SET value=? WHERE key='revision'", (str(revision),)
        )
        return int(revision)

    def _get(self, collection: str, identifier: str) -> dict[str, Any] | None:
        if collection not in COLLECTIONS:
            raise KnowledgeError("unsupported graph collection")
        row = self._conn.execute(
            f"SELECT payload FROM {collection} WHERE id=?", (identifier,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def _all(self, collection: str) -> builtins.list[dict[str, Any]]:
        if collection not in COLLECTIONS:
            raise KnowledgeError("unsupported graph collection")
        return [
            json.loads(row["payload"])
            for row in self._conn.execute(
                f"SELECT payload FROM {collection} ORDER BY id"
            )
        ]

    def _put(self, collection: str, item: Mapping[str, Any]) -> bool:
        identifier = require_string(item, "id")
        if self._get(collection, identifier) == dict(item):
            return False
        payload = canonical_json(dict(item))
        if collection == "nodes":
            self._conn.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name, scope=excluded.scope, status=excluded.status, payload=excluded.payload",
                (
                    identifier,
                    item["kind"],
                    item["name"],
                    item["scope"],
                    item["status"],
                    payload,
                ),
            )
            self._index_node(identifier)
        elif collection == "edges":
            self._conn.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET subject=excluded.subject, predicate=excluded.predicate, object=excluded.object, status=excluded.status, payload=excluded.payload",
                (
                    identifier,
                    item["subject"],
                    item["predicate"],
                    item["object"],
                    item["status"],
                    payload,
                ),
            )
        else:
            self._conn.execute(
                f"INSERT INTO {collection} VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (identifier, payload),
            )
        return True

    def _index_node(self, identifier: str) -> None:
        node = self._get("nodes", identifier)
        self._conn.execute("DELETE FROM graph_fts WHERE id=?", (identifier,))
        if node is None:
            return
        text = f"{node['name']} {node['scope']} {canonical_json(node['properties'])}"
        doc = self._get("documents", identifier)
        if doc:
            version = self._get("document_versions", doc["current_version"])
            if version:
                text += " " + version["content"]
        self._conn.execute("INSERT INTO graph_fts VALUES (?, ?)", (identifier, text))

    def _resolve(self, identifier: str, *, scope: str | None = None) -> str:
        if self._get("nodes", identifier):
            return identifier
        rows = self._conn.execute(
            "SELECT id, scope FROM nodes WHERE name=? COLLATE NOCASE ORDER BY id",
            (identifier,),
        ).fetchall()
        matches = [row["id"] for row in rows if scope is None or row["scope"] == scope]
        if not matches:
            raise KnowledgeError(f"node not found: {identifier}")
        if len(matches) != 1:
            raise KnowledgeError(
                f"ambiguous node name: {identifier}",
                remediation="select an ID or pass an explicit scope",
                candidates=matches,
            )
        return str(matches[0])

    @_read_snapshot
    def list(
        self,
        *,
        kind: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> builtins.list[dict[str, Any]]:
        validate_bounds(limit=limit)
        conditions, args = [], []
        for key, value in (("kind", kind), ("scope", scope), ("status", status)):
            if value is not None:
                conditions.append(f"{key}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._conn.execute(
            f"SELECT payload FROM nodes{where} ORDER BY scope, name, id LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    @_read_snapshot
    def search(
        self, query: str, *, scope: str | None = None, limit: int = 20
    ) -> builtins.list[dict[str, Any]]:
        validate_bounds(limit=limit)
        if not query.strip():
            return []
        match = " OR ".join(
            '"' + token.replace('"', '""') + '"' for token in query.split()
        )
        scope_sql, args = (" AND n.scope=?", [scope]) if scope is not None else ("", [])
        rows = self._conn.execute(
            f"SELECT n.payload, bm25(graph_fts) score FROM graph_fts JOIN nodes n ON n.id=graph_fts.id WHERE graph_fts MATCH ?{scope_sql} ORDER BY score, n.id LIMIT ?",
            (match, *args, limit),
        ).fetchall()
        results = [
            {**json.loads(row["payload"]), "score": -row["score"], "match": "fts"}
            for row in rows
        ]
        # unicode61 keeps a CJK phrase as one token. Substring fallback makes
        # Chinese term lookup useful without a downloaded tokenizer/model.
        if len(results) < limit:
            seen = {row["id"] for row in results}
            escaped = (
                query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            more = self._conn.execute(
                f"SELECT n.payload FROM graph_fts JOIN nodes n ON n.id=graph_fts.id WHERE graph_fts.text LIKE ? ESCAPE '\\'{scope_sql} ORDER BY n.id LIMIT ?",
                (f"%{escaped}%", *args, limit),
            ).fetchall()
            results.extend(
                {**json.loads(row["payload"]), "score": 0.0, "match": "substring"}
                for row in more
                if json.loads(row["payload"])["id"] not in seen
            )
        return results[:limit]

    @_read_snapshot
    def show(self, identifier: str, *, scope: str | None = None) -> dict[str, Any]:
        for collection in COLLECTIONS:
            item = self._get(collection, identifier)
            if item is not None:
                result: dict[str, Any] = {"collection": collection, **item}
                if collection == "nodes":
                    result["document"] = self._get("documents", identifier)
                if collection == "edges":
                    result["evidence"] = [
                        self._get("evidence", eid) for eid in item["evidence_ids"]
                    ]
                return result
        return self.show(self._resolve(identifier, scope=scope))

    @_read_snapshot
    def export_graph(self) -> dict[str, Any]:
        # Consistent read snapshot even when another process writes concurrently.
        return {
            "kind": GRAPH_KIND,
            **self.meta(),
            **{name: self._all(name) for name in COLLECTIONS},
        }

    def _validate_graph(
        self, graph: Mapping[str, builtins.list[dict[str, Any]]]
    ) -> None:
        nodes = {item["id"]: item for item in graph["nodes"]}
        docs = {item["id"]: item for item in graph["documents"]}
        versions = {item["id"]: item for item in graph["document_versions"]}
        evidence = {item["id"]: item for item in graph["evidence"]}
        for node in nodes.values():
            evidence_ids = node.get(
                "evidence_ids", node["properties"].get("evidence_ids", [])
            )
            if not isinstance(evidence_ids, list) or any(
                not isinstance(eid, str) or eid not in evidence for eid in evidence_ids
            ):
                raise KnowledgeError("node evidence does not exist")
        for doc in docs.values():
            if doc["id"] not in nodes or nodes[doc["id"]]["kind"] != "document":
                raise KnowledgeError("document must refer to a document node")
            version = versions.get(require_string(doc, "current_version"))
            if version is None or version.get("document_id") != doc["id"]:
                raise KnowledgeError(
                    "document current_version is missing or belongs to another document"
                )
            require_string(doc, "locator")
        for version in versions.values():
            if version.get("document_id") not in docs or not isinstance(
                version.get("content"), str
            ):
                raise KnowledgeError("document version has no document or content")
            if content_hash(version["content"]) != version.get("content_hash"):
                raise KnowledgeError("document version content hash does not match")
        for item in evidence.values():
            if item["source_kind"] == "document":
                version = versions.get(item["source_version"])
                if version is None or version.get("document_id") != item["source_id"]:
                    raise KnowledgeError("evidence document version is missing")
                lines = version["content"].splitlines()
                if item["end_line"] > len(lines):
                    raise KnowledgeError("evidence line span is outside the document")
                excerpt = "\n".join(lines[item["start_line"] - 1 : item["end_line"]])
                if (
                    excerpt != item["excerpt"]
                    or content_hash(excerpt) != item["content_hash"]
                ):
                    raise KnowledgeError(
                        "document evidence span/hash does not match source"
                    )
        for proposal in graph["proposals"]:
            if not isinstance(proposal.get("status"), str) or proposal[
                "status"
            ] not in {"candidate", "conflict", "applied", "rejected"}:
                raise KnowledgeError("invalid proposal status")
            if not isinstance(proposal.get("patch"), dict):
                raise KnowledgeError("proposal patch must be an object")
            self._patch_target(proposal["patch"])
            if (
                type(proposal.get("base_revision")) is not int
                or type(proposal.get("expected_revision")) is not int
                or proposal["base_revision"] < 0
                or proposal["expected_revision"] < 1
                or "before_value" not in proposal
            ):
                raise KnowledgeError(
                    "proposal requires its base revision and before_value"
                )
        for source in graph["sources"]:
            require_string(source, "current_revision")
            revisions = source.get("known_revisions")
            if (
                not isinstance(revisions, list)
                or any(
                    not isinstance(revision, str) or not revision
                    for revision in revisions
                )
                or source["current_revision"] not in revisions
            ):
                raise KnowledgeError("source requires its current and known revisions")
            snapshots = source.get("snapshots", {})
            if not isinstance(snapshots, dict) or any(
                not isinstance(revision, str)
                or not revision
                or not isinstance(value, dict)
                for revision, value in snapshots.items()
            ):
                raise KnowledgeError(
                    "source snapshots must map revisions to metadata objects"
                )
            for metadata in [source, *snapshots.values()]:
                for field in ("source_snapshot", "source_sections"):
                    if field in metadata and not isinstance(metadata[field], dict):
                        raise KnowledgeError(f"{field} must be an object")
                if "source_snapshots" in metadata and not isinstance(
                    metadata["source_snapshots"], (dict, list)
                ):
                    raise KnowledgeError(
                        "source_snapshots must preserve a section mapping or array"
                    )
        for edge in graph["edges"]:
            if edge["subject"] not in nodes or edge["object"] not in nodes:
                raise KnowledgeError("relation endpoint does not exist")
            if any(eid not in evidence for eid in edge["evidence_ids"]):
                raise KnowledgeError("relation evidence does not exist")

    def import_graph(
        self, mapping: Mapping[str, Any], *, replace: bool = False
    ) -> dict[str, Any]:
        if (
            not isinstance(mapping, Mapping)
            or mapping.get("kind") != GRAPH_KIND
            or mapping.get("schema_version") != SCHEMA_VERSION
        ):
            raise KnowledgeError("unsupported knowledge graph kind or schema_version")
        incoming: dict[str, builtins.list[dict[str, Any]]] = {}
        for name in COLLECTIONS:
            values = mapping.get(name, [])
            if (
                not isinstance(values, list)
                or len(values) > MAX_GRAPH_ITEMS
                or any(not isinstance(item, dict) for item in values)
            ):
                raise KnowledgeError(f"{name} must be a bounded list of objects")
            incoming[name] = [dict(item) for item in values]
            unique: dict[str, dict[str, Any]] = {}
            for item in incoming[name]:
                identifier = require_string(item, "id")
                if identifier in unique and unique[identifier] != item:
                    raise KnowledgeError(
                        f"duplicate IDs in {name} have conflicting payloads"
                    )
                # One structured declaration may mention the same dependency
                # through multiple fields. Replayed identical claims are one
                # graph record; an ID collision never chooses a winning value.
                unique[identifier] = item
            incoming[name] = list(unique.values())
        incoming["nodes"] = [normalize_node(item) for item in incoming["nodes"]]
        incoming["edges"] = [normalize_edge(item) for item in incoming["edges"]]
        incoming["evidence"] = [
            normalize_evidence(item) for item in incoming["evidence"]
        ]
        full = (
            all(name in mapping for name in COLLECTIONS)
            and "kb_id" in mapping
            and not mapping.get("fragment", False)
        )
        if full and (
            type(mapping.get("revision")) is not int or mapping["revision"] < 0
        ):
            raise KnowledgeError("graph revision must be a non-negative integer")
        if full:
            require_string(mapping, "kb_id")
        with self._write():
            current = {name: self._all(name) for name in COLLECTIONS}
            source_changed = False
            if "source_id" in mapping or "source_revision" in mapping:
                source_id = require_string(mapping, "source_id")
                source_revision = require_string(mapping, "source_revision")
                previous_source = self._get("sources", source_id)
                source_metadata = {
                    key: mapping[key]
                    for key in _SOURCE_METADATA_FIELDS
                    if key in mapping
                }
                hash_payload: dict[str, Any] = {
                    name: incoming[name] for name in ("nodes", "edges", "evidence")
                }
                if source_metadata:
                    hash_payload["source_metadata"] = source_metadata
                source_hash = content_hash(canonical_json(hash_payload))
                if (
                    previous_source
                    and source_revision != previous_source["current_revision"]
                    and source_revision in previous_source["known_revisions"]
                ):
                    raise KnowledgeError(
                        "cannot replace the current source with a previously superseded revision"
                    )
                if (
                    previous_source
                    and source_revision == previous_source["current_revision"]
                    and source_hash != previous_source.get("snapshot_hash")
                ):
                    raise KnowledgeError(
                        "source revision was reused for different contents"
                    )
                snapshots = dict((previous_source or {}).get("snapshots", {}))
                snapshots[source_revision] = source_metadata
                same_revision = (
                    previous_source is not None
                    and source_revision == previous_source["current_revision"]
                )
                source_changed = previous_source is not None and not same_revision
                if same_revision:
                    # Source acquisition is not a local review reset. The hash
                    # above compares against the original acquired snapshot,
                    # while local nodes/edges may now carry accepted edits.
                    for name in COLLECTIONS:
                        if name != "sources":
                            incoming[name] = []
                incoming["sources"].append(
                    {
                        "id": source_id,
                        "current_revision": source_revision,
                        "known_revisions": sorted(
                            set(
                                (previous_source or {}).get("known_revisions", [])
                                + [source_revision]
                            )
                        ),
                        "snapshot_hash": source_hash,
                        "snapshots": snapshots,
                        **source_metadata,
                    }
                )
            empty = not any(current.values())
            if replace and not empty:
                raise KnowledgeError(
                    "replacement of a non-empty KB is not supported",
                    remediation="import into a new KB to preserve local work",
                )
            if full and not empty and mapping["kb_id"] != self.meta()["kb_id"]:
                raise KnowledgeError(
                    "cannot merge a different KB identity",
                    remediation="use a fragment without kb_id or a new empty KB",
                )
            merged: dict[str, builtins.list[dict[str, Any]]] = {}
            reviewed_targets = set()
            if source_changed:
                for proposal in current["proposals"]:
                    if proposal["status"] == "applied":
                        reviewed_targets.add(self._patch_target(proposal["patch"]))
            for name in COLLECTIONS:
                existing = {item["id"]: item for item in current[name]}
                for index, item in enumerate(incoming[name]):
                    previous = existing.get(item["id"])
                    if (
                        name in {"evidence", "document_versions"}
                        and previous is not None
                        and previous != item
                    ):
                        raise KnowledgeError(
                            f"immutable {name} ID collision: {item['id']}"
                        )
                    if (
                        name == "nodes"
                        and previous
                        and (previous["kind"], previous["scope"])
                        != (item["kind"], item["scope"])
                    ):
                        raise KnowledgeError(f"node identity collision: {item['id']}")
                    if (
                        source_changed
                        and name in {"nodes", "edges"}
                        and previous
                        and (
                            (name, item["id"]) in reviewed_targets
                            or previous.get("local_review")
                        )
                    ):
                        # Keep the accepted local value and its original pins.
                        # A newer source is an explicit candidate for re-review,
                        # never an implicit replacement of that local decision.
                        reviewed = dict(previous)
                        reviewed["source_candidate"] = dict(item)
                        if reviewed["status"] in {"candidate", "confirmed", "conflict"}:
                            reviewed["status"] = "stale"
                        item = reviewed
                        incoming[name][index] = item
                    existing[item["id"]] = item
                merged[name] = sorted(existing.values(), key=lambda item: item["id"])
            self._validate_graph(merged)
            changed = any(current[name] != merged[name] for name in COLLECTIONS)
            if not changed:
                if full and empty:
                    self._conn.execute(
                        "UPDATE meta SET value=? WHERE key='kb_id'", (mapping["kb_id"],)
                    )
                    self._conn.execute(
                        "UPDATE meta SET value=? WHERE key='revision'",
                        (str(mapping["revision"]),),
                    )
                return {**self.meta(), "changed": False, "imported": 0}
            if full and not empty and mapping["revision"] < self.meta()["revision"]:
                raise KnowledgeError(
                    "cannot overwrite a newer KB with an older revision"
                )
            count = 0
            for name in COLLECTIONS:
                for item in incoming[name]:
                    count += self._put(name, item)
            self._mark_stale()
            self._mark_conflicts()
            if all(current[name] == self._all(name) for name in COLLECTIONS):
                return {**self.meta(), "changed": False, "imported": 0}
            if full and empty:
                self._conn.execute(
                    "UPDATE meta SET value=? WHERE key='kb_id'", (mapping["kb_id"],)
                )
                self._conn.execute(
                    "UPDATE meta SET value=? WHERE key='revision'",
                    (str(mapping["revision"]),),
                )
            else:
                self._bump()
            for node in incoming["nodes"]:
                self._index_node(node["id"])
            return {**self.meta(), "changed": True, "imported": count}

    def _mark_stale(self) -> int:
        stale_evidence = set()
        sources = {
            source["id"]: source["current_revision"] for source in self._all("sources")
        }
        for evidence in self._all("evidence"):
            if evidence["source_kind"] == "document":
                document = self._get("documents", evidence["source_id"])
                if (
                    document
                    and document["current_version"] != evidence["source_version"]
                ):
                    stale_evidence.add(evidence["id"])
            elif (
                evidence["source_id"] in sources
                and sources[evidence["source_id"]] != evidence["source_version"]
            ):
                stale_evidence.add(evidence["id"])
        count = 0
        for node in self._all("nodes"):
            evidence_ids = node.get(
                "evidence_ids", node["properties"].get("evidence_ids", [])
            )
            if node["status"] in {
                "confirmed",
                "candidate",
                "conflict",
            } and stale_evidence.intersection(evidence_ids):
                node["status"] = "stale"
                count += self._put("nodes", node)
        for edge in self._all("edges"):
            if edge["status"] in {
                "confirmed",
                "candidate",
                "conflict",
            } and stale_evidence.intersection(edge["evidence_ids"]):
                edge["status"] = "stale"
                count += self._put("edges", edge)
        return count

    def _mark_conflicts(self) -> int:
        groups: dict[str, builtins.list[dict[str, Any]]] = {}
        for edge in self._all("edges"):
            key = edge["properties"].get("conflict_key")
            if key and edge["status"] in {"confirmed", "conflict"}:
                groups.setdefault(str(key), []).append(edge)
        count = 0
        for edges in groups.values():
            if len({edge["object"] for edge in edges}) > 1:
                for edge in edges:
                    edge["status"] = "conflict"
                    count += self._put("edges", edge)
        return count

    def _evidence_span(
        self,
        doc_id: str,
        version_id: str,
        lines: builtins.list[str],
        start: int,
        end: int,
    ) -> dict[str, Any]:
        excerpt = "\n".join(lines[start - 1 : end])
        return normalize_evidence(
            {
                "id": stable_id("evidence", doc_id, version_id, str(start), str(end)),
                "source_kind": "document",
                "source_id": doc_id,
                "source_version": version_id,
                "start_line": start,
                "end_line": end,
                "excerpt": excerpt,
                "content_hash": content_hash(excerpt),
            }
        )

    def ingest_markdown(self, path: Path, *, scope: str = "") -> dict[str, Any]:
        path = Path(path)
        if path.is_dir():
            files = sorted(path.rglob("*.md"))
            if len(files) > MAX_GRAPH_ITEMS:
                raise KnowledgeError("too many Markdown documents")
            results = [self.ingest_markdown(item, scope=scope) for item in files]
            return {
                **self.meta(),
                "documents": results,
                "changed": any(result["changed"] for result in results),
            }
        try:
            with path.open("rb") as stream:
                raw_content = stream.read(MAX_DOCUMENT_BYTES + 1)
            if len(raw_content) > MAX_DOCUMENT_BYTES:
                raise KnowledgeError("Markdown document exceeds the local size limit")
            text = raw_content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise KnowledgeError(
                "cannot read Markdown document", path=str(path)
            ) from exc
        parsed = parse_markdown(text)
        metadata = parsed["metadata"]
        locator = str(path.resolve())
        scope = metadata.get("scope", scope)
        if not isinstance(scope, str):
            raise KnowledgeError("document scope must be text")
        digest = content_hash(text)
        with self._write():
            documents = self._all("documents")
            known = next(
                (item for item in documents if item["locator"] == locator), None
            )
            requested_id = metadata.get("document_id")
            if requested_id is not None and (
                not isinstance(requested_id, str) or not requested_id
            ):
                raise KnowledgeError("document_id must be a non-empty string")
            if requested_id:
                matching = self._get("documents", requested_id)
                if known and known["id"] != requested_id:
                    raise KnowledgeError(
                        "document identity cannot be changed at an existing locator"
                    )
                known = matching or known
            if known is None and requested_id is None:
                # A unique vanished locator with identical bytes is a local move,
                # not a duplicate document that happens to contain the same text.
                moved = [
                    item
                    for item in documents
                    if not Path(item["locator"]).exists()
                    and (
                        self._get("document_versions", item["current_version"]) or {}
                    ).get("content_hash")
                    == digest
                ]
                if len(moved) == 1:
                    known = moved[0]
            placeholders = [
                item
                for item in self._all("nodes")
                if item["kind"] == "document"
                and item["properties"].get("locator") == locator
            ]
            placeholder_id = placeholders[0]["id"] if len(placeholders) == 1 else None
            doc_id = requested_id or (
                known["id"] if known else placeholder_id or str(uuid.uuid4())
            )
            previous_identity = self._get("nodes", doc_id)
            if previous_identity and previous_identity["scope"] != scope:
                raise KnowledgeError(
                    "document scope cannot change at an existing identity"
                )
            version_id = stable_id("document_version", doc_id, digest)
            if (
                known
                and known["current_version"] == version_id
                and known["locator"] == locator
            ):
                return {
                    **self.meta(),
                    "id": doc_id,
                    "version": version_id,
                    "changed": False,
                    "candidates": 0,
                }
            lines = text.splitlines()
            # Empty docs still have one addressable line for explicit metadata.
            title = parsed["headings"][0]["title"] if parsed["headings"] else path.stem
            node = normalize_node(
                {
                    "id": doc_id,
                    "kind": "document",
                    "name": title,
                    "scope": scope,
                    "properties": {"locator": locator, "headings": parsed["headings"]},
                }
            )
            previous_node = self._get("nodes", doc_id)
            if previous_node and previous_node["kind"] != "document":
                raise KnowledgeError("document ID collides with another node kind")
            self._put("nodes", node)
            self._put(
                "documents",
                {"id": doc_id, "locator": locator, "current_version": version_id},
            )
            self._put(
                "document_versions",
                {
                    "id": version_id,
                    "document_id": doc_id,
                    "content_hash": digest,
                    "content": text,
                },
            )
            new_nodes = metadata.get("nodes", [])
            node_evidence = None
            if new_nodes:
                node_evidence = self._evidence_span(
                    doc_id, version_id, lines, 1, max(1, parsed["body_start"])
                )
                self._put("evidence", node_evidence)
            for raw in new_nodes:
                if not isinstance(raw, dict):
                    raise KnowledgeError("frontmatter nodes must be objects")
                item = normalize_node({"scope": scope, "status": "candidate", **raw})
                # Frontmatter is source material, never a review acceptance.
                item["status"] = "candidate"
                if node_evidence:
                    item["evidence_ids"] = [node_evidence["id"]]
                old = self._get("nodes", item["id"])
                if old and (old["kind"], old["scope"]) != (item["kind"], item["scope"]):
                    raise KnowledgeError("frontmatter node identity collision")
                if old is None:
                    self._put("nodes", item)
            candidates = 0
            relations = metadata.get("relations", [])
            for raw in relations:
                if not isinstance(raw, dict):
                    raise KnowledgeError("frontmatter relations must be objects")
                start, end = (
                    raw.get("start_line", 1),
                    raw.get("end_line", max(1, parsed["body_start"])),
                )
                if (
                    type(start) is not int
                    or type(end) is not int
                    or start < 1
                    or end < start
                    or end > len(lines)
                ):
                    raise KnowledgeError(
                        "frontmatter relation has invalid evidence span"
                    )
                evidence = self._evidence_span(doc_id, version_id, lines, start, end)
                edge = normalize_edge(
                    {
                        **raw,
                        "id": stable_id(
                            "markdown_relation",
                            doc_id,
                            version_id,
                            str(raw.get("id") or canonical_json(raw)),
                        ),
                        "status": "candidate",
                        "evidence_ids": [evidence["id"]],
                    }
                )
                if not self._get("nodes", edge["subject"]) or not self._get(
                    "nodes", edge["object"]
                ):
                    raise KnowledgeError("frontmatter relation endpoint does not exist")
                self._put("evidence", evidence)
                candidates += self._put("edges", edge)
            for link in parsed["links"]:
                target = link["target"]
                if target.startswith("kb://"):
                    ref = target.removeprefix("kb://")
                    target_id = self._resolve(ref, scope=scope)
                else:
                    try:
                        parsed_target = urlsplit(target)
                    except ValueError as exc:
                        raise KnowledgeError("invalid Markdown link locator") from exc
                    if not parsed_target.path and not parsed_target.netloc:
                        continue
                    target_locator = (
                        target.split("#", 1)[0]
                        if parsed_target.scheme or parsed_target.netloc
                        else str((path.parent / unquote(parsed_target.path)).resolve())
                    )
                    link_matches = [
                        item
                        for item in self._all("nodes")
                        if item["kind"] == "document"
                        and item["properties"].get("locator") == target_locator
                        and item["scope"] == scope
                    ]
                    target_id = (
                        link_matches[0]["id"]
                        if link_matches
                        else stable_id("document_link_target", scope, target_locator)
                    )
                    if not link_matches:
                        self._put(
                            "nodes",
                            normalize_node(
                                {
                                    "id": target_id,
                                    "kind": "document",
                                    "name": link["label"],
                                    "scope": scope,
                                    "status": "candidate",
                                    "properties": {
                                        "locator": target_locator,
                                        "acquired": False,
                                    },
                                }
                            ),
                        )
                evidence = self._evidence_span(
                    doc_id, version_id, lines, link["line"], link["line"]
                )
                self._put("evidence", evidence)
                edge = normalize_edge(
                    {
                        "id": stable_id(
                            "markdown_link",
                            doc_id,
                            version_id,
                            target_id,
                            str(link["line"]),
                        ),
                        "subject": doc_id,
                        "predicate": "mentions",
                        "object": target_id,
                        "status": "candidate",
                        "evidence_ids": [evidence["id"]],
                        "properties": {"method": "markdown_link", "target": target},
                    }
                )
                candidates += self._put("edges", edge)
            # Literal matches are only candidate mentions within the same scope.
            for target_node in self._all("nodes"):
                if (
                    target_node["scope"] != scope
                    or target_node["id"] == doc_id
                    or target_node["kind"] == "document"
                ):
                    continue
                for line_number, line in enumerate(
                    lines[parsed["body_start"] :], parsed["body_start"] + 1
                ):
                    if target_node["name"] not in line:
                        continue
                    evidence = self._evidence_span(
                        doc_id, version_id, lines, line_number, line_number
                    )
                    self._put("evidence", evidence)
                    edge = normalize_edge(
                        {
                            "id": stable_id(
                                "name_candidate",
                                doc_id,
                                version_id,
                                target_node["id"],
                                str(line_number),
                            ),
                            "subject": doc_id,
                            "predicate": "mentions",
                            "object": target_node["id"],
                            "status": "candidate",
                            "evidence_ids": [evidence["id"]],
                            "properties": {"method": "literal_name_match"},
                        }
                    )
                    candidates += self._put("edges", edge)
            stale = self._mark_stale()
            self._index_node(doc_id)
            self._bump()
            return {
                **self.meta(),
                "id": doc_id,
                "version": version_id,
                "changed": True,
                "candidates": candidates,
                "stale": stale,
            }

    def _patch_target(self, patch: Mapping[str, Any]) -> tuple[str, str]:
        kind = patch.get("kind")
        if kind == "upsert_node" and isinstance(patch.get("node"), dict):
            return "nodes", require_string(patch["node"], "id")
        if kind == "upsert_edge" and isinstance(patch.get("edge"), dict):
            return "edges", require_string(patch["edge"], "id")
        if kind == "retract_edge":
            return "edges", require_string(patch, "id")
        raise KnowledgeError(
            "unsupported proposal patch",
            remediation="use upsert_node, upsert_edge, or retract_edge",
        )

    def propose(
        self,
        patch: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
        before_value: Any = _UNSET,
        created_by: str = "user",
    ) -> dict[str, Any]:
        collection, target = self._patch_target(patch)
        with self._write():
            revision = self.meta()["revision"]
            if expected_revision is not None and expected_revision != revision:
                raise KnowledgeError("proposal base revision has changed")
            before = self._get(collection, target)
            if before_value is not _UNSET and before_value != before:
                raise KnowledgeError("proposal before_value does not match the target")
            proposal = {
                "id": str(uuid.uuid4()),
                "status": "candidate",
                "patch": dict(patch),
                "created_by": created_by,
                "base_revision": revision,
                "expected_revision": revision + 1,
                "before_value": before,
            }
            canonical_json(proposal)
            self._put("proposals", proposal)
            self._bump()
            return {**self.meta(), "proposal": proposal}

    def _apply_patch(self, patch: Mapping[str, Any]) -> None:
        collection, identifier = self._patch_target(patch)
        if patch["kind"] == "retract_edge":
            edge = self._get("edges", identifier)
            if not edge:
                raise KnowledgeError("relation to retract does not exist")
            edge["status"] = "retracted"
            edge["properties"]["retraction_reason"] = patch.get("reason", "")
            self._put("edges", edge)
            return
        if collection == "nodes":
            node = normalize_node(patch["node"])
            previous = self._get("nodes", identifier)
            if previous and (previous["kind"], previous["scope"]) != (
                node["kind"],
                node["scope"],
            ):
                raise KnowledgeError("proposal cannot change node identity")
            node["status"] = "confirmed"
            self._put("nodes", node)
        else:
            edge = normalize_edge({**patch["edge"], "status": "confirmed"})
            nodes = self._all("nodes")
            self._validate_graph(
                {
                    "nodes": nodes,
                    "documents": self._all("documents"),
                    "document_versions": self._all("document_versions"),
                    "evidence": self._all("evidence"),
                    "edges": [edge],
                    "proposals": [],
                    "sources": self._all("sources"),
                }
            )
            self._put("edges", edge)
            self._mark_stale()
            if (self._get("edges", identifier) or {}).get("status") == "stale":
                raise KnowledgeError("proposal evidence is stale")

    def apply(self, identifier: str, *, reviewed_by: str = "user") -> dict[str, Any]:
        conflict = False
        with self._write():
            proposal = self._get("proposals", identifier)
            if proposal is None or proposal["status"] != "candidate":
                raise KnowledgeError(
                    "only an existing candidate proposal can be applied"
                )
            collection, target = self._patch_target(proposal["patch"])
            if proposal["expected_revision"] != self.meta()["revision"] or proposal[
                "before_value"
            ] != self._get(collection, target):
                proposal.update(
                    status="conflict",
                    reviewed_by=reviewed_by,
                    reason="revision or target changed",
                )
                self._put("proposals", proposal)
                self._bump()
                conflict = True
            else:
                self._apply_patch(proposal["patch"])
                self._validate_graph({name: self._all(name) for name in COLLECTIONS})
                self._mark_stale()
                if (self._get(collection, target) or {}).get("status") == "stale":
                    raise KnowledgeError("proposal evidence is stale")
                reviewed_item = self._get(collection, target)
                if reviewed_item is not None:
                    reviewed_item["local_review"] = {
                        "proposal_id": identifier,
                        "reviewed_by": reviewed_by,
                        "revision": self.meta()["revision"] + 1,
                    }
                    reviewed_item.pop("source_candidate", None)
                    self._put(collection, reviewed_item)
                self._mark_conflicts()
                proposal.update(status="applied", reviewed_by=reviewed_by)
                self._put("proposals", proposal)
                self._bump()
        if conflict:
            raise KnowledgeError(
                "proposal conflicts with the current KB revision",
                proposal_id=identifier,
            )
        return {**self.meta(), "applied": True, "proposal": proposal}

    def reject(
        self, identifier: str, *, reason: str = "", reviewed_by: str = "user"
    ) -> dict[str, Any]:
        with self._write():
            proposal = self._get("proposals", identifier)
            if proposal is None or proposal["status"] not in {"candidate", "conflict"}:
                raise KnowledgeError("only a pending proposal can be rejected")
            proposal.update(status="rejected", reviewed_by=reviewed_by, reason=reason)
            self._put("proposals", proposal)
            self._bump()
        return {**self.meta(), "rejected": True, "proposal": proposal}

    def retract(self, identifier: str, *, reason: str = "") -> dict[str, Any]:
        with self._write():
            edge = self._get("edges", identifier)
            if edge is None:
                raise KnowledgeError("relation not found")
            if edge["status"] == "retracted":
                return {**self.meta(), "changed": False, "edge": edge}
            edge["status"] = "retracted"
            edge["properties"]["retraction_reason"] = reason
            self._put("edges", edge)
            self._bump()
        return {**self.meta(), "changed": True, "edge": edge}

    def _adjacent(
        self,
        identifier: str,
        direction: str,
        statuses: Sequence[str],
        limit: int,
        predicates: Sequence[str] | None = None,
    ) -> builtins.list[dict[str, Any]]:
        if (
            direction not in {"in", "out", "both"}
            or not statuses
            or any(status not in STATUSES for status in statuses)
        ):
            raise KnowledgeError("invalid graph direction or statuses")
        endpoint = (
            "subject=?"
            if direction == "out"
            else "object=?"
            if direction == "in"
            else "(subject=? OR object=?)"
        )
        values = (identifier, identifier) if direction == "both" else (identifier,)
        placeholders = ",".join("?" for _ in statuses)
        predicate_sql = ""
        predicate_values: Sequence[str] = ()
        if predicates is not None:
            predicate_sql = (
                " AND predicate IN (" + ",".join("?" for _ in predicates) + ")"
            )
            predicate_values = predicates
        rows = self._conn.execute(
            f"SELECT payload FROM edges WHERE {endpoint} AND status IN ({placeholders}){predicate_sql} ORDER BY id LIMIT ?",
            (*values, *statuses, *predicate_values, limit),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    @_read_snapshot
    def neighbors(
        self,
        identifier: str,
        *,
        direction: str = "both",
        statuses: Sequence[str] = ("confirmed",),
        limit: int = 50,
    ) -> dict[str, Any]:
        validate_bounds(limit=limit)
        identifier = self._resolve(identifier)
        edges = self._adjacent(identifier, direction, statuses, limit + 1)
        truncated = len(edges) > limit
        edges = edges[:limit]
        ids = {
            edge["object"] if edge["subject"] == identifier else edge["subject"]
            for edge in edges
        }
        return {
            **self.meta(),
            "node": self._get("nodes", identifier),
            "nodes": [self._get("nodes", nid) for nid in sorted(ids)],
            "edges": edges,
            "truncated": truncated,
            "interpretation": INTERPRETATION,
        }

    @_read_snapshot
    def path(
        self,
        start: str,
        end: str,
        *,
        max_depth: int = 4,
        limit: int = 100,
        statuses: Sequence[str] = ("confirmed",),
    ) -> dict[str, Any]:
        validate_bounds(max_depth=max_depth, limit=limit)
        start, end = self._resolve(start), self._resolve(end)
        queue: deque[tuple[str, builtins.list[str], builtins.list[dict[str, Any]]]] = (
            deque([(start, [start], [])])
        )
        visited, examined, truncated = {start}, 0, False
        while queue:
            current, nodes, edges = queue.popleft()
            if current == end:
                return {
                    **self.meta(),
                    "found": True,
                    "nodes": [self._get("nodes", nid) for nid in nodes],
                    "edges": edges,
                    "truncated": truncated,
                    "interpretation": INTERPRETATION,
                }
            if len(edges) >= max_depth:
                continue
            adjacent = self._adjacent(current, "out", statuses, limit - examined + 1)
            for edge in adjacent:
                if examined >= limit:
                    truncated = True
                    break
                examined += 1
                target = edge["object"]
                if target == end:
                    return {
                        **self.meta(),
                        "found": True,
                        "nodes": [self._get("nodes", nid) for nid in [*nodes, target]],
                        "edges": [*edges, edge],
                        "truncated": False,
                        "interpretation": INTERPRETATION,
                    }
                if target not in visited:
                    visited.add(target)
                    queue.append((target, [*nodes, target], [*edges, edge]))
            if truncated:
                break
        return {
            **self.meta(),
            "found": False,
            "nodes": [],
            "edges": [],
            "truncated": truncated,
            "interpretation": INTERPRETATION,
        }

    @_read_snapshot
    def impact(
        self, identifier: str, *, max_depth: int = 4, limit: int = 100
    ) -> dict[str, Any]:
        validate_bounds(max_depth=max_depth, limit=limit)
        identifier = self._resolve(identifier)
        queue: deque[tuple[str, int]] = deque([(identifier, 0)])
        visited, truncated = {identifier}, False
        edges: builtins.list[dict[str, Any]] = []
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._adjacent(
                current, "in", ("confirmed",), limit + 1, sorted(DEPENDENCY_PREDICATES)
            ):
                if len(edges) >= limit:
                    truncated = True
                    break
                edges.append(edge)
                subject = edge["subject"]
                if subject not in visited:
                    visited.add(subject)
                    queue.append((subject, depth + 1))
            if truncated:
                break
        return {
            **self.meta(),
            "root": identifier,
            "nodes": [
                self._get("nodes", nid) for nid in sorted(visited - {identifier})
            ],
            "edges": edges,
            "truncated": truncated,
            "interpretation": "potential dependency impact; reachability does not prove a failure or factual claim",
        }

    @_read_snapshot
    def check(self) -> dict[str, Any]:
        graph = self.export_graph()
        issues: builtins.list[dict[str, Any]] = []
        try:
            self._validate_graph(graph)
        except KnowledgeError as exc:
            issues.append({"kind": "invalid_reference", "message": exc.message})
        edges = graph["edges"]
        connected = {
            edge[key]
            for edge in edges
            for key in ("subject", "object")
            if edge["status"] == "confirmed"
        }
        issues.extend(
            {"kind": "isolated_node", "id": node["id"]}
            for node in graph["nodes"]
            if node["id"] not in connected
        )
        issues.extend(
            {"kind": edge["status"], "id": edge["id"]}
            for edge in edges
            if edge["status"] in {"stale", "conflict"}
        )
        issues.extend(
            {"kind": "missing_evidence", "id": edge["id"]}
            for edge in edges
            if not edge["evidence_ids"]
        )
        # Full iterative DFS: traversal depth limits used by path/impact must not
        # make the integrity gate silently miss long dependency cycles.
        adjacency: dict[str, builtins.list[str]] = {}
        for edge in edges:
            if (
                edge["status"] == "confirmed"
                and edge["predicate"] in DEPENDENCY_PREDICATES
            ):
                adjacency.setdefault(edge["subject"], []).append(edge["object"])
        colors: dict[str, int] = {}
        for start in sorted(adjacency):
            if colors.get(start, 0):
                continue
            colors[start] = 1
            chain = [start]
            positions = {start: 0}
            stack = [(start, iter(adjacency.get(start, [])))]
            while stack:
                current, targets = stack[-1]
                target = next(targets, None)
                if target is None:
                    colors[current] = 2
                    positions.pop(current)
                    chain.pop()
                    stack.pop()
                elif colors.get(target, 0) == 1:
                    issues.append(
                        {
                            "kind": "dependency_cycle",
                            "path": [*chain[positions[target] :], target],
                        }
                    )
                elif colors.get(target, 0) == 0:
                    colors[target] = 1
                    positions[target] = len(chain)
                    chain.append(target)
                    stack.append((target, iter(adjacency.get(target, []))))
        return {
            **self.meta(),
            "issues": issues,
            "complete": True,
            "ok": not any(
                item["kind"]
                in {
                    "invalid_reference",
                    "stale",
                    "conflict",
                    "missing_evidence",
                    "dependency_cycle",
                }
                for item in issues
            ),
            "interpretation": INTERPRETATION,
        }
