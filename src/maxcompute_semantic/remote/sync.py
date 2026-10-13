# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Revision-bound SemanticSpec synchronization, independent of MC profiles.

The sidecar retains native section JSON, including fields mcs does not map.
It never uploads package databases, sampled rows, credentials, or query logs.
Every mutation needs an exact reviewed plan or an explicitly confirmed draft
revision, and advances local state only after complete remote read-back.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from maxcompute_semantic.errors import ErrorCode, McsError

SECTIONS = (
    "dataReferences", "semanticModel", "instructions", "metricDefinitions",
    "verifiedQueries", "glossary", "benchmarks",
)
SOURCES = ("PUBLISHED", "USER_DRAFT", "SYSTEM_SUGGESTIONS")
_REVISION_FIELDS = {
    "PUBLISHED": "published_revision_id",
    "USER_DRAFT": "user_draft_revision_id",
    "SYSTEM_SUGGESTIONS": "system_suggestions_revision_id",
}
_ARRAY_KEYS = {"metricDefinitions": "name", "verifiedQueries": "name", "glossary": "term"}
_MODEL_KEYS = ("logicalTables", "namedFilters", "approvedRelationships")
_STATE_VERSION = 1
_MAX_STATE_BYTES = 32 * 1024 * 1024
_GET = "maxcompute_semanticspec_get"
_UPDATE = "maxcompute_semanticspec_update"
_PUBLISH = "maxcompute_semanticspec_publish"

# Native JSON order of Gateway Catalog SDK SemanticSpec DTOs. Section hashes
# use encoding/json on these structs, before MCP projection can alphabetically
# reorder their JSON keys. No values or unknown fields may be dropped.
_NATIVE_FIELD_ORDER = {
    "DataReference": ("project", "schema", "table", "tableId"),
    "SemanticModel": ("logicalTables", "namedFilters", "approvedRelationships"),
    "Instructions": ("generalGuidance", "dialectNotes", "supportedFunctions", "unsupportedStandardSql", "defaultFilters", "timeConventions", "businessRules"),
    "MetricDefinition": ("name", "description", "usageGuidance", "category", "semanticType", "baseLogicalTable", "expr", "dataType", "defaultAggregation", "grain", "timeDimension", "synonyms", "filterRefs", "defaultFilters", "typicalQuestions", "caveats", "additivityType", "nonAdditiveDimensions", "semiAdditiveAggregation", "derivedMetric", "format", "visibility", "tags"),
    "VerifiedQuery": ("name", "question", "usageGuidance", "trustLevel", "matchPolicy", "sql", "sqlTemplate", "parameters", "typicalQuestions", "trusted", "verifiedAt", "verifiedBy", "provenance", "costEstimate", "questionProvenance"),
    "GlossaryTerm": ("term", "definition", "alsoKnownAs", "applicableTables", "linkedMetric", "linkedDimension", "usage"),
    "Benchmarks": ("questions",),
    "LogicalTable": ("name", "baseTable", "description", "usageGuidance", "synonyms", "primaryKey", "dimensions", "timeDimensions"),
    "NamedFilter": ("name", "description", "usageGuidance", "sqlFragment", "applicableTables"),
    "ApprovedRelationship": ("relationshipId", "name", "leftTable", "rightTable", "relationshipCardinality", "joinType", "relationshipColumns", "joinCondition", "usageGuidance", "source", "sourceDetail"),
    "DefaultFilter": ("scope", "table", "sqlFragment", "description"),
    "BusinessRule": ("id", "description", "affectedMetrics"),
    "DerivedMetric": ("formula", "refMetrics", "resultDataType"),
    "MetricFormat": ("type", "currencyCode", "decimalPlaces", "thousandsSeparator", "unit", "scaleHint"),
    "MatchPolicy": ("requireExactQuestion", "similarityThreshold", "fallbackOnMiss"),
    "VqParameter": ("name", "type", "required", "defaultValue", "description", "constraints"),
    "CostEstimate": ("method", "complexity", "udf", "inputBytes", "reference"),
    "BenchmarkQuestion": ("id", "status", "question", "expectedSemantics", "expectedSql", "expectedResultMatchMode", "tags"),
    "LogicalTablePrimaryKey": ("columns",),
    "Dimension": ("name", "expr", "dataType", "description", "usageGuidance", "synonyms", "sampleValues", "isEnum", "enumValues", "visibility", "tags"),
    "TimeDimension": ("name", "expr", "dataType", "description", "usageGuidance", "visibility"),
    "RelationshipColumn": ("leftColumn", "rightColumn"),
    "RelationshipSourceDetail": ("type", "datascanName", "jobId", "detectedSources", "confidence"),
    "VqParameterConstraints": ("minValue", "maxValue", "multipleOf", "pattern", "minLength", "maxLength", "enumValues", "format", "notBefore", "notAfter", "nullable"),
    "ExpectedSemantics": ("metricName", "dimensionNames", "timeWindow", "filters", "dimensionFilters"),
    "EnumValue": ("value", "labelCn", "synonyms"),
    "TimeWindow": ("type", "value"),
    "DimensionFilter": ("dimensionName", "op", "value"),
}
_NATIVE_CHILDREN = {
    "SemanticModel": {"logicalTables": "[]LogicalTable", "namedFilters": "[]NamedFilter", "approvedRelationships": "[]ApprovedRelationship"},
    "Instructions": {"defaultFilters": "[]DefaultFilter", "businessRules": "[]BusinessRule"},
    "MetricDefinition": {"defaultFilters": "[]DefaultFilter", "derivedMetric": "DerivedMetric", "format": "MetricFormat"},
    "VerifiedQuery": {"matchPolicy": "MatchPolicy", "parameters": "[]VqParameter", "costEstimate": "CostEstimate"},
    "Benchmarks": {"questions": "[]BenchmarkQuestion"},
    "LogicalTable": {"primaryKey": "LogicalTablePrimaryKey", "dimensions": "[]Dimension", "timeDimensions": "[]TimeDimension"},
    "ApprovedRelationship": {"relationshipColumns": "[]RelationshipColumn", "sourceDetail": "RelationshipSourceDetail"},
    "VqParameter": {"constraints": "VqParameterConstraints"},
    "BenchmarkQuestion": {"expectedSemantics": "ExpectedSemantics"},
    "Dimension": {"enumValues": "[]EnumValue"},
    "ExpectedSemantics": {"timeWindow": "TimeWindow", "dimensionFilters": "[]DimensionFilter"},
}
_NATIVE_SECTION_TYPES = {
    "dataReferences": "[]DataReference", "semanticModel": "SemanticModel",
    "instructions": "Instructions", "metricDefinitions": "[]MetricDefinition",
    "verifiedQueries": "[]VerifiedQuery", "glossary": "[]GlossaryTerm", "benchmarks": "Benchmarks",
}


