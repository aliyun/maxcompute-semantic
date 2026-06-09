# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for v4 → v5 PackageDB migration: drop bm25_index, add memory_fts.

vec_index, when present, is left untouched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from maxcompute_semantic.build.storage import _SCHEMA_VERSION, PackageDB

_V4_BOOTSTRAP_SQL = """
CREATE TABLE tables (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL,
  name TEXT NOT NULL,
  schema_hash TEXT NOT NULL,
  last_built_at TEXT NOT NULL,
  errors_json TEXT,
  ai_context TEXT DEFAULT NULL,
  UNIQUE(source_key, name)
);
CREATE TABLE columns (
  table_id INTEGER REFERENCES tables(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  comment TEXT,
  is_partition INTEGER DEFAULT 0,
  sample_values_json TEXT,
  is_enum INTEGER DEFAULT 0,
  null_ratio REAL,
  distinct_count INTEGER,
  semantic_role TEXT DEFAULT NULL,
  dim_type TEXT DEFAULT NULL,
  agg TEXT DEFAULT NULL,
  id_type TEXT DEFAULT NULL,
  references_target TEXT DEFAULT NULL,
  semantic_description TEXT DEFAULT NULL,
  PRIMARY KEY (table_id, name)
);
CREATE TABLE joins (
  id INTEGER PRIMARY KEY,
  left_source_key TEXT NOT NULL,
  left_table TEXT NOT NULL,
  left_col TEXT NOT NULL,
  right_source_key TEXT NOT NULL,
  right_table TEXT NOT NULL,
  right_col TEXT NOT NULL,
  kind TEXT NOT NULL,
  confidence REAL NOT NULL,
  cardinality TEXT
);
CREATE TABLE udfs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  signature TEXT,
  class_name TEXT,
  description TEXT,
  created_locally INTEGER DEFAULT 0,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE memory_entries (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  retrieval_text TEXT NOT NULL,
  tags_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE bm25_index (
  memory_id INTEGER NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
  term TEXT NOT NULL,
  tf INTEGER NOT NULL,
  doc_len INTEGER NOT NULL,
  PRIMARY KEY (memory_id, term)
);
CREATE INDEX idx_bm25_term ON bm25_index(term);
PRAGMA user_version = 4;
"""


def _build_v4_db(path: Path, memory_rows: list[tuple[str, str, str]]) -> None:
    """Create a fully-populated v4 PackageDB on disk."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_V4_BOOTSTRAP_SQL)
    for kind, payload, retrieval in memory_rows:
        conn.execute(
            "INSERT INTO memory_entries (kind, payload_json, retrieval_text, created_at) "
            "VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (kind, payload, retrieval),
        )
        last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO bm25_index (memory_id, term, tf, doc_len) VALUES (?, 'stub', 1, 1)",
            (last_id,),
        )
    conn.commit()
    conn.close()


class TestV4ToV5Migration:
    def test_migration_bumps_user_version(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        _build_v4_db(db_path, [("user_note", '{"x":1}', "card games")])
        PackageDB(db_path).close()
        conn = sqlite3.connect(str(db_path))
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert ver == _SCHEMA_VERSION

    def test_migration_drops_bm25_index_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        _build_v4_db(db_path, [("user_note", '{"x":1}', "card games")])
        db = PackageDB(db_path)
        tables = {
            r[0]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "bm25_index" not in tables

    def test_migration_creates_memory_fts(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        _build_v4_db(db_path, [("user_note", '{"x":1}', "card games")])
        db = PackageDB(db_path)
        row = db._conn.execute("SELECT name FROM sqlite_master WHERE name='memory_fts'").fetchone()
        assert row is not None

    def test_migration_backfills_fts_text_and_indexes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        _build_v4_db(
            db_path,
            [
                ("user_note", '{"x":1}', "card games have foil cards"),
                ("verified_query", '{"x":2}', "dice rolls are random"),
            ],
        )
        db = PackageDB(db_path)
        rows = db._conn.execute("SELECT fts_text FROM memory_entries").fetchall()
        assert all(r[0] is not None and r[0] != "" for r in rows)
        hits = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchall()
        assert len(hits) >= 1

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        _build_v4_db(db_path, [("user_note", '{"x":1}', "card games")])
        PackageDB(db_path).close()
        PackageDB(db_path).close()
        conn = sqlite3.connect(str(db_path))
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert ver == _SCHEMA_VERSION

    def test_migration_partial_state_recovers(self, tmp_path: Path) -> None:
        """If a previous migration crashed after ALTER but before user_version
        bump, re-running must not fail with 'column already exists'."""
        db_path = tmp_path / "package.db"
        _build_v4_db(db_path, [("user_note", '{"x":1}', "card games")])
        conn = sqlite3.connect(str(db_path))
        conn.execute("ALTER TABLE memory_entries ADD COLUMN fts_text TEXT")
        conn.commit()
        conn.close()
        PackageDB(db_path).close()
        conn = sqlite3.connect(str(db_path))
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert ver == _SCHEMA_VERSION
