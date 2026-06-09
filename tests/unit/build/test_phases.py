"""Tests for build/phases.py — phases 2 through 7."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.phases import (
    PhaseResult,
    phase_column_profiling,
    phase_column_sampling,
    phase_describe_table,
    phase_discover_udfs,
    phase_infer_joins_heuristic,
    phase_list_tables,
    phase_mine_history,
)
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.mc_client.envelope import Envelope
from maxcompute_semantic.mc_client.errors import (
    EndpointUnreachableError,
    McsError,
    PermissionDeniedError,
    TableNotFoundError,
)

_SK = "test_project__default"
_SOURCE = DataSource(project="test_project", schema="default", tables="*")


def _make_profile(schema: str = "default") -> Profile:
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps.endpoint",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_project", schema=schema, tables="*"),),
    )


def _make_db(tmp_path: Path) -> PackageDB:
    return PackageDB(tmp_path / "test.db")


# ── PhaseResult ──────────────────────────────────────────────────────────


class TestPhaseResult:
    def test_default_fields(self) -> None:
        r = PhaseResult(status="success")
        assert r.data == {}
        assert r.warnings == []
        assert r.errors == []

    def test_explicit_fields(self) -> None:
        r = PhaseResult(
            status="partial_failure",
            data={"x": 1},
            warnings=["w1"],
            errors=[{"code": "E"}],
        )
        assert r.status == "partial_failure"
        assert r.data["x"] == 1
        assert r.warnings == ["w1"]
        assert r.errors[0]["code"] == "E"


# ── phase_list_tables ───────────────────────────────────────────────────


class TestPhaseListTables:
    def test_list_tables_success(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_tables.return_value = ["t1", "t2"]
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_list_tables(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["table_names"] == ["t1", "t2"]
        # Both tables should exist in db with schema_hash="pending".
        t1 = db.get_table(_SK, "t1")
        t2 = db.get_table(_SK, "t2")
        assert t1 is not None
        assert t1["schema_hash"] == "pending"
        assert t2 is not None
        assert t2["schema_hash"] == "pending"

    def test_list_tables_empty(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_tables.return_value = []
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_list_tables(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["table_names"] == []
        assert db.list_tables() == []

    def test_list_tables_hard_error(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_tables.side_effect = EndpointUnreachableError("connection refused")
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_list_tables(client, db, profile, _SOURCE)

        assert result.status == "hard_error"
        assert db.list_tables() == []

    def test_list_tables_passes_schema(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_tables.return_value = ["t1"]
        db = _make_db(tmp_path)
        profile = _make_profile(schema="my_schema")

        phase_list_tables(client, db, profile, profile.sources[0])

        client.list_tables.assert_called_once_with(schema="my_schema", project="test_project")

    def test_list_tables_respects_source_allowlist(self, tmp_path: Path) -> None:
        """When ``source.tables`` is an enumerated list (not the wildcard),
        ``phase_list_tables`` must intersect the live catalog with the
        allowlist. Without this, the profile-side selection is silently
        ignored and every table in the schema gets described."""
        from maxcompute_semantic.auth.schema import TableSpec

        client = MagicMock()
        client.list_tables.return_value = ["t1", "t2", "t3", "t4"]
        db = _make_db(tmp_path)
        profile = _make_profile()
        source = DataSource(
            project="test_project",
            schema="default",
            tables=(TableSpec(name="t1"), TableSpec(name="t3")),
        )

        result = phase_list_tables(client, db, profile, source)

        assert result.status == "success"
        assert result.data["table_names"] == ["t1", "t3"]
        # Placeholder rows should only be created for allowlisted tables.
        names = {row["name"] for row in db.list_tables(source_key=_SK)}
        assert names == {"t1", "t3"}

    def test_list_tables_warns_on_missing_allowlisted(self, tmp_path: Path) -> None:
        """Names listed in the profile but absent from the live catalog
        surface as a phase warning so the user catches typos / dropped
        tables, but the build continues with whatever does exist."""
        from maxcompute_semantic.auth.schema import TableSpec

        client = MagicMock()
        client.list_tables.return_value = ["t1"]
        db = _make_db(tmp_path)
        profile = _make_profile()
        source = DataSource(
            project="test_project",
            schema="default",
            tables=(TableSpec(name="t1"), TableSpec(name="ghost")),
        )

        result = phase_list_tables(client, db, profile, source)

        assert result.status == "success"
        assert result.data["table_names"] == ["t1"]
        assert any("ghost" in w for w in result.warnings)

    def test_list_tables_preserves_existing_hash(self, tmp_path: Path) -> None:
        """Re-running list against a pre-populated table must not
        clobber its cached ``schema_hash`` — otherwise ``_run_refresh``
        sees every table as "changed" and re-builds the world."""
        client = MagicMock()
        client.list_tables.return_value = ["t1", "t2"]
        db = _make_db(tmp_path)
        profile = _make_profile()
        # Seed t1 with a real (non-pending) hash, leave t2 absent.
        db.upsert_table(_SK, "t1", schema_hash="abc123", errors_json=None)

        phase_list_tables(client, db, profile, _SOURCE)

        t1 = db.get_table(_SK, "t1")
        t2 = db.get_table(_SK, "t2")
        assert t1 is not None
        assert t1["schema_hash"] == "abc123", (
            "phase_list_tables overwrote a real hash with 'pending'"
        )
        assert t2 is not None
        assert t2["schema_hash"] == "pending"


# ── phase_discover_udfs ─────────────────────────────────────────────────


class TestPhaseDiscoverUdfs:
    def test_discover_udfs_success(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_functions.return_value = [
            {
                "name": "udf1",
                "kind": "java",
                "signature": "udf1(INT)->INT",
                "class_name": "com.example.Udf1",
                "description": "my udf",
            },
            {
                "name": "udf2",
                "kind": "python",
                "signature": "udf2(STR)->STR",
                "class_name": None,
                "description": None,
            },
        ]
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_discover_udfs(client, db, profile)

        assert result.status == "success"
        assert result.data["udf_count"] == 2
        udfs = db.list_udfs()
        assert len(udfs) == 2
        assert udfs[0]["name"] == "udf1"
        assert udfs[1]["name"] == "udf2"

    def test_discover_udfs_not_implemented(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_functions.side_effect = NotImplementedError("see mcs-udf-write sub-spec")
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_discover_udfs(client, db, profile)

        assert result.status == "partial_failure"
        assert result.warnings == [
            "UDF discovery unavailable (client backend has no list_functions)"
        ]
        assert db.list_udfs() == []

    def test_discover_udfs_permission_denied(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_functions.side_effect = PermissionDeniedError("no meta access")
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_discover_udfs(client, db, profile)

        assert result.status == "partial_failure"
        # Message preserves the classified error so the user can act on it,
        # rather than collapsing every failure into a single generic string.
        assert len(result.warnings) == 1
        assert "no meta access" in result.warnings[0]
        assert result.errors[0]["code"] == "PermissionDenied"

    def test_discover_udfs_empty(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_functions.return_value = []
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_discover_udfs(client, db, profile)

        assert result.status == "success"
        assert result.data["udf_count"] == 0
        assert db.list_udfs() == []


# ── phase_describe_table ─────────────────────────────────────────────────


class TestPhaseDescribeTable:
    def test_describe_table_success(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "name": "card_games",
                "schema": [
                    {"name": "id", "type": "STRING", "comment": "game id"},
                    {"name": "name", "type": "STRING", "comment": "game name"},
                ],
                "partition_columns": [],
            },
        }
        db = _make_db(tmp_path)
        # Pre-create the table row (as list_tables would do).
        db.upsert_table(_SK, "card_games", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "card_games")

        assert result.status == "success"
        assert result.data["table_name"] == "card_games"
        assert result.data["column_count"] == 2
        assert result.data["partition_column_count"] == 0
        # schema_hash should be computed, not "pending" anymore.
        row = db.get_table(_SK, "card_games")
        assert row["schema_hash"] != "pending"
        assert row["schema_hash"] == result.data["schema_hash"]
        # Columns should be stored.
        cols = db.get_columns(row["id"])
        assert len(cols) == 2
        assert cols[0]["name"] == "id"
        assert cols[0]["is_partition"] == 0

    def test_describe_table_permission_denied(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.side_effect = PermissionDeniedError(
            "access denied to table secret_data",
            remediation="request SELECT access",
        )
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "secret_data", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "secret_data")

        assert result.status == "partial_failure"
        assert len(result.errors) == 1
        assert result.errors[0]["table"] == "secret_data"
        # errors_json should be stored on the table row.
        row = db.get_table(_SK, "secret_data")
        error_info = json.loads(row["errors_json"])
        assert error_info["phase"] == "describe"
        assert error_info["code"] == "PermissionDenied"

    def test_describe_table_not_found(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.side_effect = TableNotFoundError(
            "table 'test_project.default.vanished' was dropped between "
            "list_tables and describe_table",
            remediation=("refresh the package — the table is no longer present in the project"),
        )
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "vanished", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "vanished")

        assert result.status == "partial_failure"
        assert len(result.errors) == 1
        assert result.errors[0]["table"] == "vanished"
        assert result.errors[0]["code"] == "TableNotFound"
        row = db.get_table(_SK, "vanished")
        error_info = json.loads(row["errors_json"])
        assert error_info["phase"] == "describe"
        assert error_info["code"] == "TableNotFound"

    def test_describe_table_hard_error(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.side_effect = EndpointUnreachableError("endpoint down")
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "some_table", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "some_table")

        assert result.status == "hard_error"
        assert result.errors[0]["table"] == "some_table"
        assert result.errors[0]["code"] == "EndpointUnreachable"

    def test_describe_table_with_partition_cols(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "name": "sales",
                "schema": [
                    {"name": "amount", "type": "BIGINT", "comment": "sale amount"},
                    {"name": "product", "type": "STRING", "comment": ""},
                ],
                "partition_columns": [
                    {"name": "ds", "type": "STRING", "comment": "date partition"},
                    {"name": "region", "type": "STRING", "comment": "region partition"},
                ],
            },
        }
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "sales", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "sales")

        assert result.status == "success"
        assert result.data["column_count"] == 4  # 2 regular + 2 partition
        assert result.data["partition_column_count"] == 2
        row = db.get_table(_SK, "sales")
        cols = db.get_columns(row["id"])
        # Partition columns should have is_partition=1.
        part_cols = [c for c in cols if c["is_partition"] == 1]
        assert len(part_cols) == 2
        assert part_cols[0]["name"] == "ds"
        assert part_cols[1]["name"] == "region"
        # Regular columns should have is_partition=0.
        reg_cols = [c for c in cols if c["is_partition"] == 0]
        assert len(reg_cols) == 2

    def test_describe_table_passes_schema(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "name": "t1",
                "schema": [{"name": "id", "type": "STRING", "comment": ""}],
                "partition_columns": [],
            },
        }
        db = _make_db(tmp_path)
        profile = _make_profile(schema="my_schema")
        src = profile.sources[0]
        db.upsert_table(src.source_key(), "t1", schema_hash="pending")

        phase_describe_table(client, db, profile, src, "t1")

        client.describe_table.assert_called_once_with(
            name="t1", schema="my_schema", project="test_project"
        )

    def test_describe_table_no_columns(self, tmp_path: Path) -> None:
        """Edge case: describe returns empty schema (no columns, no partitions)."""
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "name": "empty_table",
                "schema": [],
                "partition_columns": [],
            },
        }
        db = _make_db(tmp_path)
        db.upsert_table(_SK, "empty_table", schema_hash="pending")
        profile = _make_profile()

        result = phase_describe_table(client, db, profile, _SOURCE, "empty_table")

        assert result.status == "success"
        assert result.data["column_count"] == 0
        row = db.get_table(_SK, "empty_table")
        cols = db.get_columns(row["id"])
        assert cols == []


# ── Phase 5: column_sampling ──────────────────────────────────────────────


class TestPhaseColumnSampling:
    def test_sampling_success(self, tmp_path: Path) -> None:
        """Mock execute_sql returns rows -> stats computed, columns updated."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        # Pre-populate table + columns in db.
        table_id = db.upsert_table(_SK, "games", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "game id", "is_partition": 0},
                {"name": "name", "type": "STRING", "comment": "game name", "is_partition": 0},
            ],
        )

        # Mock execute_sql returns rows.
        rows = [
            {"id": "1", "name": "Chess"},
            {"id": "2", "name": "Go"},
            {"id": "3", "name": None},  # NULL name
        ]
        client.execute_sql.return_value = Envelope.success(
            {
                "rows": rows,
                "row_count": 3,
            }
        )

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "games")

        assert result.status == "success"
        assert result.data["sampled_rows"] == 3
        assert result.data["table_name"] == "games"

        # Verify columns have stats.
        cols = db.get_columns(table_id)
        id_col = next(c for c in cols if c["name"] == "id")
        name_col = next(c for c in cols if c["name"] == "name")

        assert id_col["null_ratio"] == 0.0  # no NULLs
        assert id_col["distinct_count"] == 3  # 1, 2, 3
        assert name_col["null_ratio"] == 1 / 3  # one NULL out of 3

    def test_sampling_empty_result(self, tmp_path: Path) -> None:
        """Mock returns 0 rows -> PhaseResult with status="success", no stats."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "games", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )

        client.execute_sql.return_value = Envelope.success(
            {
                "rows": [],
                "row_count": 0,
            }
        )

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "games")

        assert result.status == "success"
        assert result.data["sampled_rows"] == 0

    def test_sampling_failure_soft(self, tmp_path: Path) -> None:
        """Mock raises McsError -> status="partial_failure", warning about sampling."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "games", schema_hash="abc123")

        client.execute_sql.side_effect = McsError("sampling query failed")

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "games")

        assert result.status == "partial_failure"
        assert len(result.warnings) == 1
        assert "Sampling failed" in result.warnings[0]
        assert len(result.errors) == 1
        assert result.errors[0]["table"] == "games"

    def test_sampling_enum_detection(self, tmp_path: Path) -> None:
        """Mock rows with <=30 distinct values -> is_enum=True, sample_values_json populated."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "games", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )

        # status has only 2 distinct values (active, inactive) -> is_enum=True
        rows = [
            {"id": "1", "status": "active"},
            {"id": "2", "status": "inactive"},
            {"id": "3", "status": "active"},
        ]
        client.execute_sql.return_value = Envelope.success(
            {
                "rows": rows,
                "row_count": 3,
            }
        )

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "games")

        assert result.status == "success"

        cols = db.get_columns(table_id)
        status_col = next(c for c in cols if c["name"] == "status")
        assert status_col["is_enum"] == 1
        assert status_col["sample_values_json"] is not None
        # sample_values_json should be a JSON array of sorted distinct values.
        sample_vals = json.loads(status_col["sample_values_json"])
        assert sorted(sample_vals) == ["active", "inactive"]

        id_col = next(c for c in cols if c["name"] == "id")
        assert id_col["is_enum"] == 1  # 3 distinct values <= 30

    def test_sampling_all_null_column_not_enum(self, tmp_path: Path) -> None:
        """All-NULL columns must not be marked is_enum (distinct_count=0 is no evidence).

        Regression guard: previously ``is_enum = distinct_count <= 30`` returned
        True for 100%-NULL columns, producing rows with ``is_enum=1`` and an
        empty ``sample_values_json``. That violated the "semantic layer must
        not generate erroneous information" invariant and bloated per-table
        markdown for wide tables (e.g. wide sports/event schemas where
        dozens of position columns are entirely NULL).
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "match", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "match_id", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "player_x1", "type": "DOUBLE", "comment": "", "is_partition": 0},
            ],
        )

        rows = [
            {"match_id": "m1", "player_x1": None},
            {"match_id": "m2", "player_x1": None},
            {"match_id": "m3", "player_x1": None},
        ]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": 3})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "match")

        assert result.status == "success"

        cols = db.get_columns(table_id)
        player_col = next(c for c in cols if c["name"] == "player_x1")
        assert player_col["null_ratio"] == 1.0
        assert player_col["distinct_count"] == 0
        assert player_col["is_enum"] == 0
        assert player_col["sample_values_json"] is None

    def test_sampling_clears_stale_enum_samples_on_flip(self, tmp_path: Path) -> None:
        """When a column flips from is_enum=True to False on re-sample, its
        previously-stored sample_values_json must be cleared. Otherwise the
        per-table markdown / JSON envelope keeps advertising stale enum
        values that the current sample no longer supports.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "events", schema_hash="abc123")
        # Pre-seed the column with a prior-run is_enum=True state.
        db.upsert_columns(
            table_id,
            [
                {
                    "name": "tag",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "is_enum": 1,
                    "distinct_count": 2,
                    "null_ratio": 0.0,
                    "sample_values_json": json.dumps(["foo", "bar"]),
                },
            ],
        )

        # New sample: 100% NULL — column should flip to is_enum=False AND
        # the stale ["foo", "bar"] sample_values_json must be cleared.
        rows = [
            {"tag": None},
            {"tag": None},
        ]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": 2})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "events")

        assert result.status == "success"
        cols = db.get_columns(table_id)
        tag_col = next(c for c in cols if c["name"] == "tag")
        assert tag_col["is_enum"] == 0
        assert tag_col["sample_values_json"] is None

    def test_sampling_non_enum_string_keeps_format_examples(self, tmp_path: Path) -> None:
        """A high-NDV STRING column whose values fit the length gate keeps
        a small set of format-example samples (≤5) so ``_date_format_hint``
        and other shape-driven heuristics fire on dates / codes / urls
        that exceed the enum cardinality ceiling. Without this the agent
        only sees the column name + type and has to probe values
        manually — and silently mis-types STRING-stored dates as native
        temporal (``YEAR(col)`` returns NULL).
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "events", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {
                    "name": "signup_date",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                },
            ],
        )

        # 60 ISO-date-shaped rows with > 30 distinct values — past the
        # enum cardinality ceiling, but max_len (10) is well under 80
        # so format examples should land.
        rows = [
            {"signup_date": f"19{90 + (i % 10):02d}-{((i * 7) % 12) + 1:02d}-{(i % 28) + 1:02d}"}
            for i in range(60)
        ]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": len(rows)})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "events")

        assert result.status == "success"
        cols = db.get_columns(table_id)
        col = next(c for c in cols if c["name"] == "signup_date")
        assert col["is_enum"] == 0, (
            f"high-NDV column must not be enum, got distinct={col['distinct_count']}"
        )
        assert col["sample_values_json"] is not None, (
            "STRING column under length gate should keep format examples"
        )
        sample_vals = json.loads(col["sample_values_json"])
        assert isinstance(sample_vals, list)
        assert 1 <= len(sample_vals) <= 5
        # Every sample should be ISO-date-shaped (cheap shape check, not
        # the full ``_date_format_hint`` regex).
        for v in sample_vals:
            assert isinstance(v, str)
            assert len(v) == 10 and v[4] == "-" and v[7] == "-", (
                f"sample {v!r} is not ISO-date-shaped"
            )

    def test_sampling_non_enum_non_string_no_format_examples(self, tmp_path: Path) -> None:
        """Numeric / temporal columns whose values exceed the enum ceiling
        do NOT get format examples — they're typed already, the agent
        doesn't need a shape hint, and storing samples would just bloat
        the markdown for a column that gains no signal from them.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "match", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "score", "type": "DOUBLE", "comment": "", "is_partition": 0},
            ],
        )

        rows = [{"score": float(i)} for i in range(50)]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": len(rows)})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "match")

        assert result.status == "success"
        cols = db.get_columns(table_id)
        col = next(c for c in cols if c["name"] == "score")
        assert col["is_enum"] == 0
        assert col["sample_values_json"] is None, (
            "non-STRING non-enum columns should never carry sample_values_json"
        )

    def test_sampling_non_enum_string_blob_no_format_examples(self, tmp_path: Path) -> None:
        """STRING column whose values exceed the 80-char length gate is
        treated as a text-blob and skipped — five 200-char snippets per
        column blow up the per-table markdown without giving the agent
        useful format signal.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "posts", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [{"name": "body", "type": "STRING", "comment": "", "is_partition": 0}],
        )

        # Vary just enough to push distinct above the enum cap.
        rows = [{"body": ("lorem ipsum " * 25) + f"#{i}"} for i in range(50)]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": len(rows)})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "posts")

        assert result.status == "success"
        cols = db.get_columns(table_id)
        col = next(c for c in cols if c["name"] == "body")
        assert col["is_enum"] == 0
        assert col["sample_values_json"] is None

    def test_sampling_non_enum_string_single_distinct_no_format_examples(
        self, tmp_path: Path
    ) -> None:
        """STRING column with a single placeholder value (distinct=1) gets
        no format examples — one repeating value carries no shape signal
        and gives the agent nothing actionable beyond what the
        ``distinct_count=1`` stat already tells the dimension classifier.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "sess", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [{"name": "u_pro", "type": "STRING", "comment": "", "is_partition": 0}],
        )

        rows = [{"u_pro": "-"} for _ in range(50)]
        client.execute_sql.return_value = Envelope.success({"rows": rows, "row_count": len(rows)})

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            result = phase_column_sampling(client, db, profile, _SOURCE, "sess")

        assert result.status == "success"
        cols = db.get_columns(table_id)
        col = next(c for c in cols if c["name"] == "u_pro")
        # distinct=1 still satisfies the enum gate (1 <= 1 <= 30),
        # so is_enum stays True — we're only asserting the new
        # format-example path doesn't fire under distinct=1.
        assert col["distinct_count"] == 1
        assert col["is_enum"] == 1

    def test_sampling_3level_compute_cross_project_uses_3segment(self, tmp_path: Path) -> None:
        """3-level compute reading any cross-project source emits the
        3-segment ``<src.proj>.<src.schema>.<table>`` form so MaxCompute
        routes to the source project. Earlier this phase keyed on the
        source's tier; a 2-level source got a bare ``FROM <table>`` and
        the 3-level compute connection resolved it under its own
        project — ``Table not found`` on every sampling SQL."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = Profile(
            name="cross",
            compute_project="compute_proj",
            endpoint="https://odps.endpoint",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            sources=(DataSource(project="data_proj", schema="default", tables="*"),),
        )
        source = profile.sources[0]
        sk = source.source_key()
        table_id = db.upsert_table(sk, "events", schema_hash="abc")
        db.upsert_columns(
            table_id,
            [{"name": "id", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        client.execute_sql.return_value = Envelope.success({"rows": [], "row_count": 0})

        def _tier_for(_profile, project, *, client=None):
            return "3" if project == "compute_proj" else "2"

        with patch("maxcompute_semantic.build.phases.get_tier", side_effect=_tier_for):
            phase_column_sampling(client, db, profile, source, "events")

        call_args = client.execute_sql.call_args
        sql = call_args.args[0] if call_args.args else call_args.kwargs.get("sql", "")
        assert "data_proj.default.events" in sql, (
            f"expected 3-segment cross-project SQL, got: {sql!r}"
        )

    def test_sampling_2level_compute_cross_project_uses_2segment(self, tmp_path: Path) -> None:
        """2-level compute reading a cross-project source must emit
        the 2-segment ``<src.proj>.<table>`` form — the 2-level SQL
        parser rejects any 3-segment form outright (verified against
        live MaxCompute: ``ODPS-0130161 Parse exception - full
        qualified name '...' is not supported``), so the 3-segment
        path that works for 3-level connections is wrong here."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = Profile(
            name="cross2",
            compute_project="compute_proj",
            endpoint="https://odps.endpoint",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            sources=(DataSource(project="data_proj", schema="default", tables="*"),),
        )
        source = profile.sources[0]
        sk = source.source_key()
        table_id = db.upsert_table(sk, "events", schema_hash="abc")
        db.upsert_columns(
            table_id,
            [{"name": "id", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        client.execute_sql.return_value = Envelope.success({"rows": [], "row_count": 0})

        # Both projects 2-level.
        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            phase_column_sampling(client, db, profile, source, "events")

        call_args = client.execute_sql.call_args
        sql = call_args.args[0] if call_args.args else call_args.kwargs.get("sql", "")
        assert "data_proj.events" in sql, f"expected 2-segment cross-project SQL, got: {sql!r}"
        assert "data_proj.default.events" not in sql, (
            f"3-segment form must not be emitted to 2-level connection, got: {sql!r}"
        )


# ── Phase 6: mine_history ─────────────────────────────────────────────────


class TestPhaseMineHistory:
    def test_history_success(self, tmp_path: Path) -> None:
        """Mock info_schema returns "tenant" + mock execute_sql returns rows -> verified queries."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        # Set up table + columns in db.
        table_id = db.upsert_table(_SK, "games", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )

        # Mock detect_info_schema_source.
        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            # Mock execute_sql returns history rows.
            history_rows = [
                {
                    "operation_text": "SELECT id, status FROM games WHERE id = '1'",
                    "signature": "sig1",
                },
                {
                    "operation_text": "SELECT id FROM games",
                    "signature": "sig2",
                },
            ]
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": history_rows,
                    "row_count": 2,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert "games" in result.data["verified_queries"]
        # Both SQLs should pass ACL filter (id, status are allowed).
        assert len(result.data["verified_queries"]["games"]) == 2

    def test_history_source_none(self, tmp_path: Path) -> None:
        """Mock detect_info_schema_source returns "none" -> history_skipped=True."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="none",
        ):
            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["history_skipped"] is True

    def test_history_query_failure(self, tmp_path: Path) -> None:
        """Mock execute_sql raises exception -> status="partial_failure"."""
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "games", schema_hash="abc123")

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.side_effect = McsError("history query failed")

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "partial_failure"
        assert len(result.warnings) == 1
        assert "History mining query failed" in result.warnings[0]

    def test_history_mining_keeps_literal_variants_for_frequency(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "games", schema_hash="abc123")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "status", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": [
                        {"operation_text": "SELECT id FROM games WHERE id = 10"},
                        {"operation_text": "SELECT id FROM games WHERE id = 20"},
                        {"operation_text": "SELECT id FROM games WHERE status = 'ACTIVE'"},
                    ],
                    "row_count": 3,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["sample_sql_candidates"]["games"] == [
            "SELECT id FROM games WHERE id = 10",
            "SELECT id FROM games WHERE id = 20",
            "SELECT id FROM games WHERE status = 'ACTIVE'",
        ]
        assert result.data["verified_queries"] == result.data["sample_sql_candidates"]

    def test_history_acl_filter_is_noop_without_allowlist(self, tmp_path: Path) -> None:
        """Profile has no ``allow_columns`` field yet, so ACL filter must be a no-op.

        Earlier code passed the describe-discovered column list as if it were
        the user's allowlist; that silently dropped every ``SELECT *``, every
        aggregate (``COUNT/SUM/MAX/DISTINCT`` parsed as unknown cols), and
        every aliased reference (``SELECT t.id``). Mining must keep history
        SQL intact until a real allowlist exists. Column-level enforcement
        of arbitrary allowlists lives in ``tests/unit/lib/test_acl_filter.py``.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        table_id = db.upsert_table(_SK, "secret_data", schema_hash="xyz789")
        db.upsert_columns(
            table_id,
            [
                {"name": "id", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "public_info", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            history_rows = [
                {"operation_text": "SELECT * FROM secret_data", "signature": "sig1"},
                {
                    "operation_text": "SELECT id, public_info FROM secret_data",
                    "signature": "sig2",
                },
            ]
            client.execute_sql.return_value = Envelope.success(
                {"rows": history_rows, "row_count": 2}
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        queries = result.data["verified_queries"]["secret_data"]
        assert len(queries) == 2

    def test_history_attribution_ignores_string_literal_match(self, tmp_path: Path) -> None:
        """A source table name appearing inside a string literal must not
        be treated as a real table reference. Pre-sqlglot regex matching
        would attribute this SQL to ``cards`` because ``Cards`` matches
        with word boundaries inside the comma-separated literal ``'Post
        Cards, Posters'``. sqlglot sees it as ``exp.Literal``, not
        ``exp.Table``, and correctly skips it.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "cards", schema_hash="abc")

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": [
                        {
                            "operation_text": (
                                "SELECT b.event_status FROM expense e "
                                "JOIN budget b ON e.link_to_budget = b.budget_id "
                                "WHERE e.expense_description = 'Post Cards, Posters'"
                            ),
                            "signature": "sig1",
                        }
                    ],
                    "row_count": 1,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["sample_sql_candidates"] == {}

    def test_history_attribution_ignores_comment_match(self, tmp_path: Path) -> None:
        """A source table name appearing inside a SQL comment must not
        trigger attribution. sqlglot strips comments before walking
        the parse tree; the legacy regex would treat the SQL as touching
        ``orders`` purely from the ``-- audit on orders`` comment.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "orders", schema_hash="abc")

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": [
                        {
                            "operation_text": "-- audit on orders\nSELECT 1 FROM unrelated",
                            "signature": "sig1",
                        }
                    ],
                    "row_count": 1,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        assert result.data["sample_sql_candidates"] == {}

    def test_history_attribution_parse_error_falls_back_to_regex(self, tmp_path: Path) -> None:
        """MaxCompute supports non-standard syntax that sqlglot occasionally
        rejects. When parsing fails, mining must fall back to the regex
        path rather than silently shrinking coverage by dropping the SQL.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "games", schema_hash="abc")

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": [
                        {
                            "operation_text": "NOT VALID SQL AT ALL @@!! games",
                            "signature": "sig1",
                        }
                    ],
                    "row_count": 1,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        # Regex fallback should still attribute by table-name match.
        assert "games" in result.data["sample_sql_candidates"]
        assert len(result.data["sample_sql_candidates"]["games"]) == 1

    def test_history_attribution_cross_source_join_still_attributed(self, tmp_path: Path) -> None:
        """A SQL that JOINs an in-source table with an out-of-source one
        is still attributed to the in-source side — the agent benefits
        from seeing how real users JOIN against this table. The
        cross-source noise gets filtered out downstream by
        ``aggregate_workload_evidence``'s ``allowed_tables`` parameter,
        not by dropping the sample SQL itself.
        """
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()

        db.upsert_table(_SK, "legalities", schema_hash="abc")

        with patch(
            "maxcompute_semantic.build.phases.detect_info_schema_source",
            return_value="tenant",
        ):
            client.execute_sql.return_value = Envelope.success(
                {
                    "rows": [
                        {
                            "operation_text": (
                                "SELECT c.name FROM cards c JOIN legalities l ON c.uuid = l.uuid"
                            ),
                            "signature": "sig1",
                        }
                    ],
                    "row_count": 1,
                }
            )

            result = phase_mine_history(client, db, profile, _SOURCE)

        assert result.status == "success"
        # ``cards`` is not in the source's db, so attribution scopes to
        # ``legalities`` only.
        assert set(result.data["sample_sql_candidates"]) == {"legalities"}


# ── Phase 7: infer_joins_heuristic ────────────────────────────────────────


def _setup_joins_db(tmp_path: Path, tables_and_cols: dict[str, list[str]]) -> PackageDB:
    """Helper to create a db with given tables and column names."""
    db = PackageDB(tmp_path / "joins_test.db")
    for table_name, col_names in tables_and_cols.items():
        table_id = db.upsert_table(_SK, table_name, schema_hash="hash_" + table_name)
        columns = [
            {"name": cn, "type": "STRING", "comment": "", "is_partition": 0} for cn in col_names
        ]
        db.upsert_columns(table_id, columns)
    return db


def _setup_multi_source_joins_db(
    tmp_path: Path,
    per_source: dict[str, dict[str, list[str]]],
) -> PackageDB:
    """Like ``_setup_joins_db`` but accepts ``{source_key: {table: [cols]}}``
    so a single DB can hold the same table name under multiple source
    keys. Used for cross-source / cross-env tests."""
    db = PackageDB(tmp_path / "joins_test.db")
    for sk, tables_and_cols in per_source.items():
        for table_name, col_names in tables_and_cols.items():
            table_id = db.upsert_table(sk, table_name, schema_hash=f"hash_{sk}_{table_name}")
            columns = [
                {"name": cn, "type": "STRING", "comment": "", "is_partition": 0} for cn in col_names
            ]
            db.upsert_columns(table_id, columns)
    return db


class TestPhaseInferJoinsHeuristic:
    def test_joins_link_to_pattern(self, tmp_path: Path) -> None:
        """games.player_id -> players.id join inferred with confidence 0.9."""
        db = _setup_joins_db(
            tmp_path,
            {
                "games": ["id", "player_id", "name"],
                "players": ["id", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        assert result.data["joins_count"] >= 1

        joins = db.list_joins()
        link_to_join = next(j for j in joins if j["kind"] == "link_to")
        assert link_to_join["left_table"] == "games"
        assert link_to_join["left_col"] == "player_id"
        assert link_to_join["right_table"] == "players"
        assert link_to_join["right_col"] == "id"
        assert link_to_join["confidence"] == 0.9
        assert link_to_join["cardinality"] == "n:1"  # games.player_id -> players.id

    def test_joins_xxx_id_pattern(self, tmp_path: Path) -> None:
        """Column ending in _id but no matching table name -> loose_id fallback."""
        db = _setup_joins_db(
            tmp_path,
            {
                "orders": ["id", "category_id", "amount"],
            },
        )
        profile = _make_profile()

        # No "category" or "categories" table -> pattern 4 (loose_id).
        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        joins = db.list_joins()
        loose_join = next(j for j in joins if j["kind"] == "loose_id")
        assert loose_join["left_table"] == "orders"
        assert loose_join["left_col"] == "category_id"
        assert loose_join["right_table"] == "category"  # base name, even if not a real table
        assert loose_join["right_col"] == "id"

    def test_joins_link_to_trailing_word_split(self, tmp_path: Path) -> None:
        """``{qualifier}_{table}_id`` resolves to ``{table}.id`` via
        trailing-word split (confidence 0.8). Concrete pattern that
        the exact-match link_to would otherwise miss:
        ``superhero.eye_colour_id`` joins to ``colour.id``. No
        ``eye_colour`` table exists, so the literal base name doesn't
        match — the trailing-word split (``eye_colour`` -> ``colour``)
        recovers the legitimate FK."""
        db = _setup_joins_db(
            tmp_path,
            {
                "superhero": ["id", "eye_colour_id", "hair_colour_id", "name"],
                "colour": ["id", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        link_to_joins = [j for j in joins if j["kind"] == "link_to"]
        eye_join = next((j for j in link_to_joins if j["left_col"] == "eye_colour_id"), None)
        hair_join = next((j for j in link_to_joins if j["left_col"] == "hair_colour_id"), None)
        assert eye_join is not None and eye_join["right_table"] == "colour"
        assert eye_join["right_col"] == "id"
        assert eye_join["confidence"] == 0.8
        assert hair_join is not None and hair_join["right_table"] == "colour"
        assert hair_join["confidence"] == 0.8

    def test_joins_link_to_prefers_exact_over_trailing(self, tmp_path: Path) -> None:
        """When both an exact base-name table and a trailing-word
        table exist, the exact match wins at confidence 0.9 and the
        trailing-word match is not emitted (so the ranked output
        doesn't carry duplicates)."""
        db = _setup_joins_db(
            tmp_path,
            {
                # Both ``eye_colour`` (exact) and ``colour``
                # (trailing-word) exist as tables.
                "superhero": ["id", "eye_colour_id"],
                "eye_colour": ["id", "label"],
                "colour": ["id", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        eye_link_to = [
            j
            for j in joins
            if j["kind"] == "link_to"
            and j["left_table"] == "superhero"
            and j["left_col"] == "eye_colour_id"
        ]
        assert len(eye_link_to) == 1, (
            f"expected single link_to edge for exact match, got {eye_link_to}"
        )
        assert eye_link_to[0]["right_table"] == "eye_colour"
        assert eye_link_to[0]["confidence"] == 0.9

    def test_joins_xxx_id_prefers_same_name_over_bare_id(self, tmp_path: Path) -> None:
        """When the right table has BOTH a bare ``id`` PK and a column
        whose name matches the FK column verbatim, the same-name
        column wins as the join target.

        Concrete pattern: ``team_attributes.team_api_id`` should join
        ``team.team_api_id`` (the shared external identifier), NOT
        ``team.id`` (the surrogate PK). The reverse-substring branch
        of pattern 2 fires (``team`` is a substring of ``team_api``),
        so without this preference the resolver would otherwise pick
        bare ``id`` and emit a wrong edge.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "team": ["id", "team_api_id", "team_long_name"],
                "team_attributes": ["id", "team_api_id", "buildupplaypassing"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        ta_joins = [
            j
            for j in joins
            if j["left_table"] == "team_attributes" and j["left_col"] == "team_api_id"
        ]
        assert ta_joins, f"expected an FK edge from team_attributes.team_api_id, got {ta_joins}"
        # Every emitted edge for this FK should resolve to
        # team.team_api_id; none should point at the bare PK.
        wrong = [j for j in ta_joins if j["right_col"] == "id"]
        assert wrong == [], (
            f"expected no edges resolving team_attributes.team_api_id to team.id, got {wrong}"
        )
        right = [j for j in ta_joins if j["right_col"] == "team_api_id"]
        assert right, f"expected at least one edge resolving to team.team_api_id, got {ta_joins}"

    def test_joins_loose_id_skips_phantom_self_loop(self, tmp_path: Path) -> None:
        """No loose_id row when stripped base name equals own table."""
        db = _setup_joins_db(
            tmp_path,
            {
                "account": ["id", "account_id"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        loose_joins = [j for j in db.list_joins() if j["kind"] == "loose_id"]
        assert loose_joins == [], (
            f"expected no loose_id rows for self-referential base name, got {loose_joins}"
        )

    def test_joins_same_name_pattern_emits_fk_shaped_edge(self, tmp_path: Path) -> None:
        """FK-shaped shared column -> same_name join with confidence 0.5.

        ``games`` and ``reviews`` both carry a ``player_id`` column
        whose ``_id`` suffix is the FK-shape signal. The shared
        ``id`` PK is filtered as a coincidental PK↔PK collision (see
        ``test_joins_same_name_skips_pk_collision``).
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "games": ["id", "player_id"],
                "reviews": ["id", "player_id"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        joins = db.list_joins()
        same_name_joins = [j for j in joins if j["kind"] == "same_name"]
        pid_joins = [j for j in same_name_joins if j["left_col"] == "player_id"]
        assert len(pid_joins) == 1
        assert pid_joins[0]["left_col"] == "player_id"
        assert pid_joins[0]["right_col"] == "player_id"
        assert pid_joins[0]["confidence"] == 0.5
        assert [j for j in same_name_joins if j["left_col"] == "id"] == []

    def test_joins_same_name_drops_attribute_collision(self, tmp_path: Path) -> None:
        """Shared non-FK column with no PK-like side -> no same_name edge.

        ``account.date = loan.date``-style attribute collisions are
        pure name coincidences; emitting them at 0.5 misleads the
        agent into Cartesian joins. The filter requires either an
        FK-shaped name or one PK-like side.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "account": ["account_id", "date", "amount"],
                "loan": ["loan_id", "date", "duration"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        joins = db.list_joins()
        date_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "date"]
        assert date_joins == [], (
            f"expected no same_name edge for attribute-name collision on `date`, got {date_joins}"
        )

    def test_joins_same_name_skips_pk_collision(self, tmp_path: Path) -> None:
        """Two unrelated tables both have ``id`` PK columns -> no
        same_name join surfaces on ``id``. PK↔PK same-name matches are
        almost always coincidental, not real FK relationships."""
        db = _setup_joins_db(
            tmp_path,
            {
                "cards": ["id", "name"],
                "sets": ["id", "code"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        id_same_name = [
            j
            for j in joins
            if j["kind"] == "same_name" and j["left_col"] == "id" and j["right_col"] == "id"
        ]
        assert id_same_name == [], (
            f"expected no same_name joins on coincidental id PK columns, got {id_same_name}"
        )

    def test_joins_same_name_skips_pk_uniqueness(self, tmp_path: Path) -> None:
        """Two tables share a near-unique column named ``uuid`` (both
        PK-like by ``uniqueness_ratio`` >= 0.95) -> drop the same_name
        edge. Catches non-canonical PK names that the ``id``-only check
        would miss."""
        db = _setup_joins_db(
            tmp_path,
            {
                "products": ["uuid", "title"],
                "orders": ["uuid", "amount"],
            },
        )
        db.update_column_profile(_SK, "products", "uuid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "orders", "uuid", uniqueness_ratio=0.98)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        uuid_same_name = [
            j
            for j in joins
            if j["kind"] == "same_name" and j["left_col"] == "uuid" and j["right_col"] == "uuid"
        ]
        assert uuid_same_name == [], (
            f"expected no same_name joins on PK-like uuid columns, got {uuid_same_name}"
        )

    def test_joins_same_name_keeps_distinctive_pk_pk_as_1_to_1(self, tmp_path: Path) -> None:
        """Two tables share a distinctive PK column name (``cdscode`` —
        long, multi-word, domain-specific) both at 100% uniqueness ->
        emit a ``same_name`` 1:1 edge at reduced confidence rather than
        dropping. Two unrelated entity tables almost never coincidentally
        share a distinctive PK name; when they do, it's a 1:1 entity-
        split (the same entity decomposed across tables for different
        attribute groups). california_schools has frpm.cdscode (PK) and
        schools.cdscode (PK) — this is the join the agent needs."""
        db = _setup_joins_db(
            tmp_path,
            {
                "frpm": ["cdscode", "school_name"],
                "schools": ["cdscode", "street"],
            },
        )
        db.update_column_profile(_SK, "frpm", "cdscode", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "schools", "cdscode", uniqueness_ratio=1.0)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        cdscode_joins = [
            j
            for j in joins
            if j["kind"] == "same_name"
            and j["left_col"] == "cdscode"
            and j["right_col"] == "cdscode"
        ]
        assert len(cdscode_joins) == 1, (
            f"expected one same_name edge between frpm.cdscode and schools.cdscode, "
            f"got {cdscode_joins}"
        )
        assert cdscode_joins[0]["cardinality"] == "1:1"
        # Distinctive PK↔PK is recovered at reduced confidence — not the
        # full 0.5 same_name baseline, since the shape is still PK↔PK.
        assert cdscode_joins[0]["confidence"] < 0.5

    def test_joins_same_name_keeps_fk_to_pk(self, tmp_path: Path) -> None:
        """When one side is PK-like (high uniqueness) and the other is
        not, the same_name edge should still surface — that is the
        legitimate FK↔PK shape that this pattern is meant to recover."""
        # ``cards.uuid`` is unique (PK); ``foreign_data.uuid`` is the FK
        # that repeats per translation -> low uniqueness.
        db = _setup_joins_db(
            tmp_path,
            {
                "cards": ["uuid", "card_name"],
                "foreign_data": ["uuid", "language"],
            },
        )
        db.update_column_profile(_SK, "cards", "uuid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "foreign_data", "uuid", uniqueness_ratio=0.2)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        uuid_joins = [
            j
            for j in joins
            if j["kind"] == "same_name" and j["left_col"] == "uuid" and j["right_col"] == "uuid"
        ]
        assert len(uuid_joins) == 1, (
            f"expected one same_name edge for FK↔PK uuid pair, got {uuid_joins}"
        )
        # Cardinality must reflect uniqueness, not just the literal "id"
        # name: cards.uuid (uniqueness=1.0, PK) → foreign_data.uuid
        # (uniqueness=0.2, FK) is a 1:n shape. The pre-fix name-only
        # check looked for ``id`` and would mislabel this n:1 when
        # ``foreign_data`` happens to also have an ``id`` column.
        assert uuid_joins[0]["cardinality"] == "1:n", (
            f"expected 1:n cardinality for PK uuid → FK uuid, got {uuid_joins[0]['cardinality']}"
        )

    def test_joins_same_name_cardinality_uses_uniqueness(self, tmp_path: Path) -> None:
        """When tables share a non-``id`` column and the FK side also
        has its own ``id`` column, cardinality must be derived from
        uniqueness — not from the presence of a same-named ``id``
        column. Concrete bug: when a child table has both ``id`` (its
        own PK) and a natural-key FK column (e.g. ``uuid``) pointing
        at the parent, the old name-only check returned ``n:1``
        because the right side had an ``id`` column, hiding that the
        parent's natural-key column is the actual one-side."""
        db = _setup_joins_db(
            tmp_path,
            {
                "cards": ["uuid", "card_name"],
                "foreign_data": ["id", "uuid", "language"],
            },
        )
        db.update_column_profile(_SK, "cards", "uuid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "foreign_data", "uuid", uniqueness_ratio=0.2)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        uuid_joins = [
            j
            for j in joins
            if j["kind"] == "same_name" and j["left_col"] == "uuid" and j["right_col"] == "uuid"
        ]
        assert len(uuid_joins) == 1
        assert uuid_joins[0]["cardinality"] == "1:n", (
            f"expected 1:n (left PK by uniqueness → right FK), got {uuid_joins[0]['cardinality']}"
        )

    def test_joins_same_name_boosts_fk_to_pk_confidence(self, tmp_path: Path) -> None:
        """FK→PK same_name joins should be boosted above the 0.5 base
        when uniqueness stats are available — a real fk-pk shape is a
        much stronger signal than two coincidentally shared non-unique
        columns. Concrete drop-target: a wide-schema profile with ~100
        same_name edges where real FK→PK pairs and coincidental shared
        values (``points``, ``url``, ``name``) all landed at flat 0.5,
        drowning the real signal in the agent's joins_to listing.

        Uses a ``uuid`` natural-key fixture (not an ``_id`` /
        ``<table>id`` shape) so the edge stays on the ``same_name``
        pattern — the FK-suffix / table-name forms now resolve
        through pattern 1's ``link_to`` and get deduped out of
        ``same_name`` entirely (see
        ``test_joins_same_name_dedup_against_link_to``).
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "cards": ["uuid", "card_name"],
                "foreign_data": ["uuid", "language"],
            },
        )
        # cards.uuid is the PK (unique); foreign_data.uuid is the FK
        # repeating per translation -> low uniqueness.
        db.update_column_profile(_SK, "cards", "uuid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "foreign_data", "uuid", uniqueness_ratio=0.2)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        uuid_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "uuid"]
        assert len(uuid_joins) == 1, f"expected one same_name edge for uuid FK↔PK, got {uuid_joins}"
        # base 0.5 + UNIQUENESS_CAP (0.20) * 1.0 = 0.70
        assert uuid_joins[0]["confidence"] > 0.5, (
            f"expected FK→PK same_name confidence boosted above 0.5, got "
            f"{uuid_joins[0]['confidence']}"
        )

    def test_joins_fact_dim_fact_pair_resolves_via_link_to(self, tmp_path: Path) -> None:
        """Two fact tables sharing an FK-shaped column that matches a
        parent dimension's natural PK — the fact↔dim edges resolve
        through pattern 1's ``link_to`` (high confidence), and the
        fact↔fact ``same_name`` edge stays suppressed.

        Concrete shape: ``articleimpressions`` / ``articlelikes``
        both carry ``articleid``, which is also the PK of the
        ``articles`` dimension. Pattern 1 emits both child→parent
        edges as ``link_to`` resolved via the right-table's natural-PK
        fallback. Pattern 3 then dedups its would-be ``same_name``
        emissions against those ``link_to`` rows, and the residual
        fact↔fact pair (which would otherwise mislead the agent into
        a Cartesian join through ``articleimpressions.articleid =
        articlelikes.articleid``) is dropped because
        neither side is PK-like and the shared-dimension guard fires.

        Pre-fix behavior emitted three rows: two fact↔dim ``same_name``
        edges at 0.5 base AND a duplicate of each via ``link_to``,
        plus a bogus parent→child ``xxx_id`` edge from
        ``articles.articleid`` (the parent's own PK) via the
        reverse-substring heuristic. The new left-PK guard in pattern
        2 closes the bogus reverse direction.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "articles": ["articleid", "name"],
                "articleimpressions": ["articleid", "points"],
                "articlelikes": ["articleid", "wins"],
            },
        )
        # Both child columns are non-unique (FKs to articles).
        db.update_column_profile(_SK, "articleimpressions", "articleid", uniqueness_ratio=0.05)
        db.update_column_profile(_SK, "articlelikes", "articleid", uniqueness_ratio=0.05)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        cid_joins = [j for j in joins if j["left_col"] == "articleid"]
        # The two fact-dim edges land as link_to at 0.9, resolved via
        # the right-table natural-PK fallback (articles has no
        # bare ``id`` — its PK IS articleid).
        fact_dim = [
            j
            for j in cid_joins
            if j["kind"] == "link_to" and "articles" in {j["left_table"], j["right_table"]}
        ]
        assert len(fact_dim) == 2, f"expected two link_to fact→dim edges, got {fact_dim}"
        assert all(j["confidence"] == 0.9 for j in fact_dim)
        # No same_name duplicates of the link_to edges.
        fact_dim_same_name = [
            j
            for j in cid_joins
            if j["kind"] == "same_name" and "articles" in {j["left_table"], j["right_table"]}
        ]
        assert fact_dim_same_name == [], (
            f"expected pattern 3 to dedup fact↔dim same_name edges "
            f"against link_to, got {fact_dim_same_name}"
        )
        # The fact↔fact edge is suppressed by the shared-dimension guard.
        fk_fk_edge = [
            j
            for j in cid_joins
            if j["kind"] == "same_name"
            and {j["left_table"], j["right_table"]} == {"articleimpressions", "articlelikes"}
        ]
        assert fk_fk_edge == [], (
            f"expected shared-dimension guard to suppress fact↔fact "
            f"articleid same_name edge, got {fk_fk_edge}"
        )
        # No bogus parent→child xxx_id from articles' own PK.
        parent_xxx_id = [
            j for j in cid_joins if j["kind"] == "xxx_id" and j["left_table"] == "articles"
        ]
        assert parent_xxx_id == [], (
            f"expected left-PK guard in pattern 2 to suppress parent's "
            f"own-PK reverse-substring edge, got {parent_xxx_id}"
        )

    def test_joins_same_name_id_fk_when_uniqueness_disagrees(self, tmp_path: Path) -> None:
        """Parent-PK / child-FK pattern: both columns are literally
        named ``id`` but only one is the actual PK. The FK side
        carries ``uniqueness_ratio`` well below the threshold, so
        the same_name edge for ``parent.id = child.id`` must still
        surface — the name-only fallback would wrongly classify
        the child's ``id`` as PK and drop the join."""
        db = _setup_joins_db(
            tmp_path,
            {
                "patient": ["id", "sex", "birthday"],
                "laboratory": ["id", "date", "got"],
            },
        )
        # patient.id is the actual PK (one row per patient); laboratory.id
        # repeats ~50× per patient -> low uniqueness.
        db.update_column_profile(_SK, "patient", "id", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "laboratory", "id", uniqueness_ratio=0.02)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        id_joins = [
            j
            for j in joins
            if j["kind"] == "same_name" and j["left_col"] == "id" and j["right_col"] == "id"
        ]
        assert len(id_joins) == 1, (
            f"expected one same_name edge for the FK↔PK id pair, got {id_joins}"
        )

    def test_joins_same_name_drops_temporal_collision_native_type(self, tmp_path: Path) -> None:
        """Two TIMESTAMP-typed ``creationdate`` columns with sub-second
        precision both have uniqueness ≈ 1.0 (every event happens at a
        distinct instant), satisfying the ``one_side_pk_like`` gate.
        Without the temporal guard this produces a phantom
        ``T1.creationdate = T2.creationdate`` same_name edge between
        unrelated tables. Concrete failure mode that motivated the
        guard: a forum-/event-style schema where ``comments`` /
        ``posts`` / ``users`` / ``votes`` / ``badges`` each carry a
        ``creationdate``, yielding many spurious ``via creationdate``
        edges in ``_overview.md`` / ``_joins.md`` that misled the
        agent's join planning.
        """
        db = PackageDB(tmp_path / "temporal_test.db")
        for table_name in ("comments", "posts"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {
                        "name": "id",
                        "type": "BIGINT",
                        "comment": "",
                        "is_partition": 0,
                    },
                    {
                        "name": "creationdate",
                        "type": "TIMESTAMP",
                        "comment": "",
                        "is_partition": 0,
                    },
                ],
            )
        # Both creationdate columns near-unique (sub-second timestamps).
        db.update_column_profile(_SK, "comments", "creationdate", uniqueness_ratio=0.99)
        db.update_column_profile(_SK, "posts", "creationdate", uniqueness_ratio=1.0)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        temporal_joins = [
            j for j in joins if j["kind"] == "same_name" and j["left_col"] == "creationdate"
        ]
        assert temporal_joins == [], (
            f"expected no same_name edge between two TIMESTAMP creationdate "
            f"columns (both sides temporal), got {temporal_joins}"
        )

    def test_joins_same_name_drops_temporal_collision_string_dates(self, tmp_path: Path) -> None:
        """STRING-typed columns whose sample values look like dates also
        count as temporal — the agent treats them the same way at SQL
        time (``[str-date]`` marker, ``SUBSTR``-based extraction). High
        cardinality from per-event timestamps would otherwise re-trigger
        the spurious-join pattern via the STRING path."""
        db = PackageDB(tmp_path / "temporal_string_test.db")
        date_samples = json.dumps(
            ["2024-01-15", "2024-02-20", "2024-03-10", "2024-04-05", "2024-05-22"]
        )
        for table_name in ("orders", "shipments"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {
                        "name": "id",
                        "type": "BIGINT",
                        "comment": "",
                        "is_partition": 0,
                    },
                    {
                        "name": "event_date",
                        "type": "STRING",
                        "comment": "",
                        "is_partition": 0,
                        "sample_values_json": date_samples,
                    },
                ],
            )
        db.update_column_profile(_SK, "orders", "event_date", uniqueness_ratio=0.97)
        db.update_column_profile(_SK, "shipments", "event_date", uniqueness_ratio=0.98)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        temporal_joins = [
            j for j in joins if j["kind"] == "same_name" and j["left_col"] == "event_date"
        ]
        assert temporal_joins == [], (
            f"expected no same_name edge between two STRING-date event_date columns, "
            f"got {temporal_joins}"
        )

    def test_joins_same_name_keeps_fk_on_temporal_with_non_temporal(self, tmp_path: Path) -> None:
        """One temporal column joined to a non-temporal column on the
        same name is a degenerate case (column names don't usually
        collide that way) but the guard must still allow it through:
        only when BOTH sides are temporal do we drop the edge. Catches
        regression where an over-broad guard would suppress legitimate
        joins. (Asymmetric guard preserves the legitimate non-temporal
        FK→PK shapes the pattern is designed to recover.)"""
        db = PackageDB(tmp_path / "asymmetric_test.db")
        # ``cards`` has a non-temporal STRING ``uuid`` PK; ``foreign_data``
        # has a STRING ``uuid`` FK. Both non-temporal — the legitimate
        # FK→PK shape — and the same_name edge must survive.
        for table_name, cols in (
            (
                "cards",
                [
                    {"name": "uuid", "type": "STRING", "comment": "", "is_partition": 0},
                    {"name": "card_name", "type": "STRING", "comment": "", "is_partition": 0},
                ],
            ),
            (
                "foreign_data",
                [
                    {"name": "uuid", "type": "STRING", "comment": "", "is_partition": 0},
                    {"name": "language", "type": "STRING", "comment": "", "is_partition": 0},
                ],
            ),
        ):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(tid, cols)
        db.update_column_profile(_SK, "cards", "uuid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "foreign_data", "uuid", uniqueness_ratio=0.2)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        uuid_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "uuid"]
        assert len(uuid_joins) == 1, (
            f"expected non-temporal uuid FK→PK same_name edge to survive, got {uuid_joins}"
        )

    def test_joins_same_name_drops_url_collision(self, tmp_path: Path) -> None:
        """Two STRING ``url`` columns holding Wikipedia URLs both have
        uniqueness ≈ 1.0 (each entity has its own page), satisfying the
        ``one_side_pk_like`` gate. Without the URL guard this produces
        a phantom ``T1.url = T2.url`` edge between unrelated tables.
        Concrete failure mode: schemas where multiple independent
        entity tables each carry a ``url`` STRING column with their
        own per-entity Wikipedia / homepage URL, yielding many
        spurious ``via url`` edges in ``_overview.md`` / ``_joins.md``
        that suggested one entity's URLs join to another's."""
        db = PackageDB(tmp_path / "url_test.db")
        url_samples_companies = json.dumps(
            [
                "http://example.com/company/alpha",
                "http://example.com/company/beta",
                "https://example.com/company/gamma",
            ]
        )
        url_samples_people = json.dumps(
            [
                "http://example.com/person/one",
                "http://example.com/person/two",
                "https://example.com/person/three",
            ]
        )
        for table_name, samples in (
            ("companies", url_samples_companies),
            ("people", url_samples_people),
        ):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "url",
                        "type": "STRING",
                        "comment": "",
                        "is_partition": 0,
                        "sample_values_json": samples,
                    },
                ],
            )
        db.update_column_profile(_SK, "companies", "url", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "people", "url", uniqueness_ratio=1.0)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        url_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "url"]
        assert url_joins == [], (
            f"expected no same_name edge between two STRING url columns holding "
            f"http(s):// values, got {url_joins}"
        )

    def test_joins_same_name_keeps_non_url_string_fk(self, tmp_path: Path) -> None:
        """A STRING ``code`` column with non-URL identifier-shaped samples
        (e.g. country codes) must NOT be classified as a URL — the guard
        only catches columns whose sample values match the
        ``http(s)://`` prefix. Catches regression where an over-broad
        STRING guard would suppress legitimate cross-table FK joins
        keyed by identifier-shaped strings."""
        db = PackageDB(tmp_path / "non_url_string_test.db")
        code_samples = json.dumps(["US", "GB", "DE", "FR", "JP"])
        for table_name in ("country", "shipment"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "code",
                        "type": "STRING",
                        "comment": "",
                        "is_partition": 0,
                        "sample_values_json": code_samples,
                    },
                ],
            )
        # ``country.code`` is the PK side (uniqueness 1.0), ``shipment.code``
        # is the FK side (low uniqueness, references country).
        db.update_column_profile(_SK, "country", "code", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "shipment", "code", uniqueness_ratio=0.05)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        code_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "code"]
        assert len(code_joins) == 1, (
            f"expected non-URL STRING code FK→PK same_name edge to survive, got {code_joins}"
        )

    def test_joins_same_name_drops_numeric_value_collision(self, tmp_path: Path) -> None:
        """A NUMERIC ``amount`` column shared across unrelated tables must
        not produce a same_name edge, even when one side has high
        uniqueness (continuous monetary distribution). Concrete failure
        mode: three transactional tables (e.g. ``loan``, ``order``,
        ``trans``) each carry an ``amount`` numeric column with high
        uniqueness on one side (~0.97 when each row has a distinct
        dollar value) and lower uniqueness on the others. Pre-fix the
        high-uniqueness side tripped ``one_side_pk_like`` and emitted
        phantom ``via amount`` edges across every pair, polluting
        ``_overview.md`` and showing up as ``amount:int [fk]`` in the
        columns_index."""
        db = PackageDB(tmp_path / "numeric_value_test.db")
        for table_name, col_type in (
            ("loan", "BIGINT"),
            ("order_tbl", "DOUBLE"),
            ("trans", "BIGINT"),
        ):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "amount",
                        "type": col_type,
                        "comment": "",
                        "is_partition": 0,
                    },
                ],
            )
        # loan.amount is high-uniqueness (each loan distinct), order/trans low —
        # exactly the shape that previously broke the heuristic.
        db.update_column_profile(_SK, "loan", "amount", uniqueness_ratio=0.97)
        db.update_column_profile(_SK, "order_tbl", "amount", uniqueness_ratio=0.66)
        db.update_column_profile(_SK, "trans", "amount", uniqueness_ratio=0.03)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        amount_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "amount"]
        assert amount_joins == [], (
            f"expected no same_name edge between numeric ``amount`` columns "
            f"(monetary value, not entity key), got {amount_joins}"
        )

    def test_joins_same_name_keeps_numeric_fk_with_id_suffix(self, tmp_path: Path) -> None:
        """A NUMERIC column whose name carries an identity-suggesting
        suffix (``_id``, ``id``, ``_key``) is exempt from the numeric-
        value guard — those are legitimate FK candidates. Regression
        guard: an over-broad NUMERIC drop would suppress legitimate
        ``team.team_api_id = team_attributes.team_api_id`` joins.

        The edge may surface either as ``xxx_id`` (when pattern 2's
        reverse-substring branch fires and resolves the right column
        to the same-name match — see
        ``test_joins_xxx_id_prefers_same_name_over_bare_id``) or as
        ``same_name`` (when pattern 2 doesn't fire and pattern 3
        catches the shared column). What matters here is that some
        edge for the shared ``team_api_id`` survives, pointing to
        ``team.team_api_id`` rather than the surrogate ``team.id``.
        """
        db = PackageDB(tmp_path / "numeric_fk_test.db")
        for table_name in ("team", "team_attributes"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "team_api_id",
                        "type": "BIGINT",
                        "comment": "",
                        "is_partition": 0,
                    },
                ],
            )
        db.update_column_profile(_SK, "team", "team_api_id", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "team_attributes", "team_api_id", uniqueness_ratio=0.1)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        fk_joins = [
            j
            for j in joins
            if j["left_table"] == "team_attributes"
            and j["left_col"] == "team_api_id"
            and j["right_table"] == "team"
            and j["right_col"] == "team_api_id"
        ]
        assert len(fk_joins) >= 1, (
            f"expected ``team_api_id`` FK→PK edge to survive (the "
            f"_id suffix makes it identity-shaped, not a metric), got {fk_joins}"
        )
        wrong_to_id = [
            j
            for j in joins
            if j["left_table"] == "team_attributes"
            and j["left_col"] == "team_api_id"
            and j["right_table"] == "team"
            and j["right_col"] == "id"
        ]
        assert wrong_to_id == [], (
            f"expected no edge resolving team_attributes.team_api_id to team.id, got {wrong_to_id}"
        )

    def test_joins_same_name_drops_label_column_collision(self, tmp_path: Path) -> None:
        """A STRING column whose name is a label keyword (``name``,
        ``title``, ``description``, ...) and lacks an FK suffix is a
        display label, not a join key. Failure mode this guards
        against: three unrelated entity tables each carry a ``name``
        STRING column with near-1.0 uniqueness (each row has a
        distinct display label). Pre-fix the unique-on-one-side check
        tripped and emitted phantom ``T1.name = T2.name`` same_name
        edges between entity types whose display-name value spaces
        don't overlap."""
        db = PackageDB(tmp_path / "label_test.db")
        for table_name in ("entity_a", "entity_b", "entity_c"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "name",
                        "type": "STRING",
                        "comment": "",
                        "is_partition": 0,
                    },
                ],
            )
        # All three name columns have high uniqueness (each entity has
        # a distinct display label) — exactly the shape that previously
        # tripped the PK-like gate.
        db.update_column_profile(_SK, "entity_a", "name", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "entity_b", "name", uniqueness_ratio=0.99)
        db.update_column_profile(_SK, "entity_c", "name", uniqueness_ratio=1.0)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        name_joins = [j for j in joins if j["kind"] == "same_name" and j["left_col"] == "name"]
        assert name_joins == [], (
            f"expected no same_name edge between label ``name`` columns "
            f"(display labels for distinct entity types), got {name_joins}"
        )

    def test_joins_same_name_keeps_label_suffixed_fk(self, tmp_path: Path) -> None:
        """A column whose name carries a label keyword as a SUFFIX
        (``country_name``, ``product_title``) is not exact-match and
        flows through the regular FK-shape branch — the label guard
        does NOT suppress it. Regression guard: an over-broad label
        drop would suppress legitimate
        ``users.user_name = login_attempt.user_name`` patterns. Here
        we use ``product_name`` shared between two tables to verify
        the suffixed form survives even when ``name`` (exact match)
        would not."""
        db = PackageDB(tmp_path / "label_suffix_test.db")
        for table_name in ("products", "inventory"):
            tid = db.upsert_table(_SK, table_name, schema_hash="h_" + table_name)
            db.upsert_columns(
                tid,
                [
                    {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                    {
                        "name": "product_name",
                        "type": "STRING",
                        "comment": "",
                        "is_partition": 0,
                    },
                ],
            )
        db.update_column_profile(_SK, "products", "product_name", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "inventory", "product_name", uniqueness_ratio=0.2)

        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        fk_joins = [
            j for j in joins if j["kind"] == "same_name" and j["left_col"] == "product_name"
        ]
        assert len(fk_joins) == 1, (
            f"expected ``product_name`` PK→FK same_name edge to survive "
            f"(suffixed form is not an exact label-keyword match), got {fk_joins}"
        )

    def test_joins_same_name_drops_shared_dimension_fact_pair(self, tmp_path: Path) -> None:
        """Two fact tables sharing a FK column that both already FK the
        same dimension (via patterns 0/1/2) should NOT get a redundant
        fact↔fact ``same_name`` edge. Concrete failure mode this guards
        against: a schema where ``articleimpressions`` and
        ``articlelikes`` both carry ``topicid`` FKed to
        ``topics.topicid`` (recovered by pattern 1 as ``link_to`` at
        0.9). Without the shared-dimension guard, pattern 3 also emits
        a noise ``articleimpressions.topicid <-> articlelikes.topicid``
        n:m@0.5 edge that the agent then mis-uses as a direct fact↔fact
        join (Cartesian over all rows per topic instead of joining
        via topics)."""
        db = _setup_joins_db(
            tmp_path,
            {
                "topics": ["topicid", "year"],
                "articleimpressions": ["id", "topicid", "points"],
                "articlelikes": ["id", "topicid", "position"],
            },
        )
        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        # Each fact table should still FK to topics via link_to.
        link_to_topics = [
            j
            for j in joins
            if j["kind"] == "link_to"
            and j["left_col"] == "topicid"
            and j["right_table"] == "topics"
        ]
        assert len(link_to_topics) == 2, (
            f"expected both fact tables to link_to topics, got {link_to_topics}"
        )
        # The redundant fact↔fact same_name edge must be suppressed.
        fact_fact = [
            j
            for j in joins
            if j["kind"] == "same_name"
            and j["left_col"] == "topicid"
            and {j["left_table"], j["right_table"]} == {"articleimpressions", "articlelikes"}
        ]
        assert fact_fact == [], (
            f"expected shared-dimension guard to suppress fact↔fact topicid "
            f"same_name edge, got {fact_fact}"
        )

    def test_joins_same_name_keeps_fact_pair_when_no_shared_dimension(self, tmp_path: Path) -> None:
        """When two tables share a FK-shaped column but no parent
        dimension exists in the profile, an edge between the two
        matching columns must survive — it's the only way the FK↔FK
        pair surfaces. Regression guard against an over-broad
        shared-dimension filter that would drop the legitimate
        ``team.team_api_id = team_attributes.team_api_id`` case the
        existing ``test_joins_same_name_emits_shared_fk_columns``
        already covers, here with the absence framed explicitly.

        The edge may surface as either ``xxx_id`` (when pattern 2's
        reverse-substring branch fires and resolves to the same-name
        match — the path exercised when the FK column embeds the
        right table's name as a substring) or ``same_name`` (when
        pattern 2 doesn't fire and pattern 3 catches the shared
        column). Either is acceptable here.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "team": ["id", "team_api_id", "team_long_name"],
                "team_attributes": ["id", "team_api_id", "buildup_play_speed"],
            },
        )
        result = phase_infer_joins_heuristic(db, _make_profile())
        assert result.status == "success"

        joins = db.list_joins()
        matching = [
            j for j in joins if j["left_col"] == "team_api_id" and j["right_col"] == "team_api_id"
        ]
        assert len(matching) >= 1, (
            f"expected a team_api_id<->team_api_id edge to survive when no parent "
            f"dimension absorbs the FK, got {matching}"
        )

    def test_joins_empty_db(self, tmp_path: Path) -> None:
        """No tables -> no joins inferred."""
        db = _make_db(tmp_path)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        assert result.data["joins_count"] == 0
        assert db.list_joins() == []

    def test_joins_same_name_emits_shared_fk_columns(self, tmp_path: Path) -> None:
        """Shared ``_id`` column between two tables (no parent table for
        ``team_api``) emits an edge on the matching column pair —
        pattern 4 (loose_id) alone is too weak (confidence 0.3) to
        surface the FK↔FK pair, and one of patterns 2/3 must catch
        it. Either ``xxx_id`` (pattern 2 reverse-substring resolving
        to the same-name match) or ``same_name`` (pattern 3) is
        acceptable; both point at the same column pair."""
        db = _setup_joins_db(
            tmp_path,
            {
                "team": ["id", "team_api_id", "team_long_name"],
                "team_attributes": ["id", "team_api_id", "buildup_play_speed"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        shared_fk = [
            j for j in joins if j["left_col"] == "team_api_id" and j["right_col"] == "team_api_id"
        ]
        assert len(shared_fk) >= 1, (
            f"expected an edge on team_api_id between team and team_attributes, got {shared_fk}"
        )
        for edge in shared_fk:
            assert {edge["left_table"], edge["right_table"]} == {"team", "team_attributes"}
            assert edge["kind"] in {"xxx_id", "same_name"}
            assert edge["confidence"] >= 0.5

    def test_joins_link_to_prefix_pattern(self, tmp_path: Path) -> None:
        """``link_to_<table>`` prefix (Airtable/Notion FK convention) ->
        join to ``<table>.id`` at confidence 0.9. Concrete pattern:
        ``attendance.link_to_event`` -> ``event.id`` and
        ``attendance.link_to_member`` -> ``member.id`` in a typical
        student-club / event-attendance schema, which neither the
        ``_id`` suffix nor the no-underscore ``id`` suffix can catch."""
        db = _setup_joins_db(
            tmp_path,
            {
                "attendance": ["link_to_event", "link_to_member"],
                "event": ["event_id", "type"],
                "member": ["member_id", "first_name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        link_to_joins = [j for j in joins if j["kind"] == "link_to"]
        event_edge = next((j for j in link_to_joins if j["left_col"] == "link_to_event"), None)
        member_edge = next((j for j in link_to_joins if j["left_col"] == "link_to_member"), None)
        assert event_edge is not None
        assert event_edge["right_table"] == "event"
        assert event_edge["confidence"] == 0.9
        assert member_edge is not None
        assert member_edge["right_table"] == "member"
        assert member_edge["confidence"] == 0.9

    def test_joins_link_to_prefix_trailing_word_split(self, tmp_path: Path) -> None:
        """``link_to_<qualifier>_<table>`` recovers ``<table>`` via the
        trailing-word split at 0.8, mirroring pattern 1's behavior for
        ``<qualifier>_<table>_id`` columns."""
        db = _setup_joins_db(
            tmp_path,
            {
                "superhero": ["link_to_eye_colour"],
                "colour": ["id", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        link_to_joins = [
            j for j in joins if j["kind"] == "link_to" and j["left_col"] == "link_to_eye_colour"
        ]
        assert len(link_to_joins) == 1
        assert link_to_joins[0]["right_table"] == "colour"
        assert link_to_joins[0]["confidence"] == 0.8

    def test_joins_no_underscore_id_pattern(self, tmp_path: Path) -> None:
        """No-underscore ``<X>id`` FK (StackExchange-style convention)
        resolves to ``<X>s.id`` via pattern 1's ``+s`` plural lookup.
        Concrete: ``posts.userid`` -> ``users.id`` in any forum-style
        schema — the missing edge that the ``_id``-only gate dropped
        on the floor."""
        db = _setup_joins_db(
            tmp_path,
            {
                "posts": ["postid", "userid", "title"],
                "users": ["userid", "displayname"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        link_to_joins = [j for j in joins if j["kind"] == "link_to"]
        userid_edge = next(
            (j for j in link_to_joins if j["left_table"] == "posts" and j["left_col"] == "userid"),
            None,
        )
        assert userid_edge is not None
        assert userid_edge["right_table"] == "users"
        assert userid_edge["confidence"] == 0.9

    def test_joins_no_underscore_id_with_qualifier(self, tmp_path: Path) -> None:
        """Qualifier-prefixed no-underscore FK (``owneruserid``,
        ``lasteditoruserid``) resolves to ``users`` via pattern 2's
        reverse-substring direction (singular ``user`` is a substring
        of ``owneruser``). The StackExchange-style convention used by
        author-FK columns in forum/post schemas."""
        db = _setup_joins_db(
            tmp_path,
            {
                "posts": ["postid", "owneruserid", "lasteditoruserid"],
                "users": ["userid", "displayname"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        xxx_id_joins = [j for j in joins if j["kind"] == "xxx_id"]
        owner_edge = next((j for j in xxx_id_joins if j["left_col"] == "owneruserid"), None)
        last_editor_edge = next(
            (j for j in xxx_id_joins if j["left_col"] == "lasteditoruserid"), None
        )
        assert owner_edge is not None
        assert owner_edge["right_table"] == "users"
        assert last_editor_edge is not None
        assert last_editor_edge["right_table"] == "users"

    def test_joins_no_underscore_id_short_safe(self, tmp_path: Path) -> None:
        """``bid``, ``aid``, ``paid``, ``uuid``, ``void`` end in ``id``
        but aren't FKs — the ``len(name) >= 5`` guard in
        ``_fk_suffix_form`` keeps them out of patterns 1, 2, and 4
        even when there's no parent table for them to spuriously
        match against."""
        db = _setup_joins_db(
            tmp_path,
            {
                "auctions": ["bid", "aid", "paid", "uuid", "void"],
                "users": ["userid", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        # None of the false friends should generate any join row.
        for col in ("bid", "aid", "paid", "uuid", "void"):
            spurious = [j for j in joins if j["left_col"] == col]
            assert spurious == [], (
                f"expected no joins for false-friend column ``{col}``, got {spurious}"
            )

    def test_joins_no_underscore_id_skips_loose_marker(self, tmp_path: Path) -> None:
        """The loose_id pattern stays restricted to the strict ``_id``
        form — a no-underscore ``<X>id`` column with no matching table
        anywhere does **not** emit a phantom loose_id marker (which
        would create a join row pointing at a nonexistent table)."""
        db = _setup_joins_db(
            tmp_path,
            {
                "posts": ["postid", "creationdate"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        loose_joins = [j for j in joins if j["kind"] == "loose_id"]
        assert loose_joins == [], (
            f"expected no loose_id markers for unmatched no-underscore id "
            f"columns, got {loose_joins}"
        )

    def test_joins_xxx_id_skips_when_right_lacks_id_and_same_name(self, tmp_path: Path) -> None:
        """Pattern 2 (``xxx_id``) must not fabricate a right-side
        column that doesn't exist. When the substring-matched right
        table has neither ``id`` nor a column whose name matches the
        FK column verbatim, the edge can't be reliably resolved and
        the emission must be skipped (previously fell back to
        ``col["name"]`` and produced a phantom column reference).

        Concrete failure mode: ``articleimpressions.articleimpressionsid``
        reverse-substring-matched ``articles`` (singular
        ``article`` is a substring of ``articleimpressions``).
        ``articles`` has no ``id`` column and no
        ``articleimpressionsid`` column — its PK is ``articleid``.
        The pre-fix code emitted ``articleimpressions.articleimpressionsid
        → articles.articleimpressionsid``, a column reference that
        breaks the moment the agent tries to use it.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "articles": ["articleid", "name"],
                "articleimpressions": ["articleimpressionsid", "points"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        phantom = [
            j
            for j in joins
            if j["kind"] == "xxx_id"
            and j["left_table"] == "articleimpressions"
            and j["left_col"] == "articleimpressionsid"
            and j["right_table"] == "articles"
        ]
        assert phantom == [], (
            f"expected pattern 2 to skip edges where the right table has "
            f"neither ``id`` nor a same-named column, got {phantom}"
        )

    def test_joins_link_to_skips_when_right_lacks_id_and_same_name(self, tmp_path: Path) -> None:
        """Pattern 0/1 (``link_to``) must also skip when the right
        table has neither ``id`` nor a column whose name matches the
        FK column verbatim. Mirrors the pattern-2 guard.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                # ``orders.customer_id`` -> ``customer`` table that
                # has neither ``id`` nor ``customer_id``. Pre-fix code
                # would have emitted ``orders.customer_id →
                # customer.customer_id`` (a non-existent column).
                "orders": ["order_id", "customer_id"],
                "customer": ["cust_no", "name"],
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        phantom = [
            j
            for j in joins
            if j["kind"] == "link_to"
            and j["left_table"] == "orders"
            and j["left_col"] == "customer_id"
            and j["right_table"] == "customer"
        ]
        assert phantom == [], (
            f"expected link_to to skip edges where the right table has "
            f"neither ``id`` nor a same-named column, got {phantom}"
        )

    def test_joins_link_to_keeps_same_named_pk_fallback(self, tmp_path: Path) -> None:
        """When the right table lacks ``id`` but DOES have a column
        whose name matches the FK column verbatim (the
        ``orders.customer_id → customers.customer_id`` convention),
        the edge resolves via the same-name fallback. The guard
        must not regress this case.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                # ``articles.articleid`` is the PK (no bare
                # ``id``). ``articleimpressions.articleid`` is the
                # FK. The fallback must resolve to
                # ``articles.articleid``.
                "articles": ["articleid", "name"],
                "articleimpressions": ["articleid", "points"],
            },
        )
        db.update_column_profile(_SK, "articles", "articleid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "articleimpressions", "articleid", uniqueness_ratio=0.05)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        link_to = [
            j
            for j in joins
            if j["kind"] == "link_to"
            and j["left_table"] == "articleimpressions"
            and j["left_col"] == "articleid"
            and j["right_table"] == "articles"
        ]
        assert len(link_to) == 1, (
            f"expected one link_to edge resolved via same-name PK fallback, got {link_to}"
        )
        assert link_to[0]["right_col"] == "articleid"

    def test_joins_same_name_dedup_against_link_to(self, tmp_path: Path) -> None:
        """When pattern 0/1 already emitted a ``link_to`` edge for
        ``(left.col, right.col)``, pattern 3 (``same_name``) must
        not re-emit the same edge. Pre-fix bug: the agent saw two
        rows in ``_joins.md`` for the same (left, right) pair with
        different cardinalities — link_to as ``n:m`` and same_name as
        ``n:1`` — because both patterns matched the column.

        Concrete failure mode: ``articleimpressions.
        articleid → articles.articleid`` rendered as both
        ``n:m via link_to`` AND ``n:1 via same_name``. Contradictory
        cardinalities for the same edge confuse the agent's join
        planning.
        """
        db = _setup_joins_db(
            tmp_path,
            {
                "articles": ["articleid", "name"],
                "articleimpressions": ["articleid", "points"],
            },
        )
        # PK on left, FK on right — link_to will fire from
        # articleimpressions to articles, resolving via the
        # same-name PK fallback (articles has no bare ``id``).
        db.update_column_profile(_SK, "articles", "articleid", uniqueness_ratio=1.0)
        db.update_column_profile(_SK, "articleimpressions", "articleid", uniqueness_ratio=0.05)
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)
        assert result.status == "success"

        joins = db.list_joins()
        cid_edges = [
            j
            for j in joins
            if j["left_col"] == "articleid"
            and j["right_col"] == "articleid"
            and {j["left_table"], j["right_table"]} == {"articles", "articleimpressions"}
        ]
        # Exactly one edge — the link_to from pattern 1. The
        # same_name pass must dedup against it.
        assert len(cid_edges) == 1, (
            f"expected exactly one edge for articleid pair (link_to), "
            f"got {len(cid_edges)}: {cid_edges}"
        )
        assert cid_edges[0]["kind"] == "link_to"