class ConnectionIdentity(Protocol):
    @property
    def alias(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def region(self) -> str: ...


class SemanticClient(Protocol):
    @property
    def connection(self) -> ConnectionIdentity: ...

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]: ...


class SemanticSyncError(McsError):
    """Safe, classified synchronization failure; never an acceptance result."""

    code = ErrorCode.SEMANTIC_SYNC


def _error(code: str, message: str, **context: Any) -> SemanticSyncError:
    return SemanticSyncError(
        message, code=code, exit_code=2,
        remediation="inspect the binding and complete section diff; reread before retrying",
        **context,
    )


def _json_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=sort_keys,
    ).encode("utf-8")


def _go_json(value: Any) -> str:
    """Match encoding/json's representation of the received section body.

    Received property order is retained: Catalog structures have declaration
    order, unlike Go maps. Go's HTML escaping and float notation differ from
    Python's default encoder and are part of Gateway's byte/hash contract.
    """
    if isinstance(value, dict):
        return "{" + ",".join(_go_json(k) + ":" + _go_json(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_go_json(item) for item in value) + "]"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("SemanticIntegrity", "section contains a non-finite number")
        if value == 0:
            return "-0" if math.copysign(1, value) < 0 else "0"
        raw = repr(value)
        if 1e-6 <= abs(value) < 1e21:
            return format(Decimal(raw), "f").removesuffix(".0")
        if "e" in raw:
            mantissa, exponent = raw.split("e")
            sign = "+" if int(exponent) >= 0 else "-"
            return mantissa.removesuffix(".0") + "e" + sign + str(abs(int(exponent)))
        return raw
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for character, escaped in (
        ("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
        ("\u2028", "\\u2028"), ("\u2029", "\\u2029"),
    ):
        raw = raw.replace(character, escaped)
    return raw


def section_integrity(content: Any) -> tuple[int, str]:
    """Gateway-compatible size and SHA256 for native JSON section content."""
    raw = _go_json(content).encode("utf-8")
    return len(raw), "sha256:" + hashlib.sha256(raw).hexdigest()


def _native_order(content: Any, kind: str = "") -> Any:
    if isinstance(content, list):
        element = kind.removeprefix("[]") if kind.startswith("[]") else ""
        return [_native_order(value, element) for value in content]
    if not isinstance(content, dict):
        return content
    field_order = _NATIVE_FIELD_ORDER.get(kind, ())
    children = _NATIVE_CHILDREN.get(kind, {})
    ordered = {key: _native_order(content[key], children.get(key, "")) for key in field_order if key in content}
    # Go maps sort their keys. Preserve all undeclared fields: an unfamiliar
    # struct order must fail checksum until supported, never erase knowledge.
    for key in sorted(content):
        if key not in ordered:
            ordered[key] = _native_order(content[key])
    return ordered


def _snapshot(content: Any, *, present: bool = True) -> dict[str, Any]:
    if not present:
        return {"present": False, "size_bytes": 0, "content_hash": ""}
    size, digest = section_integrity(content)
    return {"present": True, "size_bytes": size, "content_hash": digest, "content": copy.deepcopy(content)}


def _writable(section: str, content: Any) -> Any:
    if section == "dataReferences" and isinstance(content, list):
        return [{k: v for k, v in entry.items() if k not in {"tableId", "table_id"}} for entry in content]
    return content


def _write_equivalence(section: str, content: Any) -> Any:
    """Normalize only SDK-declared omitempty arrays for write read-back.

    Native publication evidence uses _exact_equal instead. Unknown fields,
    scalar values, physical references and non-empty arrays are never erased.
    """
    normalized = copy.deepcopy(_writable(section, content))
    if section == "semanticModel" and isinstance(normalized, dict):
        for member in _MODEL_KEYS:
            if normalized.get(member) == []:
                normalized.pop(member)
        for table in normalized.get("logicalTables", []):
            for member in ("dimensions", "timeDimensions", "synonyms"):
                if table.get(member) == []:
                    table.pop(member)
    return normalized


def _equal(section: str, left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["present"] != right["present"]:
        # Catalog may omit an intentionally cleared optional array.
        if section in _ARRAY_KEYS:
            present = left if left["present"] else right
            return present.get("content") == []
        return False
    if not left["present"]:
        return True
    return _json_bytes(_write_equivalence(section, left["content"]), sort_keys=True) == _json_bytes(
        _write_equivalence(section, right["content"]), sort_keys=True,
    )


def _exact_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Publication evidence includes every native field, including physical IDs."""
    if left["present"] != right["present"]:
        return False
    return not left["present"] or _json_bytes(left["content"], sort_keys=True) == _json_bytes(right["content"], sort_keys=True)


def _validate_section(section: str, content: Any) -> None:
    if section not in SECTIONS:
        raise _error("SemanticSectionInvalid", "unknown semantic section", section=section)
    expected = list if section == "dataReferences" or section in _ARRAY_KEYS else dict
    if not isinstance(content, expected):
        raise _error("SemanticSectionInvalid", "section has the wrong JSON type", section=section)
    if section == "dataReferences":
        if not 1 <= len(content) <= 20:
            raise _error("SemanticSectionInvalid", "dataReferences must contain 1-20 entries")
        for reference in content:
            if not isinstance(reference, dict) or any(
                not isinstance(reference.get(key), str) or not reference[key].strip()
                for key in ("project", "table")
            ):
                raise _error("SemanticSectionInvalid", "dataReferences entries require project and table")
            if reference.get("schema") is not None and not isinstance(reference["schema"], str):
                raise _error("SemanticSectionInvalid", "dataReferences schema must be a string when provided")
    elif section in _ARRAY_KEYS and isinstance(content, list):
        _index_entries(content, _ARRAY_KEYS[section])
    elif section == "semanticModel" and isinstance(content, dict):
        for member in _MODEL_KEYS:
            # Catalog may omit an empty optional list in its native response.
            if member in content:
                if not isinstance(content[member], list):
                    raise _error("SemanticSectionInvalid", "semanticModel members must be complete lists")
                _index_entries(content[member], "name")


def _index_entries(entries: list[Any], key: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str) or not entry[key].strip():
            raise _error("SemanticSectionInvalid", "entries require a non-empty natural key", key=key)
        if entry[key] in indexed:
            raise _error("SemanticSectionInvalid", "duplicate natural key", key=key)
        indexed[entry[key]] = entry
    return indexed


def _merge_fields(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(current)
    for key, value in changes.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_fields(merged[key], value)
        elif key in {"dimensions", "timeDimensions"} and isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = merge_section_entries("metricDefinitions", merged[key], value)
        elif key == "questions" and isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = _merge_named_entries(merged[key], value, "id")
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_named_entries(
    current: list[Any], changes: list[Any], key: str, removals: Sequence[str] = (),
) -> list[dict[str, Any]]:
    originals = _index_entries(current, key)
    incoming = _index_entries(changes, key)
    if set(removals) & incoming.keys() or not set(removals) <= originals.keys():
        raise _error("SemanticSectionInvalid", "removals must name existing entries without overlapping edits")
    merged = []
    for name, entry in originals.items():
        if name not in removals:
            merged.append(_merge_fields(entry, incoming.pop(name, {})))
    merged.extend(copy.deepcopy(list(incoming.values())))
    return merged


def merge_section_entries(
    section: str, current: Any, changes: Any, *, removals: Sequence[str] = (),
) -> Any:
    """Merge explicit entries while retaining all unrelated entries and fields.

    Objects are shallow field merges, so arbitrary business text is never
    interpreted as a patch language. List deletions need explicit keys.
    """
    if section == "dataReferences":
        _validate_section(section, current)
        _validate_section(section, changes)
        if removals:
            raise _error("SemanticSectionInvalid", "reference removals require explicit whole-section replacement")
        def reference_key(entry: dict[str, Any]) -> tuple[str, str, str]:
            return entry["project"], entry.get("schema") or "", entry["table"]
        references = {reference_key(entry): copy.deepcopy(entry) for entry in current}
        if len(references) != len(current):
            raise _error("SemanticSectionInvalid", "duplicate physical reference")
        incoming_references: set[tuple[str, str, str]] = set()
        for entry in changes:
            identity = reference_key(entry)
            if identity in incoming_references:
                raise _error("SemanticSectionInvalid", "duplicate physical reference")
            incoming_references.add(identity)
            references[identity] = _merge_fields(references.get(identity, {}), entry)
        return list(references.values())
    if section in _ARRAY_KEYS:
        if not isinstance(current, list) or not isinstance(changes, list):
            raise _error("SemanticSectionInvalid", "entry merge requires arrays")
        return _merge_named_entries(current, changes, _ARRAY_KEYS[section], removals)
    if removals:
        raise _error("SemanticSectionInvalid", "entry removals are supported only for named array sections")
    if not isinstance(current, dict) or not isinstance(changes, dict):
        raise _error("SemanticSectionInvalid", "object merge requires objects")
    merged_object = copy.deepcopy(current)
    if section == "semanticModel":
        for member, change in changes.items():
            if member in _MODEL_KEYS:
                merged_object[member] = merge_section_entries("metricDefinitions", current.get(member, []), change)
            else:
                merged_object = _merge_fields(merged_object, {member: change})
    elif section == "dataReferences":
        raise _error("SemanticSectionInvalid", "dataReferences requires an explicit complete replacement")
    else:
        merged_object = _merge_fields(merged_object, changes)
    return merged_object


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _error("SemanticStateInvalid", "duplicate JSON object field")
        result[key] = value
    return result


def load_section_file(path: Path) -> Any:
    """Read a bounded, duplicate-free native section JSON file."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES:
            raise _error("SemanticStateInvalid", "semantic JSON file exceeds the local size limit")
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise _error("SemanticStateInvalid", "cannot read valid semantic JSON") from exc


class SemanticPackageSync:
    """A native JSON workspace bound to one remote account/object/region."""

    def __init__(self, workspace: Path, client: SemanticClient) -> None:
        self.workspace = workspace.resolve()
        self.client = client
        self.state_path = self.workspace / "state.json"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.workspace.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(self.workspace / ".sync.lock", timeout=0):
                yield
        except FileLockTimeout as exc:
            raise _error("SemanticWorkspaceBusy", "semantic workspace is already in use") from exc

    def _read(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise _error("SemanticNotBound", "bind this workspace to a remote SemanticSpec first")
        state = load_section_file(self.state_path)
        if not isinstance(state, dict) or state.get("version") != _STATE_VERSION:
            raise _error("SemanticStateInvalid", "unsupported semantic workspace state")
        binding = state.get("binding")
        if not isinstance(binding, dict) or any(
            not isinstance(binding.get(key), str) or not binding[key]
            for key in ("remote_alias", "remote_url", "name", "region", "namespace", "semantic_spec_id")
        ):
            raise _error("SemanticStateInvalid", "semantic binding is incomplete")
        if binding["remote_url"] != self.client.connection.url or binding["remote_alias"] != self.client.connection.alias:
            raise _error("SemanticBindingChanged", "the configured remote differs from the workspace binding")
        for collection in ("base", "local"):
            if not isinstance(state.get(collection), dict):
                raise _error("SemanticStateInvalid", "semantic state has no complete section collection")
            for section, snapshot in state[collection].items():
                self._validate_snapshot(section, snapshot)
        if state["base"].keys() != state["local"].keys():
            raise _error("SemanticStateInvalid", "local and base section sets differ")
        revisions = state.get("base_revisions")
        if not isinstance(revisions, dict) or revisions.keys() != state["base"].keys():
            raise _error("SemanticStateInvalid", "base sections lack their source revision identities")
        return state

    @staticmethod
    def _validate_snapshot(section: str, snapshot: Any) -> None:
        if section not in SECTIONS or not isinstance(snapshot, dict) or type(snapshot.get("present")) is not bool:
            raise _error("SemanticIntegrity", "section presence is missing or invalid")
        if snapshot["present"]:
            if "content" not in snapshot or snapshot["content"] is None:
                raise _error("SemanticIntegrity", "present section has no complete content", section=section)
            _validate_section(section, snapshot["content"])
            size, digest = section_integrity(snapshot["content"])
            if snapshot.get("size_bytes") != size or snapshot.get("content_hash") != digest:
                native = _native_order(snapshot["content"], _NATIVE_SECTION_TYPES[section])
                native_size, native_digest = section_integrity(native)
                if snapshot.get("size_bytes") == native_size and snapshot.get("content_hash") == native_digest:
                    snapshot["content"] = native
                    size, digest = native_size, native_digest
            if type(snapshot.get("size_bytes")) is not int or snapshot["size_bytes"] != size or snapshot.get("content_hash") != digest:
                raise _error("SemanticIntegrity", "section size or content hash does not match", section=section)
        elif type(snapshot.get("size_bytes")) is not int or snapshot["size_bytes"] != 0 or snapshot.get("content_hash") != "" or "content" in snapshot:
            raise _error("SemanticIntegrity", "absent section carries inconsistent content metadata", section=section)

    def _save(self, state: dict[str, Any]) -> None:
        raw = _json_bytes(state)
        if len(raw) > _MAX_STATE_BYTES:
            raise _error("SemanticStateInvalid", "semantic state exceeds the local size limit")
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=self.workspace)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        envelope = self.client.call(tool, args)
        if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
            raise _error("SemanticIntegrity", "remote returned no valid Gateway envelope")
        if not envelope["ok"]:
            error = envelope.get("error")
            remote_code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
            details = error.get("details", {}) if isinstance(error, dict) else {}
            # Retain only classification fields, never arbitrary provider details.
            classification = {
                key: details[key] for key in ("retry_safety", "outcome_unknown", "backend_http_status")
                if isinstance(details, dict) and key in details
            }
            raise _error("SemanticRemoteRejected", "remote SemanticSpec operation failed", tool=tool, remote_code=remote_code, **classification)
        if envelope.get("has_more") or envelope.get("next_cursor") or any(
            "truncat" in str(warning).lower() for warning in envelope.get("warnings", [])
        ):
            raise _error("SemanticIntegrity", "remote section response is incomplete")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise _error("SemanticIntegrity", "remote response has no complete data object")
        return data

    def _overview(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        data = self._call(_GET, {"name": binding["name"], "region": binding["region"]})
        spec = data.get("semantic_spec")
        if not isinstance(spec, dict) or not isinstance(data.get("namespace"), str) or not data["namespace"]:
            raise _error("SemanticIntegrity", "remote management overview is incomplete")
        if spec.get("name") != binding["name"] or not isinstance(spec.get("semantic_spec_id"), str) or not spec["semantic_spec_id"]:
            raise _error("SemanticIntegrity", "remote management identity is incomplete")
        for key in ("namespace", "semantic_spec_id"):
            actual = data[key] if key == "namespace" else spec[key]
            if key in binding and binding[key] != actual:
                raise _error("SemanticBindingChanged", "remote account or SemanticSpec identity changed", field=key)
        for field in _REVISION_FIELDS.values():
            if field not in spec or (spec[field] is not None and (not isinstance(spec[field], str) or not spec[field])):
                raise _error("SemanticIntegrity", "remote overview lacks an exact revision field", field=field)
        return {**spec, "namespace": data["namespace"]}

    def bind(self, name: str, region: str) -> dict[str, Any]:
        if not name.strip() or not region.strip():
            raise _error("SemanticBindingInvalid", "name and region are required")
        with self._lock():
            target = {"name": name, "region": region}
            overview = self._overview(target)
            binding = {
                **target, "namespace": overview["namespace"], "semantic_spec_id": overview["semantic_spec_id"],
                "remote_url": self.client.connection.url, "remote_alias": self.client.connection.alias,
            }
            if self.state_path.exists():
                state = self._read()
                if state["binding"] != binding:
                    raise _error("SemanticBindingChanged", "an existing workspace cannot be rebound to another identity")
            else:
                self._save({"version": _STATE_VERSION, "binding": binding, "base": {}, "local": {}, "base_revisions": {}})
            return {"binding": binding, "workspace": str(self.workspace)}

    def _remote_snapshot(
        self, binding: Mapping[str, Any], source: str, sections: Sequence[str], revision: str | None = None,
    ) -> dict[str, Any]:
        overview = self._overview(binding)
        selected_revision = revision or overview[_REVISION_FIELDS[source]]
        if selected_revision is None:
            if source != "USER_DRAFT":
                raise _error("SemanticSourceNotReady", "the requested SemanticSpec source has no revision", source=source)
            return {"source": source, "revision_id": None, "sections": {section: _snapshot(None, present=False) for section in sections}}
        snapshots: dict[str, dict[str, Any]] = {}
        metadata: dict[str, Any] = {}
        for section in sections:
            args = {"name": binding["name"], "region": binding["region"], "source": source, "sections": [section], "include_content": True}
            if source == "PUBLISHED":
                args["revision_id"] = selected_revision
            data = self._call(_GET, args)
            spec = data.get("semantic_spec")
            if not isinstance(spec, dict) or data.get("namespace") != binding["namespace"] or spec.get("semantic_spec_id") != binding["semantic_spec_id"] or spec.get("name") != binding["name"]:
                raise _error("SemanticBindingChanged", "section response changed the remote identity")
            if spec.get("source") != source or spec.get("revision_id") != selected_revision:
                raise _error("SemanticSnapshotChanged", "remote sections do not share the requested revision")
            rows = spec.get("sections")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("section") != section:
                raise _error("SemanticIntegrity", "remote did not return exactly the complete requested section")
            snapshot = {key: value for key, value in rows[0].items() if key != "section"}
            self._validate_snapshot(section, snapshot)
            snapshots[section] = snapshot
            source_metadata = {key: spec.get(key) for key in ("source_revision_id", "publication_source")}
            if metadata and metadata != source_metadata:
                raise _error("SemanticSnapshotChanged", "remote source metadata changed during section reads")
            metadata = source_metadata
        final = self._overview(binding)
        if source != "PUBLISHED" and final[_REVISION_FIELDS[source]] != selected_revision:
            raise _error("SemanticSnapshotChanged", "remote draft changed during section reads")
        return {"source": source, "revision_id": selected_revision, "sections": snapshots, **metadata}

    def pull(self, source: str, sections: Sequence[str], revision: str | None = None) -> dict[str, Any]:
        if source not in SOURCES or not sections or len(set(sections)) != len(sections) or any(section not in SECTIONS for section in sections):
            raise _error("SemanticSectionInvalid", "select an explicit source and distinct complete sections")
        if revision is not None and (source != "PUBLISHED" or not revision.strip()):
            raise _error("SemanticRevisionInvalid", "only PUBLISHED supports an explicit immutable revision")
        with self._lock():
            state = self._read()
            remote = self._remote_snapshot(state["binding"], source, sections, revision)
            merged: dict[str, dict[str, Any]] = {}
            for section, incoming in remote["sections"].items():
                if section not in state["base"]:
                    merged[section] = incoming
                    continue
                kind, result = self._three_way(section, state["base"][section], state["local"][section], incoming)
                if kind == "conflict":
                    raise _error("SemanticConflict", "pull would overwrite divergent local changes", section=section)
                merged[section] = result
            state["base"].update(copy.deepcopy(remote["sections"]))
            state["local"].update(copy.deepcopy(merged))
            state["base_revisions"].update({section: {"source": source, "revision_id": remote["revision_id"]} for section in sections})
            state["base_source"] = source
            state["base_revision_id"] = remote["revision_id"]
            self._save(state)
            return {"source": source, "revision_id": remote["revision_id"], "sections": list(sections), "binding": state["binding"]}

    @staticmethod
    def _three_way(section: str, base: dict[str, Any], local: dict[str, Any], remote: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if _equal(section, local, remote):
            return "unchanged" if _equal(section, base, local) else "converged", remote
        if _equal(section, local, base):
            return "remote_changed", remote
        if _equal(section, remote, base):
            return "local_changed", local
        return "conflict", local

    def local_sections(self) -> dict[str, Any]:
        with self._lock():
            state = self._read()
            return {section: copy.deepcopy(snapshot["content"]) for section, snapshot in state["local"].items() if snapshot["present"]}

    def snapshot(self) -> dict[str, Any]:
        """Export verified base bodies without attributing local edits to the server."""
        with self._lock():
            state = self._read()
            revisions = list(state["base_revisions"].values())
            if not revisions or not isinstance(revisions[0], dict) or any(metadata != revisions[0] for metadata in revisions):
                raise _error("SemanticSnapshotMixed", "export requires one consistent source revision across every tracked section")
            source, revision = revisions[0].get("source"), revisions[0].get("revision_id")
            if source not in SOURCES or not isinstance(revision, str) or not revision:
                raise _error("SemanticSnapshotMixed", "export requires an exact non-empty remote revision")
            binding = state["binding"]
            return {
                "endpoint": binding["remote_url"], "namespace": binding["namespace"],
                "region": binding["region"], "semantic_spec_id": binding["semantic_spec_id"],
                "name": binding["name"], "source": source, "revision_id": revision,
                "sections": [{"section": section, **copy.deepcopy(snapshot)} for section, snapshot in state["base"].items()],
            }

    def edit_section(
        self, section: str, content: Any, *, replace: bool = False, removals: Sequence[str] = (),
    ) -> dict[str, Any]:
        with self._lock():
            state = self._read()
            if section not in state["base"]:
                raise _error("SemanticSectionNotPulled", "pull the complete section before editing", section=section)
            previous = state["local"][section]
            if replace:
                if removals:
                    raise _error("SemanticSectionInvalid", "replace and entry removals cannot be combined")
                merged = content
            elif section == "dataReferences":
                raise _error("SemanticSectionInvalid", "dataReferences edits require explicit complete replacement")
            else:
                empty: Any = [] if section in _ARRAY_KEYS else {}
                merged = merge_section_entries(section, previous.get("content", empty), content, removals=removals)
            _validate_section(section, merged)
            state["local"][section] = _snapshot(merged)
            self._save(state)
            return {"section": section, "before": previous, "after": state["local"][section], "remote_written": False}

    def stage_sections(self, sections: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and atomically merge a projection without remote operations."""
        if not isinstance(sections, Mapping) or not sections:
            raise _error("SemanticSectionInvalid", "stage requires an explicit non-empty section mapping")
        with self._lock():
            state = self._read()
            staged: dict[str, dict[str, Any]] = {}
            for section, content in sections.items():
                if section not in state["local"]:
                    raise _error("SemanticSectionNotPulled", "pull every complete target section before staging", section=section)
                previous = state["local"][section]
                empty: Any = [] if section in _ARRAY_KEYS or section == "dataReferences" else {}
                current = previous.get("content", empty)
                if section == "dataReferences" and not current:
                    merged = copy.deepcopy(content)
                else:
                    merged = merge_section_entries(section, current, content)
                _validate_section(section, merged)
                staged[section] = _snapshot(merged)
            state["local"].update(staged)
            self._save(state)
            return {"staged_sections": list(staged), "remote_written": False}

    def _plan(self, state: dict[str, Any]) -> dict[str, Any]:
        sections = list(state["base"])
        if not sections:
            raise _error("SemanticSectionNotPulled", "pull complete sections before planning a push")
        remote = self._remote_snapshot(state["binding"], "USER_DRAFT", sections)
        initial = remote["revision_id"] is None
        initialization = None
        missing_initial: list[str] = []
        if initial:
            missing_initial = [section for section in SECTIONS if section not in state["base"]]
            if not missing_initial and any(
                metadata != {"source": "PUBLISHED", "revision_id": state.get("base_revision_id")}
                for metadata in state["base_revisions"].values()
            ):
                missing_initial.append("published_baseline")
            try:
                initialization = self._remote_snapshot(state["binding"], "PUBLISHED", sections)
            except SemanticSyncError as exc:
                if str(exc.code) != "SemanticSourceNotReady":
                    raise
                missing_initial.append("published_baseline")
                initialization = copy.deepcopy(remote)
            remote["initialization_source"] = initialization
        writes: dict[str, Any] = {}
        merged: dict[str, dict[str, Any]] = {}
        changes = []
        conflicts = missing_initial.copy()
        for section in sections:
            base, local = state["base"][section], state["local"][section]
            incoming = initialization["sections"][section] if initialization is not None else remote["sections"][section]
            kind, result = self._three_way(section, base, local, incoming)
            if initial and kind != "conflict":
                kind = "initialize"
            merged[section] = result
            if kind == "conflict":
                conflicts.append(section)
            elif kind in {"initialize", "local_changed"} and result["present"]:
                writes[section] = _writable(section, copy.deepcopy(result["content"]))
            elif kind == "local_changed" and not result["present"]:
                raise _error("SemanticSectionInvalid", "clear optional arrays explicitly rather than removing section presence")
            changes.append({"section": section, "state": kind, "before": incoming, "after": result})
        if initial and (not isinstance(writes.get("dataReferences"), list) or not writes["dataReferences"]):
            conflicts.append("dataReferences")
        plan = {
            "binding": state["binding"], "base_source": state.get("base_source"),
            "base_revision_id": state.get("base_revision_id"), "base_revisions": state["base_revisions"], "base": state["base"], "local": state["local"],
            "remote": remote, "writes": writes, "merged": merged, "changes": changes,
            "conflicts": list(dict.fromkeys(conflicts)), "ready": not conflicts and not initial,
            "blocked_reason": "SemanticFirstDraftUnsupported" if initial else None,
            "initialization_missing_sections": missing_initial,
        }
        plan["plan_hash"] = "sha256:" + hashlib.sha256(_json_bytes(plan, sort_keys=True)).hexdigest()
        return plan

    def diff(self) -> dict[str, Any]:
        with self._lock():
            return self._plan(self._read())

    def push(self, dry_run: bool = True, approved_plan_hash: str | None = None) -> dict[str, Any]:
        with self._lock():
            state = self._read()
            plan = self._plan(state)
            if dry_run:
                return {**plan, "dry_run": True, "remote_written": False}
            if not approved_plan_hash or approved_plan_hash != plan["plan_hash"]:
                raise _error("SemanticPlanChanged", "approve the exact current plan hash before writing")
            if not plan["ready"]:
                if plan["blocked_reason"]:
                    raise _error("SemanticFirstDraftUnsupported", "the remote has no atomic object-ID guard for first-draft writes; bootstrap and review the draft on the server first")
                raise _error("SemanticConflict", "resolve section conflicts before writing", sections=plan["conflicts"])
            if _json_bytes(self._read(), sort_keys=True) != _json_bytes(state, sort_keys=True):
                raise _error("SemanticPlanChanged", "local sections changed while preparing the approved write")
            remote = plan["remote"]
            if plan["writes"]:
                args = {"name": state["binding"]["name"], "region": state["binding"]["region"], "sections": plan["writes"]}
                if remote["revision_id"] is not None:
                    args["expected_draft_revision_id"] = remote["revision_id"]
                updated = self._call(_UPDATE, args)
                spec = updated.get("semantic_spec")
                if updated.get("namespace") != state["binding"]["namespace"] or not isinstance(spec, dict) or spec.get("semantic_spec_id") != state["binding"]["semantic_spec_id"] or spec.get("name") != state["binding"]["name"]:
                    raise _error("SemanticIntegrity", "write response changed the bound identity; inspect remote before retrying")
                revision = spec.get("user_draft_revision_id")
                if not isinstance(revision, str) or not revision:
                    raise _error("SemanticIntegrity", "write response omitted its new revision; inspect remote before retrying")
                remote = self._remote_snapshot(state["binding"], "USER_DRAFT", list(state["base"]), revision)
                for section, expected in plan["merged"].items():
                    if not _equal(section, expected, remote["sections"][section]):
                        raise _error("SemanticReadbackMismatch", "write read-back differs from the approved sections", section=section)
            state["base"] = copy.deepcopy(remote["sections"])
            state["local"] = copy.deepcopy(remote["sections"])
            state["base_source"] = "USER_DRAFT"
            state["base_revision_id"] = remote["revision_id"]
            state["base_revisions"] = {section: {"source": "USER_DRAFT", "revision_id": remote["revision_id"]} for section in state["base"]}
            self._save(state)
            return {"remote_written": bool(plan["writes"]), "revision_id": remote["revision_id"], "plan_hash": plan["plan_hash"], "published": False}

    def publish(self, expected_revision: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm or not expected_revision.strip():
            raise _error("SemanticPublicationUnconfirmed", "explicitly confirm the exact draft revision to publish")
        with self._lock():
            state = self._read()
            if set(state["base"]) != set(SECTIONS) or any(
                metadata != {"source": "USER_DRAFT", "revision_id": expected_revision}
                for metadata in state["base_revisions"].values()
            ):
                raise _error("SemanticPublicationUnreviewed", "pull and review all seven sections at the exact USER_DRAFT revision before publication")
            if not state["base"] or state.get("base_source") != "USER_DRAFT" or state.get("base_revision_id") != expected_revision:
                raise _error("SemanticPublicationStale", "publish only the exact synchronized and reviewed USER_DRAFT revision")
            if any(not _exact_equal(state["base"][section], local) for section, local in state["local"].items()):
                raise _error("SemanticPublicationStale", "local changes must be pushed and reviewed before publication")
            draft = self._remote_snapshot(state["binding"], "USER_DRAFT", list(state["base"]), expected_revision)
            if any(not _exact_equal(snapshot, draft["sections"][section]) for section, snapshot in state["base"].items()):
                raise _error("SemanticPublicationStale", "the reviewed draft no longer matches local complete sections")
            before = self._overview(state["binding"])["published_revision_id"]
            recovered = False
            try:
                result = self._call(_PUBLISH, {"name": state["binding"]["name"], "region": state["binding"]["region"], "source": "USER_DRAFT", "expected_revision_id": expected_revision})
            except McsError as exc:
                remote_code = str(exc.context.get("remote_code", exc.code)).lower()
                status = exc.context.get("backend_http_status")
                outcome_unknown = (
                    exc.context.get("retry_safety") == "NON_IDEMPOTENT_OPERATION"
                    or exc.context.get("outcome_unknown") is True
                    or (type(status) is int and status >= 500)
                    or any(term in remote_code for term in ("timeout", "unavailable", "transport", "unknown", "integrity", "protocol"))
                )
                if not outcome_unknown:
                    raise
                try:
                    result = self._overview(state["binding"])
                except McsError as read_error:
                    raise _error("SemanticPublishOutcomeUnknown", "cannot verify publication outcome; do not retry automatically") from read_error
                if result["published_revision_id"] is None or result["published_revision_id"] == before:
                    raise _error("SemanticPublishOutcomeUnknown", "publication outcome remains unknown; do not retry automatically") from exc
                recovered = True
            revision = result.get("published_revision_id")
            if not isinstance(revision, str) or not revision:
                raise _error("SemanticPublishOutcomeUnknown", "publication response has no published revision")
            try:
                published = self._remote_snapshot(state["binding"], "PUBLISHED", list(state["base"]), revision)
            except McsError as read_error:
                raise _error("SemanticPublishOutcomeUnknown", "cannot verify the complete published revision; do not retry automatically") from read_error
            if published.get("source_revision_id") != expected_revision or published.get("publication_source") != "USER_DRAFT" or any(
                not _exact_equal(snapshot, published["sections"][section]) for section, snapshot in draft["sections"].items()
            ):
                raise _error("SemanticPublishOutcomeUnknown", "publication lacks exact source and content read-back evidence")
            state["last_publication"] = {"published_revision_id": revision, "source_revision_id": expected_revision, "recovered_by_readback": recovered}
            self._save(state)
            return {"published": True, **state["last_publication"]}
