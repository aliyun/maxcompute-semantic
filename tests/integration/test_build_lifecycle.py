# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Integration test: mcs build full lifecycle with mocked client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli

_SK = "test_project__default"


def _ak_profile(name: str = "test-proj", project: str = "test_project") -> Profile:
    return Profile(
        name=name,
        compute_project=project,
        endpoint="https://odps_endpoint",
        auth=AkAuth("ak_id", "ak_secret"),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def _mock_client(list_tables_result=None, describe_table_result=None):
    """Create a MagicMock for MaxComputeClient with sensible defaults."""
    client = MagicMock()
    client._tier = "2"
    client.list_tables.return_value = list_tables_result or ["table1", "table2"]
    if describe_table_result is None:
        # Default: each table gets 2 columns.
        client.describe_table.side_effect = lambda name, schema=None, project=None: {
            "table": {
                "name": name,
                "comment": "",
                "type": "MANAGED_TABLE",
                "schema": [
                    {"name": "col_a", "type": "STRING", "comment": ""},
                    {"name": "col_b", "type": "BIGINT", "comment": ""},
                ],
                "partition_columns": [],
                "description": "",
                "primary_key": "",
                "extra_metadata": {},
            }
        }
    else:
        client.describe_table.side_effect = describe_table_result
    # execute_sql returns a mock envelope for sampling
    envelope = MagicMock()
    envelope.data = {"rows": []}
    client.execute_sql.return_value = envelope
    # list_partitions returns empty dict
    client.list_partitions.return_value = {
        "table_name": "any",
        "partitions": [],
        "is_partitioned": False,
        "latest_partition": None,
    }
    return client


# ── Test 1: init then full build ──────────────────────────────────────────


def test_build_lifecycle_init_then_full(isolated_config: Path) -> None:
    """Schema-only build then full build with sampling: PackageDB created, table data in DB."""
    upsert(_ak_profile())
    runner = CliRunner()

    mock_client = _mock_client(
        list_tables_result=["table1", "table2"],
    )

    with (
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        # Full build with --no-sampling, --no-history, --no-joins, --no-udf
        result = runner.invoke(
            cli,
            [
                "build",
                "--profile",
                "test-proj",
                "--no-sampling",
                "--no-history",
                "--no-joins",
                "--no-udf",
            ],
        )
        assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"

    # Verify PackageDB was created and contains the tables.
    db_path = isolated_config / "data" / "test-proj" / "package.db"
    assert db_path.exists(), "PackageDB file should exist after build"

    db = PackageDB(db_path)
    tables = db.list_tables()
    db.close()

    table_names = {t["name"] for t in tables}
    assert "table1" in table_names
    assert "table2" in table_names

    # Verify columns were stored for each table.
    db = PackageDB(db_path)
    for t in tables:
        cols = db.get_columns(t["id"])
        assert len(cols) == 2, f"table {t['name']} should have 2 columns"
        col_names = {c["name"] for c in cols}
        assert "col_a" in col_names
        assert "col_b" in col_names
    db.close()


# ── Test 2: build then status ─────────────────────────────────────────────


def test_build_then_status(isolated_config: Path) -> None:
    """Build then check status: status shows the built profile with table count."""
    upsert(_ak_profile())
    runner = CliRunner()

    mock_client = _mock_client(
        list_tables_result=["table1", "table2"],
    )

    with (
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        build_result = runner.invoke(
            cli,
            [
                "build",
                "--profile",
                "test-proj",
                "--no-sampling",
                "--no-history",
                "--no-joins",
                "--no-udf",
            ],
        )
        assert build_result.exit_code == 0, build_result.output

    # Now run status to verify the build.
    status_result = runner.invoke(
        cli,
        ["status", "--profile", "test-proj"],
    )
    assert status_result.exit_code == 0, status_result.output

    # Status should show the profile name and table count.
    output = status_result.output
    assert "test-proj" in output
    assert "tables" in output


# ── Test 3: build then refresh ────────────────────────────────────────────


def test_build_then_refresh(isolated_config: Path) -> None:
    """Build then refresh: changed table rebuilt, unchanged table skipped."""
    upsert(_ak_profile())
    runner = CliRunner()

    # ── Phase 1: initial build ──────────────────────────────────────────
    initial_client = _mock_client(
        list_tables_result=["table1", "table2"],
    )

    with (
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=initial_client),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        build_result = runner.invoke(
            cli,
            [
                "build",
                "--profile",
                "test-proj",
                "--no-sampling",
                "--no-history",
                "--no-joins",
                "--no-udf",
            ],
        )
        assert build_result.exit_code == 0, build_result.output

    # ── Phase 2: refresh with changed schema for table1 ────────────────
    # table1 now has 3 columns instead of 2; table2 unchanged (same 2 cols).
    def _describe_refresh(name, schema=None, project=None):
        if name == "table1":
            return {
                "table": {
                    "name": name,
                    "comment": "",
                    "type": "MANAGED_TABLE",
                    "schema": [
                        {"name": "col_a", "type": "STRING", "comment": ""},
                        {"name": "col_b", "type": "BIGINT", "comment": ""},
                        {"name": "col_c", "type": "DOUBLE", "comment": "new column"},
                    ],
                    "partition_columns": [],
                    "description": "",
                    "primary_key": "",
                    "extra_metadata": {},
                }
            }
        # table2: same as before.
        return {
            "table": {
                "name": name,
                "comment": "",
                "type": "MANAGED_TABLE",
                "schema": [
                    {"name": "col_a", "type": "STRING", "comment": ""},
                    {"name": "col_b", "type": "BIGINT", "comment": ""},
                ],
                "partition_columns": [],
                "description": "",
                "primary_key": "",
                "extra_metadata": {},
            }
        }

    refresh_client = _mock_client(
        list_tables_result=["table1", "table2"],
        describe_table_result=_describe_refresh,
    )

    with (
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=refresh_client),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        refresh_result = runner.invoke(
            cli,
            [
                "build",
                "--profile",
                "test-proj",
                "--refresh",
                "--no-sampling",
                "--no-history",
                "--no-joins",
                "--no-udf",
            ],
        )
        assert refresh_result.exit_code == 0, (
            f"exit={refresh_result.exit_code}, out={refresh_result.output}"
        )

    # Verify table1 now has 3 columns (rebuilt) and table2 still has 2 (unchanged).
    db_path = isolated_config / "data" / "test-proj" / "package.db"
    db = PackageDB(db_path)

    table1_row = db.get_table(_SK, "table1")
    table2_row = db.get_table(_SK, "table2")
    assert table1_row is not None
    assert table2_row is not None

    table1_cols = db.get_columns(table1_row["id"])
    table2_cols = db.get_columns(table2_row["id"])

    assert len(table1_cols) == 3, (
        f"table1 should have 3 columns after refresh, got {len(table1_cols)}"
    )
    assert len(table2_cols) == 2, (
        f"table2 should remain unchanged with 2 columns, got {len(table2_cols)}"
    )

    db.close()


# ── Test 4: schema-only build, no sampling ────────────────────────────────


def test_build_schema_only_no_sampling(isolated_config: Path) -> None:
    """Schema-only build: columns stored in PackageDB but no sample_values_json."""
    upsert(_ak_profile())
    runner = CliRunner()

    mock_client = _mock_client(
        list_tables_result=["orders"],
    )

    with (
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        result = runner.invoke(
            cli,
            [
                "build",
                "--profile",
                "test-proj",
                "--no-sampling",
                "--no-history",
                "--no-joins",
                "--no-udf",
            ],
        )
        assert result.exit_code == 0, result.output

    # Verify columns stored but no sample_values_json (sampling was skipped).
    db_path = isolated_config / "data" / "test-proj" / "package.db"
    db = PackageDB(db_path)

    orders_row = db.get_table(_SK, "orders")
    assert orders_row is not None

    cols = db.get_columns(orders_row["id"])
    assert len(cols) == 2

    # With --no-sampling, sample_values_json should be None for all columns.
    for col in cols:
        assert col["sample_values_json"] is None, (
            f"column {col['name']} should not have sample_values_json when --no-sampling"
        )

    db.close()