class TestPhaseInferJoinsHeuristicSuppressedPairs:
    """``suppressed_source_pairs`` drops every cross-source edge whose
    ``(left_sk, right_sk)`` pair matches an entry, so the dev/prod
    duplicate sources identified by ``build.cross_env`` don't pollute
    the joins table with mirror-image phantom joins."""

    def test_default_no_suppression_emits_cross_source_edges(self, tmp_path: Path) -> None:
        """Baseline: without ``suppressed_source_pairs``, a same-named
        ``users.id`` column under two sources emits cross-source edges
        for the ``orders.user_id → users.id`` FK in both sources."""
        db = _setup_multi_source_joins_db(
            tmp_path,
            {
                "acme__prod": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id", "amount"],
                },
                "acme__staging": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id", "amount"],
                },
            },
        )
        profile = _make_profile()

        result = phase_infer_joins_heuristic(db, profile)

        assert result.status == "success"
        joins = db.list_joins()
        cross_edges = [j for j in joins if j["left_source_key"] != j["right_source_key"]]
        assert cross_edges, "expected cross-source edges without suppression"

    def test_suppressed_pair_drops_all_cross_source_edges(self, tmp_path: Path) -> None:
        """With ``acme__prod`` ↔ ``acme__staging`` suppressed, no edge
        crosses between the two — within-source joins still emit."""
        db = _setup_multi_source_joins_db(
            tmp_path,
            {
                "acme__prod": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id", "amount"],
                },
                "acme__staging": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id", "amount"],
                },
            },
        )
        profile = _make_profile()
        suppressed = frozenset({frozenset({"acme__prod", "acme__staging"})})

        result = phase_infer_joins_heuristic(db, profile, suppressed_source_pairs=suppressed)

        assert result.status == "success"
        joins = db.list_joins()
        cross_edges = [j for j in joins if j["left_source_key"] != j["right_source_key"]]
        assert cross_edges == [], (
            f"expected no cross-source edges under suppression, got {cross_edges}"
        )
        # Within-source edges still surface — e.g. orders.user_id → users.id
        # in each source individually.
        within_prod = [
            j for j in joins if j["left_source_key"] == "acme__prod" == j["right_source_key"]
        ]
        within_staging = [
            j for j in joins if j["left_source_key"] == "acme__staging" == j["right_source_key"]
        ]
        assert within_prod, "expected within-source joins under acme__prod"
        assert within_staging, "expected within-source joins under acme__staging"

    def test_suppression_only_affects_named_pair(self, tmp_path: Path) -> None:
        """A 3-source profile with one suppressed pair still emits
        cross-source edges between the un-suppressed pairs."""
        db = _setup_multi_source_joins_db(
            tmp_path,
            {
                "acme__prod": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id"],
                },
                "acme__staging": {
                    "users": ["id", "email"],
                    "orders": ["id", "user_id"],
                },
                "billing__main": {
                    "users": ["id", "email"],
                    "invoices": ["id", "user_id"],
                },
            },
        )
        profile = _make_profile()
        suppressed = frozenset({frozenset({"acme__prod", "acme__staging"})})

        result = phase_infer_joins_heuristic(db, profile, suppressed_source_pairs=suppressed)

        assert result.status == "success"
        joins = db.list_joins()
        cross_sks = {
            frozenset({j["left_source_key"], j["right_source_key"]})
            for j in joins
            if j["left_source_key"] != j["right_source_key"]
        }
        assert frozenset({"acme__prod", "acme__staging"}) not in cross_sks
        # billing edges with either acme source are still allowed.
        billing_acme_prod = frozenset({"acme__prod", "billing__main"})
        billing_acme_staging = frozenset({"acme__staging", "billing__main"})
        assert billing_acme_prod in cross_sks or billing_acme_staging in cross_sks


