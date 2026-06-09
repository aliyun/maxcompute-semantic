"""Tests for ``mcs package`` proposal review workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB


def _profile(tmp_path: Path, name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
        package_path=tmp_path / name,
    )


def _multi_source_profile(tmp_path: Path, name: str = "multi") -> Profile:
    return Profile(
        name=name,
        compute_project="proj_a",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
        package_path=tmp_path / name,
    )


def _seed_package(profile: Profile) -> None:
    assert profile.package_path is not None
    profile.package_path.mkdir(parents=True, exist_ok=True)
    db = PackageDB(profile.package_path / "package.db")
    try:
        tid = db.upsert_table("proj__default", "orders", schema_hash="hash1")
        db.upsert_columns(
            tid,
            [
                {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "amount", "type": "DOUBLE", "comment": "", "is_partition": 0},
            ],
        )
    finally:
        db.close()


def _seed_multi_source_orders_package(profile: Profile) -> None:
    assert profile.package_path is not None
    profile.package_path.mkdir(parents=True, exist_ok=True)
    db = PackageDB(profile.package_path / "package.db")
    try:
        for source_key in ("proj_a__default", "proj_b__default"):
            tid = db.upsert_table(source_key, "orders", schema_hash=f"hash-{source_key}")
            db.upsert_columns(
                tid,
                [
                    {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
                    {"name": "amount", "type": "DOUBLE", "comment": "", "is_partition": 0},
                ],
            )
    finally:
        db.close()


def _seed_suggestion(
    profile: Profile,
    *,
    column: str = "status",
    confidence: float = 0.86,
    role: str = "dimension",
    subtype: str | None = "categorical",
) -> None:
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        db.upsert_annotation_suggestion(
            source_key="proj__default",
            table_name="orders",
            column_name=column,
            suggested_role=role,
            suggested_subtype=subtype,
            confidence=confidence,
            evidence=[{"source": "history_sql", "where_count": 4}],
        )
    finally:
        db.close()


def _seed_proposal(
    profile: Profile,
    *,
    column: str = "status",
    confidence: float = 0.86,
    proposal_key: str | None = None,
) -> int:
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        return db.upsert_semantic_proposal(
            proposal_key=proposal_key or f"manual:proj__default.orders.{column}",
            target_type="column",
            target_ref=f"proj__default.orders.{column}",
            operation="promote_annotation_suggestion",
            patch={
                "kind": "column_semantics",
                "source_key": "proj__default",
                "table": "orders",
                "column": column,
                "role": "dimension",
                "dim_type": "categorical",
                "agg": None,
                "id_type": None,
                "references_target": None,
                "semantic_description": "Order lifecycle state.",
            },
            confidence=confidence,
            evidence=[{"source": "history_sql", "where_count": 4}],
            provenance="test",
            created_by="tester",
        )
    finally:
        db.close()


def _read_proposal(profile: Profile, proposal_id: int) -> dict[str, Any]:
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        row = db.get_semantic_proposal(proposal_id)
        assert row is not None
        return row
    finally:
        db.close()


def _read_semantics(profile: Profile) -> dict[str, Any] | None:
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        return db.get_column_semantics("proj__default", "orders", "status")
    finally:
        db.close()


def _patch_command_context(monkeypatch: pytest.MonkeyPatch, profile: Profile) -> dict[str, Any]:
    import maxcompute_semantic.commands._profile_command as pc_mod
    import maxcompute_semantic.commands.package as package_cmd
    from maxcompute_semantic._internal.output import Renderer
    from maxcompute_semantic.auth.context import ProfileContext

    calls: dict[str, Any] = {"resolved": [], "forks": [], "commits": []}

    def mock_resolve(
        *,
        profile_name: str | None = None,
        project: str | None = None,
        schema: str | None = None,
        renderer: Renderer | None = None,
    ) -> ProfileContext:
        calls["resolved"].append((project, profile_name))
        return ProfileContext(
            profile=profile,
            project_override=project,
            schema_override=schema,
            renderer=renderer or Renderer(),
        )

    def mock_reject_if_fork(self: ProfileContext) -> None:
        calls["forks"].append(self.profile.name)

    def mock_commit(prof: Profile, *, action: str, summary: str) -> None:
        calls["commits"].append((prof.name, action, summary))

    monkeypatch.setattr(
        ProfileContext, "resolve", classmethod(lambda cls, **kw: mock_resolve(**kw))
    )
    monkeypatch.setattr(ProfileContext, "reject_if_fork", mock_reject_if_fork)
    monkeypatch.setattr(pc_mod, "commit_after_command", mock_commit)
    # Also patch in package_cmd for the apply_cmd's manual commit_after_command
    monkeypatch.setattr(package_cmd, "commit_after_command", mock_commit)
    return calls


def _invoke(args: list[str], *, input: str | None = None) -> object:
    from maxcompute_semantic.commands.package import package_group

    return CliRunner().invoke(
        package_group,
        args,
        input=input,
        obj={"format": "json", "quiet": False},
    )


def test_propose_from_suggestions_json_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _seed_suggestion(profile, column="status", confidence=0.86)
    _seed_suggestion(profile, column="amount", confidence=0.62, role="measure", subtype="SUM")
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "propose",
            "--from-suggestions",
            "--min-confidence",
            "0.8",
            "--profile",
            "test",
            "--project",
            "proj",
        ]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    assert envelope["data"] == {
        "profile": "test",
        "created": 1,
        "updated": 0,
        "skipped": 1,
    }
    assert calls["resolved"] == [("proj", "test")]
    assert calls["forks"] == ["test"]
    assert calls["commits"] == [("test", "package", "propose from suggestions")]


def test_propose_noop_does_not_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _seed_suggestion(profile, column="status", confidence=0.62)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "propose",
            "--from-suggestions",
            "--min-confidence",
            "0.8",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"] == {
        "profile": "test",
        "created": 0,
        "updated": 0,
        "skipped": 1,
    }
    assert calls["forks"] == ["test"]
    assert calls["commits"] == []


def test_propose_from_stdin_creates_column_semantics_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "propose",
            "--from-stdin",
            "--profile",
            "test",
        ],
        input=(
            "tables:\n"
            "  - table: orders\n"
            "    columns:\n"
            "      status: {role: identifier, id_type: primary, description: Order key.}\n"
        ),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"] == {
        "profile": "test",
        "created": 1,
        "updated": 0,
        "reopened": 0,
        "skipped": 0,
    }
    assert calls["commits"] == [("test", "package", "propose from stdin")]

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
    finally:
        db.close()
    assert len(rows) == 1
    payload = json.loads(rows[0]["patch_json"])
    assert payload == {
        "kind": "column_semantics",
        "source_key": "proj__default",
        "table": "orders",
        "column": "status",
        "role": "identifier",
        "dim_type": None,
        "agg": None,
        "id_type": "primary",
        "references_target": None,
        "semantic_description": "Order key.",
    }

    apply_result = _invoke(["apply", str(rows[0]["id"]), "--profile", "test"])

    assert apply_result.exit_code == 0, apply_result.output
    semantics = _read_semantics(profile)
    assert semantics is not None
    assert semantics["semantic_role"] == "identifier"
    assert semantics["id_type"] == "primary"
    assert semantics["semantic_description"] == "Order key."


def test_propose_from_stdin_rejects_oversized_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input="x" * (10 * 1024 * 1024),
    )

    assert result.exit_code != 0
    assert "stdin input exceeds 10 MB limit" in result.output
    assert calls["commits"] == []


def test_propose_from_stdin_accepts_typo_keys_and_column_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input=(
            "tables:\n"
            "  - table: orders\n"
            "    description: Order fact table.\n"
            "    fields:\n"
            "      - column_name: amount\n"
            "        role: measure\n"
            "        agg: sum\n"
        ),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["created"] == 2
    assert envelope["data"]["skipped"] == 0

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
    finally:
        db.close()
    assert {row["operation"] for row in rows} == {
        "set_ai_context",
        "set_column_semantics",
    }
    by_operation = {row["operation"]: json.loads(row["patch_json"]) for row in rows}
    assert by_operation["set_ai_context"]["ai_context"] == "Order fact table."
    assert by_operation["set_column_semantics"]["column"] == "amount"
    assert by_operation["set_column_semantics"]["agg"] == "SUM"


def test_propose_from_stdin_multi_source_bare_table_fails_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous bare table names fail before any proposals are written."""
    profile = _multi_source_profile(tmp_path)
    _seed_multi_source_orders_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "multi"],
        input=(
            "tables:\n"
            "  - table: orders\n"
            "    columns:\n"
            "      status: {role: dimension, dim_type: categorical}\n"
        ),
    )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "TableResolution"
    assert "ambiguous" in envelope["error"]["message"]
    assert calls["commits"] == []

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        assert db.list_semantic_proposals(status=None) == []
    finally:
        db.close()


