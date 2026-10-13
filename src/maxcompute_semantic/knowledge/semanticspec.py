# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Explicit, evidence-preserving projections between the local graph and Catalog.

SQL is retained verbatim. Projection checks MEASURE scalar expressions with the
existing MaxCompute parser; it does not rewrite a local metric into a server
measure. Executable items require an explicit server-native binding.
This module neither contacts a backend nor publishes any resulting sections.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from maxcompute_semantic.knowledge.model import (
    GRAPH_KIND,
    SCHEMA_VERSION,
    KnowledgeError,
    canonical_json,
    content_hash,
    stable_id,
)

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.build.storage import PackageDB

_SECTIONS = frozenset(
    {
        "dataReferences",
        "semanticModel",
        "instructions",
        "metricDefinitions",
        "verifiedQueries",
        "glossary",
        "benchmarks",
    }
)
_MODEL_PARTS = ("logicalTables", "namedFilters", "approvedRelationships")
_DEPENDENCIES = frozenset(
    {"depends_on", "derived_from", "uses", "reads", "implements", "maps_to"}
)
_EXTENSION_KEYS = frozenset(
    {"custom_extensions", "customExtensions", "privateExtensions", "extensions"}
)


def _properties(item: Mapping[str, Any]) -> dict[str, Any]:
    value = item.get("properties", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _native(item: Mapping[str, Any]) -> dict[str, Any]:
    value = _properties(item).get("server_native")
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _section(item: Mapping[str, Any]) -> str:
    explicit = _properties(item).get("server_section")
    if isinstance(explicit, str):
        return explicit.removeprefix("semanticModel.")
    if item.get("kind") in {"entity", "business_entity"} and isinstance(
        _properties(item).get("server_binding"), Mapping
    ):
        return "logicalTables"
    return {
        "term": "glossary",
        "table": "logicalTables",
        "metric": "metricDefinitions",
        "query": "verifiedQueries",
    }.get(str(item.get("kind")), "")


def _evidence_ids(item: Mapping[str, Any]) -> list[str]:
    value = item.get("evidence_ids", _properties(item).get("evidence_ids", []))
    if not isinstance(value, list) or any(
        not isinstance(v, str) or not v for v in value
    ):
        return []
    return value


def _physical_ref(base_table: Any) -> dict[str, str] | None:
    # This is a separately typed table reference, never a SQL statement.
    if not isinstance(base_table, str):
        return None
    parts = base_table.split(".")
    if len(parts) != 3 or not parts[0] or not parts[2]:
        return None
    if any(
        not all(c.isascii() and (c.isalnum() or c == "_") for c in part)
        for part in (parts[0], parts[2])
    ):
        return None
    if parts[1] and not all(
        c.isascii() and (c.isalnum() or c == "_") for c in parts[1]
    ):
        return None
    ref = {"project": parts[0], "table": parts[2]}
    if parts[1]:
        ref["schema"] = parts[1]
    return ref


def _references(section: str, native: Mapping[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if section == "metricDefinitions":
        if native.get("baseLogicalTable"):
            refs.append(("logicalTables", str(native["baseLogicalTable"])))
        derived = native.get("derivedMetric", {})
        if isinstance(derived, Mapping):
            refs.extend(
                ("metricDefinitions", str(v)) for v in derived.get("refMetrics", [])
            )
        refs.extend(("namedFilters", str(v)) for v in native.get("filterRefs", []))
    elif section == "approvedRelationships":
        refs.extend(
            ("logicalTables", str(native[k]))
            for k in ("leftTable", "rightTable")
            if native.get(k)
        )
    elif section in {"glossary", "namedFilters"}:
        refs.extend(
            ("logicalTables", str(v)) for v in native.get("applicableTables", [])
        )
        if native.get("linkedMetric"):
            refs.append(("metricDefinitions", str(native["linkedMetric"])))
        if native.get("linkedDimension"):
            refs.append(
                ("logicalTables", str(native["linkedDimension"]).partition(".")[0])
            )
    return refs


def _has_extensions(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(_EXTENSION_KEYS.intersection(value)) or any(
            _has_extensions(v) for v in value.values()
        )
    if isinstance(value, list):
        return any(_has_extensions(v) for v in value)
    return False


def _validate_measure_expression(expression: Any) -> tuple[str, str] | None:
    """Check scalar AST shape without rewriting SQL or claiming schema validity."""
    if not isinstance(expression, str) or not expression.strip():
        return "invalid_measure_expression", "MEASURE expr must be non-empty SQL text"

    # The SELECT wrapper uses the existing dialect parser to consume the whole
    # fragment. It is never emitted or executed: the native expression remains
    # unchanged, including literal and quoted-identifier spellings.
    from sqlglot import ErrorLevel, exp

    from maxcompute_semantic.dialect import parse_mc

    try:
        statements = parse_mc("SELECT " + expression, error_level=ErrorLevel.RAISE)
    except Exception:  # noqa: BLE001 -- parser failures leave arbitrary user fragments unvalidated
        return (
            "invalid_measure_expression",
            "the MaxCompute parser could not validate MEASURE expr",
        )
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return (
            "invalid_measure_expression",
            "MEASURE expr must contain one scalar expression",
        )
    statement = statements[0]
    if len(statement.expressions) != 1 or any(
        key != "expressions" and value is not None
        for key, value in statement.args.items()
    ):
        return (
            "invalid_measure_expression",
            "MEASURE expr cannot contain query clauses or multiple expressions",
        )
    scalar = statement.expressions[0]
    if isinstance(scalar, (exp.Alias, exp.Star, exp.Tuple)) or any(
        isinstance(part, (exp.AggFunc, exp.Window, exp.Query, exp.Command, exp.Star))
        for part in scalar.walk()
    ):
        return (
            "invalid_measure_expression",
            "MEASURE expr must be row-level, without aggregates, windows, subqueries or aliases",
        )
    if any(isinstance(part, exp.Anonymous) for part in scalar.walk()):
        return (
            "unverified_measure_function",
            "the parser cannot classify a function as scalar; obtain owner or server validation instead of assuming it is not an aggregate",
        )
    return None


def _current_evidence_error(
    proof: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    versions: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, str],
) -> tuple[str, str] | None:
    excerpt = proof.get("excerpt")
    if not isinstance(excerpt, str) or proof.get("content_hash") != content_hash(
        excerpt
    ):
        return (
            "invalid_evidence_hash",
            "evidence hash does not match its pinned content",
        )
    source_id, source_version = str(proof["source_id"]), str(proof["source_version"])
    if proof.get("source_kind") == "document":
        document = documents.get(source_id)
        if document is None or source_version not in versions:
            return (
                "untracked_source",
                "document evidence requires its source and retained version",
            )
        if document.get("current_version") != source_version:
            return "stale_evidence", "document evidence is from a superseded version"
        version = versions[source_version]
        content = version.get("content")
        if (
            version.get("document_id") != source_id
            or not isinstance(content, str)
            or version.get("content_hash") != content_hash(content)
        ):
            return (
                "invalid_document_evidence",
                "document version identity or content hash is invalid",
            )
        lines = content.splitlines()
        start, end = proof.get("start_line"), proof.get("end_line")
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= len(lines)
        ):
            return (
                "invalid_document_evidence",
                "document evidence must have an exact valid line span",
            )
        if "\n".join(lines[start - 1 : end]) != excerpt:
            return (
                "invalid_document_evidence",
                "document evidence does not match its cited source lines",
            )
    elif proof.get("source_kind") in {"package", "semanticspec"}:
        if source_id not in sources:
            return (
                "untracked_source",
                "imported evidence requires the source's current revision",
            )
        if sources[source_id] != source_version:
            return (
                "stale_evidence",
                "imported evidence is from a superseded source revision",
            )
    elif proof.get("source_kind") != "user":
        return "unsupported_evidence", "evidence source kind is unsupported"
    return None


def _cyclic_metric_dependencies(
    included: set[str],
    payloads: Mapping[str, tuple[str, dict[str, Any], str]],
    by_key: Mapping[tuple[str, str], list[str]],
) -> set[str]:
    remaining: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {}
    for item_id in included:
        section, native, _ = payloads[item_id]
        if section != "metricDefinitions":
            continue
        remaining[item_id] = {
            matches[0]
            for ref in _references(section, native)
            if ref[0] == "metricDefinitions"
            and len(matches := by_key.get(ref, [])) == 1
            and matches[0] in included
        }
        for target in remaining[item_id]:
            dependents.setdefault(target, set()).add(item_id)
    ready = deque(key for key, refs in remaining.items() if not refs)
    while ready:
        resolved = ready.popleft()
        remaining.pop(resolved, None)
        for dependent in dependents.get(resolved, set()):
            if dependent in remaining:
                remaining[dependent].discard(resolved)
                if not remaining[dependent]:
                    ready.append(dependent)
    return set(remaining)


def _projection_payload(item: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    section = _section(item)
    props = _properties(item)
    native = _native(item)
    if section == "glossary" and not native:
        definition = props.get("definition")
        if not isinstance(definition, str) or not definition.strip():
            return section, {}, "term requires an explicit definition"
        native = {"term": item.get("name"), "definition": definition}
        aliases = props.get("aka", props.get("aliases", props.get("alsoKnownAs", [])))
        if not isinstance(aliases, list) or any(
            not isinstance(v, str) or not v for v in aliases
        ):
            return section, {}, "term aliases must be a list of non-empty strings"
        if aliases:
            native["alsoKnownAs"] = deepcopy(aliases)
    elif section == "logicalTables" and not native:
        binding = props.get("server_binding")
        if (
            not isinstance(binding, Mapping)
            or not binding.get("name")
            or not binding.get("baseTable")
        ):
            return (
                section,
                {},
                "table requires an explicit logical name and physical baseTable binding",
            )
        native = deepcopy(dict(binding))
    if not native:
        return (
            section,
            {},
            "no explicit server-native binding; local SQL and metric expressions remain unmapped",
        )
    if _has_extensions(native):
        return (
            section,
            {},
            "private or generic extensions cannot be projected into SemanticSpec",
        )
    if section == "logicalTables" and (
        not native.get("name") or _physical_ref(native.get("baseTable")) is None
    ):
        return (
            section,
            {},
            "logical table requires a name and a fully qualified physical baseTable",
        )
    if section == "glossary" and (
        not native.get("term") or not native.get("definition")
    ):
        return section, {}, "glossary requires term and definition"
    if section == "metricDefinitions":
        semantic_type = native.get("semanticType")
        if semantic_type == "MEASURE":
            if (
                not native.get("name")
                or not native.get("baseLogicalTable")
                or not native.get("expr")
            ):
                return (
                    section,
                    {},
                    "MEASURE requires name, baseLogicalTable and an explicit row-level expr",
                )
            if native.get("derivedMetric") is not None:
                return section, {}, "MEASURE cannot contain derivedMetric"
        elif semantic_type == "CALCULATED_METRIC":
            derived = native.get("derivedMetric")
            if (
                not native.get("name")
                or not isinstance(derived, Mapping)
                or not derived.get("formula")
            ):
                return (
                    section,
                    {},
                    "CALCULATED_METRIC requires name and derivedMetric.formula",
                )
            if any(
                k in native for k in ("baseLogicalTable", "expr", "defaultAggregation")
            ):
                return (
                    section,
                    {},
                    "CALCULATED_METRIC cannot contain measure-only fields",
                )
        else:
            return section, {}, "metric requires an explicit server semanticType"
    if section == "approvedRelationships":
        pairs = native.get("relationshipColumns")
        if (
            not all(native.get(k) for k in ("name", "leftTable", "rightTable"))
            or not isinstance(pairs, list)
            or not pairs
        ):
            return (
                section,
                {},
                "relationship requires named physical endpoint bindings and explicit column pairs",
            )
        if any(
            not isinstance(pair, Mapping)
            or not pair.get("leftColumn")
            or not pair.get("rightColumn")
            for pair in pairs
        ):
            return (
                section,
                {},
                "relationship column pairs require leftColumn and rightColumn",
            )
    if section == "verifiedQueries":
        if (
            not native.get("name")
            or not native.get("question")
            or not (native.get("sql") or native.get("sqlTemplate"))
        ):
            return (
                section,
                {},
                "query requires an explicit server-native name, question and SQL or template",
            )
        # Imported trust is preserved; local verification never implicitly upgrades it.
        if native.get("trusted") is True and props.get("source_kind") != "semanticspec":
            return (
                section,
                {},
                "trusted query requires preserved server provenance; local verification is insufficient",
            )
        native.setdefault("trusted", False)
    if section not in {
        *_MODEL_PARTS,
        "metricDefinitions",
        "verifiedQueries",
        "glossary",
        "dataReferences",
    }:
        return (
            section,
            {},
            "this graph item has no supported typed SemanticSpec mapping",
        )
    return section, native, ""


def project_semanticspec(
    graph: Mapping[str, Any], selected_ids: Sequence[str]
) -> dict[str, Any]:
    """Return a reviewable projection, its dependency closure, and every gap.

    ``publishable`` means that structural/evidence checks and the finite MEASURE
    scalar AST checks passed. It is not schema/business SQL validation, derived
    formula validation, server acceptance or authorization.
    Empty selection produces an explicit issue instead of selecting everything.
    """
    if isinstance(selected_ids, (str, bytes)) or any(
        not isinstance(v, str) for v in selected_ids
    ):
        raise KnowledgeError("selected_ids must be a sequence of graph IDs")
    nodes = {
        n["id"]: n
        for n in graph.get("nodes", [])
        if isinstance(n, Mapping) and isinstance(n.get("id"), str)
    }
    edges = {
        e["id"]: e
        for e in graph.get("edges", [])
        if isinstance(e, Mapping) and isinstance(e.get("id"), str)
    }
    evidence = {
        e["id"]: e
        for e in graph.get("evidence", [])
        if isinstance(e, Mapping) and isinstance(e.get("id"), str)
    }
    documents = {
        d["id"]: d
        for d in graph.get("documents", [])
        if isinstance(d, Mapping) and isinstance(d.get("id"), str)
    }
    versions = {
        v["id"]: v
        for v in graph.get("document_versions", [])
        if isinstance(v, Mapping) and isinstance(v.get("id"), str)
    }
    sources = {
        s["id"]: s["current_revision"]
        for s in graph.get("sources", [])
        if isinstance(s, Mapping)
        and isinstance(s.get("id"), str)
        and isinstance(s.get("current_revision"), str)
    }
    if (
        graph.get("fragment") is True
        and "kb_id" not in graph
        and isinstance(graph.get("source_id"), str)
        and isinstance(graph.get("source_revision"), str)
    ):
        sources.setdefault(graph["source_id"], graph["source_revision"])
    items = {**nodes, **edges}
    issues: list[dict[str, str]] = []
    unmapped: list[dict[str, str]] = []
    manifest: list[dict[str, Any]] = []
    validated_measures: list[str] = []

    def issue(code: str, item_id: str, message: str) -> None:
        record = {"code": code, "id": item_id, "message": message}
        if record not in issues:
            issues.append(record)

    by_key: dict[tuple[str, str], list[str]] = {}
    payloads: dict[str, tuple[str, dict[str, Any], str]] = {}
    for item_id, item in items.items():
        payloads[item_id] = _projection_payload(item)
        section, native, _ = payloads[item_id]
        key = native.get("term" if section == "glossary" else "name")
        if isinstance(key, str):
            by_key.setdefault((section, key), []).append(item_id)
    outgoing: dict[str, list[str]] = {}
    for edge_id, edge in edges.items():
        if edge.get("predicate") in _DEPENDENCIES:
            outgoing.setdefault(str(edge.get("subject", "")), []).append(edge_id)
    queue = deque(dict.fromkeys(selected_ids))
    included: set[str] = set()
    if not queue:
        issue("empty_selection", "", "select graph items explicitly")
    while queue:
        item_id = queue.popleft()
        if item_id in included:
            continue
        found = items.get(item_id)
        if found is None:
            issue(
                "missing_item",
                item_id,
                "selected or required graph item does not exist",
            )
            continue
        included.add(item_id)
        if item_id in edges:
            queue.extend(str(found.get(key, "")) for key in ("subject", "object"))
        for edge_id in outgoing.get(item_id, []):
            queue.extend([edge_id, str(edges[edge_id].get("object", ""))])
        section, native, _ = payloads[item_id]
        for ref in _references(section, native):
            matches = by_key.get(ref, [])
            if len(matches) != 1:
                issue(
                    "missing_dependency" if not matches else "ambiguous_dependency",
                    item_id,
                    f"dependency {ref[0]}.{ref[1]} must resolve to exactly one graph item",
                )
            else:
                queue.append(matches[0])
    # Also expose unmapped business relations inside the selected subgraph.
    included.update(
        edge_id
        for edge_id, edge in edges.items()
        if edge.get("subject") in included and edge.get("object") in included
    )
    for item_id in sorted(_cyclic_metric_dependencies(included, payloads, by_key)):
        issue(
            "dependency_cycle",
            item_id,
            "executable metric dependencies must be acyclic",
        )
    sections: dict[str, Any] = {}
    keys_seen: dict[tuple[str, str], str] = {}
    references: dict[str, dict[str, str]] = {}
    for item_id in sorted(included):
        item = items[item_id]
        ids = _evidence_ids(item)
        if item.get("status") != "confirmed":
            issue(
                "unconfirmed_item",
                item_id,
                "only confirmed graph items can be projected",
            )
            continue
        if not ids or any(
            eid not in evidence
            or any(
                not isinstance(evidence[eid].get(key), str)
                or not evidence[eid].get(key)
                for key in (
                    "source_kind",
                    "source_id",
                    "source_version",
                    "content_hash",
                )
            )
            for eid in ids
        ):
            issue(
                "missing_evidence",
                item_id,
                "projected items require existing pinned evidence",
            )
            continue
        invalid_proofs = [
            _current_evidence_error(evidence[eid], documents, versions, sources)
            for eid in ids
        ]
        if any(invalid_proofs):
            for error in invalid_proofs:
                if error is not None:
                    issue(error[0], item_id, error[1])
            continue
        section, native, reason = payloads[item_id]
        if section == "metricDefinitions" and native.get("semanticType") == "MEASURE":
            expression_issue = _validate_measure_expression(native.get("expr"))
            if expression_issue is not None:
                code, message = expression_issue
                issue(code, item_id, message)
                continue
            validated_measures.append(item_id)
        if section == "verifiedQueries" and native.get("trusted") is True:
            props = _properties(item)
            preserved = any(
                evidence[eid].get("source_kind") == "semanticspec"
                and evidence[eid].get("source_id") == props.get("source_id")
                and evidence[eid].get("source_version") == props.get("source_revision")
                and _properties(evidence[eid]).get("native_snapshot") == native
                for eid in ids
            )
            if not preserved:
                issue(
                    "query_trust_evidence",
                    item_id,
                    "query trust can only be preserved from the exact unchanged server snapshot",
                )
                continue
        if (
            item_id in edges
            and item.get("predicate") in _DEPENDENCIES
            and not _native(item)
        ):
            manifest.append(
                {
                    "id": item_id,
                    "section": None,
                    "role": "dependency",
                    "evidence_ids": ids,
                }
            )
            continue
        if (
            item_id in edges
            and item.get("predicate") != "joins_with"
            and section == "approvedRelationships"
        ):
            reason = (
                "only explicitly bound joins_with edges map to approvedRelationships"
            )
        if item_id in edges and section == "approvedRelationships" and not reason:
            endpoints_match = all(
                payloads.get(str(item.get(endpoint)), ("", {}, ""))[0]
                == "logicalTables"
                and payloads[str(item.get(endpoint))][1].get("name")
                == native.get(table_field)
                for endpoint, table_field in (
                    ("subject", "leftTable"),
                    ("object", "rightTable"),
                )
            )
            if not endpoints_match:
                issue(
                    "relationship_binding_mismatch",
                    item_id,
                    "native relationship endpoints must match the graph's explicitly bound physical tables",
                )
                continue
        if reason:
            unmapped.append(
                {"id": item_id, "kind": str(item.get("kind", "edge")), "reason": reason}
            )
            continue
        key = str(native.get("term" if section == "glossary" else "name", ""))
        if key and (section, key) in keys_seen:
            issue(
                "duplicate_natural_key",
                item_id,
                f"{section}.{key} duplicates {keys_seen[(section, key)]}",
            )
            continue
        keys_seen[(section, key)] = item_id
        if section == "logicalTables":
            physical = _physical_ref(native["baseTable"])
            assert physical is not None
            references[canonical_json(physical)] = physical
        if section == "dataReferences":
            physical = {
                k: native[k] for k in ("project", "schema", "table") if k in native
            }
            if not physical.get("project") or not physical.get("table"):
                issue(
                    "invalid_data_reference",
                    item_id,
                    "data reference requires project and table",
                )
                continue
            references[canonical_json(physical)] = physical
        elif section in _MODEL_PARTS:
            sections.setdefault("semanticModel", {}).setdefault(section, []).append(
                native
            )
        else:
            sections.setdefault(section, []).append(native)
        manifest.append(
            {
                "id": item_id,
                "section": "semanticModel." + section
                if section in _MODEL_PARTS
                else section,
                "natural_key": key,
                "evidence_ids": ids,
            }
        )
    if references:
        sections["dataReferences"] = [references[k] for k in sorted(references)]
        if len(references) > 20:
            issue(
                "data_reference_limit",
                "",
                "SemanticSpec permits at most 20 physical data references",
            )
    return {
        "sections": sections,
        "mapping_manifest": manifest,
        "unmapped": unmapped,
        "validation_issues": issues,
        "source_revision": graph.get("revision"),
        "source_kb_id": graph.get("kb_id"),
        "selected_ids": list(dict.fromkeys(selected_ids)),
        "included_ids": sorted(included),
        "publishable": bool(sections) and not issues and not unmapped,
        "validation_scope": "structural_and_measure_scalar_ast",
        "sql_validation": {
            "validated_measure_ids": validated_measures,
            "unchecked": [
                "schema_and_business_semantics",
                "runtime_function_behavior",
                "calculated_formula_grammar",
                "named_filter_expressions",
                "verified_query_sql",
            ],
        },
    }


def _fragment(source_id: str, source_revision: str) -> dict[str, Any]:
    return {
        "kind": GRAPH_KIND,
        "schema_version": SCHEMA_VERSION,
        "fragment": True,
        "source_id": source_id,
        "source_revision": source_revision,
        "nodes": [],
        "edges": [],
        "evidence": [],
    }


def _add_source_item(
    fragment: dict[str, Any],
    *,
    namespace: str,
    section: str,
    native: Mapping[str, Any],
    kind: str,
    name: str,
    status: str,
    source_kind: str,
    source_revision: str,
    key: str,
    scope: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item_id = stable_id(namespace, section, key)
    excerpt = canonical_json(native)
    eid = stable_id(namespace, source_revision, section, key, content_hash(excerpt))
    props = {
        "source_kind": source_kind,
        "source_id": namespace,
        "source_revision": source_revision,
        "server_section": section,
        "server_native": deepcopy(dict(native)),
    }
    if extra:
        props.update(extra)
    item = {
        "id": item_id,
        "kind": kind,
        "name": name,
        "scope": scope,
        "status": status,
        "evidence_ids": [eid],
        "properties": props,
    }
    fragment["nodes"].append(item)
    fragment["evidence"].append(
        {
            "id": eid,
            "source_kind": source_kind,
            "source_id": namespace,
            "source_version": source_revision,
            "content_hash": content_hash(excerpt),
            "start_line": None,
            "end_line": None,
            "excerpt": excerpt,
            "properties": {
                "section": section,
                "native_snapshot": deepcopy(dict(native)),
            },
        }
    )
    return item


def _unwrap_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(snapshot)
    if normalized.get("ok") is False or normalized.get("success") is False:
        raise KnowledgeError("cannot import an unsuccessful SemanticSpec response")
    data = normalized.get("data")
    if isinstance(data, Mapping):
        normalized.update(data)
    spec = normalized.get("semantic_spec")
    if isinstance(spec, Mapping):
        normalized = {**spec, **normalized}
    binding = normalized.get("binding")
    if isinstance(binding, Mapping):
        normalized = {**binding, **normalized}
    normalized.setdefault("endpoint", normalized.get("remote_url"))
    normalized.setdefault("namespace", normalized.get("account_id"))
    return normalized


def _snapshot_sections(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    from maxcompute_semantic.remote.sync import section_integrity

    raw = snapshot.get("sections")
    if isinstance(raw, Mapping):
        entries = [(str(section), content) for section, content in raw.items()]
    elif isinstance(raw, list):
        entries = []
        for entry in raw:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("section"), str
            ):
                raise KnowledgeError(
                    "SemanticSpec section snapshots require section and content"
                )
            entries.append((entry["section"], entry))
    else:
        raise KnowledgeError("SemanticSpec snapshot requires section content")
    sections: dict[str, Any] = {}
    seen: set[str] = set()
    for section, value in entries:
        if section in seen:
            raise KnowledgeError("SemanticSpec snapshot contains duplicate sections")
        seen.add(section)
        if isinstance(value, Mapping) and ("present" in value or "section" in value):
            if "present" in value and type(value["present"]) is not bool:
                raise KnowledgeError("SemanticSpec section present must be a boolean")
            if value.get("present") is False:
                if (
                    "content" in value
                    or value.get("content_hash", "")
                    or value.get("size_bytes", 0) != 0
                ):
                    raise KnowledgeError(
                        "absent SemanticSpec section cannot carry content or integrity metadata"
                    )
                continue
            if "content" not in value:
                raise KnowledgeError(
                    "SemanticSpec section snapshots require section and content"
                )
            content = value["content"]
            size, digest = section_integrity(content)
            if "content_hash" in value and value["content_hash"] != digest:
                raise KnowledgeError(
                    "SemanticSpec section content_hash does not match its body"
                )
            if "size_bytes" in value and (
                type(value["size_bytes"]) is not int or value["size_bytes"] != size
            ):
                raise KnowledgeError(
                    "SemanticSpec section size_bytes does not match its body"
                )
        else:
            content = value
        sections[section] = deepcopy(content)
    return sections


def import_semanticspec(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Import a revision-pinned remote definition without changing native trust.

    IDs use endpoint/namespace/region/spec ID plus natural keys, independently of revisions.
    Published source means a confirmed *definition*, not proven business truth.
    """
    snapshot = _unwrap_snapshot(snapshot)
    name, revision = snapshot.get("name"), snapshot.get("revision_id")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(revision, str)
        or not revision
    ):
        raise KnowledgeError(
            "SemanticSpec snapshot requires name and exact revision_id"
        )
    source = snapshot.get("source")
    if source not in {"PUBLISHED", "USER_DRAFT", "SYSTEM_SUGGESTIONS"}:
        raise KnowledgeError(
            "SemanticSpec snapshot requires an explicit supported source"
        )
    identity_fields = ("endpoint", "namespace", "region", "semantic_spec_id")
    if any(
        not isinstance(snapshot.get(key), str) or not snapshot[key].strip()
        for key in identity_fields
    ):
        raise KnowledgeError(
            "SemanticSpec source identity requires endpoint, namespace, region and semantic_spec_id"
        )
    namespace = "semanticspec:" + stable_id(*(snapshot[key] for key in identity_fields))
    fragment = _fragment(namespace, revision)
    fragment["source"] = source
    fragment["source_snapshot"] = {
        k: deepcopy(snapshot[k])
        for k in ("name", "revision_id", "source", *identity_fields)
        if k in snapshot
    }
    sections = _snapshot_sections(snapshot)
    fragment["source_sections"] = deepcopy(sections)
    fragment["source_snapshots"] = deepcopy(snapshot["sections"])
    status = "confirmed" if source == "PUBLISHED" else "candidate"
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    pending_edges: list[tuple[str, Mapping[str, Any], str, str, str]] = []
    for section, content in sections.items():
        if section == "semanticModel":
            if not isinstance(content, Mapping):
                raise KnowledgeError("semanticModel content must be an object")
            parts = [(part, content.get(part, [])) for part in _MODEL_PARTS]
        elif section in {"instructions", "benchmarks"} or section not in _SECTIONS:
            parts = [(section, [content])]
        else:
            parts = [(section, content)]
        for part, values in parts:
            if not isinstance(values, list) or any(
                not isinstance(v, Mapping) for v in values
            ):
                raise KnowledgeError(f"{part} content must contain objects")
            for position, native in enumerate(values):
                item_name = str(
                    native.get("term" if part == "glossary" else "name")
                    or f"{part}_{position}"
                )
                key = (
                    canonical_json(
                        {
                            k: native[k]
                            for k in ("project", "schema", "table")
                            if k in native
                        }
                    )
                    if part == "dataReferences"
                    else item_name
                )
                kind = {
                    "glossary": "term",
                    "logicalTables": "table",
                    "metricDefinitions": "metric",
                    "verifiedQueries": "query",
                    "dataReferences": "table",
                }.get(part, "claim")
                if part == "approvedRelationships":
                    pending_edges.append((part, native, item_name, key, status))
                    continue
                item = _add_source_item(
                    fragment,
                    namespace=namespace,
                    section=part,
                    native=native,
                    kind=kind,
                    name=item_name,
                    status=status,
                    source_kind="semanticspec",
                    source_revision=revision,
                    key=key,
                    scope=str(snapshot.get("scope", "")),
                )
                indexed[(part, item_name)] = item
    for part, native, item_name, key, edge_status in pending_edges:
        left = indexed.get(("logicalTables", str(native.get("leftTable", ""))))
        right = indexed.get(("logicalTables", str(native.get("rightTable", ""))))
        item = _add_source_item(
            fragment,
            namespace=namespace,
            section=part,
            native=native,
            kind="claim",
            name=item_name,
            status=edge_status,
            source_kind="semanticspec",
            source_revision=revision,
            key=key,
        )
        if left and right:
            fragment["nodes"].remove(item)
            fragment["edges"].append(
                {
                    "id": item["id"],
                    "subject": left["id"],
                    "object": right["id"],
                    "predicate": "joins_with",
                    "status": edge_status,
                    "evidence_ids": item["evidence_ids"],
                    "properties": item["properties"],
                }
            )
        else:
            item["properties"]["unresolved_endpoints"] = True
    for item in list(fragment["nodes"]):
        for ref in _references(_section(item), _native(item)):
            target = indexed.get(ref)
            if target:
                fragment["edges"].append(
                    {
                        "id": stable_id(
                            namespace, item["id"], "depends_on", target["id"]
                        ),
                        "subject": item["id"],
                        "predicate": "depends_on",
                        "object": target["id"],
                        "status": status,
                        "evidence_ids": item["evidence_ids"],
                        "properties": {"source_kind": "semanticspec"},
                    }
                )
    return fragment


def import_package(
    package_db: PackageDB, profile: Profile | Mapping[str, Any] | str | None = None
) -> dict[str, Any]:
    """Read public PackageDB methods only; keep inferred relations as candidates.

    Only the profile name is read. Credentials, helpers and environment values
    never enter the graph. SQL content is preserved rather than parsed.
    """
    if isinstance(profile, str):
        profile_name = profile
    elif isinstance(profile, Mapping):
        profile_name = str(profile.get("name", "local"))
    else:
        profile_name = str(getattr(profile, "name", "local"))
    namespace = "package:" + profile_name
    tables = package_db.list_tables()
    columns = package_db.get_columns_bulk([t["id"] for t in tables])
    metrics, joins = package_db.list_metrics(), package_db.list_joins()
    candidates = package_db.list_join_candidates()
    memories = package_db.list_memories(limit=100_000)
    revision = content_hash(
        canonical_json(
            {
                "tables": tables,
                "columns": columns,
                "metrics": metrics,
                "joins": joins,
                "candidates": candidates,
                "memories": memories,
            }
        )
    )
    fragment = _fragment(namespace, revision)
    table_ids: dict[tuple[str, str], str] = {}

    def add(
        kind: str,
        name: str,
        native: Mapping[str, Any],
        key: str,
        status: str = "candidate",
    ) -> dict[str, Any]:
        item = _add_source_item(
            fragment,
            namespace=namespace,
            section="package." + kind,
            native=native,
            kind=kind,
            name=name,
            status=status,
            source_kind="package",
            source_revision=revision,
            key=key,
            scope=profile_name,
        )
        # Package metadata is not a server-native binding.
        item["properties"]["package_record"] = item["properties"].pop("server_native")
        item["properties"].pop("server_section")
        return item

    for table in tables:
        source_key, table_name = table["source_key"], table["name"]
        row = {
            k: table[k]
            for k in (
                "source_key",
                "name",
                "schema_hash",
                "last_built_at",
                "table_type",
                "ai_context",
            )
            if k in table
        }
        item = add("table", table_name, row, f"{source_key}:{table_name}", "confirmed")
        table_ids[(source_key, table_name)] = item["id"]
        for column in columns.get(table["id"], []):
            row = {
                k: column[k]
                for k in (
                    "name",
                    "type",
                    "comment",
                    "is_partition",
                    "semantic_role",
                    "dim_type",
                    "agg",
                    "id_type",
                    "references_target",
                    "semantic_description",
                )
                if k in column
            }
            col = add(
                "column",
                column["name"],
                row,
                f"{source_key}:{table_name}:{column['name']}",
                "confirmed",
            )
            fragment["edges"].append(
                {
                    "id": stable_id(namespace, item["id"], "has_column", col["id"]),
                    "subject": item["id"],
                    "predicate": "has_column",
                    "object": col["id"],
                    "status": "confirmed",
                    "evidence_ids": col["evidence_ids"],
                    "properties": {"source_kind": "package"},
                }
            )
    for metric in metrics:
        add("metric", metric["name"], metric, str(metric["name"]))
    for origin, rows in (("joins", joins), ("join_candidates", candidates)):
        for row in rows:
            left = table_ids.get((row["left_source_key"], row["left_table"]))
            right = table_ids.get((row["right_source_key"], row["right_table"]))
            key = canonical_json(
                [
                    origin,
                    row["left_source_key"],
                    row["left_table"],
                    row["left_col"],
                    row["right_source_key"],
                    row["right_table"],
                    row["right_col"],
                ]
            )
            assertion = add(
                "claim",
                f"{row['left_table']}.{row['left_col']} → {row['right_table']}.{row['right_col']}",
                row,
                key,
            )
            if left and right:
                fragment["nodes"].remove(assertion)
                fragment["edges"].append(
                    {
                        "id": assertion["id"],
                        "subject": left,
                        "predicate": "joins_with",
                        "object": right,
                        "status": "candidate",
                        "evidence_ids": assertion["evidence_ids"],
                        "properties": {
                            **assertion["properties"],
                            "confidence": row.get("confidence"),
                            "approval": "not_confirmed",
                            "origin": origin,
                        },
                    }
                )
    for memory in memories:
        try:
            payload = json.loads(memory.get("payload_json", "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {"raw_payload": memory.get("payload_json")}
        if not isinstance(payload, Mapping):
            payload = {"payload": payload}
        kind = (
            "query"
            if memory.get("kind")
            in {"verified_queries", "sample_sql", "failed_queries"}
            else "claim"
        )
        native = {
            "memory_kind": memory.get("kind"),
            "payload": dict(payload),
            "created_at": memory.get("created_at"),
        }
        item = add(
            kind,
            str(
                payload.get("name")
                or payload.get("question")
                or f"memory_{memory['id']}"
            ),
            native,
            str(memory["id"]),
        )
        if kind == "query":
            item["properties"]["trusted"] = False
    return fragment
