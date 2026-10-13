# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Behavior at the public sync/client seam; no model or MaxCompute calls."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock

from maxcompute_semantic.remote.config import RemoteConnection, RemoteError
from maxcompute_semantic.remote.sync import (
    SECTIONS,
    SemanticPackageSync,
    SemanticSyncError,
    load_section_file,
    merge_section_entries,
    section_integrity,
)

GET = "maxcompute_semanticspec_get"
UPDATE = "maxcompute_semanticspec_update"
PUBLISH = "maxcompute_semanticspec_publish"


def _metric() -> dict[str, Any]:
    return {"name": "gmv", "expr": "amount", "sourceDetail": {"author": "owner", "revision": "evidence-1"}}


class Gateway:
    """Stateful external service double implementing documented Gateway envelopes."""

    def __init__(self) -> None:
        self.connection = RemoteConnection("test", "http://127.0.0.1:12345/mcp", "cn-test")
        self.namespace = "account-1"
        self.spec_id = "immutable-1"
        self.name = "sales"
        self.draft_revision: str | None = "draft-1"
        self.published_revision: str | None = "published-1"
        self.suggestions_revision: str | None = None
        self.draft: dict[str, Any] = {
            "dataReferences": [{"project": "warehouse", "schema": "default", "table": "orders", "tableId": "physical-1"}],
            "metricDefinitions": [_metric(), {"name": "count", "expr": "1", "source": "MANUAL"}],
        }
        self.history = {"published-1": copy.deepcopy(self.draft)}
        self.publication_sources = {"published-1": "draft-0"}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rejection: dict[str, Any] | None = None
        self.bad_section: str | None = None
        self.corruption: str | None = None
        self.change_during_read = False
        self.fail_update_readback = False
        self.publish_unknown = False
        self.publish_unknown_commits = True
        self.bad_publication_source = False

    def overview(self) -> dict[str, Any]:
        return {
            "name": self.name, "semantic_spec_id": self.spec_id,
            "user_draft_revision_id": self.draft_revision,
            "published_revision_id": self.published_revision,
            "system_suggestions_revision_id": self.suggestions_revision,
        }

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, copy.deepcopy(args)))
        if self.rejection is not None:
            return copy.deepcopy(self.rejection)
        if tool == UPDATE:
            if args.get("expected_draft_revision_id") != self.draft_revision:
                return {"ok": False, "error": {"code": "conflict"}}
            self.draft.update(copy.deepcopy(args["sections"]))
            self.draft_revision = "draft-2"
            if self.fail_update_readback:
                self.bad_section = next(iter(args["sections"]))
                self.corruption = "hash"
            return {"ok": True, "data": {"namespace": self.namespace, "semantic_spec": self.overview()}}
        if tool == PUBLISH:
            if args["expected_revision_id"] != self.draft_revision:
                return {"ok": False, "error": {"code": "conflict"}}
            if not self.publish_unknown or self.publish_unknown_commits:
                self.published_revision = "published-2"
                self.history["published-2"] = copy.deepcopy(self.draft)
                self.publication_sources["published-2"] = "other-draft" if self.bad_publication_source else self.draft_revision
            if self.publish_unknown:
                return {"ok": False, "error": {"code": "timeout", "retryable": False}}
            return {"ok": True, "data": {"namespace": self.namespace, "semantic_spec_id": self.spec_id, "name": self.name, "published_revision_id": self.published_revision}}
        assert tool == GET
        if "source" not in args:
            return {"ok": True, "data": {"namespace": self.namespace, "semantic_spec": self.overview()}}
        source = args["source"]
        revision = self.draft_revision
        content = self.draft
        if source == "PUBLISHED":
            revision = args.get("revision_id", self.published_revision)
            content = self.history[revision]
        rows = []
        for section in args["sections"]:
            if section in content:
                value = copy.deepcopy(content[section])
                size, digest = section_integrity(value)
                row = {"section": section, "present": True, "content": value, "size_bytes": size, "content_hash": digest}
            else:
                row = {"section": section, "present": False, "size_bytes": 0, "content_hash": ""}
            if section == self.bad_section:
                if self.corruption == "hash":
                    row["content_hash"] = "sha256:" + "0" * 64
                elif self.corruption == "size":
                    row["size_bytes"] += 1
                elif self.corruption == "body":
                    row.pop("content", None)
                elif self.corruption == "presence":
                    row["present"] = "true"
                elif self.corruption == "duplicate":
                    rows.append(copy.deepcopy(row))
            rows.append(row)
        spec = {"name": self.name, "semantic_spec_id": self.spec_id, "source": source, "revision_id": revision, "sections": rows}
        if source == "PUBLISHED":
            spec.update({"source_revision_id": self.publication_sources[revision], "publication_source": "USER_DRAFT"})
        if self.change_during_read and source == "USER_DRAFT":
            self.draft_revision = "draft-concurrent"
        return {"ok": True, "data": {"namespace": self.namespace, "semantic_spec": spec}}


