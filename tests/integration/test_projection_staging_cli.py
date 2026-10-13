# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Staging never turns an incomplete knowledge preview into a remote write."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic.cli import cli
from maxcompute_semantic.remote.client import SemanticSpecClient
from maxcompute_semantic.remote.config import RemoteStore
from tests.unit.remote.test_semantic_sync import PUBLISH, UPDATE, Gateway


def test_unpublishable_projection_is_rejected_before_workspace_or_auth_access(
    isolated_config: Path,
) -> None:
    report = isolated_config / "projection.json"
    report.write_text(json.dumps({
        "publishable": False, "sections": {"glossary": [{"term": "customer"}]},
        "unmapped": [{"id": "missing-binding", "reason": "missing evidence"}],
        "validation_issues": [],
    }))
    workspace = isolated_config / "unbound"
    result = CliRunner().invoke(cli, [
        "-f", "json", "package", "stage-projection", "--file", str(report),
        "--workspace", str(workspace),
    ])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "SemanticProjectionRejected"
    assert not workspace.exists()


def test_projection_cannot_hide_unmapped_items_behind_a_publishable_flag(
    isolated_config: Path,
) -> None:
    report = isolated_config / "projection.json"
    report.write_text(json.dumps({
        "publishable": True, "sections": {"glossary": [{"term": "customer"}]},
        "unmapped": [{"id": "unresolved", "reason": "missing physical table"}],
        "validation_issues": [],
    }))
    result = CliRunner().invoke(cli, [
        "-f", "json", "package", "stage-projection", "--file", str(report),
        "--workspace", str(isolated_config / "unbound"),
    ])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "SemanticProjectionRejected"


def test_registered_cli_stages_exports_pushes_and_publishes_a_complete_revision(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway()
    gateway.draft = {
        "dataReferences": [{"project": "warehouse", "schema": "default", "table": "orders", "tableId": "physical-1"}],
        "semanticModel": {"logicalTables": [{"name": "orders", "baseTable": "warehouse.default.orders"}]},
        "metricDefinitions": [{"name": "gmv", "semanticType": "MEASURE", "baseLogicalTable": "orders",
                               "expr": "amount", "dataType": "DECIMAL", "defaultAggregation": "SUM"}],
        "glossary": [{"term": "order", "definition": "an accepted purchase", "applicableTables": ["orders"]}],
    }
    gateway.history["published-1"] = deepcopy(gateway.draft)
    RemoteStore().add(gateway.connection)

    def external_mcp_call(_client: SemanticSpecClient, tool: str, args: dict) -> dict:
        return gateway.call(tool, args)

    monkeypatch.setattr(SemanticSpecClient, "call", external_mcp_call)
    workspace = isolated_config / "remote-workspace"
    runner = CliRunner()

    def call(*arguments: str) -> dict:
        result = runner.invoke(cli, ["-f", "json", "package", *arguments,
                                     "--workspace", str(workspace)])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)["data"]

    call("bind-remote", "test", "--name", "sales")
    call("pull", "--source", "USER_DRAFT")
    report = isolated_config / "projection.json"
    report.write_text(json.dumps({
        "publishable": True, "unmapped": [], "validation_issues": [],
        "sections": {"glossary": [{"term": "active_customer", "definition": "a subscribed customer"}]},
    }))
    staged = call("stage-projection", "--file", str(report))
    assert staged["remote_written"] is False
    snapshot_path = isolated_config / "snapshot.json"
    call("export-semantic", "--output", str(snapshot_path))
    snapshot = json.loads(snapshot_path.read_text())
    glossary = next(row["content"] for row in snapshot["sections"] if row["section"] == "glossary")
    assert glossary == [{"term": "order", "definition": "an accepted purchase", "applicableTables": ["orders"]}]
    assert not any(tool in {UPDATE, PUBLISH} for tool, _ in gateway.calls)
    preview = call("push", "--dry-run")
    call("push", "--apply", "--plan-hash", preview["plan_hash"])
    assert {row["term"] for row in gateway.draft["glossary"]} == {"order", "active_customer"}
    reviewed = call("pull", "--source", "USER_DRAFT")
    published = call("publish", "--revision", reviewed["revision_id"], "--confirm")
    assert published["published_revision_id"] == "published-2"
    assert gateway.history["published-2"]["metricDefinitions"] == gateway.draft["metricDefinitions"]
    assert sum(tool == UPDATE for tool, _ in gateway.calls) == 1
    assert sum(tool == PUBLISH for tool, _ in gateway.calls) == 1