class TestPhaseColumnProfiling:
    def test_profiling_updates_profile_stats(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.execute_sql.return_value = Envelope(
            status="success",
            data={
                "rows": [
                    {
                        "row_count": 100,
                        "id__approx_ndv": 100,
                        "id__nulls": 0,
                        "status__approx_ndv": 3,
                        "status__nulls": 5,
                    }
                ],
            },
        )
        client.list_partitions.return_value = {"latest_partition": "ds=20260521"}
        client._tier = "2"

        db = _make_db(tmp_path)
        profile = _make_profile()
        tid = db.upsert_table(_SK, "orders", "hash")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "BIGINT", "is_partition": 0},
                {"name": "status", "type": "STRING", "is_partition": 0},
                {"name": "ds", "type": "STRING", "is_partition": 1},
            ],
        )

        result = phase_column_profiling(
            client,
            db,
            profile,
            _SOURCE,
            "orders",
            workload_columns={"status"},
        )

        assert result.status == "success"
        assert result.data["profiled_columns"] == 2
        cols = {c["name"]: c for c in db.get_columns(tid)}
        assert cols["id"]["row_count"] == 100
        assert cols["id"]["approx_ndv"] == 100
        assert cols["id"]["uniqueness_ratio"] == 1.0
        assert cols["status"]["null_ratio"] == 0.05
        assert cols["status"]["is_enum"] == 1
        assert cols["ds"]["row_count"] is None

    def test_profiling_no_candidates_returns_zero(self, tmp_path: Path) -> None:
        client = MagicMock()
        db = _make_db(tmp_path)
        profile = _make_profile()
        tid = db.upsert_table(_SK, "orders", "hash")
        db.upsert_columns(tid, [{"name": "ds", "type": "STRING", "is_partition": 1}])

        result = phase_column_profiling(
            client,
            db,
            profile,
            _SOURCE,
            "orders",
            workload_columns=set(),
            max_columns=1,
        )

        assert result.status == "success"
        assert result.data["profiled_columns"] == 0