def _bound(tmp_path: Path, sections: tuple[str, ...] = ("metricDefinitions",)) -> tuple[SemanticPackageSync, Gateway]:
    gateway = Gateway()
    sync = SemanticPackageSync(tmp_path / "workspace", gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", sections)
    return sync, gateway


def _error_code(error: pytest.ExceptionInfo[SemanticSyncError]) -> str:
    return str(error.value.code)


def test_approved_push_preserves_unrelated_entries_and_unknown_fields(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "net_amount", "sourceDetail": {"review": "human"}}])
    preview = sync.push()
    assert preview["dry_run"] and preview["ready"]
    assert not any(tool == UPDATE for tool, _ in gateway.calls)
    result = sync.push(False, preview["plan_hash"])
    assert result["revision_id"] == "draft-2" and not result["published"]
    assert gateway.draft["metricDefinitions"][1] == {"name": "count", "expr": "1", "source": "MANUAL"}
    assert gateway.draft["metricDefinitions"][0]["sourceDetail"] == {"author": "owner", "revision": "evidence-1", "review": "human"}
    assert sync.local_sections()["metricDefinitions"] == gateway.draft["metricDefinitions"]


def test_remote_only_change_is_preserved_without_a_remote_write(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    gateway.draft["metricDefinitions"][0]["expr"] = "remote_amount"
    gateway.draft_revision = "remote-2"
    plan = sync.diff()
    assert plan["changes"][0]["state"] == "remote_changed"
    result = sync.push(False, plan["plan_hash"])
    assert not result["remote_written"]
    assert sync.local_sections()["metricDefinitions"][0]["expr"] == "remote_amount"
    assert not any(tool == UPDATE for tool, _ in gateway.calls)


def test_divergent_section_conflict_does_not_write_or_advance_base(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    before = sync.state_path.read_bytes()
    gateway.draft["metricDefinitions"][0]["expr"] = "remote"
    gateway.draft_revision = "remote-2"
    plan = sync.diff()
    assert plan["conflicts"] == ["metricDefinitions"] and not plan["ready"]
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, plan["plan_hash"])
    assert _error_code(error) == "SemanticConflict"
    assert sync.state_path.read_bytes() == before
    assert not any(tool == UPDATE for tool, _ in gateway.calls)


@pytest.mark.parametrize("changed", ["local", "remote", "revision"])
def test_plan_hash_pins_exact_local_remote_and_revision(tmp_path: Path, changed: str) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    approved = sync.diff()["plan_hash"]
    if changed == "local":
        sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "changed-again"}])
    elif changed == "remote":
        gateway.draft["metricDefinitions"][1]["expr"] = "2"
    else:
        gateway.draft_revision = "draft-3"
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, approved)
    assert _error_code(error) == "SemanticPlanChanged"
    assert not any(tool == UPDATE for tool, _ in gateway.calls)


def test_cas_rejection_is_not_retried_or_recorded_as_synced(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    approved = sync.diff()["plan_hash"]
    before = sync.state_path.read_bytes()
    original_call = gateway.call
    def reject_update(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == UPDATE:
            gateway.calls.append((tool, copy.deepcopy(args)))
            return {"ok": False, "error": {"code": "conflict"}}
        return original_call(tool, args)
    gateway.call = reject_update  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, approved)
    assert error.value.context["remote_code"] == "conflict"
    assert sum(tool == UPDATE for tool, _ in gateway.calls) == 1
    assert sync.state_path.read_bytes() == before


def test_successful_update_without_valid_readback_does_not_advance_base(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    approved = sync.diff()["plan_hash"]
    before = sync.state_path.read_bytes()
    gateway.fail_update_readback = True
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, approved)
    assert _error_code(error) == "SemanticIntegrity"
    assert gateway.draft_revision == "draft-2"
    assert sync.state_path.read_bytes() == before


@pytest.mark.parametrize("code", ["permission_denied", "result_too_large", "not_found", "unsupported_operation", "timeout"])
def test_remote_pull_failures_preserve_base(tmp_path: Path, code: str) -> None:
    sync, gateway = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    gateway.rejection = {"ok": False, "error": {"code": code}}
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["metricDefinitions"])
    assert error.value.context["remote_code"] == code
    assert sync.state_path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["hash", "size", "body", "presence", "duplicate"])
