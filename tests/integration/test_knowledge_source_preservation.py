# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge acquisition and artifact output preserve active source assets."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli
from maxcompute_semantic.knowledge import KnowledgeStore


def test_ingesting_a_legacy_package_does_not_migrate_or_modify_its_source(
    isolated_config: Path,
) -> None:
    package = isolated_config / "legacy" / "package.db"
    package.parent.mkdir()
    db = PackageDB(package)
    db.upsert_table("warehouse__default", "orders", "schema-v1")
    db.close()
    with sqlite3.connect(package) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("DROP TABLE semantic_proposals")
        conn.execute("PRAGMA user_version=12")
    before = package.read_bytes()
    before_mtime = package.stat().st_mtime_ns
    upsert(Profile(
        name="legacy", compute_project="warehouse", endpoint="https://example.invalid/api",
        auth=AkAuth("${env:UNSET_TEST_AK}", "${env:UNSET_TEST_SK}"), package_path=package.parent,
    ))
    result = CliRunner().invoke(cli, [
        "-f", "json", "knowledge", "--kb", str(isolated_config / "knowledge.db"),
        "ingest-package", "--profile", "legacy",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "success"
    assert package.read_bytes() == before
    assert package.stat().st_mtime_ns == before_mtime
    assert not Path(str(package) + "-wal").exists()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink", "wal", "shm", "journal"])
def test_projection_output_cannot_overwrite_its_source_database_or_journals(
    isolated_config: Path, alias: str,
) -> None:
    kb = isolated_config / "knowledge.db"
    runner = CliRunner()
    initialized = runner.invoke(cli, ["-f", "json", "knowledge", "init", "--kb", str(kb)])
    assert initialized.exit_code == 0, initialized.output
    output = kb
    if alias == "symlink":
        output = isolated_config / "alias.json"
        output.symlink_to(kb)
    elif alias == "hardlink":
        output = isolated_config / "alias.json"
        os.link(kb, output)
    elif alias in {"wal", "shm", "journal"}:
        output = Path(str(kb) + "-" + alias)
    result = runner.invoke(cli, [
        "-f", "json", "knowledge", "project", "--selected", "missing",
        "--kb", str(kb), "--output", str(output),
    ])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "ArtifactSourceOverwrite"
    readable = runner.invoke(cli, ["-f", "json", "knowledge", "list", "--kb", str(kb)])
    assert readable.exit_code == 0, readable.output
    assert kb.read_bytes().startswith(b"SQLite format 3")


@pytest.mark.parametrize("command", ["export", "project"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_symlink_database_output_protects_real_sqlite_sidecars(
    isolated_config: Path, command: str, suffix: str,
) -> None:
    database = isolated_config / "real" / "knowledge.db"
    alias = isolated_config / "linked.db"
    with KnowledgeStore(database) as store:
        alias.symlink_to(database)
        output = Path(str(database) + suffix)
        args = ["-f", "json", "knowledge", "--kb", str(alias), command]
        if command == "project":
            args.extend(["--selected", "absent", "--output", str(output)])
        else:
            args.append(str(output))
        result = CliRunner().invoke(cli, args)
        assert result.exit_code != 0
        assert "overwrite an active source store" in result.stdout
        assert store.export_graph()["kb_id"] == store.meta()["kb_id"]
        if output.exists():
            assert not output.read_bytes().startswith(b"{")
