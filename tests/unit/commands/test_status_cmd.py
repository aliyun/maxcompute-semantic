# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/status.py — mcs status CLI command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from maxcompute_semantic.auth.link_store import set_link
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands.status import status_cmd

_SK = "test_project__default"


def _ak_profile(name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="test_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _invoke(args: list[str], obj: dict | None = None) -> object:
    runner = CliRunner()
    return runner.invoke(status_cmd, args, obj=obj)


def _setup_profile_with_data(isolated_config: Path) -> Path:
    """Create a profile + PackageDB with sample data for status tests."""
    p = Profile(
        name="sales-dw",
        compute_project="sales_dw",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="sales_dw", schema="default", tables="*"),),
    )
    upsert(p)
    set_link(str(Path.cwd()), "sales-dw")

    # Create profile data directory and PackageDB.
    from maxcompute_semantic._internal.paths import profile_data_dir

    # Profile is 1:1 with its package: package.db / _state.json live
    # directly under <profile>/. (The 0.3.0a8 per-schema sub-dir was
    # removed in the 2026-05-14 vocab cleanup.)
    profile_dir = profile_data_dir("sales-dw")
    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path = profile_dir / "package.db"
    db = PackageDB(db_path)

    # Insert tables.
    now_iso = datetime.now(timezone.utc).isoformat()
    tid1 = db.upsert_table(_SK, "orders", "hash1", errors_json=None)
    tid2 = db.upsert_table(_SK, "players", "hash2", errors_json=None)
    db.upsert_table(
        _SK,
        "restricted_tbl",
        "hash3",
        errors_json=json.dumps({"code": "PermissionDenied", "message": "access denied"}),
    )

    # Insert columns for orders (15 cols, 2 enum).
    cols_cg = [
        {"name": "game_type", "type": "STRING", "is_enum": 1},
        {"name": "game_id", "type": "STRING", "is_enum": 1},
        {"name": "col3", "type": "INT", "is_enum": 0},
    ] + [{"name": f"col{i}", "type": "INT", "is_enum": 0} for i in range(4, 16)]
    db.upsert_columns(tid1, cols_cg)

    # Insert columns for players (8 cols, 1 enum).
    cols_p = [{"name": "status", "type": "STRING", "is_enum": 1}] + [
        {"name": f"pcol{i}", "type": "INT", "is_enum": 0} for i in range(1, 8)
    ]
    db.upsert_columns(tid2, cols_p)

    # No columns for restricted_tbl (describe failed).

    # Insert UDFs.
    db.upsert_udf("my_udf1", kind="scalar", last_seen_at=now_iso)
    db.upsert_udf("my_udf2", kind="aggregate", last_seen_at=now_iso)
    db.upsert_udf("my_udf3", kind="table", last_seen_at=now_iso)

    # Insert a join.
    db.upsert_join(_SK, "orders", "game_id", _SK, "players", "player_id", "FK", 0.9, "1:N")
    db.close()

    # Write _state.json.
    state = {
        "version": 1,
        "tier": "3-level",
        "last_built_at": now_iso,
        "has_history": True,
        "per_table": {
            "orders": {"schema_hash": "hash1"},
            "players": {"schema_hash": "hash2"},
            "restricted_tbl": {"schema_hash": "hash3"},
        },
    }
    (profile_dir / "_state.json").write_text(json.dumps(state))

    return profile_dir


def test_status_no_build_shows_empty(isolated_config: Path) -> None:
    """Profile exists but no PackageDB -> shows 'no build data'."""
    p = _ak_profile()
    upsert(p)
    set_link(str(Path.cwd()), "test")

    result = _invoke([])

    assert result.exit_code == 0
    assert "no build data" in result.output


