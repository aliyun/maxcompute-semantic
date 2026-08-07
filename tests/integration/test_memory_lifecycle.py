# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Integration test: mcs memory lifecycle — build -> verify -> recall."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli
from maxcompute_semantic.memory.package_doc import generate_package_docs

_SK = "test_project__default"


def _ak_profile(name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="test_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _parse_json_output(output: str) -> dict:
    """Parse the last JSON line from CLI output (envelope format)."""
    lines = output.strip().split("\n")
    # Find the last line that parses as JSON
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON envelope found in output: {output[:200]}")


def test_build_then_recall_returns_package_doc(isolated_config: Path) -> None:
    """After build (generating package_doc entries), recall finds them."""
    upsert(_ak_profile())
    # Simulate build output by directly creating PackageDB + package_doc entries
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir("test") / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(db_path)
    tid = db.upsert_table(_SK, "card_games", "h1")
    db.upsert_columns(
        tid,
        [
            {"name": "game_id", "type": "STRING", "comment": "game identifier", "is_partition": 0},
            {"name": "game_type", "type": "STRING", "comment": "game category", "is_partition": 0},
        ],
    )
    generate_package_docs(db)
    db.close()

    runner = CliRunner()
    # MemoryTokenizer treats underscores as part of tokens, so "card_games"
    # is one token. Searching for "card_games" matches the package_doc.
    result = runner.invoke(
        cli,
        ["-f", "json", "memory", "recall", "card_games", "--profile", "test"],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
    payload = _parse_json_output(result.output)
    assert payload["status"] == "success"
    results = payload["data"]["results"]
    assert len(results) >= 1
    # At least one result should be a package_doc about card_games
    found_pkg = any(
        r["kind"] == "package_doc" and "card_games" in r["retrieval_text"] for r in results
    )
    assert found_pkg


def test_build_then_verify_then_recall_mixed(isolated_config: Path) -> None:
    """After build + user verify, recall returns both package_doc and verified_query."""
    upsert(_ak_profile())
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir("test") / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(db_path)
    tid = db.upsert_table(_SK, "card_games", "h1")
    db.upsert_columns(
        tid,
        [
            {"name": "game_id", "type": "STRING", "comment": "identifier", "is_partition": 0},
        ],
    )
    generate_package_docs(db)
    db.close()

    runner = CliRunner()
    # Add a verified query
    result = runner.invoke(
        cli,
        [
            "memory",
            "verify",
            "--question",
            "How many card games have foil?",
            "--sql",
            "SELECT count(*) FROM card_games WHERE foil IS NOT NULL",
            "--tables",
            "card_games",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"

    # Recall should return both kinds. "card_games" token appears in
    # both package_doc (table name) and verified_query (SQL/Tables lines).
    result = runner.invoke(
        cli,
        ["-f", "json", "memory", "recall", "card_games", "--profile", "test"],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
    payload = _parse_json_output(result.output)
    results = payload["data"]["results"]
    kinds = {r["kind"] for r in results}
    assert "package_doc" in kinds
    assert "verified_query" in kinds


def test_reindex_after_manual_fts_clear(isolated_config: Path) -> None:
    """After manually clearing fts_text and FTS index, reindex restores searchability."""
    upsert(_ak_profile())
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir("test") / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(db_path)
    db.upsert_memory("user_note", '{"text":"card games"}', "card games")
    # Clear fts_text and wipe the FTS index to simulate corruption
    db._conn.execute("UPDATE memory_entries SET fts_text = ''")
    db._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
    db._conn.commit()
    db.close()

    runner = CliRunner()
    # Before reindex, recall should return nothing
    result = runner.invoke(
        cli,
        ["-f", "json", "memory", "recall", "card games", "--profile", "test"],
    )
    assert result.exit_code == 0
    payload_before = _parse_json_output(result.output)
    assert payload_before["data"]["results"] == []

    # Reindex
    result = runner.invoke(cli, ["-f", "json", "memory", "reindex", "--profile", "test"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"

    # After reindex, recall works
    result = runner.invoke(
        cli,
        ["-f", "json", "memory", "recall", "card games", "--profile", "test"],
    )
    assert result.exit_code == 0
    payload_after = _parse_json_output(result.output)
    assert len(payload_after["data"]["results"]) >= 1
