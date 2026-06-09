"""mcs udf's commit_after_command wiring (T8).

Three write verbs under ``udf`` share the ``udf:`` action prefix:

  * ``udf create``           — ``udf: create <name>``
  * ``udf remove``           — ``udf: remove <name>``
  * ``udf resource remove``  — ``udf: resource-remove <name>``

These tests pin the prefix and the per-verb summary. The UDF verbs
need a live MaxComputeClient (for ``execute_sql`` / ``drop_function``
/ ``drop_resource``) and a PackageDB; we monkeypatch
``ProfileContext.resolve`` and ``MaxComputeClient`` so the verb body
runs end-to-end without hitting the network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _head_subject(profile: Profile) -> str:
    repo = GitRepo(profile_data_dir(profile))
    rows = repo.log(limit=None)
    assert rows, "expected at least one commit"
    return rows[0].message


@pytest.fixture
def fake_udf_runtime(versioned_profile: Profile, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``ProfileContext.resolve``, ``ProfileContext.open_db``,
    and ``MaxComputeClient`` so the verb body runs end-to-end without
    hitting the network.

    The returned MagicMock is the client instance; tests can poke its
    ``execute_sql`` / ``_ensure_odps`` return values for verb-specific
    behavior. The default ``_ensure_odps`` returns a MagicMock with
    ``drop_function`` / ``drop_resource`` / ``get_function`` configured
    to succeed silently.
    """
    fake_client = MagicMock()
    fake_client.execute_sql.return_value = MagicMock()
    fake_odps = MagicMock()
    fake_func = MagicMock()
    fake_func.resources = []
    fake_odps.get_function.return_value = fake_func
    fake_odps.drop_function.return_value = None
    fake_odps.drop_resource.return_value = None
    fake_client._ensure_odps.return_value = fake_odps

    @classmethod  # type: ignore[misc]
    def _fake_resolve(
        cls,
        *,
        profile_name=None,
        project=None,
        schema=None,
        renderer=None,
    ):
        return ProfileContext(
            profile=versioned_profile,
            project_override=project,
            schema_override=schema,
            renderer=renderer or Renderer(),
        )

    def _fake_open_db(self):
        db_path = profile_data_dir(versioned_profile) / "package.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return PackageDB(db_path)

    monkeypatch.setattr(ProfileContext, "resolve", _fake_resolve)
    monkeypatch.setattr(ProfileContext, "open_db", _fake_open_db)
    monkeypatch.setattr(
        "maxcompute_semantic.commands.udf.MaxComputeClient",
        lambda prof: fake_client,
    )
    return fake_client


def test_udf_create_commits_with_udf_prefix(
    versioned_profile: Profile, fake_udf_runtime: MagicMock, tmp_path: Path
) -> None:
    """``mcs udf create`` lands a ``udf: create <name>`` commit."""
    # Write a tiny inline-python script. The verb only cares about
    # the class name extracted from the file content.
    script = tmp_path / "my_udf.py"
    script.write_text(
        "from odps.udf import annotate\n"
        "\n"
        "@annotate('string->string')\n"
        "class MyUdf(object):\n"
        "    def evaluate(self, x):\n"
        "        return x\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "udf",
            "create",
            "my_udf",
            "--inline-python",
            str(script),
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _head_subject(versioned_profile) == "udf: create my_udf"


def test_udf_remove_commits_with_udf_prefix(
    versioned_profile: Profile, fake_udf_runtime: MagicMock
) -> None:
    """``mcs udf remove`` lands a ``udf: remove <name>`` commit."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "udf",
            "remove",
            "doomed_udf",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _head_subject(versioned_profile) == "udf: remove doomed_udf"


def test_udf_resource_remove_commits_with_udf_prefix(
    versioned_profile: Profile, fake_udf_runtime: MagicMock
) -> None:
    """``mcs udf resource remove`` lands a ``udf: resource-remove <name>``
    commit. resource-remove doesn't touch ``package.db`` directly but
    the hook still fires to leave a traceable timeline entry; the
    package.sql dump captures the udfs/resources tables exactly as-is."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "udf",
            "resource",
            "remove",
            "my_resource.jar",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _head_subject(versioned_profile) == "udf: resource-remove my_resource.jar"


def test_udf_create_no_versioning_env_suppresses_commit(
    versioned_profile: Profile,
    fake_udf_runtime: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``MCS_NO_VERSIONING=1`` suppresses the udf-create commit; only
    the inaugural commit remains."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    script = tmp_path / "u.py"
    script.write_text(
        "class U(object):\n    def evaluate(self, x):\n        return x\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "udf",
            "create",
            "u",
            "--inline-python",
            str(script),
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output

    repo = GitRepo(profile_data_dir(versioned_profile))
    rows = repo.log(limit=None)
    assert len(rows) == 1
    assert rows[0].message == "init: import existing data"