# ── Regression: schema= must be passed to execute_sql ────────────────────


class TestSchemaArgPropagation:
    """Both sampling and profiling phases must forward ``schema=source.schema``
    to ``client.execute_sql`` so 3-level projects get
    ``odps.namespace.schema=true`` injected by ``build_hints`` and the
    3-part ``project.schema.table`` form parses.

    Regression for the smoke-CI bug where every per-table sampling /
    profiling query silently failed with "full qualified name ... is
    not supported", the phase returned partial_failure, the orchestrator
    discarded it, and the build summary reported ``errors: []``. Result:
    every column row in package.db had row_count=NULL / sample_values_json=NULL
    and downstream consumers (LLM context, join validation) had no data.
    """

    def test_sampling_passes_schema_to_execute_sql(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.execute_sql.return_value = Envelope.success({"rows": [], "row_count": 0})
        db = _make_db(tmp_path)
        profile = _make_profile(schema="sample_schema")
        source = profile.sources[0]
        db.upsert_table(source.source_key(), "legalities", schema_hash="abc")

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="3"):
            phase_column_sampling(client, db, profile, source, "legalities")

        assert client.execute_sql.called
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("schema") == "sample_schema", (
            "phase_column_sampling must forward schema=source.schema to "
            "execute_sql so 3-level fq_name parses"
        )

    def test_profiling_passes_schema_to_execute_sql(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.execute_sql.return_value = Envelope.success({"rows": []})
        db = _make_db(tmp_path)
        profile = _make_profile(schema="sample_schema")
        source = profile.sources[0]
        tid = db.upsert_table(source.source_key(), "legalities", schema_hash="abc")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "is_partition": 0}],
        )

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="3"):
            phase_column_profiling(
                client,
                db,
                profile,
                source,
                "legalities",
                workload_columns=set(),
            )

        assert client.execute_sql.called
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("schema") == "sample_schema", (
            "phase_column_profiling must forward schema=source.schema to "
            "execute_sql so 3-level fq_name parses"
        )