def test_status_shows_profile_info(isolated_config: Path) -> None:
    """PackageDB + _state.json -> displays profile name, project, table count, age."""
    _setup_profile_with_data(isolated_config)

    result = _invoke([])

    assert result.exit_code == 0
    output = result.output
    # Profile name.
    assert "sales-dw" in output
    # Project.
    assert "sales_dw" in output
    # Table count.
    assert "tables: 3" in output
    # Tier.
    assert "3-level" in output
    # UDFs.
    assert "udfs: 3" in output


def test_status_tables_flag(isolated_config: Path) -> None:
    """--tables shows per-table detail with column count, enum cols, date, status."""
    _setup_profile_with_data(isolated_config)

    result = _invoke(["--tables"])

    assert result.exit_code == 0
    output = result.output
    # Per-table entries.
    assert "orders" in output
    assert "players" in output
    assert "restricted_tbl" in output
    # Column counts.
    assert "15" in output
    assert "8" in output
    # Enum columns.
    assert "game_type" in output
    assert "status" in output
    # Status markers.
    assert "OK" in output
    assert "PermissionDenied" in output


def test_status_json_mode(isolated_config: Path) -> None:
    """-f json -> JSON envelope with status data."""
    _setup_profile_with_data(isolated_config)

    result = _invoke([], obj={"format": "json", "quiet": False})

    assert result.exit_code == 0
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["status"] == "success"
    data = payload["data"]
    assert data["profile"] == "sales-dw"
    assert data["compute_project"] == "sales_dw"
    assert data["tables"] == 3
    assert data["tier"] == "3-level"
    assert data["udfs"] == 3


def test_status_quiet(isolated_config: Path) -> None:
    """-q -> just profile name."""
    _setup_profile_with_data(isolated_config)

    result = _invoke([], obj={"format": "plain", "quiet": True})

    assert result.exit_code == 0
    # In quiet+plain mode, quiet_essential prints profile name.
    lines = [line for line in result.output.strip().splitlines() if line.strip()]
    assert lines == ["sales-dw"]


