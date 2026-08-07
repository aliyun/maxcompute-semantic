# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/info_schema.py — history source detection + mining."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.info_schema import (
    build_history_sql,
    detect_info_schema_source,
)


def _profile(project: str = "meta_dev") -> Profile:
    return Profile(
        name=project,
        compute_project=project,
        endpoint="http://x",
        auth=AkAuth("ak", "secret"),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def test_detect_tenant_success(tmp_path: Path) -> None:
    """Tenant-level probe succeeds -> source is 'tenant'."""
    client = MagicMock()
    client.execute_sql.return_value = MagicMock(status="success", data={})
    result = detect_info_schema_source(_profile(), client, cache_dir=tmp_path)
    assert result == "tenant"


def test_detect_project_fallback(tmp_path: Path) -> None:
    """Tenant fails, project succeeds -> source is 'project'."""
    client = MagicMock()
    # Tenant probe throws exception (NoPermission)
    from odps.errors import NoPermission

    tenant_exc = NoPermission("ODPS-0420111: Authorization failure")
    project_env = MagicMock(status="success", data={})
    client.execute_sql.side_effect = [tenant_exc, project_env]
    result = detect_info_schema_source(_profile(), client, cache_dir=tmp_path)
    assert result == "project"


def test_detect_none_both_fail(tmp_path: Path) -> None:
    """Both probes fail -> source is 'none'."""
    client = MagicMock()
    client.execute_sql.side_effect = Exception("connection refused")
    result = detect_info_schema_source(_profile(), client, cache_dir=tmp_path)
    assert result == "none"


def test_detect_cached_source(tmp_path: Path) -> None:
    """Cache file exists -> returns cached value without probing."""
    (tmp_path / ".info-schema-source").write_text("tenant")
    client = MagicMock()
    result = detect_info_schema_source(_profile(), client, cache_dir=tmp_path)
    assert result == "tenant"
    client.execute_sql.assert_not_called()


def test_detect_keys_on_compute_project_not_first_source(tmp_path: Path) -> None:
    """Multi-source profile where compute_project != sources[0].project must
    probe with compute_project, not sources[0].project.

    Both INFORMATION_SCHEMA forms (tenant SYSTEM_CATALOG view and bare
    project-form) resolve against the compute project. Keying the probe
    on the first DataSource (the pre-fix behavior) used a project name
    in the tenant probe's ``task_catalog = '...'`` filter that the AK
    may not have any rows for — turning a legitimate tenant-level
    access into an empty/failed probe and silently falling through to
    project form (or "none") for AKs that should have caught the
    tenant path.
    """
    profile = Profile(
        name="multi",
        compute_project="compute_proj",
        endpoint="http://x",
        auth=AkAuth("ak", "secret"),
        sources=(
            DataSource(project="data_proj_a", schema="default", tables="*"),
            DataSource(project="data_proj_b", schema="default", tables="*"),
        ),
    )
    client = MagicMock()
    client.execute_sql.return_value = MagicMock(status="success", data={})
    result = detect_info_schema_source(profile, client, cache_dir=tmp_path)
    assert result == "tenant"
    # The tenant probe SQL must filter on compute_proj, not data_proj_a.
    submitted_sql = client.execute_sql.call_args_list[0].args[0]
    assert "task_catalog = 'compute_proj'" in submitted_sql
    assert "data_proj_a" not in submitted_sql


def test_build_history_sql_tenant() -> None:
    sql = build_history_sql("meta_dev", source="tenant", lookback_days=14, limit=2000)
    assert "tasks_history" in sql.lower()
    assert "information_schema" in sql.lower()
    assert "task_catalog = 'meta_dev'" in sql


def test_build_history_sql_project() -> None:
    sql = build_history_sql("meta_dev", source="project", lookback_days=14, limit=2000)
    assert "tasks_history" in sql.lower()
    assert "SYSTEM_CATALOG" not in sql


def test_build_history_sql_rejects_unsafe_project() -> None:
    with pytest.raises(ValueError):
        build_history_sql("DROP TABLE; --", source="tenant")
