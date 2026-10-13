# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""CLI authorization and formatting through the public sync seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from maxcompute_semantic.commands import package_remote
from maxcompute_semantic.remote.config import RemoteStore
from maxcompute_semantic.remote.sync import SemanticPackageSync
from tests.unit.remote.test_semantic_sync import PUBLISH, UPDATE, Gateway


@pytest.fixture
def command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[click.Group, Gateway]:
    gateway = Gateway()
    @click.group()
    @click.pass_context
    def group(ctx: click.Context) -> None:
        ctx.obj = {"format": "json", "quiet": False}
    package_remote.register_package_remote_commands(group)
    def client(workspace: Path, *, remote: str | None = None) -> SemanticPackageSync:
        return SemanticPackageSync(workspace, gateway)
    monkeypatch.setattr(package_remote, "_sync", client)
    return group, gateway


def _invoke(command: click.Group, workspace: Path, *arguments: str) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(command, [*arguments, "--workspace", str(workspace)])
    return result.exit_code, json.loads(result.output)


def _prepare(command: click.Group, workspace: Path) -> None:
    code, _ = _invoke(command, workspace, "bind-remote", "test", "--name", "sales")
    assert code == 0
    code, _ = _invoke(command, workspace, "pull", "--source", "USER_DRAFT", "--section", "metricDefinitions")
    assert code == 0


def test_bind_and_partial_pull_require_no_maxcompute_profile(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    _prepare(group, tmp_path)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["binding"]["region"] == gateway.connection.region
    assert list(state["local"]) == ["metricDefinitions"]


def test_edit_preview_then_exact_approval_is_required_to_write(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    _prepare(group, tmp_path)
    path = tmp_path / "selected-metric.json"
    path.write_text('[{"name":"gmv","expr":"net_amount"}]')
    code, edit = _invoke(group, tmp_path, "edit-section", "metricDefinitions", "--file", str(path))
    assert code == 0 and not edit["data"]["remote_written"]
    code, preview = _invoke(group, tmp_path, "push", "--dry-run")
    assert code == 0 and preview["data"]["dry_run"]
    assert not any(tool == UPDATE for tool, _ in gateway.calls)
    code, error = _invoke(group, tmp_path, "push", "--apply")
    assert code == 2 and error["error"]["code"] == "SemanticPlanChanged"
    code, result = _invoke(group, tmp_path, "push", "--apply", "--plan-hash", preview["data"]["plan_hash"])
    assert code == 0 and result["data"]["remote_written"]
    assert gateway.draft["metricDefinitions"][1]["name"] == "count"
    assert not any(tool == PUBLISH for tool, _ in gateway.calls)


def test_publication_without_full_section_review_is_an_error(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    _prepare(group, tmp_path)
    code, error = _invoke(group, tmp_path, "publish", "--revision", "draft-1", "--confirm")
    assert code == 2 and error["error"]["code"] == "SemanticPublicationUnreviewed"
    assert not any(tool == PUBLISH for tool, _ in gateway.calls)


def test_explicit_entry_removal_is_local_and_preserves_other_entries(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    _prepare(group, tmp_path)
    path = tmp_path / "changes.json"
    path.write_text("[]")
    code, result = _invoke(group, tmp_path, "edit-section", "metricDefinitions", "--file", str(path), "--remove", "count")
    assert code == 0
    assert [row["name"] for row in result["data"]["after"]["content"]] == ["gmv"]
    assert len(gateway.draft["metricDefinitions"]) == 2


def test_json_remote_failure_is_not_a_success_envelope(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    _prepare(group, tmp_path)
    gateway.rejection = {"ok": False, "error": {"code": "permission_denied"}}
    code, result = _invoke(group, tmp_path, "diff")
    assert code == 2 and result["status"] == "error"


def test_connection_factory_uses_named_remote_and_sidecar_without_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gateway = Gateway()
    monkeypatch.setenv("MCS_CONFIG_DIR", str(tmp_path / "config"))
    RemoteStore().add(gateway.connection)
    monkeypatch.setattr(package_remote, "SemanticSpecClient", lambda connection: gateway)
    sync = package_remote._sync(tmp_path / "workspace", remote="test")
    sync.bind("sales", "cn-test")
    rebound = package_remote._sync(tmp_path / "workspace")
    rebound.pull("USER_DRAFT", ["metricDefinitions"])
    assert rebound.local_sections()["metricDefinitions"] == gateway.draft["metricDefinitions"]


def test_unbound_connection_factory_has_a_classified_error(tmp_path: Path) -> None:
    with pytest.raises(package_remote.SemanticSyncError) as error:
        package_remote._sync(tmp_path)
    assert str(error.value.code) == "SemanticNotBound"


def test_apply_and_dry_run_options_are_mutually_exclusive(command: tuple[click.Group, Gateway], tmp_path: Path) -> None:
    group, gateway = command
    result = CliRunner().invoke(group, ["push", "--apply", "--dry-run", "--workspace", str(tmp_path)])
    assert result.exit_code == 2 and "mutually exclusive" in result.output
    assert not gateway.calls