# ── Cross-tier connection-form / hint propagation ────────────────────────


def _make_xtier_profile(*, compute_project: str, src_project: str, src_schema: str) -> Profile:
    """Profile with a single cross-project source — used to exercise the
    sampling/profiling phases against the connection-tier matrix in
    ``DataSource.qualified_for_connection``."""
    return Profile(
        name="xtier",
        compute_project=compute_project,
        endpoint="https://odps.endpoint",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project=src_project, schema=src_schema, tables="*"),),
    )


class TestCrossTierConnectionForm:
    """Sampling and profiling must build the right ``FROM`` form and
    forward the right extra hints for every row of the cross-tier matrix
    in ``DataSource.qualified_for_connection``. The single-statement SQL
    these phases emit needs ``odps.namespace.schema=true`` only for the
    fourth row (2-level conn cross-reading a non-default 3-level schema);
    for every other row no extra hint is required.
    """

    def _run_sampling(
        self,
        tmp_path: Path,
        *,
        compute_project: str,
        src_project: str,
        src_schema: str,
        conn_tier: str,
    ) -> tuple[MagicMock, str]:
        client = MagicMock()
        client.execute_sql.return_value = Envelope.success({"rows": [], "row_count": 0})
        db = _make_db(tmp_path)
        profile = _make_xtier_profile(
            compute_project=compute_project,
            src_project=src_project,
            src_schema=src_schema,
        )
        source = profile.sources[0]
        db.upsert_table(source.source_key(), "orders", schema_hash="abc")

        with patch("maxcompute_semantic.build.phases.get_tier", return_value=conn_tier):
            phase_column_sampling(client, db, profile, source, "orders")

        assert client.execute_sql.called
        sql = client.execute_sql.call_args.args[0]
        return client, sql

    def test_sampling_3level_conn_uses_3segment_no_extra_hint(self, tmp_path: Path) -> None:
        """3-level connection: 3-segment FQN, no extra hints from
        ``connection_hints`` (the 3-level ``build_hints`` injection on the
        client side handles ``odps.namespace.schema=true``)."""
        client, sql = self._run_sampling(
            tmp_path,
            compute_project="cp",
            src_project="data_proj",
            src_schema="sales",
            conn_tier="3",
        )
        assert "data_proj.sales.orders" in sql
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("hints") == {}

    def test_sampling_2level_conn_same_project_uses_bare(self, tmp_path: Path) -> None:
        """2-level connection reading its own project: bare table name."""
        client, sql = self._run_sampling(
            tmp_path,
            compute_project="cp",
            src_project="cp",
            src_schema="default",
            conn_tier="2",
        )
        assert " FROM orders " in sql
        assert "cp.orders" not in sql
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("hints") == {}

    def test_sampling_2level_conn_xproj_default_schema_uses_2segment(self, tmp_path: Path) -> None:
        """2-level connection cross-reading another project's default
        schema: 2-segment ``other_proj.table``, no extra hints."""
        client, sql = self._run_sampling(
            tmp_path,
            compute_project="cp",
            src_project="other_proj",
            src_schema="default",
            conn_tier="2",
        )
        assert "other_proj.orders" in sql
        assert "other_proj.default.orders" not in sql
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("hints") == {}

    def test_sampling_2level_conn_xproj_non_default_schema_uses_3segment_with_hint(
        self, tmp_path: Path
    ) -> None:
        """2-level connection cross-reading a 3-level source's non-default
        schema: 3-segment FQN paired with ``odps.namespace.schema=true``
        passed via the ``hints=`` kwarg (no inline ``SET`` and no
        ``odps.sql.submit.mode=script`` — the pyodps API applies the hint
        to the single statement)."""
        client, sql = self._run_sampling(
            tmp_path,
            compute_project="cp",
            src_project="src_proj",
            src_schema="other_schema",
            conn_tier="2",
        )
        assert "src_proj.other_schema.orders" in sql
        kwargs = client.execute_sql.call_args.kwargs
        assert kwargs.get("hints") == {"odps.namespace.schema": "true"}

    def test_profiling_2level_conn_xproj_non_default_schema_passes_hint(
        self, tmp_path: Path
    ) -> None:
        """Same escape-hatch path through the profiling phase — the FK /
        approx-NDV query needs the hint at the same call site so the
        3-segment FQN parses on a 2-level connection."""
        client = MagicMock()
        client.execute_sql.return_value = Envelope.success({"rows": []})
        db = _make_db(tmp_path)
        profile = _make_xtier_profile(
            compute_project="cp",
            src_project="src_proj",
            src_schema="other_schema",
        )
        source = profile.sources[0]
        tid = db.upsert_table(source.source_key(), "account", schema_hash="abc")
        db.upsert_columns(
            tid,
            [{"name": "account_id", "type": "BIGINT", "is_partition": 0}],
        )

        with patch("maxcompute_semantic.build.phases.get_tier", return_value="2"):
            phase_column_profiling(client, db, profile, source, "account", workload_columns=set())

        assert client.execute_sql.called
        sql = client.execute_sql.call_args.args[0]
        kwargs = client.execute_sql.call_args.kwargs
        assert "src_proj.other_schema.account" in sql
        assert kwargs.get("hints") == {"odps.namespace.schema": "true"}


