# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for integration tests of the per-profile git-
versioning surface (T21).

Mirrors the fixture family from ``tests/unit/commands/conftest.py``
— ``versioned_profile`` spins up a freshly-created profile whose
data-dir is already a git repo with the ``init: import existing
data`` inaugural commit; ``fake_maxcompute`` patches the three
``commands.build`` import sites (``MaxComputeClient`` /
``resolve_credentials`` / ``get_tier``) so build invocations don't
reach the network. Lifted to a sibling conftest at the integration
root so the lifecycle test gets both fixtures via standard pytest
auto-discovery — no explicit ``from ... import`` needed at the
test-file top.

The two fixtures are duplicated rather than shared from the unit
conftest because (1) pytest conftest discovery doesn't pull from
sibling directories, and (2) the duplication is short (~30 lines
each) and the integration variant may diverge over time (e.g.
extra fixtures for the ``customers`` table the lifecycle test
needs that the per-verb unit tests don't).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.commands.profile import profile_group

# The git binary is a hard prereq for every versioning test. Same
# pattern as the unit-level conftest: file-name substring routing
# means the lifecycle test gets the skip mark without needing a
# top-of-file pytestmark line.
_REQUIRES_GIT = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile git versioning requires the ``git`` binary on PATH",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if shutil.which("git") is not None:
        return
    for item in items:
        if item.path.name == "test_versioning_lifecycle.py":
            item.add_marker(_REQUIRES_GIT)


def _canonical_spec(name: str, compute_project: str = "acme_proj") -> str:
    """Same minimal valid full-profile spec the unit conftest uses,
    with the same ``acme_proj`` default so the per-source key
    (``acme_proj__default``) is identical across the two trees.
    """
    import json

    return json.dumps(
        {
            "name": name,
            "compute_project": compute_project,
            "endpoint": "http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:MY_AK_ID}",
                "access_key_secret": "${env:MY_AK_SEC}",
            },
            "sources": [
                {"project": compute_project, "schema": "default", "tables": "*"},
            ],
        }
    )


@pytest.fixture
def versioned_profile(isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> Profile:
    """Profile created via ``mcs profile create --from-spec --no-test``
    — the data-dir is a git repo with the inaugural ``init: import
    existing data`` commit. Every lifecycle assertion against the
    git log starts from this baseline state.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    name = "acme"
    runner = CliRunner()
    result = runner.invoke(
        profile_group,
        ["create", "--from-spec", _canonical_spec(name), "--no-test"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, (
        f"versioned_profile fixture's `mcs profile create` failed; "
        f"output: {result.output!r}, exception: "
        f"{getattr(result, 'exception', None)!r}"
    )
    return get_profile(name)


@pytest.fixture
def fake_maxcompute() -> Iterator[MagicMock]:
    """Patch the three network-touching import sites in
    ``commands.build``. Default seed: two tables (``customers`` /
    ``orders``) each with two columns; the lifecycle test uses the
    ``customers`` table for the annotate / fork-write / diff arc.
    """
    client = MagicMock()
    client._tier = "2"
    client.list_tables.return_value = ["customers", "orders"]
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
    envelope = MagicMock()
    envelope.data = {"rows": []}
    client.execute_sql.return_value = envelope
    client.list_partitions.return_value = {
        "table_name": "any",
        "partitions": [],
        "is_partitioned": False,
        "latest_partition": None,
    }

    with (
        patch(
            "maxcompute_semantic.commands.build.MaxComputeClient",
            return_value=client,
        ),
        patch("maxcompute_semantic.commands.build.resolve_credentials"),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
    ):
        yield client
