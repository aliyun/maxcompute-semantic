# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/memory.py -- mcs memory write and read/manage subcommands."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli


def _ak_profile(name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="test_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _open_db(profile_name: str) -> PackageDB:
    """Open PackageDB for the given profile name in isolated config."""
    from maxcompute_semantic._internal.paths import profile_data_dir

    db_path = profile_data_dir(profile_name) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return PackageDB(db_path)


def test_memory_verify_creates_entry(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "verify",
            "--question",
            "How many card games?",
            "--sql",
            "SELECT count(*) FROM card_games",
            "--tables",
            "card_games",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    # Verify the entry was actually created in the database
    db = _open_db("test")
    entries = db.list_memories(kind="verified_query")
    assert len(entries) == 1
    payload = json.loads(entries[0]["payload_json"])
    assert payload["question"] == "How many card games?"
    assert payload["sql"] == "SELECT count(*) FROM card_games"
    # Chain ε: table_refs are stored as ``{source_key, table}`` dicts
    # so recall can return source-aware FQN-qualified references.
    assert payload["table_refs"] == [{"source_key": "test_project__default", "table": "card_games"}]
    db.close()


def test_memory_verify_with_evidence(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "verify",
            "--question",
            "How many foils?",
            "--sql",
            "SELECT count(*) FROM t WHERE foil IS NOT NULL",
            "--tables",
            "card_games",
            "--evidence",
            "foil refers to cardKingdomFoilId",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    db = _open_db("test")
    entries = db.list_memories(kind="verified_query")
    payload = json.loads(entries[0]["payload_json"])
    assert payload["evidence_text"] == "foil refers to cardKingdomFoilId"
    db.close()


def test_memory_verify_marks_matching_sample_sql_pattern_verified(isolated_config: Path) -> None:
    from maxcompute_semantic.memory.sample_sql import persist_sample_sqls

    upsert(_ak_profile())
    db = _open_db("test")
    sk = "test_project__default"
    tid = db.upsert_table(sk, "cards", "h1")
    db.upsert_columns(
        tid,
        [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
    )
    persist_sample_sqls(
        db,
        {"cards": ["SELECT id FROM cards WHERE id = 10"]},
        sk,
    )
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "verify",
            "--profile",
            "test",
            "--question",
            "Find card 20",
            "--sql",
            "SELECT id FROM cards WHERE id = 20",
            "--tables",
            "cards",
        ],
    )

    assert result.exit_code == 0, result.output
    db = _open_db("test")
    rows = db.list_sample_sqls(source_key=sk, table="cards")
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["verified_count"] == 1
    assert payload["confidence"] == "user_verified"
    assert db.list_memories(kind="verified_query")
    db.close()


def test_memory_fail_creates_entry(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "fail",
            "--question",
            "Show top 10 players",
            "--sql",
            "SELECT * FROM players LIMIT 10",
            "--error-code",
            "FULL_SCAN_BLOCKED",
            "--error-msg",
            "ODPS-0421065: Full scan is not allowed",
            "--remediation",
            "Add a partition filter",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    db = _open_db("test")
    entries = db.list_memories(kind="failed_query")
    assert len(entries) == 1
    payload = json.loads(entries[0]["payload_json"])
    assert payload["error_code"] == "FULL_SCAN_BLOCKED"
    assert payload["remediation"] == "Add a partition filter"
    db.close()


def test_memory_fail_auto_classifies_error_code(isolated_config: Path) -> None:
    """When --error-code is omitted, auto-classify from --error-msg."""
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "fail",
            "--question",
            "Show top 10",
            "--sql",
            "SELECT * FROM t",
            "--error-msg",
            "ODPS-0421065: Full scan is not allowed",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    db = _open_db("test")
    entries = db.list_memories(kind="failed_query")
    payload = json.loads(entries[0]["payload_json"])
    # Auto-classified to FULL_SCAN_BLOCKED from the ODPS code in error_msg
    assert payload["error_code"] == "FULL_SCAN_BLOCKED"
    db.close()


def test_memory_note_creates_entry(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "note",
            "Always use ds partition filter for card_games",
            "--tags",
            "preference,project-x",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    db = _open_db("test")
    entries = db.list_memories(kind="user_note")
    assert len(entries) == 1
    payload = json.loads(entries[0]["payload_json"])
    assert payload["text"] == "Always use ds partition filter for card_games"
    assert payload["tags"] == ["preference", "project-x"]
    assert entries[0]["tags_json"] == json.dumps(["preference", "project-x"], ensure_ascii=False)
    db.close()


def test_memory_note_without_tags(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "note",
            "some plain note",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    db = _open_db("test")
    entries = db.list_memories(kind="user_note")
    assert len(entries) == 1
    assert entries[0]["tags_json"] is None
    db.close()


def test_memory_verify_json_mode(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "memory",
            "verify",
            "--question",
            "Q1",
            "--sql",
            "SELECT 1",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["status"] == "success"


def test_memory_no_persistent_profile_env_vars_fallback(isolated_config: Path) -> None:
    """When no persistent profile exists, memory commands use env vars fallback."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "verify",
            "--question",
            "Q1",
            "--sql",
            "SELECT 1",
        ],
    )
    # With env vars fallback in _resolve_profile_for_project, the command
    # resolves a profile even without a persistent profile on disk.
    assert result.exit_code == 0


def test_memory_note_quiet_mode(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "-q",
            "memory",
            "note",
            "quiet note text",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    # Quiet mode: no output on success (plain format)
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# Read / manage command tests
# ---------------------------------------------------------------------------


def test_memory_recall_returns_results(isolated_config: Path) -> None:
    """mcs memory recall <query> returns BM25-ranked results."""
    upsert(_ak_profile())
    # Seed some data
    db = _open_db("test")
    db.upsert_memory(
        "verified_query",
        '{"question":"card games foil"}',
        "Q: How many card games have foil?\nSQL: SELECT count(*) FROM t\nTables: t\nEvidence: ",
    )
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "recall",
            "card games foil",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_recall_with_kind_filter(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "card games verified query")
    db.upsert_memory("user_note", '{"q":2}', "card games note")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "recall",
            "card games",
            "--kind",
            "verified_query",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_recall_json_mode(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "card games foil")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "memory",
            "recall",
            "card games",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["status"] == "success"


def test_memory_recall_empty_db(isolated_config: Path) -> None:
    """recall on empty DB returns empty results, not an error."""
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "recall",
            "something",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_list_shows_entries(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.upsert_memory("user_note", '{"q":2}', "text2")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "list",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_list_with_kind_filter(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.upsert_memory("user_note", '{"q":2}', "text2")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "list",
            "--kind",
            "verified_query",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_list_empty_returns_empty(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "list",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_list_json_mode(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "-f",
            "json",
            "memory",
            "list",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_show_existing_entry(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    id_ = db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "show",
            str(id_),
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_show_missing_entry_exits_1(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "show",
            "99999",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 1


def test_memory_remove_existing_entry(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    id_ = db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "remove",
            str(id_),
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_remove_missing_entry_exits_1(isolated_config: Path) -> None:
    upsert(_ak_profile())
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "remove",
            "99999",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 1


def test_memory_clear_by_kind(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "text1")
    db.upsert_memory("user_note", '{"q":2}', "text2")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "clear",
            "--kind",
            "verified_query",
            "--yes",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_reindex(isolated_config: Path) -> None:
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("user_note", '{"q":1}', "card games foil")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "memory",
            "reindex",
            "--profile",
            "test",
        ],
    )
    assert result.exit_code == 0


def test_memory_clear_default_preserves_generated_entries(isolated_config: Path) -> None:
    """``mcs memory clear`` without ``--include-generated`` preserves package_doc/sample_sql."""
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "verified")
    db.upsert_memory("user_note", '{"q":2}', "note")
    db.upsert_memory("package_doc", '{"q":3}', "package")
    db.upsert_memory(
        "sample_sql",
        json.dumps(
            {
                "source_key": "test_project__default",
                "table": "orders",
                "sql": "SELECT * FROM orders",
            }
        ),
        "sample sql",
    )
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["memory", "clear", "--yes", "--profile", "test"])

    assert result.exit_code == 0, result.output
    db = _open_db("test")
    kinds = {entry["kind"] for entry in db.list_memories(limit=50)}
    assert kinds == {"package_doc", "sample_sql"}
    db.close()


def test_memory_clear_include_generated_deletes_everything(isolated_config: Path) -> None:
    """``mcs memory clear --include-generated`` deletes all entries including generated."""
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "verified")
    db.upsert_memory("package_doc", '{"q":2}', "package")
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["memory", "clear", "--include-generated", "--yes", "--profile", "test"],
    )

    assert result.exit_code == 0, result.output
    db = _open_db("test")
    assert db.list_memories(limit=50) == []
    db.close()


def test_memory_clear_aborts_without_yes_when_user_says_no(isolated_config: Path) -> None:
    """Without ``--yes``, answering ``n`` at the prompt leaves entries intact."""
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "verified")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["memory", "clear", "--profile", "test"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "aborted" in result.output
    db = _open_db("test")
    kinds = {entry["kind"] for entry in db.list_memories(limit=50)}
    assert "verified_query" in kinds
    db.close()


def test_memory_clear_proceeds_when_user_confirms(isolated_config: Path) -> None:
    """Without ``--yes`` but typing ``y`` at the prompt, the clear runs."""
    upsert(_ak_profile())
    db = _open_db("test")
    db.upsert_memory("verified_query", '{"q":1}', "verified")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["memory", "clear", "--profile", "test"], input="y\n")

    assert result.exit_code == 0, result.output
    db = _open_db("test")
    kinds = {entry["kind"] for entry in db.list_memories(limit=50)}
    assert "verified_query" not in kinds
    db.close()


class TestSampleSqlRedactionAtMemoryVerbs:
    """``mcs memory recall`` / ``mcs memory show`` must apply the same
    SELECT-projection redaction that ``mcs show --table`` does, so the
    agent can't trip the copy-paste wire from a different verb.
    """

    @staticmethod
    def _seed_sample_sql(confidence: str = "mined_low") -> int:
        db = _open_db("test")
        payload = {
            "table": "cards",
            "source_key": "test_project__default",
            "sql": "SELECT name, cardkingdomid FROM cards WHERE cardkingdomid IS NOT NULL",
            "representative_sql": (
                "SELECT name, cardkingdomid FROM cards WHERE cardkingdomid IS NOT NULL"
            ),
            "canonical_sql": (
                "SELECT name, cardkingdomid FROM cards WHERE cardkingdomid IS NOT NULL"
            ),
            "shape_key": "shape_xyz",
            "frequency": 1,
            "confidence": confidence,
        }
        retrieval_text = (
            f"sample_sql for test_project__default:cards: shape=shape_xyz "
            f"freq=1 verified=0 "
            f"canonical={payload['canonical_sql']} "
            f"representative={payload['representative_sql']}"
        )
        eid = db.upsert_memory("sample_sql", json.dumps(payload), retrieval_text)
        db.close()
        return eid

    def test_recall_json_redacts_mined_sample_sql_payload(self, isolated_config: Path) -> None:
        upsert(_ak_profile())
        self._seed_sample_sql(confidence="mined_low")

        result = CliRunner().invoke(
            cli,
            ["-f", "json", "memory", "recall", "cards", "--profile", "test"],
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output.strip().split("\n")[-1])
        results = body["data"]["results"]
        assert len(results) >= 1
        sample = next(r for r in results if r["kind"] == "sample_sql")
        payload = json.loads(sample["payload_json"])
        # Projection redacted; WHERE clause preserved.
        assert "<col>" in payload["sql"]
        assert "WHERE" in payload["sql"].upper()
        assert "FROM cards" in payload["sql"]
        # Raw column list must not leak through.
        assert "SELECT name, cardkingdomid" not in payload["sql"]
        assert "<col>" in payload["canonical_sql"]
        assert "<col>" in payload["representative_sql"]

    def test_recall_preserves_user_verified_sample_sql_payload(self, isolated_config: Path) -> None:
        upsert(_ak_profile())
        self._seed_sample_sql(confidence="user_verified")

        result = CliRunner().invoke(
            cli,
            ["-f", "json", "memory", "recall", "cards", "--profile", "test"],
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output.strip().split("\n")[-1])
        sample = next(r for r in body["data"]["results"] if r["kind"] == "sample_sql")
        payload = json.loads(sample["payload_json"])
        # user_verified entries are passed through untouched.
        assert "SELECT name, cardkingdomid" in payload["sql"]
        assert "<col>" not in payload["sql"]

    def test_show_redacts_mined_sample_sql_payload(self, isolated_config: Path) -> None:
        upsert(_ak_profile())
        eid = self._seed_sample_sql(confidence="mined_low")

        result = CliRunner().invoke(
            cli,
            ["-f", "json", "memory", "show", str(eid), "--profile", "test"],
        )
        assert result.exit_code == 0, result.output
        body = json.loads(result.output.strip().split("\n")[-1])
        payload = json.loads(body["data"]["entry"]["payload_json"])
        assert "<col>" in payload["sql"]
        assert "SELECT name, cardkingdomid" not in payload["sql"]
