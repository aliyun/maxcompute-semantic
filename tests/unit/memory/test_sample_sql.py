# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory/sample_sql.py — persist mined SQL into memory entries."""

from __future__ import annotations

import json
from pathlib import Path

from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.memory.sample_sql import persist_sample_sqls

_SK = "test_project__default"


class TestPersistSampleSqls:
    def test_persists_one_entry_per_query(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": [
                "SELECT name, type FROM cards WHERE rarity = 'mythic'",
                "SELECT COUNT(*) FROM cards",
            ],
            "sets": ["SELECT * FROM sets WHERE block = 'Ice Age'"],
        }
        result = persist_sample_sqls(db, verified_queries, _SK)
        assert result.created == 3
        entries = db.list_memories(kind="sample_sql")
        assert len(entries) == 3

    def test_payload_has_table_source_sql(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": ["SELECT name FROM cards LIMIT 10"],
        }
        persist_sample_sqls(db, verified_queries, _SK)
        entries = db.list_memories(kind="sample_sql")
        payload = json.loads(entries[0]["payload_json"])
        assert payload["table"] == "cards"
        assert payload["source_key"] == _SK
        assert payload["sql"] == "SELECT name FROM cards LIMIT 10"

    def test_retrieval_text_has_source_qualified_prefix(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": ["SELECT name FROM cards"],
        }
        persist_sample_sqls(db, verified_queries, _SK)
        entries = db.list_memories(kind="sample_sql")
        text = entries[0]["retrieval_text"]
        assert text.startswith(f"sample_sql for {_SK}:cards:")

    def test_rebuild_clears_old_entries(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "old_table": ["SELECT * FROM old_table"],
        }
        # First build
        persist_sample_sqls(db, verified_queries, _SK)
        assert len(db.list_memories(kind="sample_sql")) == 1
        # Second build with different tables
        new_queries = {
            "new_table": ["SELECT * FROM new_table"],
        }
        persist_sample_sqls(db, new_queries, _SK)
        entries = db.list_memories(kind="sample_sql")
        assert len(entries) == 1
        payload = json.loads(entries[0]["payload_json"])
        assert payload["table"] == "new_table"

    def test_empty_queries_returns_zero(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        result = persist_sample_sqls(db, {}, _SK)
        assert result.created == 0
        assert len(db.list_memories(kind="sample_sql")) == 0

    def test_fts5_index_created_for_entries(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": ["SELECT name FROM cards WHERE rarity = 'mythic'"],
        }
        persist_sample_sqls(db, verified_queries, _SK)
        rows = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"rarity"',)
        ).fetchall()
        assert len(rows) > 0
        entry_ids = {e["id"] for e in db.list_memories(kind="sample_sql")}
        for row in rows:
            assert row[0] in entry_ids

    def test_does_not_touch_package_doc_entries(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(_SK, "cards", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        from maxcompute_semantic.memory.package_doc import generate_package_docs

        generate_package_docs(db)
        assert len(db.list_memories(kind="package_doc")) == 1
        # Now persist sample_sqls — should not clear package_doc
        verified_queries = {"cards": ["SELECT * FROM cards"]}
        persist_sample_sqls(db, verified_queries, _SK)
        assert len(db.list_memories(kind="package_doc")) == 1

    def test_fts5_can_match_sql_keywords(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": ["SELECT name FROM cards WHERE rarity = 'mythic'"],
        }
        persist_sample_sqls(db, verified_queries, _SK)
        from maxcompute_semantic.memory.search import FTS5Searcher

        searcher = FTS5Searcher(db)
        results = searcher.search("rarity mythic", kind_filter=["sample_sql"])
        assert len(results) > 0
        payload = json.loads(results[0]["payload_json"])
        assert payload["table"] == "cards"

    def test_rebuild_clears_fts5_index_for_sample_sql(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        verified_queries = {
            "cards": ["SELECT name FROM cards"],
        }
        persist_sample_sqls(db, verified_queries, _SK)
        fts_before = db._conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?", ('"cards"',)
        ).fetchone()[0]
        assert fts_before > 0
        # Rebuild
        persist_sample_sqls(db, verified_queries, _SK)
        fts_after = db._conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?", ('"cards"',)
        ).fetchone()[0]
        assert fts_after > 0
        # Same count (old FTS rows auto-deleted by trigger, new ones created)
        assert fts_after == fts_before

    def test_persist_sample_sqls_preserves_other_sources(self, tmp_path: Path) -> None:
        """Multi-source builds keep each source's sample_sql entries."""
        db = PackageDB(tmp_path / "package.db")
        first = persist_sample_sqls(
            db,
            {"orders": ["SELECT * FROM orders"]},
            "acme__warehouse",
        )
        second = persist_sample_sqls(
            db,
            {"raw_events": ["SELECT * FROM raw_events"]},
            "acme__staging",
        )
        assert first.created == 1
        assert first.touched_tables == {"orders"}
        assert second.created == 1
        assert second.touched_tables == {"raw_events"}
        rows = db.list_memories(kind="sample_sql", limit=50)
        payloads = [json.loads(row["payload_json"]) for row in rows]
        assert {p["source_key"] for p in payloads} == {"acme__warehouse", "acme__staging"}
        assert {p["table"] for p in payloads} == {"orders", "raw_events"}

    def test_persist_sample_sqls_returns_old_and_new_touched_tables(self, tmp_path: Path) -> None:
        """Rebuild tracks both old (removed) and new table names."""
        db = PackageDB(tmp_path / "package.db")
        persist_sample_sqls(
            db,
            {"old_table": ["SELECT * FROM old_table"]},
            "acme__warehouse",
        )
        result = persist_sample_sqls(
            db,
            {"new_table": ["SELECT * FROM new_table"]},
            "acme__warehouse",
        )
        assert result.created == 1
        assert result.touched_tables == {"old_table", "new_table"}
        rows = db.list_memories(kind="sample_sql", limit=50)
        payloads = [json.loads(row["payload_json"]) for row in rows]
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["table"] == "new_table"
        assert payload["source_key"] == "acme__warehouse"
        assert payload["sql"] == "SELECT * FROM new_table"

    def test_literal_variants_are_grouped_with_frequency(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")

        result = persist_sample_sqls(
            db,
            {
                "cards": [
                    "SELECT name FROM cards WHERE id = 10",
                    "SELECT name FROM cards WHERE id = 20",
                    "SELECT name FROM cards WHERE id = 30",
                ]
            },
            _SK,
        )

        assert result.created == 1
        rows = db.list_memories(kind="sample_sql")
        payload = json.loads(rows[0]["payload_json"])
        assert payload["table"] == "cards"
        assert payload["source_key"] == _SK
        assert payload["sql"] == "SELECT name FROM cards WHERE id = 10"
        assert payload["representative_sql"] == "SELECT name FROM cards WHERE id = 10"
        assert payload["representative_sqls"] == [
            "SELECT name FROM cards WHERE id = 10",
            "SELECT name FROM cards WHERE id = 20",
            "SELECT name FROM cards WHERE id = 30",
        ]
        assert payload["frequency"] == 3
        assert payload["provenance"] == "mined_history"
        assert payload["verified_count"] == 0
        assert payload["confidence"] == "mined_high"
        assert payload["where_predicates"] == ["id = ?"]
        assert payload["canonical_sql"] == "SELECT name FROM cards WHERE id = ?"
        assert payload["normalizer_version"] == 1
        assert len(payload["shape_key"]) == 16

    def test_two_literal_variants_are_mined_medium(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")

        persist_sample_sqls(
            db,
            {
                "cards": [
                    "SELECT name FROM cards WHERE id = 10",
                    "SELECT name FROM cards WHERE id = 20",
                ]
            },
            _SK,
        )

        payload = json.loads(db.list_memories(kind="sample_sql")[0]["payload_json"])
        assert payload["frequency"] == 2
        assert payload["confidence"] == "mined_medium"

    def test_conflicting_join_keys_remain_separate_patterns(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")

        persist_sample_sqls(
            db,
            {
                "legalities": [
                    ("SELECT c.id FROM cards c JOIN legalities l ON c.uuid = l.uuid "
                    "WHERE l.format = 'commander'"),
                    ("SELECT c.id FROM cards c JOIN legalities l ON c.id = l.id "
                    "WHERE l.format = 'commander'"),
                ]
            },
            _SK,
        )

        rows = db.list_memories(kind="sample_sql", limit=10)
        payloads = [json.loads(row["payload_json"]) for row in rows]
        assert len(payloads) == 2
        assert {tuple(payload["join_edges"]) for payload in payloads} == {
            ("cards.uuid = legalities.uuid",),
            ("cards.id = legalities.id",),
        }
        assert {payload["confidence"] for payload in payloads} == {"mined_low"}
