# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cross-project scenarios: same / valid-cross / unauthorized-cross.

These tests focus on the error-classification surface -- the actual SQL
execution paths are mocked. The point is that the agent always gets a
clear classified error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from odps import errors as odps_errors

from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.errors import (
    PermissionDeniedError,
    TableNotFoundError,
)


def _profile(project: str) -> Profile:
    # Disable the cost gate — these tests focus on error classification
    # along the cross-project axis and mock odps directly, so a MagicMock
    # returned by execute_sql_cost would crash on numeric comparison.
    return Profile(
        name=project,
        compute_project=project,
        endpoint="http://x",
        auth=AkAuth("ak", "secret"),
        sources=(DataSource(project=project, schema="default", tables="*"),),
        cost_thresholds=CostThresholds(enabled=False),
    )


def test_same_project_query_succeeds(isolated_config: Path) -> None:
    """Profile bound to project A, SQL FROM project_A.t -> works."""
    c = MaxComputeClient(_profile("project_A"))
    fake_odps = MagicMock()
    fake_instance = MagicMock()
    reader = MagicMock()
    reader.__enter__ = lambda self: self
    reader.__exit__ = lambda *a: None
    reader.schema.columns = []
    reader.__iter__ = MagicMock(return_value=iter([]))
    fake_instance.open_reader.return_value = reader
    fake_instance.get_logview_address.return_value = "http://logview/x"
    fake_odps.run_sql.return_value = fake_instance
    with patch.object(c, "_ensure_odps", return_value=fake_odps):
        env = c.execute_sql("SELECT * FROM project_A.t LIMIT 0")
    assert env.to_dict()["status"] == "success"


def test_cross_project_authorized_succeeds(isolated_config: Path) -> None:
    """Profile bound to project A with authorization for project B -> works."""
    c = MaxComputeClient(_profile("project_A"))
    fake_odps = MagicMock()
    fake_instance = MagicMock()
    reader = MagicMock()
    reader.__enter__ = lambda self: self
    reader.__exit__ = lambda *a: None
    reader.schema.columns = []
    reader.__iter__ = MagicMock(return_value=iter([]))
    fake_instance.open_reader.return_value = reader
    fake_instance.get_logview_address.return_value = "http://logview/x"
    fake_odps.run_sql.return_value = fake_instance
    with patch.object(c, "_ensure_odps", return_value=fake_odps):
        env = c.execute_sql("SELECT * FROM project_B.t LIMIT 0")
    assert env.to_dict()["status"] == "success"


def test_cross_project_unauthorized_raises_permission_denied(isolated_config: Path) -> None:
    """Profile bound to project A, no auth for project B -> PermissionDeniedTable."""
    c = MaxComputeClient(_profile("project_A"))
    fake_odps = MagicMock()
    exc = odps_errors.NoPermission("ODPS-0130013: Access Denied - SELECT on Table 'project_B.t'")
    exc.code = "NoPermission"
    fake_odps.run_sql.side_effect = exc
    with (
        patch.object(c, "_ensure_odps", return_value=fake_odps),
        pytest.raises(PermissionDeniedError),
    ):
        c.execute_sql("SELECT * FROM project_B.t LIMIT 0")


def test_cross_project_not_found_raises_table_not_found(isolated_config: Path) -> None:
    """Profile bound to project A, queries non-existent project B -> TableNotFound."""
    c = MaxComputeClient(_profile("project_A"))
    fake_odps = MagicMock()
    exc = odps_errors.NoSuchObject("ODPS-0130131: Table not found - 'project_B.t'")
    exc.code = "NoSuchObject"
    fake_odps.run_sql.side_effect = exc
    with patch.object(c, "_ensure_odps", return_value=fake_odps), pytest.raises(TableNotFoundError):
        c.execute_sql("SELECT * FROM project_B.t LIMIT 0")