def test_status_tables_annotated_column_is_per_source(isolated_config: Path) -> None:
    """Two sources, both have a 'users' table. After annotating
    a__default.users but not b__default.users, --tables must show
    yes for the first and no for the second.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="ms-prof",
        compute_project="proj_a",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(
            DataSource(project="proj_a", schema="default", tables="*"),
            DataSource(project="proj_b", schema="default", tables="*"),
        ),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    tid_a = db.upsert_table("proj_a__default", "users", "ha")
    tid_b = db.upsert_table("proj_b__default", "users", "hb")
    db.upsert_columns(tid_a, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.upsert_columns(tid_b, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}])
    db.set_table_ai_context("proj_a__default", "users", "annotated source A")
    db.set_column_semantics("proj_a__default", "users", "id", role="identifier", id_type="primary")
    db.mark_build_complete("proj_a__default", ["users"])
    db.mark_build_complete("proj_b__default", ["users"])
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["-f", "json", "status", "--tables", "--profile", "ms-prof"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    details = envelope["data"]["table_details"]
    by_sk_name = {(d["source_key"], d["name"]): d["annotated"] for d in details}
    assert by_sk_name[("proj_a__default", "users")] == "yes"
    assert by_sk_name[("proj_b__default", "users")] == "no"


def test_status_tables_json_exposes_per_table_annotation_coverage(
    isolated_config: Path,
) -> None:
    """Runtime build/enrich skills depend on explicit coverage counters."""
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="coverage-prof",
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    tid = db.upsert_table("proj__default", "orders", "h1")
    db.upsert_columns(
        tid,
        [
            {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
            {"name": "amount", "type": "DOUBLE", "comment": "", "is_partition": 0},
        ],
    )
    db.set_table_ai_context("proj__default", "orders", "Order fact table.")
    db.set_column_semantics(
        "proj__default",
        "orders",
        "amount",
        role="measure",
        agg="SUM",
        semantic_description="Raw order amount.",
    )
    db.mark_build_complete("proj__default", ["orders"])
    db.close()

    result = CliRunner().invoke(
        cli,
        ["-f", "json", "status", "--tables", "--profile", "coverage-prof"],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    details = envelope["data"]["table_details"]
    assert len(details) == 1
    assert details[0]["has_ai_context"] is True
    assert details[0]["columns_with_description"] == 1
    assert details[0]["columns_annotated"] == 1
    assert details[0]["columns_total"] == 2


def test_status_tier_reflects_compute_project_sentinel(isolated_config: Path) -> None:
    """Multi-source profile where compute_project differs from
    sources[0].project: the summary tier must reflect the
    compute project's tier_cache sentinel, NOT the first source's
    state.json tier. The agent's SQL emission form (3-segment vs
    bare) hinges on the compute project's tier because that's what
    governs the session's namespace.schema behavior.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir, tier_cache_path
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="split-prof",
        compute_project="compute_proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="data_proj", schema="default", tables="*"),),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    # Write a sentinel for compute_proj saying it's 3-level, and a
    # state.json claiming the first source is 2-level. Status must
    # prefer the compute sentinel.
    sentinel = tier_cache_path(p, "compute_proj")
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("3", encoding="utf-8")
    (pdir / "_state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {"data_proj__default": {"tier": "2-level"}},
                "last_built_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    db = PackageDB(pdir / "package.db")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["-f", "json", "status", "--profile", "split-prof"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["tier"] == "3-level"


def test_status_tables_includes_table_type(isolated_config: Path) -> None:
    """--tables JSON payload must surface each table's table_type so
    users can see why VIRTUAL_VIEW / OBJECT_TABLE rows were skipped by
    the default no-sample/no-profile policy.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="tt-prof",
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    tid_managed = db.upsert_table("proj__default", "orders", "h1", table_type="MANAGED_TABLE")
    tid_view = db.upsert_table("proj__default", "orders_view", "h2", table_type="VIRTUAL_VIEW")
    db.upsert_columns(
        tid_managed, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}]
    )
    db.upsert_columns(
        tid_view, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}]
    )
    db.mark_build_complete("proj__default", ["orders", "orders_view"])
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["-f", "json", "status", "--tables", "--profile", "tt-prof"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    details = envelope["data"]["table_details"]
    by_name = {d["name"]: d["table_type"] for d in details}
    assert by_name["orders"] == "MANAGED_TABLE"
    assert by_name["orders_view"] == "VIRTUAL_VIEW"


def test_status_tables_null_table_type_renders_em_dash(isolated_config: Path) -> None:
    """Legacy pre-v9 builds have NULL table_type. --tables payload must
    render an em-dash for those rows, matching the file's missing-field
    convention (see how enum_str defaults).
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="legacy-prof",
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    # Omit table_type to simulate legacy NULL.
    tid = db.upsert_table("proj__default", "t", "h")
    db.upsert_columns(tid, [{"name": "c", "type": "INT", "comment": "", "is_partition": 0}])
    db.mark_build_complete("proj__default", ["t"])
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["-f", "json", "status", "--tables", "--profile", "legacy-prof"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    details = envelope["data"]["table_details"]
    assert len(details) == 1
    assert details[0]["table_type"] == "—"


def test_status_by_source_does_not_use_closed_db(isolated_config: Path) -> None:
    """Regression: --by-source previously called _emit_by_source after
    db.close(), which crashed the moment _emit_by_source tried to
    get_columns. Must work cleanly now.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.cli import cli

    p = Profile(
        name="bs-prof",
        compute_project="proj",
        endpoint="https://example.com",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )
    upsert(p)
    pdir = profile_data_dir(p)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    tid = db.upsert_table("proj__default", "t", "h")
    db.upsert_columns(tid, [{"name": "c", "type": "INT", "comment": "", "is_partition": 0}])
    db.mark_build_complete("proj__default", ["t"])
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--by-source", "--profile", "bs-prof"])
    assert result.exit_code == 0, result.output
