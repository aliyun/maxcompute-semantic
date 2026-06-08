# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Semantic proposal construction and application helpers.

The proposal layer is intentionally a review queue, not a second annotation
writer. Builders create suggested patches with evidence. Apply paths write
through existing PackageDB annotation methods so all validation stays in one
place.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.errors.annotate import AnnotateValidationError
from maxcompute_semantic.mc_client.errors import McsError

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile


class SemanticProposalError(McsError):
    """Raised when a semantic proposal cannot be read or applied."""

    code = "SemanticProposalError"
    exit_code = 2


_REQUIRED_COLUMN_PATCH_FIELDS = ("source_key", "table", "column")
_REQUIRED_TABLE_PATCH_FIELDS = ("source_key", "table")
_SUPPORTED_PATCH_KINDS = frozenset(
    {"column_semantics", "table_ai_context", "column_description", "metric"}
)
_COLUMN_SEMANTICS_YAML_KEYS = frozenset(
    {
        "role",
        "dim_type",
        "agg",
        "aggregation",
        "id_type",
        "references",
        "references_target",
        "subtype",
    }
)


def proposal_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Decode a semantic_proposals row into a JSON-ready object."""
    out = dict(row)
    for key in ("patch_json", "evidence_json", "validation_json"):
        value = out.pop(key, None)
        decoded_key = key.removesuffix("_json")
        if value in (None, ""):
            out[decoded_key] = None if key == "validation_json" else []
            continue
        try:
            out[decoded_key] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            out[decoded_key] = value
    return out


def _annotation_proposal_key(suggestion: dict[str, Any]) -> str:
    return (
        "annotation:"
        f"{suggestion['source_key']}:"
        f"{suggestion['table_name']}:"
        f"{suggestion['column_name']}:"
        f"{suggestion['suggested_role']}"
    )


def _patch_from_annotation_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    role = suggestion["suggested_role"]
    subtype = suggestion.get("suggested_subtype")
    return {
        "kind": "column_semantics",
        "source_key": suggestion["source_key"],
        "table": suggestion["table_name"],
        "column": suggestion["column_name"],
        "role": role,
        "dim_type": subtype if role == "dimension" else None,
        "agg": subtype.upper() if role == "measure" and subtype else None,
        "id_type": subtype if role == "identifier" else None,
        "references_target": None,
        "semantic_description": None,
    }


def create_annotation_promotion_proposals(
    db: PackageDB,
    *,
    min_confidence: float = 0.7,
) -> dict[str, int]:
    """Promote high-confidence annotation_suggestions into proposal rows."""
    created = 0
    updated = 0
    skipped = 0
    existing_by_key = {row["proposal_key"]: row for row in db.list_semantic_proposals(limit=None)}
    for suggestion in db.list_annotation_suggestions():
        confidence = float(suggestion.get("confidence") or 0.0)
        if confidence < min_confidence:
            skipped += 1
            continue
        proposal_key = _annotation_proposal_key(suggestion)
        existing = existing_by_key.get(proposal_key)
        if existing is not None and existing.get("status") != "suggested":
            skipped += 1
            continue
        evidence = suggestion.get("evidence")
        if evidence is None:
            raw = suggestion.get("evidence_json") or "[]"
            try:
                evidence = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                evidence = []
        patch = _patch_from_annotation_suggestion(suggestion)
        target_ref = (
            f"{suggestion['source_key']}.{suggestion['table_name']}.{suggestion['column_name']}"
        )
        db.upsert_semantic_proposal(
            proposal_key=proposal_key,
            target_type="column",
            target_ref=target_ref,
            operation="promote_annotation_suggestion",
            patch=patch,
            confidence=confidence,
            evidence=evidence,
            provenance="build.annotation_suggestions",
            created_by="mcs-build",
        )
        if existing is None:
            created += 1
            existing_by_key[proposal_key] = {"status": "suggested"}
        else:
            updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _validation_payload(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "code": getattr(exc, "code", exc.__class__.__name__),
        "message": str(exc),
    }


def _record_validation_failure(
    db: PackageDB,
    proposal_id: int,
    *,
    reviewed_by: str | None,
    exc: Exception,
) -> None:
    db.update_semantic_proposal_status(
        proposal_id,
        status="suggested",
        reviewed_by=reviewed_by,
        validation=_validation_payload(exc),
    )


def _column_semantics_patch(proposal_id: int, patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has malformed patch payload",
            remediation="patch must be a JSON object with kind='column_semantics'",
        )
    if patch.get("kind") != "column_semantics":
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has unsupported patch kind",
            remediation="this mcs version can apply only kind='column_semantics'",
        )
    missing = [field for field in _REQUIRED_COLUMN_PATCH_FIELDS if patch.get(field) in (None, "")]
    if missing:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} is missing required patch fields: "
            f"{', '.join(missing)}",
            remediation=("recreate the proposal with source_key, table, and column in the patch"),
        )
    return patch


def _table_ai_context_patch(proposal_id: int, patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or patch.get("kind") != "table_ai_context":
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has unexpected patch kind for table_ai_context",
            remediation="patch must have kind='table_ai_context'",
        )
    missing = [f for f in _REQUIRED_TABLE_PATCH_FIELDS if patch.get(f) in (None, "")]
    if missing:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} is missing required fields: {', '.join(missing)}",
            remediation="recreate with source_key and table in the patch",
        )
    if "ai_context" not in patch:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} is missing ai_context field",
            remediation="patch must include an ai_context string",
        )
    return patch


def _column_description_patch(proposal_id: int, patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or patch.get("kind") != "column_description":
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has unexpected patch kind for column_description",
            remediation="patch must have kind='column_description'",
        )
    missing = [f for f in _REQUIRED_COLUMN_PATCH_FIELDS if patch.get(f) in (None, "")]
    if missing:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} is missing required fields: {', '.join(missing)}",
            remediation="recreate with source_key, table, and column in the patch",
        )
    if "description" not in patch:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} is missing description field",
            remediation="patch must include a description string",
        )
    return patch


def _metric_patch(proposal_id: int, patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or patch.get("kind") != "metric":
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has unexpected patch kind for metric",
            remediation="patch must have kind='metric'",
        )
    for field in ("name", "expression"):
        if not patch.get(field):
            raise SemanticProposalError(
                f"semantic proposal {proposal_id} is missing required field: {field}",
                remediation=f"patch must include a non-empty {field} string",
            )
    return patch


def _validate_patch(proposal_id: int, patch: Any) -> dict[str, Any]:
    """Dispatch to the right validator by patch kind."""
    if not isinstance(patch, dict):
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has malformed patch payload",
            remediation="patch must be a JSON object with a 'kind' field",
        )
    kind = patch.get("kind")
    if kind == "column_semantics":
        return _column_semantics_patch(proposal_id, patch)
    if kind == "table_ai_context":
        return _table_ai_context_patch(proposal_id, patch)
    if kind == "column_description":
        return _column_description_patch(proposal_id, patch)
    if kind == "metric":
        return _metric_patch(proposal_id, patch)
    raise SemanticProposalError(
        f"semantic proposal {proposal_id} has unsupported patch kind {kind!r}",
        remediation=f"supported kinds: {', '.join(sorted(_SUPPORTED_PATCH_KINDS))}",
    )


def _apply_patch(db: PackageDB, patch: dict[str, Any]) -> None:
    """Write a validated patch through existing PackageDB methods."""
    kind = patch["kind"]
    if kind == "column_semantics":
        db.set_column_semantics(
            patch["source_key"],
            patch["table"],
            patch["column"],
            role=patch.get("role"),
            dim_type=patch.get("dim_type"),
            agg=patch.get("agg"),
            id_type=patch.get("id_type"),
            references_target=patch.get("references_target"),
            semantic_description=patch.get("semantic_description"),
        )
    elif kind == "table_ai_context":
        db.set_table_ai_context(
            patch["source_key"],
            patch["table"],
            patch["ai_context"],
        )
    elif kind == "column_description":
        existing = db.get_column_semantics(
            patch["source_key"], patch["table"], patch["column"]
        )
        db.set_column_semantics(
            patch["source_key"],
            patch["table"],
            patch["column"],
            role=(existing or {}).get("semantic_role"),
            dim_type=(existing or {}).get("dim_type"),
            agg=(existing or {}).get("agg"),
            id_type=(existing or {}).get("id_type"),
            references_target=(existing or {}).get("references_target"),
            semantic_description=patch["description"],
        )
    elif kind == "metric":
        from maxcompute_semantic.metric_validator import validate_metric_expression

        lint = validate_metric_expression(patch["expression"], db)
        if not lint.ok:
            raise AnnotateValidationError(
                f"metric expression is invalid: {lint.error}",
                remediation="fix the expression and re-propose",
            )
        db.add_metric(
            name=patch["name"],
            expression=patch["expression"],
            description=patch.get("description"),
            ai_context=patch.get("ai_context"),
        )


def apply_semantic_proposal(
    db: PackageDB,
    proposal_id: int,
    *,
    reviewed_by: str | None = None,
) -> dict[str, Any]:
    """Apply one semantic proposal through existing PackageDB write methods."""
    row = db.get_semantic_proposal(proposal_id)
    if row is None:
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} not found",
            remediation="run `mcs package list-proposals` and choose an existing id",
        )
    if row.get("status") != "suggested":
        raise SemanticProposalError(
            f"semantic proposal {proposal_id} has status {row.get('status')!r}",
            remediation="only proposals with status='suggested' can be applied",
        )
    payload = proposal_payload(row)

    try:
        patch = _validate_patch(proposal_id, payload.get("patch"))
        _apply_patch(db, patch)
    except McsError as exc:
        _record_validation_failure(db, proposal_id, reviewed_by=reviewed_by, exc=exc)
        raise

    db.update_semantic_proposal_status(
        proposal_id,
        status="applied",
        reviewed_by=reviewed_by,
        validation={"ok": True},
    )
    return {
        "applied": True,
        "id": proposal_id,
        "target_ref": row["target_ref"],
        "patch": patch,
    }


def _normalize_agent_column_semantics_spec(
    spec: dict[str, Any],
) -> dict[str, Any]:
    role = spec.get("role")
    if isinstance(role, str) and role.strip().lower() == "none":
        role = None

    dim_type = spec.get("dim_type")
    id_type = spec.get("id_type")
    agg = spec.get("agg") or spec.get("aggregation")
    subtype = spec.get("subtype")
    if subtype is not None:
        if role == "dimension" and not dim_type:
            dim_type = subtype
        elif role == "identifier" and not id_type:
            id_type = subtype
        elif role == "measure" and not agg:
            agg = subtype
    if isinstance(agg, str) and agg.strip().lower() in ("none", "null", ""):
        agg = None
    if isinstance(agg, str):
        lowered = agg.strip().lower()
        if "_or_" in lowered:
            agg = lowered.split("_or_", 1)[0]
        agg = agg.upper()

    return {
        "role": role,
        "dim_type": dim_type,
        "agg": agg,
        "id_type": id_type,
        "references_target": spec.get("references_target") or spec.get("references"),
        "semantic_description": spec.get("semantic_description") or spec.get("description"),
    }


_ENTRY_KEY_DID_YOU_MEAN: dict[str, str] = {
    "description": "ai_context",
    "desc": "ai_context",
    "comment": "ai_context",
    "context": "ai_context",
    "ai_description": "ai_context",
    "ai_desc": "ai_context",
    "table_description": "ai_context",
    "col": "columns",
    "column": "columns",
    "cols": "columns",
    "fields": "columns",
}


def _normalize_entry_keys(entry: dict[str, Any]) -> dict[str, Any]:
    """Silently promote common typo keys to their canonical forms."""
    out = dict(entry)
    for typo, canonical in _ENTRY_KEY_DID_YOU_MEAN.items():
        if typo in out and canonical not in out:
            out[canonical] = out.pop(typo)
    return out


_COLUMN_NAME_KEYS = ("name", "column", "col", "column_name")


def _coerce_columns_list(columns: list[Any]) -> dict[str, Any]:
    """Convert [{name: col, role: ...}, ...] list form to {col: {...}} dict."""
    result: dict[str, Any] = {}
    for item in columns:
        if not isinstance(item, dict):
            continue
        col = dict(item)
        name = None
        for key in _COLUMN_NAME_KEYS:
            if key in col:
                name = col.pop(key)
                break
        if name:
            result[name] = col
    return result


def create_proposals_from_yaml(
    db: PackageDB,
    yaml_content: str,
    *,
    created_by: str = "agent",
    source_key: str | None = None,
    profile: Profile | None = None,
) -> dict[str, int]:
    """Create proposals from YAML (ai_context + descriptions + metrics).

    Accepts tables and/or metrics::

        tables:
          - table: results
            ai_context: "Each row is one driver's race result."
            columns:
              points: {description: "Points scored in this single race."}
        metrics:
          - name: total_revenue
            expression: SUM(orders.amount)
            description: Gross order revenue

    Each ``ai_context`` entry creates a ``table_ai_context`` proposal.
    Each column ``description`` entry creates a ``column_description`` proposal.
    Columns with semantic fields (``role``, ``dim_type``, ``agg``, ``id_type``,
    ``references``) create a ``column_semantics`` proposal.
    Each metric entry creates a ``metric`` proposal.
    """
    from ruamel.yaml import YAML

    y = YAML(typ="safe")
    data = y.load(yaml_content)
    if data is None:
        return {"created": 0, "skipped": 0}

    from maxcompute_semantic.commands._table_resolve import resolve_table_to_source

    entries: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if "tables" in data and isinstance(data["tables"], list):
            entries = data["tables"]
        elif "table" in data:
            entries = [data]
    elif isinstance(data, list):
        entries = data

    created = 0
    updated = 0
    reopened = 0
    skipped = 0
    existing_by_key = {row["proposal_key"]: row for row in db.list_semantic_proposals(limit=None)}

    def _upsert_agent_proposal(
        *,
        proposal_key: str,
        target_type: str,
        target_ref: str,
        operation: str,
        patch: dict[str, Any],
    ) -> None:
        nonlocal created, updated, reopened, skipped
        existing = existing_by_key.get(proposal_key)
        if existing is None:
            created += 1
        elif existing.get("status") == "suggested":
            updated += 1
        elif existing.get("status") == "rejected":
            reopened += 1
        else:
            skipped += 1
            return
        db.upsert_semantic_proposal(
            proposal_key=proposal_key,
            target_type=target_type,
            target_ref=target_ref,
            operation=operation,
            patch=patch,
            confidence=1.0,
            evidence=[{"source": "agent", "method": "from_stdin"}],
            provenance="agent.from_stdin",
            created_by=created_by,
            reopen_rejected=True,
        )
        existing_by_key[proposal_key] = {"status": "suggested"}

    resolved_entries: list[tuple[dict[str, Any], str, str]] = []
    for entry in entries:
        entry = _normalize_entry_keys(entry)
        table_ref = entry.get("table") or entry.get("name")
        if not table_ref:
            skipped += 1
            continue
        sk, table = resolve_table_to_source(
            str(table_ref),
            db,
            source_key=source_key or entry.get("source") or None,
            profile=profile,
        )
        resolved_entries.append((entry, sk, table))

    for entry, sk, table in resolved_entries:
        ai_ctx = entry.get("ai_context")
        if ai_ctx:
            _upsert_agent_proposal(
                proposal_key=f"ai_context:{sk}:{table}",
                target_type="table",
                target_ref=f"{sk}.{table}",
                operation="set_ai_context",
                patch={
                    "kind": "table_ai_context",
                    "source_key": sk,
                    "table": table,
                    "ai_context": ai_ctx,
                },
            )

        columns = entry.get("columns")
        if isinstance(columns, list):
            columns = _coerce_columns_list(columns)
        if isinstance(columns, dict):
            for col_name, col_spec in columns.items():
                if not isinstance(col_spec, dict):
                    continue
                has_column_semantics = any(key in col_spec for key in _COLUMN_SEMANTICS_YAML_KEYS)
                if has_column_semantics:
                    patch = {
                        "kind": "column_semantics",
                        "source_key": sk,
                        "table": table,
                        "column": col_name,
                        **_normalize_agent_column_semantics_spec(col_spec),
                    }
                    _upsert_agent_proposal(
                        proposal_key=f"semantics:{sk}:{table}:{col_name}",
                        target_type="column",
                        target_ref=f"{sk}.{table}.{col_name}",
                        operation="set_column_semantics",
                        patch=patch,
                    )
                    continue
                desc = col_spec.get("description") or col_spec.get("semantic_description")
                if desc:
                    _upsert_agent_proposal(
                        proposal_key=f"description:{sk}:{table}:{col_name}",
                        target_type="column",
                        target_ref=f"{sk}.{table}.{col_name}",
                        operation="set_description",
                        patch={
                            "kind": "column_description",
                            "source_key": sk,
                            "table": table,
                            "column": col_name,
                            "description": desc,
                        },
                    )

    metrics = data.get("metrics") if isinstance(data, dict) else None
    if isinstance(metrics, list):
        for m in metrics:
            if not isinstance(m, dict):
                skipped += 1
                continue
            m_name = m.get("name")
            m_expr = m.get("expression")
            if not m_name or not m_expr:
                skipped += 1
                continue
            _upsert_agent_proposal(
                proposal_key=f"metric:{m_name}",
                target_type="metric",
                target_ref=m_name,
                operation="add_metric",
                patch={
                    "kind": "metric",
                    "name": m_name,
                    "expression": m_expr,
                    "description": m.get("description"),
                    "ai_context": m.get("ai_context"),
                },
            )

    return {
        "created": created,
        "updated": updated,
        "reopened": reopened,
        "skipped": skipped,
    }
