# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end: `mcs profile export <name> [--osi] --out FILE`.

Exercises both branches of the new dual-format export verb: the default
tar.gz path (must remain unchanged) and the new ``--osi`` YAML path
(emits an OSI-conformant semantic model that round-trips through
:func:`validate_all`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from ruamel.yaml import YAML

from maxcompute_semantic.auth import profile_store
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands.profile_export import export_cmd

from ._osi_validators import validate_all


@pytest.fixture
def cli_profile(tmp_path, monkeypatch):
    """Tmp HOME + a registered ``demo`` profile whose package data lives
    at ``<tmp_path>/pkg/package.db`` and carries the same two-table OSI
    fixture the rest of the osi/ suite uses (orders + customers + FK
    join + per-column semantics)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MCS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.delenv("MCS_PROFILE", raising=False)
    monkeypatch.delenv("MCS_PROFILES_DIR", raising=False)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pkg_dir / "package.db")

    orders_id = db.upsert_table(
        source_key="warehouse",
        name="orders",
        schema_hash="hash1",
        table_type="MANAGED_TABLE",
    )
    cust_id = db.upsert_table(
        source_key="warehouse",
        name="customers",
        schema_hash="hash2",
        table_type="MANAGED_TABLE",
    )
    db.upsert_columns(
        orders_id,
        [
            {"name": "order_id", "type": "BIGINT", "comment": "PK", "is_partition": 0},
            {
                "name": "customer_id",
                "type": "BIGINT",
                "comment": "FK to customers",
                "is_partition": 0,
            },
            {"name": "order_date", "type": "DATE", "comment": "When placed", "is_partition": 1},
        ],
    )
    db.upsert_columns(
        cust_id,
        [
            {"name": "id", "type": "BIGINT", "comment": "PK", "is_partition": 0},
            {"name": "name", "type": "STRING", "comment": "", "is_partition": 0},
        ],
    )
    db.set_table_ai_context("warehouse", "orders", "Order line items, one row per ordered SKU.")
    db.set_column_semantics(
        "warehouse",
        "customers",
        "id",
        role="identifier",
        id_type="primary",
    )
    db.set_column_semantics(
        "warehouse",
        "orders",
        "order_date",
        role="dimension",
        dim_type="time",
        semantic_description="Date the order was placed.",
    )
    db.set_column_semantics(
        "warehouse",
        "orders",
        "customer_id",
        role="identifier",
        id_type="foreign",
        references_target="customers.id",
    )
    db.upsert_join(
        left_source_key="warehouse",
        left_table="orders",
        left_col="customer_id",
        right_source_key="warehouse",
        right_table="customers",
        right_col="id",
        kind="fk",
        confidence=1.0,
        cardinality="many_to_one",
    )
    # Close before invoking the CLI so the CLI's own PackageDB
    # connection isn't fighting an open one (WAL is permissive, but
    # closing makes the test's intent unambiguous).
    db.close()

    profile = Profile(
        name="demo",
        compute_project="warehouse",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="warehouse", schema="default", tables="*"),),
        package_path=pkg_dir,
    )
    profile_store.upsert(profile)
    return profile


def test_export_osi_writes_valid_yaml(tmp_path, cli_profile):
    out_path = tmp_path / "demo.osi.yaml"
    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["demo", "--osi", "--output", str(out_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    parsed = YAML(typ="safe").load(out_path.read_text(encoding="utf-8"))
    errors = validate_all(parsed)
    assert errors == [], "OSI validation failed:\n" + "\n".join(errors)


def test_export_osi_creates_output_parent_directory(tmp_path, cli_profile):
    out_path = tmp_path / "nested" / "dir" / "demo.osi.yaml"
    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["demo", "--osi", "--output", str(out_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()


def test_export_osi_wraps_output_directory_failure(tmp_path, cli_profile, monkeypatch):
    out_path = tmp_path / "nested" / "dir" / "demo.osi.yaml"
    real_mkdir = Path.mkdir

    def fail_target_parent(self, *args, **kwargs):
        if self == out_path.parent:
            raise OSError("permission denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_target_parent)
    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["demo", "--osi", "--output", str(out_path)],
    )
    assert result.exit_code == 4, result.output
    assert "failed to write OSI YAML export" in result.output


def test_export_without_osi_still_writes_tarball(tmp_path, cli_profile):
    out_path = tmp_path / "demo.tar.gz"
    runner = CliRunner()
    result = runner.invoke(
        export_cmd,
        ["demo", "--output", str(out_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    # gzip magic bytes: tar.gz format
    assert out_path.read_bytes()[:2] == b"\x1f\x8b"