class TestSchemaMigrationV9:
    """Schema v9 adds tables.table_type. Migration must be additive and
    leave existing rows with NULL (conservative: legacy rows behave as
    tables until next refresh re-describes them)."""

    def test_migration_adds_table_type_column(self, tmp_path: Path) -> None:
        import sqlite3

        from maxcompute_semantic.build.storage import _migrate_v8_to_v9

        # Build a fresh DB at the previous (v8) version with one
        # pre-populated table row, then run the migration.
        path = tmp_path / "package.db"
        conn = sqlite3.connect(str(path))
        conn.execute("""
            CREATE TABLE tables (
                id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL,
                name TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                last_built_at TEXT NOT NULL,
                errors_json TEXT,
                ai_context TEXT DEFAULT NULL,
                UNIQUE(source_key, name)
            )
        """)
        conn.execute(
            "INSERT INTO tables (source_key, name, schema_hash, last_built_at) "
            "VALUES ('sk', 'legacy_tbl', 'h', '2026-05-24')"
        )
        conn.commit()

        _migrate_v8_to_v9(conn)

        # Column exists.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tables)").fetchall()]
        assert "table_type" in cols
        # Existing row is NULL (not 'MANAGED_TABLE' — that would be a
        # false claim about an object the migration never probed).
        row = conn.execute("SELECT table_type FROM tables WHERE name='legacy_tbl'").fetchone()
        assert row[0] is None
        conn.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        import sqlite3

        from maxcompute_semantic.build.storage import _migrate_v8_to_v9

        path = tmp_path / "package.db"
        conn = sqlite3.connect(str(path))
        conn.execute("""
            CREATE TABLE tables (
                id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL,
                name TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                last_built_at TEXT NOT NULL,
                table_type TEXT DEFAULT NULL
            )
        """)
        conn.commit()
        # Should not raise "duplicate column" — must be guarded by
        # pragma_table_info check.
        _migrate_v8_to_v9(conn)
        _migrate_v8_to_v9(conn)
        conn.close()


