# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""A shared definition can be searched and projected using only the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maxcompute_semantic.cli import cli


def test_published_glossary_is_local_searchable_and_projectable_without_profile(
    isolated_config: Path,
) -> None:
    snapshot = isolated_config / "snapshot.json"
    snapshot.write_text(json.dumps({
        "semantic_spec_id": "shared-spec", "namespace": "example", "name": "sales",
        "endpoint": "https://mcp.example.test/mcp", "region": "cn-shanghai",
        "source": "PUBLISHED", "revision_id": "published-1",
        "sections": {"glossary": {"present": True, "content": [{
            "term": "active_customer", "definition": "a customer with a confirmed subscription",
            "alsoKnownAs": ["subscribed customer"],
        }]}},
    }), encoding="utf-8")
    kb = isolated_config / "knowledge.db"
    runner = CliRunner()
    imported = runner.invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(kb), "ingest-semantic", str(snapshot),
    ])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["status"] == "success"

    searched = runner.invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(kb), "search", "subscription",
    ])
    assert searched.exit_code == 0, searched.output
    assert "active_customer" in searched.stdout

    listed = runner.invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(kb), "list", "--kind", "term",
    ])
    assert listed.exit_code == 0, listed.output
    node = next(item for item in json.loads(listed.stdout)["data"]["nodes"]
                if item["name"] == "active_customer")
    output = isolated_config / "projection.json"
    projected = runner.invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(kb), "project",
        "--selected", node["id"], "--output", str(output),
    ])
    assert projected.exit_code == 0, projected.output
    report = json.loads(output.read_text())
    assert report["publishable"] is True
    assert report["sections"]["glossary"] == [{
        "term": "active_customer", "definition": "a customer with a confirmed subscription",
        "alsoKnownAs": ["subscribed customer"],
    }]
    assert "access_token" not in output.read_text()


def test_missing_snapshot_is_reported_without_creating_a_knowledge_database(
    isolated_config: Path,
) -> None:
    kb = isolated_config / "knowledge.db"
    result = CliRunner().invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(kb), "ingest-semantic",
        str(isolated_config / "absent.json"),
    ])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["status"] == "error"
    assert not kb.exists()
