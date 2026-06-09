# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory/package_doc.py — auto-generate package_doc entries."""

from __future__ import annotations

import json
from pathlib import Path

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.memory.package_doc import generate_package_docs

_SK = "test_project__default"


class TestGeneratePackageDocs:
    def test_generates_one_entry_per_table(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid1 = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid1,
            [
                {
                    "name": "game_id",
                    "type": "STRING",
                    "comment": "unique game identifier",
                    "is_partition": 0,
                },
                {
                    "name": "game_type",
                    "type": "STRING",
                    "comment": "game category, enum: card/board/dice",
                    "is_partition": 0,
                },
                {
                    "name": "ds",
                    "type": "STRING",
                    "comment": "partition",
                    "is_partition": 1,
                },
            ],
        )
        tid2 = db.upsert_table(_SK, "players", "h2")
        db.upsert_columns(
            tid2,
            [
                {
                    "name": "player_id",
                    "type": "BIGINT",
                    "comment": "player identifier",
                    "is_partition": 0,
                },
            ],
        )
        count = generate_package_docs(db)
        assert count == 2
        entries = db.list_memories(kind="package_doc")
        assert len(entries) == 2

    def test_generates_one_entry_per_udf(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_udf(
            "my_agg",
            "java",
            signature="my_agg(INT)->INT",
            description="custom aggregation",
        )
        db.upsert_udf(
            "my_extract",
            "python",
            signature="my_extract(STRING)->STRING",
            description="text extractor",
        )
        count = generate_package_docs(db)
        assert count == 2
        entries = db.list_memories(kind="package_doc")
        assert len(entries) == 2

    def test_generates_for_tables_and_udfs(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "game_id",
                    "type": "STRING",
                    "comment": "identifier",
                    "is_partition": 0,
                },
            ],
        )
        db.upsert_udf(
            "my_func",
            "java",
            signature="my_func(INT)->INT",
            description="custom func",
        )
        count = generate_package_docs(db)
        assert count == 2

    def test_table_summary_includes_column_descriptions(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "game_id",
                    "type": "STRING",
                    "comment": "unique game identifier",
                    "is_partition": 0,
                },
                {
                    "name": "game_type",
                    "type": "STRING",
                    "comment": "game category",
                    "is_partition": 0,
                },
            ],
        )
        generate_package_docs(db)
        entries = db.list_memories(kind="package_doc")
        table_entry = [e for e in entries if "card_games" in e["retrieval_text"]][0]
        payload = json.loads(table_entry["payload_json"])
        assert payload["table_or_udf_name"] == "card_games"
        assert "game_id" in payload["summary"]

    def test_errors_json_not_injected_into_retrieval_text(self, tmp_path: Path) -> None:
        """B3 regression: tables.errors_json holds phase-failure JSON
        (e.g. {"phase":"describe","code":"PermissionDenied"}), not a
        table description. Earlier package_doc used it as a comment
        fallback, polluting retrieval text on permission-restricted
        tables. The summary must not echo the error payload back."""
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(
            _SK,
            "restricted",
            "h1",
            errors_json='{"phase":"describe","code":"PermissionDenied"}',
        )
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        generate_package_docs(db)
        entries = db.list_memories(kind="package_doc")
        text = entries[0]["retrieval_text"]
        assert "PermissionDenied" not in text
        assert "phase" not in text or "phase" in "id BIGINT"
        assert "errors_json" not in text

    def test_udf_summary_includes_signature(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_udf(
            "my_agg",
            "java",
            signature="my_agg(INT)->INT",
            description="custom aggregation",
        )
        generate_package_docs(db)
        entries = db.list_memories(kind="package_doc")
        udf_entry = [e for e in entries if "my_agg" in e["retrieval_text"]][0]
        payload = json.loads(udf_entry["payload_json"])
        assert payload["table_or_udf_name"] == "my_agg"
        assert "java" in payload["summary"]
        assert "my_agg(INT)->INT" in payload["summary"]

    def test_rebuild_clears_old_entries(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "old_table", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "INT", "comment": "", "is_partition": 0},
            ],
        )
        # First build
        generate_package_docs(db)
        entries_before = db.list_memories(kind="package_doc")
        assert len(entries_before) == 1
        # Second build (rebuild)
        generate_package_docs(db)
        entries_after = db.list_memories(kind="package_doc")
        assert len(entries_after) == 1  # old entries replaced, not duplicated

    def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        count = generate_package_docs(db)
        assert count == 0

    def test_fts5_index_created_for_package_docs(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "game_id", "type": "STRING", "comment": "identifier", "is_partition": 0},
            ],
        )
        generate_package_docs(db)
        # Verify memory_fts rows were created
        rows = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchall()
        assert len(rows) > 0
        # Verify the memory_fts rowid corresponds to a package_doc entry
        entry_ids = {e["id"] for e in db.list_memories(kind="package_doc")}
        for row in rows:
            assert row[0] in entry_ids

    def test_table_with_no_columns_produces_package_doc(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(_SK, "empty_table", "h1")
        count = generate_package_docs(db)
        assert count == 1
        entries = db.list_memories(kind="package_doc")
        assert len(entries) == 1
        payload = json.loads(entries[0]["payload_json"])
        assert payload["table_or_udf_name"] == "empty_table"

    def test_udf_with_minimal_info_produces_package_doc(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_udf("bare_udf", "java")
        count = generate_package_docs(db)
        assert count == 1
        entries = db.list_memories(kind="package_doc")
        assert len(entries) == 1
        payload = json.loads(entries[0]["payload_json"])
        assert payload["table_or_udf_name"] == "bare_udf"
        assert "no signature" in payload["summary"]
        assert "no description" in payload["summary"]

    def test_partition_column_marked_in_summary(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "game_id", "type": "STRING", "comment": "identifier", "is_partition": 0},
                {"name": "ds", "type": "STRING", "comment": "", "is_partition": 1},
            ],
        )
        generate_package_docs(db)
        entries = db.list_memories(kind="package_doc")
        payload = json.loads(entries[0]["payload_json"])
        assert "partition" in payload["summary"]

    def test_rebuild_clears_fts5_index(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "game_id", "type": "STRING", "comment": "identifier", "is_partition": 0},
            ],
        )
        generate_package_docs(db)
        fts_count_before = db._conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchone()[0]
        assert fts_count_before > 0
        # Rebuild
        generate_package_docs(db)
        fts_count_after = db._conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchone()[0]
        assert fts_count_after > 0
        # Same count (old FTS rows were auto-deleted by trigger, new ones created)
        assert fts_count_after == fts_count_before