class TestUpsertTableType:
    """upsert_table accepts a table_type kwarg and persists it. Calling
    without the kwarg preserves the existing value (does not blank it on
    refresh of an already-typed row)."""

    def test_upsert_persists_table_type(self, tmp_path: Path) -> None:
        from maxcompute_semantic.build.storage import PackageDB

        db = PackageDB(tmp_path / "package.db")
        db.upsert_table("sk", "v1", schema_hash="h", table_type="VIRTUAL_VIEW")
        row = db.get_table("sk", "v1")
        assert row["table_type"] == "VIRTUAL_VIEW"

    def test_upsert_without_kwarg_preserves_existing(self, tmp_path: Path) -> None:
        from maxcompute_semantic.build.storage import PackageDB

        db = PackageDB(tmp_path / "package.db")
        db.upsert_table("sk", "t1", schema_hash="h1", table_type="MANAGED_TABLE")
        # Re-upsert without table_type kwarg (e.g. an error-recording
        # refresh path). The existing 'MANAGED_TABLE' value must survive.
        db.upsert_table("sk", "t1", schema_hash="h2")
        row = db.get_table("sk", "t1")
        assert row["table_type"] == "MANAGED_TABLE"

    def test_upsert_with_kwarg_overwrites(self, tmp_path: Path) -> None:
        from maxcompute_semantic.build.storage import PackageDB

        db = PackageDB(tmp_path / "package.db")
        # Object changed type (rare but legal: e.g. dropped+recreated
        # as a different object kind). Explicit kwarg overrides.
        db.upsert_table("sk", "t1", schema_hash="h1", table_type="MANAGED_TABLE")
        db.upsert_table("sk", "t1", schema_hash="h2", table_type="VIRTUAL_VIEW")
        row = db.get_table("sk", "t1")
        assert row["table_type"] == "VIRTUAL_VIEW"
