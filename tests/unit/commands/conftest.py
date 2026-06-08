# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Fixtures shared by the T8 ``versioning``-wiring test files.

The per-verb test files (``test_build_versioning.py``,
``test_annotate_versioning.py``, ``test_memory_versioning.py``,
``test_udf_versioning.py``, ``test_profile_import_versioning.py``,
``test_no_hook_on_yaml_only_commands.py``) all need the same
two-step starting state:

1.  An XDG-isolated config tree (provided by the package-level
    ``isolated_config`` fixture in ``tests/conftest.py``).
2.  A *versioned* profile — one whose data-dir is already a git repo
    carrying the ``init: import existing data`` inaugural commit.
    This is the state that a fresh ``mcs profile create --no-test``
    leaves on disk; the T6 plumbing landed in ``versioning/hook.py``
    creates the ``.git/`` automatically when the first
    ``commit_after_command`` runs.

The ``versioned_profile`` fixture spins up that starting state with
a minimal valid profile spec via the non-interactive ``--from-spec
--no-test`` entry point — same wiring the T6 test file
(``test_profile_create_versioning.py``) uses, lifted into a shared
fixture so the per-verb T8 tests don't each have to redo the
bootstrap.

The ``fake_maxcompute`` fixture is a thin wrapper around the
existing MagicMock-based recipe from
``tests/integration/test_build_lifecycle.py``; it patches the three
``commands.build`` import sites (``MaxComputeClient``,
``resolve_credentials``, ``get_tier``) so the build / annotate /
memory test invocations don't reach the network. Each fixture
yields the ``unittest.mock.patch`` context manager exits cleanly
on test teardown.
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

# The T6 / T7 tests already skip themselves when ``git`` isn't on
# PATH because the contracts they pin require a live ``git`` binary.
# Every T8 test file does the same; the module-level skip mark below
# is re-exported via ``pytest_collection_modifyitems`` so any test
# file that imports this conftest's fixtures gets the skip mark for
# free without having to add a top-of-file ``pytestmark`` line.
_REQUIRES_GIT = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile git versioning requires the ``git`` binary on PATH",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the git-binary skip mark to every test in this directory
    whose name carries the ``_versioning`` substring. The
    non-versioning tests (e.g. ``test_memory_cmd.py``) don't depend
    on git being available, so we don't blanket-mark every test
    under ``tests/unit/commands/`` — only the ones that exercise
    the per-profile repo plumbing."""
    if shutil.which("git") is not None:
        return
    for item in items:
        # ``item.path`` is the file path on Python 3.9+; the substring
        # check matches both the ``_versioning`` suffix and the anti-
        # test file (``test_no_hook_on_yaml_only_commands.py``) which
        # also asserts on the per-profile repo.
        name = item.path.name
        if "_versioning" in name or "test_no_hook_on_yaml_only_commands" in name:
            item.add_marker(_REQUIRES_GIT)


def _canonical_spec(name: str, compute_project: str = "acme_proj") -> str:
    """Minimal valid full-profile spec matching the shape the T6
    tests use. Keeps the spec narrow — single source, no annotations,
    no cost thresholds beyond the schema defaults — so the bootstrap
    is fast and the per-verb tests are the ones to add fixtures for
    table data."""
    import json

    return json.dumps(
        {
            "name": name,
            "compute_project": compute_project,
            "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
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
    """Create a fresh profile via ``mcs profile create --from-spec
    --no-test`` and return the resolved ``Profile`` object. The
    profile's data-dir is a git repo with the canonical inaugural
    commit (``init: import existing data``) — the standard starting
    state every T8 verb test asserts the *next* commit against."""
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    name = "t8test"
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
    """Patch the three ``commands.build`` import sites that touch the
    network. The returned ``MagicMock`` is the client instance the
    patched ``MaxComputeClient(...)`` constructor will return; tests
    that need to customize ``list_tables`` / ``describe_table`` /
    ``execute_sql`` can poke at it before invoking the CLI.

    The default seed is the same shape the integration test uses
    (``test_build_lifecycle.py``): two tables (``table1`` / ``table2``)
    each with two columns (``col_a`` STRING, ``col_b`` BIGINT) and
    no partitions. Tests that need different table lists override
    by setting ``client.list_tables.return_value = [...]`` before
    invoking the CLI verb."""
    client = MagicMock()
    client._tier = "2"
    client.list_tables.return_value = ["table1", "table2"]
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