def test_incomplete_or_corrupt_section_never_advances_base(tmp_path: Path, corruption: str) -> None:
    sync, gateway = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    gateway.bad_section = "metricDefinitions"
    gateway.corruption = corruption
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["metricDefinitions"])
    assert _error_code(error) == "SemanticIntegrity"
    assert sync.state_path.read_bytes() == before


def test_draft_reads_require_one_consistent_revision(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    gateway.change_during_read = True
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["dataReferences", "metricDefinitions"])
    assert _error_code(error) == "SemanticSnapshotChanged"
    assert sync.state_path.read_bytes() == before


def test_published_pull_is_pinned_to_known_immutable_revision(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    gateway.published_revision = "published-new"
    gateway.history["published-new"] = {"metricDefinitions": [{"name": "different", "expr": "1"}]}
    result = sync.pull("PUBLISHED", ["metricDefinitions"], "published-1")
    assert result["revision_id"] == "published-1"
    published_calls = [args for tool, args in gateway.calls if tool == GET and args.get("source") == "PUBLISHED"]
    assert published_calls and all(args["revision_id"] == "published-1" for args in published_calls)


@pytest.mark.parametrize("identity", ["namespace", "spec_id"])
def test_recreated_object_or_changed_account_is_rejected(tmp_path: Path, identity: str) -> None:
    sync, gateway = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    setattr(gateway, identity, "different")
    with pytest.raises(SemanticSyncError) as error:
        sync.diff()
    assert _error_code(error) == "SemanticBindingChanged"
    assert sync.state_path.read_bytes() == before


def test_pull_preserves_local_changes_and_rejects_divergence(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    sync.pull("USER_DRAFT", ["metricDefinitions"])
    assert sync.local_sections()["metricDefinitions"][0]["expr"] == "local"
    before = sync.state_path.read_bytes()
    gateway.draft["metricDefinitions"][0]["expr"] = "remote"
    with pytest.raises(SemanticSyncError):
        sync.pull("USER_DRAFT", ["metricDefinitions"])
    assert sync.state_path.read_bytes() == before


def test_first_draft_preserves_a_complete_published_plan_but_requires_atomic_id_guard(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    gateway.draft_revision = None
    gateway.draft = {}
    plan = sync.diff()
    assert not plan["ready"] and "dataReferences" in plan["initialization_missing_sections"]
    assert plan["blocked_reason"] == "SemanticFirstDraftUnsupported"
    sync.pull("PUBLISHED", SECTIONS)
    plan = sync.diff()
    assert not plan["initialization_missing_sections"]
    assert plan["writes"]["metricDefinitions"] == gateway.history["published-1"]["metricDefinitions"]
    assert "tableId" not in plan["writes"]["dataReferences"][0]
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, plan["plan_hash"])
    assert _error_code(error) == "SemanticFirstDraftUnsupported"
    assert not gateway.draft and not any(tool == UPDATE for tool, _ in gateway.calls)


def test_publish_rejects_unread_sections_and_requires_confirmation(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert _error_code(error) == "SemanticPublicationUnreviewed"
    sync.pull("USER_DRAFT", SECTIONS)
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1")
    assert _error_code(error) == "SemanticPublicationUnconfirmed"
    assert not any(tool == PUBLISH for tool, _ in gateway.calls)


def test_publish_requires_all_sections_at_the_same_reviewed_revision(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    gateway.draft_revision = "draft-2"
    sync.pull("USER_DRAFT", ["metricDefinitions"])
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-2", True)
    assert _error_code(error) == "SemanticPublicationUnreviewed"
    assert not any(tool == PUBLISH for tool, _ in gateway.calls)


def test_publish_success_requires_source_and_content_readback(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    result = sync.publish("draft-1", True)
    assert result == {"published": True, "published_revision_id": "published-2", "source_revision_id": "draft-1", "recovered_by_readback": False}
    pinned = [args for tool, args in gateway.calls if tool == GET and args.get("source") == "PUBLISHED"]
    assert len(pinned) == len(SECTIONS) and all(args["revision_id"] == "published-2" for args in pinned)


def test_unknown_publish_recovers_only_from_new_exact_publication_evidence(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    gateway.publish_unknown = True
    result = sync.publish("draft-1", True)
    assert result["published"] and result["recovered_by_readback"]
    assert sum(tool == PUBLISH for tool, _ in gateway.calls) == 1


@pytest.mark.parametrize("failure", ["not_committed", "wrong_source"])
def test_unknown_publish_without_exact_evidence_does_not_advance_state(tmp_path: Path, failure: str) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    before = sync.state_path.read_bytes()
    gateway.publish_unknown = True
    gateway.publish_unknown_commits = failure != "not_committed"
    gateway.bad_publication_source = failure == "wrong_source"
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert _error_code(error) == "SemanticPublishOutcomeUnknown"
    assert sync.state_path.read_bytes() == before
    assert sum(tool == PUBLISH for tool, _ in gateway.calls) == 1


def test_publish_rejects_unsynced_local_changes(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert _error_code(error) == "SemanticPublicationStale"
    assert not any(tool == PUBLISH for tool, _ in gateway.calls)


def test_explicit_removal_preserves_retained_fields(tmp_path: Path) -> None:
    sync, _ = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [], removals=["count"])
    assert sync.local_sections()["metricDefinitions"] == [_metric()]
    with pytest.raises(SemanticSyncError):
        sync.edit_section("metricDefinitions", [], removals=["missing"])
    sync.edit_section("metricDefinitions", [], replace=True)
    assert sync.local_sections()["metricDefinitions"] == []


def test_model_entry_merge_retains_other_lists_and_unknown_fields() -> None:
    model = {"logicalTables": [{"name": "orders", "baseTable": "p.s.orders", "unknown": "kept"}], "namedFilters": [{"name": "paid", "sqlFragment": "paid = true"}], "approvedRelationships": [], "serverExtension": {"x": 1}}
    changed = merge_section_entries("semanticModel", model, {"logicalTables": [{"name": "orders", "description": "new"}]})
    assert changed["logicalTables"][0]["unknown"] == "kept"
    assert changed["namedFilters"] == model["namedFilters"]
    assert changed["serverExtension"] == {"x": 1}


def test_native_catalog_model_can_omit_empty_optional_lists(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.draft["semanticModel"] = {"logicalTables": [{"name": "orders", "baseTable": "p.s.orders"}]}
    sync = SemanticPackageSync(tmp_path, gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", ["semanticModel"])
    assert sync.local_sections()["semanticModel"] == gateway.draft["semanticModel"]


def test_integrity_matches_go_html_unicode_and_numeric_bytes() -> None:
    content = {"guidance": "金额 < 1 & x > 0\u2028\u2029", "numbers": [1.0, 1e-6, 1e-7, 1e20, 1e21]}
    expected = '{"guidance":"金额 \\u003c 1 \\u0026 x \\u003e 0\\u2028\\u2029","numbers":[1,0.000001,1e-7,100000000000000000000,1e+21]}'.encode()
    assert section_integrity(content) == (len(expected), "sha256:" + hashlib.sha256(expected).hexdigest())


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "section.json"
    path.write_text('{"name":"a","name":"b"}', encoding="utf-8")
    with pytest.raises(SemanticSyncError) as error:
        load_section_file(path)
    assert _error_code(error) == "SemanticStateInvalid"


def test_sync_sidecar_contains_no_profile_or_execution_data(tmp_path: Path) -> None:
    sync, _ = _bound(tmp_path)
    state = json.loads(sync.state_path.read_text())
    assert state["binding"]["remote_url"].endswith("/mcp")
    assert set(state) == {"version", "binding", "base", "local", "base_revisions", "base_source", "base_revision_id"}


@pytest.mark.parametrize("envelope", [
    {"ok": 1, "data": {}}, {"ok": True, "data": []},
    {"ok": True, "data": {}, "has_more": True},
    {"ok": True, "data": {}, "next_cursor": "size-truncated"},
    {"ok": True, "data": {}, "warnings": ["content was truncated"]},
])
def test_malformed_or_truncated_envelope_is_never_used_as_a_baseline(tmp_path: Path, envelope: dict[str, Any]) -> None:
    sync, gateway = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    gateway.rejection = envelope
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["metricDefinitions"])
    assert _error_code(error) == "SemanticIntegrity"
    assert sync.state_path.read_bytes() == before


def test_absent_content_metadata_is_verified(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    original = gateway.call
    def inconsistent_absence(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if args.get("source"):
            result["data"]["semantic_spec"]["sections"][0] = {"section": "glossary", "present": False, "content": [], "size_bytes": 0, "content_hash": ""}
        return result
    gateway.call = inconsistent_absence  # type: ignore[method-assign]
    before = sync.state_path.read_bytes()
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["glossary"])
    assert _error_code(error) == "SemanticIntegrity"
    assert sync.state_path.read_bytes() == before


def test_unknown_update_transport_failure_never_retries_or_advances_state(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    plan = sync.diff()
    before = sync.state_path.read_bytes()
    original = gateway.call
    def transport_failure(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == UPDATE:
            raise RemoteError("operation outcome may be unknown", code="RemoteTransportError")
        return result
    gateway.call = transport_failure  # type: ignore[method-assign]
    with pytest.raises(RemoteError):
        sync.push(False, plan["plan_hash"])
    assert gateway.draft_revision == "draft-2"
    assert sum(tool == UPDATE for tool, _ in gateway.calls) == 1
    assert sync.state_path.read_bytes() == before


def test_readback_with_valid_hash_but_wrong_content_does_not_advance_state(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    plan = sync.diff()
    before = sync.state_path.read_bytes()
    original = gateway.call
    def wrong_content(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == UPDATE:
            gateway.draft["metricDefinitions"][0]["expr"] = "backend-changed"
        return result
    gateway.call = wrong_content  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, plan["plan_hash"])
    assert _error_code(error) == "SemanticReadbackMismatch"
    assert sync.state_path.read_bytes() == before


@pytest.mark.parametrize("missing", ["identity", "revision"])
def test_write_success_envelope_requires_identity_and_new_revision(tmp_path: Path, missing: str) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local"}])
    plan = sync.diff()
    before = sync.state_path.read_bytes()
    original = gateway.call
    def incomplete_write(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == UPDATE:
            result["data"]["semantic_spec"].pop("semantic_spec_id" if missing == "identity" else "user_draft_revision_id")
        return result
    gateway.call = incomplete_write  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.push(False, plan["plan_hash"])
    assert _error_code(error) == "SemanticIntegrity"
    assert sync.state_path.read_bytes() == before


def test_conflicting_publication_is_never_retried(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    before = sync.state_path.read_bytes()
    original = gateway.call
    def reject_publication(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == PUBLISH:
            gateway.calls.append((tool, copy.deepcopy(args)))
            return {"ok": False, "error": {"code": "conflict"}}
        return original(tool, args)
    gateway.call = reject_publication  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert error.value.context["remote_code"] == "conflict"
    assert sum(tool == PUBLISH for tool, _ in gateway.calls) == 1
    assert sync.state_path.read_bytes() == before


def test_workspace_lock_prevents_competing_sync_writes(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    calls = len(gateway.calls)
    with FileLock(sync.workspace / ".sync.lock", timeout=0), pytest.raises(SemanticSyncError) as error:
        sync.diff()
    assert _error_code(error) == "SemanticWorkspaceBusy"
    assert len(gateway.calls) == calls


def test_remote_url_reconfiguration_cannot_redirect_a_bound_workspace(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    calls = len(gateway.calls)
    gateway.connection = RemoteConnection("test", "http://127.0.0.1:12346/mcp", "cn-test")
    with pytest.raises(SemanticSyncError) as error:
        sync.diff()
    assert _error_code(error) == "SemanticBindingChanged"
    assert len(gateway.calls) == calls


@pytest.mark.parametrize("section,content", [
    ("metricDefinitions", [{"name": "a"}, {"name": "a"}]),
    ("metricDefinitions", [{"expr": "no-name"}]),
    ("dataReferences", []), ("dataReferences", [{"project": "p"}]),
    ("semanticModel", {"logicalTables": "not-a-list"}),
    ("instructions", []),
])
def test_invalid_native_section_edits_preserve_local_work(tmp_path: Path, section: str, content: Any) -> None:
    sync, _ = _bound(tmp_path, SECTIONS)
    before = sync.state_path.read_bytes()
    with pytest.raises(SemanticSyncError) as error:
        sync.edit_section(section, content, replace=True)
    assert _error_code(error) == "SemanticSectionInvalid"
    assert sync.state_path.read_bytes() == before


def test_optional_array_clear_can_read_back_as_absent(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [], replace=True)
    plan = sync.diff()
    original = gateway.call
    def omit_cleared_array(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == UPDATE:
            gateway.draft.pop("metricDefinitions")
        return result
    gateway.call = omit_cleared_array  # type: ignore[method-assign]
    result = sync.push(False, plan["plan_hash"])
    assert result["remote_written"] and "metricDefinitions" not in sync.local_sections()


@pytest.mark.parametrize("source,sections,revision", [
    ("unknown", ["metricDefinitions"], None),
    ("USER_DRAFT", ["metricDefinitions", "metricDefinitions"], None),
    ("USER_DRAFT", [], None), ("USER_DRAFT", ["unknown"], None),
    ("USER_DRAFT", ["metricDefinitions"], "old-draft"),
])
def test_invalid_pull_selectors_are_rejected_before_remote_reads(tmp_path: Path, source: str, sections: list[str], revision: str | None) -> None:
    sync, gateway = _bound(tmp_path)
    calls = len(gateway.calls)
    with pytest.raises(SemanticSyncError):
        sync.pull(source, sections, revision)
    assert len(gateway.calls) == calls


def test_named_merges_require_explicit_unambiguous_removal() -> None:
    with pytest.raises(SemanticSyncError):
        merge_section_entries("metricDefinitions", [{"name": "a"}], [{"name": "a"}], removals=["a"])
    with pytest.raises(SemanticSyncError):
        merge_section_entries("instructions", {}, {}, removals=["a"])
    assert merge_section_entries("instructions", {"extra": {"source": "owner", "x": 1}}, {"extra": {"x": 2}}) == {"extra": {"source": "owner", "x": 2}}


def test_projection_staging_is_atomic_and_preserves_other_tables_dimensions_and_references(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    sync.edit_section("semanticModel", {"logicalTables": [{"name": "orders", "dimensions": [{"name": "region", "expr": "region", "extra": "kept"}, {"name": "old", "expr": "old"}]}], "namedFilters": [{"name": "paid", "sqlFragment": "paid = true"}], "approvedRelationships": []}, replace=True)
    calls = len(gateway.calls)
    result = sync.stage_sections({
        "dataReferences": [{"project": "warehouse", "schema": "default", "table": "customers"}],
        "semanticModel": {"logicalTables": [{"name": "orders", "dimensions": [{"name": "region", "description": "geography"}]}]},
        "metricDefinitions": [{"name": "gmv", "expr": "net_amount"}],
    })
    assert not result["remote_written"] and len(gateway.calls) == calls
    staged = sync.local_sections()
    assert [reference["table"] for reference in staged["dataReferences"]] == ["orders", "customers"]
    assert staged["dataReferences"][0]["tableId"] == "physical-1"
    model = staged["semanticModel"]
    assert [dimension["name"] for dimension in model["logicalTables"][0]["dimensions"]] == ["region", "old"]
    assert model["logicalTables"][0]["dimensions"][0]["extra"] == "kept"
    assert model["namedFilters"][0]["name"] == "paid"
    assert staged["metricDefinitions"][1]["name"] == "count"
    before = sync.state_path.read_bytes()
    with pytest.raises(SemanticSyncError):
        sync.stage_sections({"metricDefinitions": [{"name": "gmv", "expr": "would-change"}], "glossary": [{"no_term": "bad"}]})
    assert sync.state_path.read_bytes() == before


def test_model_nested_extension_fields_and_benchmark_questions_are_retained() -> None:
    current = {"logicalTables": [], "serverExtension": {"edited": "old", "unknown": "retained"}}
    changed = merge_section_entries("semanticModel", current, {"serverExtension": {"edited": "new"}})
    assert changed["serverExtension"] == {"edited": "new", "unknown": "retained"}
    benchmarks = {"questions": [{"id": "q1", "question": "old", "expected": "kept"}, {"id": "q2", "question": "other"}]}
    changed = merge_section_entries("benchmarks", benchmarks, {"questions": [{"id": "q1", "question": "new"}]})
    assert changed["questions"] == [{"id": "q1", "question": "new", "expected": "kept"}, {"id": "q2", "question": "other"}]


def test_snapshot_exports_verified_base_not_unsynced_local_edits(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path)
    sync.edit_section("metricDefinitions", [{"name": "gmv", "expr": "local-unverified"}])
    calls = len(gateway.calls)
    snapshot = sync.snapshot()
    assert snapshot["source"] == "USER_DRAFT" and snapshot["revision_id"] == "draft-1"
    assert snapshot["semantic_spec_id"] == "immutable-1" and snapshot["namespace"] == "account-1"
    assert snapshot["sections"][0]["content"][0]["expr"] == "amount"
    assert len(gateway.calls) == calls


def test_snapshot_rejects_mixed_sources_or_revisions(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    gateway.draft_revision = "draft-2"
    sync.pull("USER_DRAFT", ["metricDefinitions"])
    with pytest.raises(SemanticSyncError) as error:
        sync.snapshot()
    assert _error_code(error) == "SemanticSnapshotMixed"


def test_model_clear_readback_may_omit_known_empty_sdk_arrays(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    gateway.draft["semanticModel"] = {"logicalTables": [{"name": "orders", "baseTable": "warehouse.default.orders"}], "namedFilters": [{"name": "paid", "sqlFragment": "paid = true"}]}
    sync.pull("USER_DRAFT", SECTIONS)
    model = {"logicalTables": [{"name": "orders", "baseTable": "warehouse.default.orders", "dimensions": [], "timeDimensions": [], "synonyms": []}], "namedFilters": [], "approvedRelationships": []}
    sync.edit_section("semanticModel", model, replace=True)
    approved = sync.diff()["plan_hash"]
    original = gateway.call
    def typed_sdk_omitempty(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == UPDATE:
            # Literal generated with Go encoding/json and the repository's
            # Catalog SDK SemanticModel/LogicalTable types, all five empty
            # slice fields explicitly supplied. Not a Python canonicalizer.
            gateway.draft["semanticModel"] = json.loads('{"logicalTables":[{"name":"orders","baseTable":"warehouse.default.orders"}]}')
        return result
    gateway.call = typed_sdk_omitempty  # type: ignore[method-assign]
    result = sync.push(False, approved)
    assert result["remote_written"]
    assert sync.local_sections()["semanticModel"] == {"logicalTables": [{"name": "orders", "baseTable": "warehouse.default.orders"}]}
    publication = sync.publish("draft-2", True)
    assert publication["published"]


def test_unknown_empty_arrays_are_not_erased_by_write_equivalence(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    gateway.draft["semanticModel"] = {"logicalTables": [], "futureFields": ["old"]}
    sync.pull("USER_DRAFT", ["semanticModel"])
    sync.edit_section("semanticModel", {"logicalTables": [], "futureFields": []}, replace=True)
    plan = sync.diff()
    assert plan["writes"]["semanticModel"]["futureFields"] == []


def _catalog_golden(section: str) -> dict[str, Any]:
    # Produced independently by Go encoding/json over the real Catalog SDK
    # SemanticSpec DTOs, then projected through interface{} to sort wire keys.
    # Synthetic data only. futureModel adds a typed map extension to test that
    # unknown knowledge remains complete; it is not an existing SDK field.
    path = Path(__file__).with_name("catalog-sdk-native-json.golden.json")
    return json.loads(path.read_text(encoding="utf-8"))[section]


def _golden_client(section: str, row: dict[str, Any]) -> Gateway:
    gateway = Gateway()
    original = gateway.call
    def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == GET and args.get("source"):
            result["data"]["semantic_spec"]["sections"] = [{**copy.deepcopy(row), "section": section}]
        return result
    gateway.call = call  # type: ignore[method-assign]
    return gateway


@pytest.mark.parametrize("section", SECTIONS)
def test_catalog_sdk_native_hash_survives_sorted_wire_projection(tmp_path: Path, section: str) -> None:
    golden = _catalog_golden(section)
    row = golden["snapshot"]
    gateway = _golden_client(section, row)
    sync = SemanticPackageSync(tmp_path, gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", [section])
    body = sync.local_sections()[section]
    assert body == json.loads(golden["native_json"])
    assert section_integrity(body) == (row["size_bytes"], row["content_hash"])
    exported = sync.snapshot()["sections"][0]
    assert section_integrity(exported["content"])[1] == row["content_hash"]


def test_native_hash_repair_retains_complete_unknown_nested_maps(tmp_path: Path) -> None:
    golden = _catalog_golden("futureModel")
    row = golden["snapshot"]
    gateway = _golden_client("semanticModel", row)
    sync = SemanticPackageSync(tmp_path, gateway)
    sync.bind("sales", "cn-test")
    sync.pull("USER_DRAFT", ["semanticModel"])
    body = sync.local_sections()["semanticModel"]
    assert body["serverExtension"] == {"z": "last", "a": {"z": "last", "a": "first"}}
    assert body == json.loads(golden["native_json"])
    assert section_integrity(body) == (row["size_bytes"], row["content_hash"])


@pytest.mark.parametrize("corruption", ["value", "size", "hash", "unknown_field"])
def test_native_order_repair_never_accepts_corrupted_or_dropped_content(tmp_path: Path, corruption: str) -> None:
    row = copy.deepcopy(_catalog_golden("semanticModel")["snapshot"])
    if corruption == "value":
        row["content"]["logicalTables"][0]["baseTable"] = "wrong.default.table"
    elif corruption == "size":
        row["size_bytes"] += 1
    elif corruption == "hash":
        row["content_hash"] = "sha256:" + "0" * 64
    else:
        row["content"]["unknown"] = {"must_not_be_dropped": "complete"}
    gateway = _golden_client("semanticModel", row)
    sync = SemanticPackageSync(tmp_path, gateway)
    sync.bind("sales", "cn-test")
    before = sync.state_path.read_bytes()
    with pytest.raises(SemanticSyncError) as error:
        sync.pull("USER_DRAFT", ["semanticModel"])
    assert _error_code(error) == "SemanticIntegrity"
    assert sync.state_path.read_bytes() == before


def test_stage_unpulled_section_is_rejected_without_partial_local_mutation(tmp_path: Path) -> None:
    sync, _ = _bound(tmp_path)
    before = sync.state_path.read_bytes()
    with pytest.raises(SemanticSyncError) as error:
        sync.stage_sections({"metricDefinitions": [{"name": "gmv", "expr": "would-change"}], "instructions": {"generalGuidance": "new"}})
    assert _error_code(error) == "SemanticSectionNotPulled"
    assert sync.state_path.read_bytes() == before


def test_gateway_publish_http500_unknown_markers_trigger_readback(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    original = gateway.call
    def gateway_500(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == PUBLISH:
            return {"ok": False, "error": {"code": "maxcompute_error", "details": {"retry_safety": "NON_IDEMPOTENT_OPERATION", "backend_http_status": 500}}}
        return result
    gateway.call = gateway_500  # type: ignore[method-assign]
    result = sync.publish("draft-1", True)
    assert result["published"] and result["recovered_by_readback"]
    assert sum(tool == PUBLISH for tool, _ in gateway.calls) == 1


def test_publication_physical_identity_drift_is_not_accepted_as_equal_content(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    before = sync.state_path.read_bytes()
    original = gateway.call
    def physical_drift(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == PUBLISH:
            gateway.history["published-2"]["dataReferences"][0]["tableId"] = "physical-recreated"
        return result
    gateway.call = physical_drift  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert _error_code(error) == "SemanticPublishOutcomeUnknown"
    assert sync.state_path.read_bytes() == before


def test_publication_readback_failure_retains_an_explicit_unknown_outcome(tmp_path: Path) -> None:
    sync, gateway = _bound(tmp_path, SECTIONS)
    before = sync.state_path.read_bytes()
    original = gateway.call
    def lose_readback(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = original(tool, args)
        if tool == PUBLISH:
            gateway.rejection = {"ok": False, "error": {"code": "permission_denied"}}
        return result
    gateway.call = lose_readback  # type: ignore[method-assign]
    with pytest.raises(SemanticSyncError) as error:
        sync.publish("draft-1", True)
    assert _error_code(error) == "SemanticPublishOutcomeUnknown"
    assert sync.state_path.read_bytes() == before