def test_propose_from_stdin_resolution_failure_writes_no_partial_proposals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any table-resolution failure aborts the whole YAML batch."""
    profile = _multi_source_profile(tmp_path)
    _seed_multi_source_orders_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "multi"],
        input=(
            "tables:\n"
            "  - table: proj_a.default.orders\n"
            "    columns:\n"
            "      status: {role: dimension, dim_type: categorical}\n"
            "  - table: orders\n"
            "    columns:\n"
            "      amount: {role: measure, agg: SUM}\n"
        ),
    )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "TableResolution"
    assert "ambiguous" in envelope["error"]["message"]
    assert calls["commits"] == []

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status=None)
    finally:
        db.close()
    assert rows == []


def test_propose_from_stdin_multi_source_fqn_targets_requested_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _multi_source_profile(tmp_path)
    _seed_multi_source_orders_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "multi"],
        input=(
            "tables:\n"
            "  - table: proj_b.default.orders\n"
            "    columns:\n"
            "      status: {role: dimension, dim_type: categorical}\n"
        ),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"] == {
        "profile": "multi",
        "created": 1,
        "updated": 0,
        "reopened": 0,
        "skipped": 0,
    }
    assert calls["commits"] == [("multi", "package", "propose from stdin")]

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
    finally:
        db.close()
    assert len(rows) == 1
    payload = json.loads(rows[0]["patch_json"])
    assert payload["source_key"] == "proj_b__default"
    assert payload["table"] == "orders"
    assert rows[0]["target_ref"] == "proj_b__default.orders.status"


def test_propose_from_stdin_reopens_rejected_agent_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_key = "semantics:proj__default:orders:status"
    proposal_id = _seed_proposal(profile, proposal_key=proposal_key)
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        assert db.update_semantic_proposal_status(
            proposal_id,
            status="rejected",
            reviewed_by="agent",
            validation={"ok": True, "decision": "rejected", "reason": "first pass"},
        )
    finally:
        db.close()
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "propose",
            "--from-stdin",
            "--profile",
            "test",
        ],
        input=(
            "tables:\n"
            "  - table: orders\n"
            "    columns:\n"
            "      status: {role: dimension, dim_type: categorical, description: Current status.}\n"
        ),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"] == {
        "profile": "test",
        "created": 0,
        "updated": 0,
        "reopened": 1,
        "skipped": 0,
    }
    assert calls["commits"] == [("test", "package", "propose from stdin")]

    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
    finally:
        db.close()
    assert [row["id"] for row in rows] == [proposal_id]
    payload = json.loads(rows[0]["patch_json"])
    assert payload["semantic_description"] == "Current status."


def test_list_proposals_json_decodes_payload_and_filters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "list-proposals",
            "--status",
            "suggested",
            "--target-type",
            "column",
            "--limit",
            "5",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    proposals = envelope["data"]["proposals"]
    assert envelope["data"]["profile"] == "test"
    assert len(proposals) == 1
    assert proposals[0]["id"] == proposal_id
    assert proposals[0]["patch"]["column"] == "status"
    assert proposals[0]["evidence"] == [{"source": "history_sql", "where_count": 4}]
    assert proposals[0]["validation"] is None
    assert "patch_json" not in proposals[0]
    assert "evidence_json" not in proposals[0]
    assert "validation_json" not in proposals[0]
    assert calls["forks"] == []
    assert calls["commits"] == []


def test_list_proposals_defaults_to_suggested_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    suggested_id = _seed_proposal(profile, column="status")
    rejected_id = _seed_proposal(profile, column="amount")
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        assert db.update_semantic_proposal_status(
            rejected_id,
            status="rejected",
            reviewed_by="owner",
            validation={"ok": True, "decision": "rejected", "reason": "no"},
        )
    finally:
        db.close()
    _patch_command_context(monkeypatch, profile)

    result = _invoke(["list-proposals", "--profile", "test"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    ids = [row["id"] for row in envelope["data"]["proposals"]]
    assert ids == [suggested_id]


def test_list_missing_package_db_returns_json_error_without_creating_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    assert profile.package_path is not None
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(["list-proposals", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "run `mcs build` first" in envelope["error"]["remediation"]
    assert not (profile.package_path / "package.db").exists()
    assert calls["commits"] == []


def test_show_proposal_json_decodes_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        assert db.update_semantic_proposal_status(
            proposal_id,
            status="rejected",
            reviewed_by="owner",
            validation={"ok": False, "reason": "wrong grain"},
        )
    finally:
        db.close()
    _patch_command_context(monkeypatch, profile)

    result = _invoke(["show-proposal", str(proposal_id), "--profile", "test"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    proposal = envelope["data"]["proposal"]
    assert proposal["id"] == proposal_id
    assert proposal["patch"]["semantic_description"] == "Order lifecycle state."
    assert proposal["evidence"][0]["source"] == "history_sql"
    assert proposal["validation"] == {"ok": False, "reason": "wrong grain"}
    assert "patch_json" not in proposal


def test_show_missing_package_db_returns_json_error_without_creating_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    assert profile.package_path is not None
    _patch_command_context(monkeypatch, profile)

    result = _invoke(["show-proposal", "1", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "run `mcs build` first" in envelope["error"]["remediation"]
    assert not (profile.package_path / "package.db").exists()


def test_apply_writes_semantics_status_rerenders_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "apply",
            str(proposal_id),
            "--reviewed-by",
            "owner",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["applied"] is True
    assert envelope["data"]["rerendered_files"] == [
        "proj__default/orders.md",
        "_overview.md",
        "_state.json",
    ]
    semantics = _read_semantics(profile)
    assert semantics is not None
    assert semantics["semantic_role"] == "dimension"
    assert semantics["dim_type"] == "categorical"
    row = _read_proposal(profile, proposal_id)
    assert row["status"] == "applied"
    assert row["reviewed_by"] == "owner"
    assert json.loads(row["validation_json"]) == {"ok": True}
    assert profile.package_path is not None
    assert (profile.package_path / "proj__default" / "orders.md").exists()
    assert (profile.package_path / "_overview.md").exists()
    assert (profile.package_path / "_state.json").exists()
    assert calls["forks"] == ["test"]
    assert calls["commits"] == [("test", "package", f"apply proposal {proposal_id}")]


def test_apply_missing_proposal_returns_semantic_error_without_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(["apply", "999", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "999" in envelope["error"]["message"]
    assert calls["commits"] == []


def test_apply_malformed_patch_records_validation_and_commits_failed_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        proposal_id = db.upsert_semantic_proposal(
            proposal_key="manual:bad-patch",
            target_type="column",
            target_ref="proj__default.orders.status",
            operation="promote_annotation_suggestion",
            patch={
                "kind": "column_semantics",
                "source_key": "proj__default",
                "table": "orders",
                "role": "dimension",
                "dim_type": "categorical",
            },
            confidence=0.8,
            evidence=[],
            provenance="test",
            created_by="tester",
        )
    finally:
        db.close()
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "apply",
            str(proposal_id),
            "--reviewed-by",
            "owner",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "SemanticProposalError"
    row = _read_proposal(profile, proposal_id)
    assert row["status"] == "suggested"
    assert row["reviewed_by"] == "owner"
    validation = json.loads(row["validation_json"])
    assert validation["ok"] is False
    assert validation["code"] == "SemanticProposalError"
    assert calls["commits"] == [("test", "package", f"apply proposal {proposal_id} failed")]


def test_reject_writes_rejected_status_reason_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "reject",
            str(proposal_id),
            "--reason",
            "domain owner declined",
            "--reviewed-by",
            "owner",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["rejected"] is True
    row = _read_proposal(profile, proposal_id)
    assert row["status"] == "rejected"
    assert row["reviewed_by"] == "owner"
    assert json.loads(row["validation_json"]) == {
        "ok": True,
        "decision": "rejected",
        "reason": "domain owner declined",
    }
    assert calls["forks"] == ["test"]
    assert calls["commits"] == [("test", "package", f"reject proposal {proposal_id}")]


def test_reject_without_reason_succeeds_and_stores_empty_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(["reject", str(proposal_id), "--profile", "test"])

    assert result.exit_code == 0, result.output
    row = _read_proposal(profile, proposal_id)
    assert row["status"] == "rejected"
    assert json.loads(row["validation_json"]) == {
        "ok": True,
        "decision": "rejected",
        "reason": "",
    }
    assert calls["commits"] == [("test", "package", f"reject proposal {proposal_id}")]


def test_reject_blank_reason_returns_json_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(["reject", str(proposal_id), "--reason", "   ", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "blank" in envelope["error"]["message"]
    row = _read_proposal(profile, proposal_id)
    assert row["status"] == "suggested"
    assert row["validation_json"] is None
    assert calls["commits"] == []


def test_reject_applied_proposal_fails_without_mutation_or_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    proposal_id = _seed_proposal(profile)
    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        assert db.update_semantic_proposal_status(
            proposal_id,
            status="applied",
            reviewed_by="owner",
            validation={"ok": True},
        )
    finally:
        db.close()
    before = _read_proposal(profile, proposal_id)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(
        [
            "reject",
            str(proposal_id),
            "--reason",
            "no longer wanted",
            "--profile",
            "test",
        ]
    )

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "SemanticProposalError"
    after = _read_proposal(profile, proposal_id)
    assert after["status"] == "applied"
    assert after["applied_at"] == before["applied_at"]
    assert after["validation_json"] == before["validation_json"]
    assert calls["commits"] == []


def test_reject_missing_proposal_returns_semantic_error_without_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    calls = _patch_command_context(monkeypatch, profile)

    result = _invoke(["reject", "999", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "999" in envelope["error"]["message"]
    assert calls["commits"] == []


def test_show_missing_proposal_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    result = _invoke(["show-proposal", "999", "--profile", "test"])

    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "SemanticProposalError"
    assert "999" in envelope["error"]["message"]


# ── metric proposal tests ────────────────────────────────────────


def test_propose_from_stdin_creates_metric_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    result = _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input=(
            "metrics:\n"
            "  - name: total_revenue\n"
            "    expression: SUM(orders.amount)\n"
            "    description: Gross revenue\n"
        ),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["created"] == 1

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
    finally:
        db.close()
    assert len(rows) == 1
    payload = json.loads(rows[0]["patch_json"])
    assert payload["kind"] == "metric"
    assert payload["name"] == "total_revenue"
    assert payload["expression"] == "SUM(orders.amount)"


def test_metric_proposal_apply_writes_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input="metrics:\n  - name: rev\n    expression: SUM(orders.amount)\n",
    )

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
        proposal_id = rows[0]["id"]
    finally:
        db.close()

    apply_result = _invoke(["apply", str(proposal_id), "--profile", "test"])
    assert apply_result.exit_code == 0, apply_result.output

    db = PackageDB(profile.package_path / "package.db")
    try:
        metrics = db.list_metrics()
    finally:
        db.close()
    assert any(m["name"] == "rev" for m in metrics)


def test_metric_proposal_apply_duplicate_records_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying a metric proposal with a duplicate name must record
    the failure in the proposal's validation_json, not leave it
    as suggested with no trace."""
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        db.add_metric(name="rev", expression="SUM(orders.amount)")
    finally:
        db.close()

    _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input="metrics:\n  - name: rev\n    expression: COUNT(orders.amount)\n",
    )

    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
        proposal_id = rows[0]["id"]
    finally:
        db.close()

    apply_result = _invoke(["apply", str(proposal_id), "--profile", "test"])
    assert apply_result.exit_code != 0

    db = PackageDB(profile.package_path / "package.db")
    try:
        row = db.get_semantic_proposal(proposal_id)
    finally:
        db.close()
    assert row is not None
    assert row["status"] == "suggested"
    assert row["validation_json"] is not None


def test_metric_proposal_apply_bad_expression_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable metric expression must be caught at apply time."""
    profile = _profile(tmp_path)
    _seed_package(profile)
    _patch_command_context(monkeypatch, profile)

    _invoke(
        ["propose", "--from-stdin", "--profile", "test"],
        input="metrics:\n  - name: bad\n    expression: SUM(((\n",
    )

    assert profile.package_path is not None
    db = PackageDB(profile.package_path / "package.db")
    try:
        rows = db.list_semantic_proposals(status="suggested")
        proposal_id = rows[0]["id"]
    finally:
        db.close()

    apply_result = _invoke(["apply", str(proposal_id), "--profile", "test"])
    assert apply_result.exit_code != 0
