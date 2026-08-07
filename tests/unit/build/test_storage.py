# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/storage.py — PackageDB (SQLite CRUD).

Schema version 3 (0.4.0a4) is per-source-keyed: every ``tables``
and ``joins`` row carries the ``DataSource.source_key()`` of its
origin (``f"{project}__{schema}"``). The tests use ``"acme__s1"``
as the canonical fixture source_key throughout — the old
single-source tests (which never passed a source_key) get the
fixture key injected so behavior is identical, and the new
multi-source tests use ``"acme__s2"`` as a second key to verify
the composite-uniqueness path.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from maxcompute_semantic.build.errors import RebuildRequiredError
from maxcompute_semantic.build.storage import _SCHEMA_VERSION, PackageDB

# Canonical fixture source_keys. The double-underscore separator
# matches ``DataSource.source_key()``'s output shape.
SK_A = "acme__s1"
SK_B = "acme__s2"


class TestPackageDBInit:
    def test_init_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        PackageDB(db_path)
        assert db_path.exists()

    def test_init_creates_tables(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        # Verify all 4 tables exist
        cursor = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        assert "tables" in tables
        assert "columns" in tables
        assert "joins" in tables
        assert "udfs" in tables

    def test_init_existing_db_preserves_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.upsert_table(SK_A, "my_table", "hash1")
        db2 = PackageDB(db_path)
        result = db2.get_table(SK_A, "my_table")
        assert result is not None
        assert result["name"] == "my_table"

    def test_init_stamps_user_version(self, tmp_path: Path) -> None:
        """Fresh DBs land at the current schema's ``PRAGMA user_version``."""
        db = PackageDB(tmp_path / "package.db")
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _SCHEMA_VERSION

    def test_init_creates_package_settings_table(self, tmp_path: Path) -> None:
        """v7 adds the ``package_settings`` key/value table."""
        db = PackageDB(tmp_path / "package.db")
        cursor = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='package_settings'"
        )
        assert cursor.fetchone() is not None

    def test_del_does_not_import_during_interpreter_shutdown(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "contextlib":
                raise AssertionError("__del__ must not import during shutdown")
            return real_import(name, *args, **kwargs)

        meta_path = sys.meta_path
        try:
            builtins.__import__ = guarded_import
            sys.meta_path = None
            db.__del__()
        finally:
            sys.meta_path = meta_path
            builtins.__import__ = real_import

    def test_init_rejects_old_schema_with_rebuild_required(self, tmp_path: Path) -> None:
        """A pre-0.4.0a4 package (no source_key column, user_version
        != 3) is rejected at open time. The user has to re-run
        ``mcs build`` because in-place migration is not supported on
        the alpha line.
        """
        import sqlite3

        db_path = tmp_path / "package.db"
        # Materialize a v2-shape DB by hand: ``tables(name UNIQUE)``
        # without a ``source_key`` column, ``user_version`` left at
        # the default (0) — the same shape PackageDB used to emit.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE tables (id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, schema_hash TEXT, "
            "last_built_at TEXT, errors_json TEXT)"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

        with pytest.raises(RebuildRequiredError, match="user_version=2"):
            PackageDB(db_path)

    def test_init_raises_rebuild_required_when_fts5_unavailable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """If sqlite3 was built without FTS5, init must fail with
        RebuildRequiredError carrying a remediation message, not a raw
        OperationalError."""
        import sqlite3

        from maxcompute_semantic.build import storage as storage_mod

        real_connect = sqlite3.connect

        class _FakeConn:
            """Proxy that forwards everything to a real sqlite3
            connection but rejects ``executescript`` calls that mention
            FTS5 — same shape OperationalError that an FTS5-less
            sqlite3 build would raise."""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def executescript(self, script: str):
                if "fts5" in script.lower():
                    raise sqlite3.OperationalError("no such module: fts5")
                return self._real.executescript(script)

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            def __setattr__(self, name: str, value) -> None:
                if name == "_real":
                    object.__setattr__(self, name, value)
                else:
                    setattr(self._real, name, value)

        def fake_connect(*args, **kwargs):
            return _FakeConn(real_connect(*args, **kwargs))

        monkeypatch.setattr(storage_mod.sqlite3, "connect", fake_connect)

        from maxcompute_semantic.build.errors import RebuildRequiredError

        with pytest.raises(RebuildRequiredError) as excinfo:
            PackageDB(tmp_path / "package.db")
        assert "fts5" in str(excinfo.value).lower()
        assert excinfo.value.remediation  # non-empty remediation text


class TestPackageDBUpsertTable:
    def test_upsert_table_inserts_new(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_table(SK_A, "card_games", "abc123")
        assert id_ > 0
        row = db.get_table(SK_A, "card_games")
        assert row["name"] == "card_games"
        assert row["schema_hash"] == "abc123"
        assert row["source_key"] == SK_A

    def test_upsert_table_updates_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "card_games", "hash1")
        db.upsert_table(SK_A, "card_games", "hash2")
        row = db.get_table(SK_A, "card_games")
        assert row["schema_hash"] == "hash2"

    def test_upsert_table_with_errors_json(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(
            SK_A,
            "bad_table",
            "h1",
            errors_json='{"phase":"describe","code":"PermissionDenied"}',
        )
        row = db.get_table(SK_A, "bad_table")
        assert row["errors_json"] is not None

    def test_get_table_returns_none_for_missing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        assert db.get_table(SK_A, "nonexistent") is None

    def test_list_tables_empty(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        assert db.list_tables() == []

    def test_list_tables_returns_all(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.upsert_table(SK_A, "t2", "h2")
        names = [r["name"] for r in db.list_tables()]
        assert set(names) == {"t1", "t2"}

    def test_get_schema_hash(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "hash123")
        assert db.get_schema_hash(SK_A, "t1") == "hash123"
        assert db.get_schema_hash(SK_A, "nonexistent") is None


class TestPackageDBSourceKeyIsolation:
    """The ``UNIQUE(source_key, name)`` composite is the multi-source
    correctness invariant — same name under two different sources
    must coexist as two distinct rows.
    """

    def test_same_name_different_source_coexists(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id_a = db.upsert_table(SK_A, "users", "ha")
        id_b = db.upsert_table(SK_B, "users", "hb")
        assert id_a != id_b
        row_a = db.get_table(SK_A, "users")
        row_b = db.get_table(SK_B, "users")
        assert row_a["schema_hash"] == "ha"
        assert row_b["schema_hash"] == "hb"

    def test_list_tables_filters_by_source(self, tmp_path: Path) -> None:
        """``list_tables(source_key=...)`` is what
        ``BuildPipeline._run_refresh`` will call to compute per-source
        ``existing_names`` for the diff-vs-removed classification.
        """
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "orders", "h1")
        db.upsert_table(SK_A, "users", "h2")
        db.upsert_table(SK_B, "events", "h3")
        a_names = {r["name"] for r in db.list_tables(source_key=SK_A)}
        b_names = {r["name"] for r in db.list_tables(source_key=SK_B)}
        assert a_names == {"orders", "users"}
        assert b_names == {"events"}

    def test_list_tables_no_filter_returns_all_sources(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.upsert_table(SK_B, "t1", "h2")
        all_rows = db.list_tables()
        assert len(all_rows) == 2

    def test_find_table_by_name_returns_all_sources(self, tmp_path: Path) -> None:
        """``find_table_by_name`` is the cross-source helper memory will
        use to auto-resolve bare names — single match means unambiguous,
        multiple matches means the user has to disambiguate via the
        3-segment FQN form.
        """
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "users", "h1")
        db.upsert_table(SK_B, "users", "h2")
        db.upsert_table(SK_A, "orders", "h3")
        rows = db.find_table_by_name("users")
        assert len(rows) == 2
        assert {r["source_key"] for r in rows} == {SK_A, SK_B}
        # bare-name lookup of a single-source name is unambiguous
        rows = db.find_table_by_name("orders")
        assert len(rows) == 1

    def test_delete_table_only_deletes_matching_source(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "users", "ha")
        db.upsert_table(SK_B, "users", "hb")
        db.delete_table(SK_A, "users")
        assert db.get_table(SK_A, "users") is None
        assert db.get_table(SK_B, "users") is not None


class TestPackageDBUpsertColumns:
    def test_upsert_columns_inserts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "card_games", "h1")
        cols = [
            {"name": "game_id", "type": "STRING", "comment": "id", "is_partition": 0},
            {
                "name": "game_type",
                "type": "STRING",
                "comment": "type",
                "is_partition": 0,
                "is_enum": 1,
                "sample_values_json": '["card","board"]',
            },
        ]
        db.upsert_columns(tid, cols)
        rows = db.get_columns(tid)
        assert len(rows) == 2
        assert rows[0]["name"] == "game_id"

    def test_upsert_columns_replaces_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "card_games", "h1")
        db.upsert_columns(tid, [{"name": "c1", "type": "INT", "comment": "", "is_partition": 0}])
        db.upsert_columns(tid, [{"name": "c2", "type": "STR", "comment": "", "is_partition": 0}])
        rows = db.get_columns(tid)
        assert len(rows) == 1  # old columns replaced
        assert rows[0]["name"] == "c2"

    def test_upsert_columns_preserves_annotations_and_profile_stats(self, tmp_path: Path) -> None:
        """Re-running ``mcs build`` (which re-emits the schema-derived
        rows via ``upsert_columns``) must not wipe the annotation
        fields written by the proposal workflow or the profiling stats
        written by ``mcs build``'s column_profiling phase. Both layers
        target the same row but live on different fields; the schema
        round-trip used to clobber them via DELETE+INSERT."""
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "users", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "user_id",
                    "type": "STRING",
                    "comment": "id",
                    "is_partition": 0,
                }
            ],
        )

        # Simulate the proposal workflow writing an annotation and the
        # column_profiling phase writing stats.
        db._conn.execute(
            "UPDATE columns SET semantic_role=?, dim_type=?, "
            "id_type=?, semantic_description=?, "
            "row_count=?, approx_ndv=?, uniqueness_ratio=?, cast_rate=? "
            "WHERE table_id=? AND name=?",
            (
                "identifier",
                "categorical",
                "primary",
                "the user identifier",
                1000,
                950,
                0.95,
                1.0,
                tid,
                "user_id",
            ),
        )
        db._conn.commit()

        # Re-emit the schema row (e.g. another ``mcs build`` run with
        # an updated comment).
        db.upsert_columns(
            tid,
            [
                {
                    "name": "user_id",
                    "type": "STRING",
                    "comment": "the unique user id",
                    "is_partition": 0,
                }
            ],
        )

        rows = db.get_columns(tid)
        assert len(rows) == 1
        row = rows[0]
        # Schema-derived fields update.
        assert row["comment"] == "the unique user id"
        # Annotation fields preserved.
        assert row["semantic_role"] == "identifier"
        assert row["dim_type"] == "categorical"
        assert row["id_type"] == "primary"
        assert row["semantic_description"] == "the user identifier"
        # Profile stats preserved.
        assert row["row_count"] == 1000
        assert row["approx_ndv"] == 950
        assert row["uniqueness_ratio"] == 0.95
        assert row["cast_rate"] == 1.0

    def test_upsert_columns_preserves_sample_stats_when_re_described(self, tmp_path: Path) -> None:
        """A schema-only re-emit (e.g. the schema-hash probe that
        ``_run_refresh`` runs against every live table during
        classification) must not wipe ``sample_values_json`` /
        ``is_enum`` / ``null_ratio`` / ``distinct_count`` written by
        the column-sampling phase. Pre-0.10.9 the unconditional ON
        CONFLICT UPDATE bound ``col.get("null_ratio")`` etc. to NULL
        and clobbered the sampled values whenever
        ``phase_describe_table`` re-ran during refresh — invisible
        until an inference-logic version bump triggered a
        ``render_all`` and the missing fields surfaced in the
        per-table markdown.
        """
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "users", "h1")

        # Initial describe + sampling: schema fields + sample fields.
        db.upsert_columns(
            tid,
            [
                {
                    "name": "status",
                    "type": "STRING",
                    "comment": "lifecycle state",
                    "is_partition": 0,
                    "sample_values_json": '["active", "paused", "deleted"]',
                    "is_enum": 1,
                    "null_ratio": 0.0,
                    "distinct_count": 3,
                }
            ],
        )

        # Refresh-path schema re-emit: only the schema fields are
        # supplied (mirrors ``phase_describe_table``'s payload shape).
        db.upsert_columns(
            tid,
            [
                {
                    "name": "status",
                    "type": "STRING",
                    "comment": "updated comment",
                    "is_partition": 0,
                }
            ],
        )

        rows = db.get_columns(tid)
        assert len(rows) == 1
        row = rows[0]
        # Schema fields update.
        assert row["comment"] == "updated comment"
        # Sample fields preserved.
        assert row["sample_values_json"] == '["active", "paused", "deleted"]'
        assert row["is_enum"] == 1
        assert row["null_ratio"] == 0.0
        assert row["distinct_count"] == 3

    def test_upsert_columns_sampling_can_clear_sample_values(self, tmp_path: Path) -> None:
        """Sampling deliberately sets ``sample_values_json=None`` when
        a re-sample no longer supports the cached values (cardinality
        grew past the enum gate, or the sample is now 100% NULL). The
        explicit-key check in ``upsert_columns`` must let a
        present-but-None sample value reach the row, so downstream
        renderers don't keep advertising stale enum values.
        """
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "users", "h1")

        db.upsert_columns(
            tid,
            [
                {
                    "name": "status",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "sample_values_json": '["active", "paused"]',
                    "is_enum": 1,
                    "null_ratio": 0.0,
                    "distinct_count": 2,
                }
            ],
        )

        # Re-sample: cardinality blew past the enum gate; sampling
        # signals "drop the cached enum" via explicit None.
        db.upsert_columns(
            tid,
            [
                {
                    "name": "status",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "sample_values_json": None,
                    "is_enum": 0,
                    "null_ratio": 0.01,
                    "distinct_count": 5000,
                }
            ],
        )

        row = db.get_columns(tid)[0]
        assert row["sample_values_json"] is None
        assert row["is_enum"] == 0
        assert row["null_ratio"] == 0.01
        assert row["distinct_count"] == 5000

    def test_upsert_columns_drops_columns_no_longer_present(self, tmp_path: Path) -> None:
        """A re-run with one column removed must drop the stale row."""
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "users", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "a", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "b", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )
        db.upsert_columns(
            tid,
            [{"name": "a", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        rows = db.get_columns(tid)
        assert {r["name"] for r in rows} == {"a"}


class TestPackageDBUpsertJoins:
    def test_upsert_join(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join(SK_A, "games", "player_id", SK_A, "players", "id", "xxx_id", 0.85, "1:n")
        joins = db.list_joins()
        assert len(joins) == 1
        assert joins[0]["left_table"] == "games"
        assert joins[0]["left_source_key"] == SK_A
        assert joins[0]["right_source_key"] == SK_A

    def test_upsert_join_deduplicates(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join(SK_A, "games", "player_id", SK_A, "players", "id", "xxx_id", 0.85, "1:n")
        db.upsert_join(SK_A, "games", "player_id", SK_A, "players", "id", "xxx_id", 0.9, "1:n")
        assert len(db.list_joins()) == 1  # same key, updated confidence

    def test_upsert_cross_source_join(self, tmp_path: Path) -> None:
        """Cross-source joins are addressable: a join from
        ``acme__s1.users`` to ``acme__s2.events`` is a distinct row
        from any same-table-name within-source join.
        """
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join(SK_A, "users", "id", SK_B, "events", "user_id", "xxx_id", 0.7, "1:n")
        joins = db.list_joins()
        assert len(joins) == 1
        assert joins[0]["left_source_key"] == SK_A
        assert joins[0]["right_source_key"] == SK_B

    def test_within_and_cross_source_joins_coexist(self, tmp_path: Path) -> None:
        """Same column-name pair under two different (source, source)
        endpoint pairs is two rows, not a deduplicated one.
        """
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join(SK_A, "u", "id", SK_A, "o", "user_id", "xxx_id", 0.9, "1:n")
        db.upsert_join(SK_A, "u", "id", SK_B, "o", "user_id", "xxx_id", 0.7, "1:n")
        assert len(db.list_joins()) == 2


class TestPackageDBUpsertUdf:
    def test_upsert_udf_inserts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_udf("my_udf", "java", signature="my_udf(INT)->INT", description="custom agg")
        udfs = db.list_udfs()
        assert len(udfs) == 1
        assert udfs[0]["name"] == "my_udf"

    def test_upsert_udf_updates_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_udf("my_udf", "java", signature="old")
        db.upsert_udf("my_udf", "java", signature="new")
        udfs = db.list_udfs()
        assert udfs[0]["signature"] == "new"


class TestPackageDBDelete:
    def test_delete_table_cascades_columns(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "t1", "h1")
        db.upsert_columns(tid, [{"name": "c1", "type": "INT", "comment": "", "is_partition": 0}])
        db.delete_table(SK_A, "t1")
        assert db.get_table(SK_A, "t1") is None
        assert db.get_columns(tid) == []

    def test_delete_table_nonexistent_is_idempotent(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.delete_table(SK_A, "nonexistent")  # no error


class TestPackageDBMarkBuildComplete:
    def test_mark_build_complete_updates_timestamp(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.mark_build_complete(SK_A, ["t1"])
        row = db.get_table(SK_A, "t1")
        assert row["last_built_at"] is not None

    def test_mark_build_complete_only_targets_matching_source(self, tmp_path: Path) -> None:
        """Same-named tables under different sources have independent
        ``last_built_at`` timestamps.
        """
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "users", "h1")
        db.upsert_table(SK_B, "users", "h2")
        # Stash the initial row's timestamp before refresh.
        initial_b = db.get_table(SK_B, "users")["last_built_at"]
        # Mark only SK_A's "users" complete with a fresh timestamp.
        # The SK_B row's timestamp must be untouched.
        db.mark_build_complete(SK_A, ["users"])
        post_b = db.get_table(SK_B, "users")["last_built_at"]
        assert post_b == initial_b

    def test_upsert_table_starts_incomplete(self, tmp_path: Path) -> None:
        """A freshly described table is build_complete=0 (not yet sampled)."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        assert db.get_table(SK_A, "t1")["build_complete"] == 0

    def test_mark_build_complete_sets_flag(self, tmp_path: Path) -> None:
        """mark_build_complete flips build_complete to 1."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.mark_build_complete(SK_A, ["t1"])
        assert db.get_table(SK_A, "t1")["build_complete"] == 1

    def test_mark_build_incomplete_resets_flag(self, tmp_path: Path) -> None:
        """mark_build_incomplete resets build_complete to 0."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.mark_build_complete(SK_A, ["t1"])
        db.mark_build_incomplete(SK_A, ["t1"])
        assert db.get_table(SK_A, "t1")["build_complete"] == 0

    def test_upsert_update_preserves_build_complete(self, tmp_path: Path) -> None:
        """Re-describing a completed table (refresh classification) must
        not silently reset its build_complete flag."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.mark_build_complete(SK_A, ["t1"])
        # Re-describe with a new hash — update path, not insert.
        db.upsert_table(SK_A, "t1", "h2")
        assert db.get_table(SK_A, "t1")["build_complete"] == 1

    def test_mark_build_complete_does_not_stamp_sampled_at(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.mark_build_complete(SK_A, ["t1"])
        row = db.get_table(SK_A, "t1")
        assert row["build_complete"] == 1
        assert row["last_sampled_at"] is None

    def test_record_sampled_sets_freshness_fields(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.record_sampled(SK_A, "t1", "2026-05-29T00:00:00+00:00")
        row = db.get_table(SK_A, "t1")
        assert row["build_complete"] == 1
        assert row["data_modified_at"] == "2026-05-29T00:00:00+00:00"
        assert row["last_sampled_at"] is not None

    def test_upsert_update_preserves_data_modified_at(self, tmp_path: Path) -> None:
        """Re-describe (upsert update) must not clobber the data-change
        baseline written by record_sampled."""
        db = PackageDB(tmp_path / "package.db")
        db.upsert_table(SK_A, "t1", "h1")
        db.record_sampled(SK_A, "t1", "2026-05-29T00:00:00+00:00")
        db.upsert_table(SK_A, "t1", "h2")  # re-describe
        assert db.get_table(SK_A, "t1")["data_modified_at"] == "2026-05-29T00:00:00+00:00"


class TestPackageDBMemoryTables:
    def test_init_creates_memory_entries_table(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        cursor = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        assert "memory_entries" in tables

    def test_init_creates_memory_fts_virtual_table(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        rows = db._conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name='memory_fts'"
        ).fetchall()
        assert len(rows) == 1
        assert "fts5" in rows[0]["sql"].lower()

    def test_init_does_not_create_bm25_index_table(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tables = [
            r[0]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "bm25_index" not in tables

    def test_init_creates_memory_fts_triggers(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        triggers = {
            r[0]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert {"memory_ai", "memory_ad", "memory_au"}.issubset(triggers)

    def test_init_memory_entries_has_fts_text_column(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        cols = {
            r[0]
            for r in db._conn.execute(
                "SELECT name FROM pragma_table_info('memory_entries')"
            ).fetchall()
        }
        assert "fts_text" in cols


class TestPackageDBUpsertMemory:
    def test_upsert_memory_inserts_verified_query(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        payload = (
            '{"question":"How many card games?","sql":"SELECT count(*) FROM t","table_refs":["t"]}'
        )
        retrieval_text = (
            "Q: How many card games?\nSQL: SELECT count(*) FROM t\nTables: t\nEvidence: "
        )
        id_ = db.upsert_memory("verified_query", payload, retrieval_text)
        assert id_ > 0
        entry = db.get_memory(id_)
        assert entry is not None
        assert entry["kind"] == "verified_query"
        assert entry["payload_json"] == payload

    def test_upsert_memory_inserts_user_note_with_tags(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        payload = '{"text":"Always use ds partition filter"}'
        retrieval_text = "Always use ds partition filter"
        tags = '["preference","project-x"]'
        id_ = db.upsert_memory("user_note", payload, retrieval_text, tags_json=tags)
        entry = db.get_memory(id_)
        assert entry["tags_json"] == tags

    def test_upsert_memory_inserts_without_tags(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        payload = '{"text":"some note"}'
        retrieval_text = "some note"
        id_ = db.upsert_memory("user_note", payload, retrieval_text)
        entry = db.get_memory(id_)
        assert entry["tags_json"] is None

    def test_upsert_memory_indexes_via_fts5(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("verified_query", '{"q":1}', "card games have foil cards")
        row = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            ('"card"',),
        ).fetchone()
        assert row is not None
        assert row[0] == id_

    def test_upsert_memory_overwrites_via_trigger(self, tmp_path: Path) -> None:
        """An UPDATE to memory_entries replaces the FTS row via memory_au."""
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("user_note", '{"q":1}', "foo")
        db._conn.execute(
            "UPDATE memory_entries SET retrieval_text=?, fts_text=? WHERE id=?",
            ("bar", "bar", id_),
        )
        db._conn.commit()
        new_match = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"bar"',)
        ).fetchone()
        assert new_match is not None
        old_match = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"foo"',)
        ).fetchone()
        assert old_match is None

    def test_upsert_memory_replaces_existing_entry(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        payload1 = '{"question":"old question"}'
        payload2 = '{"question":"new question"}'
        id1 = db.upsert_memory("verified_query", payload1, "old question")
        id2 = db.upsert_memory("verified_query", payload2, "new question")
        assert id2 > id1
        # Old entry still exists (upsert_memory always inserts new)
        old = db.get_memory(id1)
        assert old["payload_json"] == payload1
        new = db.get_memory(id2)
        assert new["payload_json"] == payload2


class TestPackageDBGetMemory:
    def test_get_memory_returns_none_for_missing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        assert db.get_memory(99999) is None


class TestPackageDBListMemories:
    def test_list_memories_returns_all(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("verified_query", '{"q":1}', "text1")
        db.upsert_memory("user_note", '{"q":2}', "text2")
        entries = db.list_memories()
        assert len(entries) == 2

    def test_list_memories_filters_by_kind(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("verified_query", '{"q":1}', "text1")
        db.upsert_memory("user_note", '{"q":2}', "text2")
        entries = db.list_memories(kind="verified_query")
        assert len(entries) == 1
        assert entries[0]["kind"] == "verified_query"

    def test_list_memories_limit(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        for i in range(5):
            db.upsert_memory("user_note", f'{{"q":{i}}}', f"text{i}")
        entries = db.list_memories(limit=3)
        assert len(entries) == 3


class TestPackageDBRemoveMemory:
    def test_remove_memory_deletes_entry(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("user_note", '{"text":"x"}', "x")
        result = db.remove_memory(id_)
        assert result is True
        assert db.get_memory(id_) is None

    def test_remove_memory_clears_fts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id_ = db.upsert_memory("user_note", '{"q":1}', "card games")
        assert db.remove_memory(id_) is True
        rows = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            ('"card"',),
        ).fetchall()
        assert rows == []

    def test_remove_memory_returns_false_for_missing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        assert db.remove_memory(99999) is False


class TestPackageDBClearMemories:
    def test_clear_memories_deletes_by_kind(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("verified_query", '{"q":1}', "text1")
        db.upsert_memory("user_note", '{"q":2}', "text2")
        count = db.clear_memories(kind="verified_query")
        assert count == 1
        entries = db.list_memories()
        assert len(entries) == 1
        assert entries[0]["kind"] == "user_note"

    def test_clear_memories_deletes_all_when_no_kind(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("verified_query", '{"q":1}', "text1")
        db.upsert_memory("user_note", '{"q":2}', "text2")
        count = db.clear_memories()
        assert count == 2
        assert db.list_memories() == []

    def test_clear_memories_clears_fts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("user_note", '{"q":1}', "foo")
        db.upsert_memory("user_note", '{"q":2}', "bar")
        db.clear_memories()
        rows = db._conn.execute("SELECT rowid FROM memory_fts").fetchall()
        assert rows == []

    def test_clear_memories_before_date(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        id1 = db.upsert_memory("user_note", '{"text":"old"}', "old")
        # Manually set created_at to a past date for testing
        db._conn.execute(
            "UPDATE memory_entries SET created_at='2025-01-01T00:00:00+00:00' WHERE id=?", (id1,)
        )
        db._conn.commit()
        id2 = db.upsert_memory("user_note", '{"text":"new"}', "new")
        count = db.clear_memories(before="2026-01-01")
        assert count == 1
        assert db.get_memory(id2) is not None


class TestPackageDBReindexBM25:
    def test_reindex_memory_fts_rebuilds_all(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_memory("user_note", '{"q":1}', "card games")
        db.upsert_memory("user_note", '{"q":2}', "dice rolls")
        # Wipe the FTS index to simulate corruption. On external-content
        # FTS5 tables `SELECT COUNT(*)` proxies to the content table and
        # still returns 2 post-wipe, so we probe via MATCH to confirm the
        # shadow index is actually empty.
        db._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('delete-all')")
        db._conn.commit()
        pre = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchall()
        assert pre == []
        count = db.reindex_memory_fts()
        assert count == 2
        post_card = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"card"',)
        ).fetchone()
        post_dice = db._conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ('"dice"',)
        ).fetchone()
        assert post_card is not None
        assert post_dice is not None

    def test_reindex_memory_fts_empty_db(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        count = db.reindex_memory_fts()
        assert count == 0


def test_upsert_memory_does_not_index_vector_by_default(tmp_path: Path, monkeypatch) -> None:
    """Vector indexing is skipped when MCS_AUTO_VECTOR is not set."""
    db = PackageDB(tmp_path / "package.db")
    monkeypatch.delenv("MCS_AUTO_VECTOR", raising=False)

    with patch.object(db, "_index_vector") as mock_index:
        db.upsert_memory("user_note", '{"q":1}', "card games")

    mock_index.assert_not_called()


def test_upsert_memory_indexes_vector_when_env_enabled(tmp_path: Path, monkeypatch) -> None:
    """Vector indexing runs when MCS_AUTO_VECTOR=1."""
    db = PackageDB(tmp_path / "package.db")
    monkeypatch.setenv("MCS_AUTO_VECTOR", "1")

    with patch.object(db, "_index_vector") as mock_index:
        db.upsert_memory("user_note", '{"q":1}', "card games")

    mock_index.assert_called_once()


class TestColumnProfileFields:
    """Tests for column profiling fields in storage."""

    def test_update_column_profile_writes_row_count_and_ndv(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(tid, [{"name": "id", "type": "BIGINT", "is_partition": 0}])

        db.update_column_profile(SK_A, "orders", "id", row_count=1000, approx_ndv=998)

        col = db.get_columns(tid)[0]
        assert col["row_count"] == 1000
        assert col["approx_ndv"] == 998

    def test_update_column_profile_writes_uniqueness_and_scope(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(tid, [{"name": "id", "type": "BIGINT", "is_partition": 0}])

        db.update_column_profile(
            SK_A,
            "orders",
            "id",
            uniqueness_ratio=0.998,
            profile_scope="latest_partition",
            profile_method="approx_ndv",
            profile_confidence=0.9,
        )

        col = db.get_columns(tid)[0]
        assert col["uniqueness_ratio"] == 0.998
        assert col["profile_scope"] == "latest_partition"
        assert col["profile_method"] == "approx_ndv"
        assert col["profile_confidence"] == 0.9

    def test_profile_fields_default_null_on_fresh_db(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(tid, [{"name": "id", "type": "BIGINT", "is_partition": 0}])

        col = db.get_columns(tid)[0]
        assert col["row_count"] is None
        assert col["approx_ndv"] is None
        assert col["uniqueness_ratio"] is None
        assert col["profile_scope"] is None

    def test_update_columns_profile_batch(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "BIGINT", "is_partition": 0},
                {"name": "status", "type": "STRING", "is_partition": 0},
            ],
        )

        db.update_columns_profile_batch(
            SK_A,
            "orders",
            [
                {"name": "id", "row_count": 100, "approx_ndv": 100, "uniqueness_ratio": 1.0},
                {
                    "name": "status",
                    "row_count": 100,
                    "approx_ndv": 3,
                    "is_enum": 1,
                    "null_ratio": 0.05,
                },
            ],
        )

        cols = {c["name"]: c for c in db.get_columns(tid)}
        assert cols["id"]["uniqueness_ratio"] == 1.0
        assert cols["status"]["is_enum"] == 1
        assert cols["status"]["null_ratio"] == 0.05


class TestJoinCandidates:
    """Tests for join_candidates storage."""

    def test_upsert_join_candidate_inserts_new(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="customer_id",
            right_source_key=SK_A,
            right_table="customers",
            right_col="id",
            confidence=0.71,
            evidence=[{"source": "name_heuristic", "weight": 0.4}],
        )

        rows = db.list_join_candidates(left_source_key=SK_A, left_table="orders")
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.71
        assert rows[0]["evidence"][0]["source"] == "name_heuristic"

    def test_upsert_join_candidate_updates_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="customer_id",
            right_source_key=SK_A,
            right_table="customers",
            right_col="id",
            confidence=0.71,
            evidence=[{"source": "name_heuristic"}],
        )
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="customer_id",
            right_source_key=SK_A,
            right_table="customers",
            right_col="id",
            confidence=0.86,
            evidence=[{"source": "history_sql", "frequency": 4}],
            coverage_ratio=0.97,
            right_uniqueness_ratio=1.0,
        )

        rows = db.list_join_candidates(left_source_key=SK_A, left_table="orders")
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.86
        assert rows[0]["coverage_ratio"] == 0.97

    def test_clear_join_candidates(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="customer_id",
            right_source_key=SK_A,
            right_table="customers",
            right_col="id",
            confidence=0.71,
            evidence=[{"source": "name_heuristic"}],
        )
        assert db.clear_join_candidates() == 1
        assert db.list_join_candidates() == []

    def test_list_join_candidates_by_right_table(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="customer_id",
            right_source_key=SK_A,
            right_table="customers",
            right_col="id",
            confidence=0.9,
            evidence=[{"source": "link_to"}],
        )
        db.upsert_join_candidate(
            left_source_key=SK_A,
            left_table="orders",
            left_col="product_id",
            right_source_key=SK_A,
            right_table="products",
            right_col="id",
            confidence=0.7,
            evidence=[{"source": "xxx_id"}],
        )

        rows = db.list_join_candidates(right_source_key=SK_A, right_table="customers")
        assert len(rows) == 1


class TestAnnotationSuggestions:
    """Tests for annotation_suggestions storage."""

    def test_upsert_annotation_suggestion_inserts(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(tid, [{"name": "amount", "type": "DOUBLE", "is_partition": 0}])

        db.upsert_annotation_suggestion(
            source_key=SK_A,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            suggested_subtype="SUM",
            confidence=0.78,
            evidence=[{"source": "history_sql", "aggregate": "SUM"}],
        )

        suggestions = db.list_annotation_suggestions(source_key=SK_A, table_name="orders")
        assert len(suggestions) == 1
        assert suggestions[0]["suggested_role"] == "metric"
        assert suggestions[0]["suggested_subtype"] == "SUM"
        assert suggestions[0]["evidence"][0]["aggregate"] == "SUM"

    def test_suggestion_is_separate_from_confirmed_annotation(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        tid = db.upsert_table(SK_A, "orders", "hash")
        db.upsert_columns(tid, [{"name": "amount", "type": "DOUBLE", "is_partition": 0}])

        db.upsert_annotation_suggestion(
            source_key=SK_A,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            confidence=0.78,
            evidence=[{"source": "history_sql"}],
        )

        semantics = db.get_column_semantics(SK_A, "orders", "amount")
        assert semantics is not None
        assert semantics["semantic_role"] is None

    def test_upsert_annotation_suggestion_updates_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_annotation_suggestion(
            source_key=SK_A,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            confidence=0.6,
            evidence=[{"source": "name_heuristic"}],
        )
        db.upsert_annotation_suggestion(
            source_key=SK_A,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            suggested_subtype="SUM",
            confidence=0.85,
            evidence=[{"source": "history_sql", "aggregate": "SUM"}],
        )

        suggestions = db.list_annotation_suggestions(source_key=SK_A, table_name="orders")
        assert len(suggestions) == 1
        assert suggestions[0]["confidence"] == 0.85
        assert suggestions[0]["suggested_subtype"] == "SUM"

    def test_clear_annotation_suggestions_by_source(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.upsert_annotation_suggestion(
            source_key=SK_A,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            confidence=0.78,
            evidence=[{"source": "x"}],
        )
        db.upsert_annotation_suggestion(
            source_key=SK_B,
            table_name="orders",
            column_name="amount",
            suggested_role="metric",
            confidence=0.5,
            evidence=[{"source": "y"}],
        )
        assert db.clear_annotation_suggestions(source_key=SK_A) == 1
        assert len(db.list_annotation_suggestions(source_key=SK_A)) == 0
        assert len(db.list_annotation_suggestions(source_key=SK_B)) == 1


def test_migration_v5_to_v6_adds_profile_columns_and_new_tables(tmp_path: Path) -> None:
    """A v5 database should migrate to v6 with new columns + tables."""
    db_path = tmp_path / "package.db"
    # Create a v5 DB
    conn = __import__("sqlite3").connect(str(db_path))
    conn.execute("PRAGMA user_version = 5")
    # Write minimal v5 schema (just columns table)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS columns ("
        "table_id INTEGER, name TEXT NOT NULL, type TEXT NOT NULL, "
        "comment TEXT, is_partition INTEGER DEFAULT 0, "
        "sample_values_json TEXT, is_enum INTEGER DEFAULT 0, "
        "null_ratio REAL, distinct_count INTEGER, "
        "semantic_role TEXT DEFAULT NULL, dim_type TEXT DEFAULT NULL, "
        "agg TEXT DEFAULT NULL, id_type TEXT DEFAULT NULL, "
        "references_target TEXT DEFAULT NULL, semantic_description TEXT DEFAULT NULL, "
        "PRIMARY KEY (table_id, name))"
    )
    conn.commit()
    conn.close()

    # Open with PackageDB — should auto-migrate to v6
    db = PackageDB(db_path)
    col_rows = db._conn.execute("SELECT name FROM pragma_table_info('columns')").fetchall()
    col_info = [r[0] for r in col_rows]
    assert "row_count" in col_info
    assert "approx_ndv" in col_info
    assert "uniqueness_ratio" in col_info
    assert "profile_confidence" in col_info

    table_rows = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {r[0] for r in table_rows}
    assert "join_candidates" in tables
    assert "annotation_suggestions" in tables
    db.close()


def test_migration_v6_to_v7_adds_package_settings_table(tmp_path: Path) -> None:
    """A v6 database should migrate to v7 with the ``package_settings`` table."""
    import sqlite3

    db_path = tmp_path / "package.db"
    # Build a minimal v6 DB by running the fresh-init path against the
    # current code, then rewinding ``user_version`` back to 6 and
    # dropping ``package_settings`` to mimic a real v6 layout.
    db = PackageDB(db_path)
    db.close()
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS package_settings")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()

    # Open with PackageDB — should auto-migrate to the current schema
    # version (the chain walks v6→v7→v8→v9...).
    db = PackageDB(db_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == _SCHEMA_VERSION
    table_rows = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='package_settings'"
    ).fetchall()
    assert len(table_rows) == 1
    db.close()


def test_migration_v7_to_v8_adds_cast_rate_column(tmp_path: Path) -> None:
    """A v7 database (no ``cast_rate`` on ``columns``) migrates to v8 by
    adding the column with NULL default — existing rows stay NULL until
    the next ``mcs build`` populates them."""
    import sqlite3

    db_path = tmp_path / "package.db"
    db = PackageDB(db_path)
    db.close()
    conn = sqlite3.connect(str(db_path))
    # Simulate a v7 layout: drop cast_rate (idempotent-upgrade may have
    # added it during the first open) and rewind user_version.
    cols = [r[1] for r in conn.execute("PRAGMA table_info('columns')").fetchall()]
    if "cast_rate" in cols:
        # SQLite < 3.35 can't DROP COLUMN; rebuild without it.
        keep = [c for c in cols if c != "cast_rate"]
        col_list = ", ".join(f'"{c}"' for c in keep)
        conn.execute(f"CREATE TABLE columns_new AS SELECT {col_list} FROM columns")
        conn.execute("DROP TABLE columns")
        conn.execute("ALTER TABLE columns_new RENAME TO columns")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()

    db = PackageDB(db_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == _SCHEMA_VERSION
    col_names = [r[1] for r in db._conn.execute("PRAGMA table_info('columns')").fetchall()]
    assert "cast_rate" in col_names
    db.close()


def test_migration_v7_to_v8_idempotent_when_column_already_exists(
    tmp_path: Path,
) -> None:
    """If a half-migrated v7 DB already has ``cast_rate`` (e.g. the
    idempotent-upgrade path added it before the user_version bump
    landed), running the v7→v8 migrator must not raise 'duplicate
    column name'."""
    import sqlite3

    db_path = tmp_path / "package.db"
    db = PackageDB(db_path)
    db.close()
    conn = sqlite3.connect(str(db_path))
    # cast_rate already present from the v8 fresh-init; just rewind.
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()

    # Should migrate cleanly without raising.
    db = PackageDB(db_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == _SCHEMA_VERSION
    db.close()


def test_migration_v10_to_v11_adds_build_complete_and_backfills(tmp_path: Path) -> None:
    """A v10 database (no ``build_complete`` on ``tables``) migrates to v11
    by adding the column and backfilling every PRE-EXISTING row to 1 —
    packages built before v11 were fully built under the old logic, so a
    refresh must not re-sample them all on first upgrade."""
    import sqlite3

    db_path = tmp_path / "package.db"
    db = PackageDB(db_path)
    # Seed a row that simulates a fully-built pre-v11 table.
    db.upsert_table("acme__warehouse", "orders", "h_orders")
    db.close()

    conn = sqlite3.connect(str(db_path))
    # Simulate a v10 layout: drop build_complete (fresh-init added it)
    # and rewind user_version. SQLite < 3.35 can't DROP COLUMN; rebuild.
    cols = [r[1] for r in conn.execute("PRAGMA table_info('tables')").fetchall()]
    if "build_complete" in cols:
        keep = [c for c in cols if c != "build_complete"]
        col_list = ", ".join(f'"{c}"' for c in keep)
        conn.execute(f"CREATE TABLE tables_new AS SELECT {col_list} FROM tables")
        conn.execute("DROP TABLE tables")
        conn.execute("ALTER TABLE tables_new RENAME TO tables")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    db = PackageDB(db_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == _SCHEMA_VERSION
    col_names = [r[1] for r in db._conn.execute("PRAGMA table_info('tables')").fetchall()]
    assert "build_complete" in col_names
    # The pre-existing row must be backfilled to complete (1).
    assert db.get_table("acme__warehouse", "orders")["build_complete"] == 1
    db.close()


def test_migration_v11_to_v12_adds_freshness_columns(tmp_path: Path) -> None:
    """A v11 database (no data-freshness columns) migrates to v12 by adding
    ``data_modified_at`` and ``last_sampled_at`` (NULL for existing rows —
    no baseline until the next build/refresh)."""
    import sqlite3

    db_path = tmp_path / "package.db"
    db = PackageDB(db_path)
    db.upsert_table("acme__warehouse", "orders", "h_orders")
    db.close()

    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info('tables')").fetchall()]
    drop = {"data_modified_at", "last_sampled_at"} & set(cols)
    if drop:
        keep = [c for c in cols if c not in drop]
        col_list = ", ".join(f'"{c}"' for c in keep)
        conn.execute(f"CREATE TABLE tables_new AS SELECT {col_list} FROM tables")
        conn.execute("DROP TABLE tables")
        conn.execute("ALTER TABLE tables_new RENAME TO tables")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()

    db = PackageDB(db_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == _SCHEMA_VERSION
    col_names = [r[1] for r in db._conn.execute("PRAGMA table_info('tables')").fetchall()]
    assert "data_modified_at" in col_names
    assert "last_sampled_at" in col_names
    row = db.get_table("acme__warehouse", "orders")
    assert row["data_modified_at"] is None
    assert row["last_sampled_at"] is None
    db.close()


class TestPackageDBSettings:
    """``get_setting`` / ``set_setting`` CRUD."""

    def test_get_setting_returns_none_when_missing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        assert db.get_setting("missing") is None

    def test_set_and_get_setting(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.set_setting("foo", "bar")
        assert db.get_setting("foo") == "bar"

    def test_set_setting_overwrites_existing(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.set_setting("foo", "a")
        db.set_setting("foo", "b")
        assert db.get_setting("foo") == "b"

    def test_set_setting_none_deletes_row(self, tmp_path: Path) -> None:
        db = PackageDB(tmp_path / "package.db")
        db.set_setting("foo", "bar")
        db.set_setting("foo", None)
        assert db.get_setting("foo") is None

    def test_setting_persists_across_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.set_setting("foo", "bar")
        db.close()
        db2 = PackageDB(db_path)
        assert db2.get_setting("foo") == "bar"
        db2.close()
