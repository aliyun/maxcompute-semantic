# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/build.py — mcs build CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.pipeline import BuildSummary
from maxcompute_semantic.commands.build import build_cmd

_RESOLVE = "maxcompute_semantic.commands.build.resolve_profile_for_project"


def _ak_profile(name: str = "test") -> Profile:
    return Profile(
        name=name,
        compute_project="test_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _invoke(isolated_config: Path, args: list[str], obj: dict | None = None) -> object:
    runner = CliRunner()
    return runner.invoke(build_cmd, args, obj=obj)


def _make_mock_pipeline_return(summary: BuildSummary | None = None) -> BuildSummary:
    """Create a default BuildSummary for mocking."""
    if summary is None:
        summary = BuildSummary(
            tables_built=3,
            tables_skipped=0,
            tables_new=0,
            tables_changed=0,
            tables_removed=0,
            tables_unchanged=0,
            phases_skipped=["history"],
            errors=[],
            warnings=[],
        )
    return summary


def test_build_no_profile_raises_error(isolated_config: Path) -> None:
    """Invoke mcs build with no profiles -> exit code 3 (NoProfilesConfiguredError)."""
    from maxcompute_semantic.auth.errors import NoProfilesConfiguredError

    with patch(
        _RESOLVE,
        side_effect=NoProfilesConfiguredError("no profiles", remediation=""),
    ):
        result = _invoke(isolated_config, [])
    assert result.exit_code == 3


def test_build_json_mode(isolated_config: Path) -> None:
    """Invoke mcs build --profile test -f json --no-sampling -> verify JSON envelope."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 5
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--no-sampling"], obj={"format": "json"})

    assert result.exit_code == 0
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["status"] == "success"
    assert "build_summary" in payload["data"] or "tables_built" in payload["data"]
    assert payload["data"]["tables_built"] == 3
    assert payload["data"]["annotation_suggestions_count"] == 5


def test_build_no_history_flag(isolated_config: Path) -> None:
    """Invoke mcs build --no-history -> verify no_history=True in BuildOptions."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return(
        BuildSummary(
            tables_built=2,
            tables_skipped=0,
            tables_new=0,
            tables_changed=0,
            tables_removed=0,
            tables_unchanged=0,
            phases_skipped=["history"],
            errors=[],
            warnings=[],
        )
    )

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--no-history"])

    assert result.exit_code == 0
    # Verify BuildOptions had no_history=True.
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.no_history is True
    assert "history" in summary.phases_skipped


def test_build_mcs_no_history_env(isolated_config: Path, monkeypatch) -> None:
    """Set MCS_NO_HISTORY=1 env var -> verify no_history defaults to True."""
    monkeypatch.setenv("MCS_NO_HISTORY", "1")
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = BuildSummary(
        tables_built=2,
        tables_skipped=0,
        tables_new=0,
        tables_changed=0,
        tables_removed=0,
        tables_unchanged=0,
        phases_skipped=["history"],
        errors=[],
        warnings=[],
    )

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, [])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.no_history is True


def test_build_tables_filter(isolated_config: Path) -> None:
    """Invoke mcs build --tables t1,t2 -> verify only those tables built."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--tables", "t1,t2"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.tables_filter == ["t1", "t2"]


def test_build_success_with_mocked_client(isolated_config: Path) -> None:
    """Mock client, all skip flags -> exit 0, PackageDB created."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = BuildSummary(
        tables_built=0,
        tables_skipped=0,
        tables_new=0,
        tables_changed=0,
        tables_removed=0,
        tables_unchanged=0,
        phases_skipped=["history", "sampling", "udf", "joins"],
        errors=[],
        warnings=[],
    )

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(
            isolated_config,
            ["--no-sampling", "--no-history", "--no-joins", "--no-udf"],
        )

    assert result.exit_code == 0
    # PackageDB was instantiated.
    assert mock_db_cls.called
    # BuildPipeline was called with the right options.
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.no_sampling is True
    assert opts.no_history is True
    assert opts.no_joins is True
    assert opts.no_udf is True


def test_build_credential_failure_exits_4(isolated_config: Path) -> None:
    """Credential resolution failure propagates AuthBinaryMissingError.exit_code (4)."""
    from maxcompute_semantic.auth.errors import AuthBinaryMissingError

    upsert(_ak_profile())
    fake_profile = _ak_profile()
    cred_error = AuthBinaryMissingError("binary missing", remediation="install ncs")

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", side_effect=cred_error),
    ):
        result = _invoke(isolated_config, [])

    assert result.exit_code == 4


def test_build_tier_probe_failure_exits(isolated_config: Path) -> None:
    """Tier probe failure propagates EndpointUnreachableError.exit_code (1)."""
    from maxcompute_semantic.mc_client.errors import EndpointUnreachableError

    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    tier_error = EndpointUnreachableError("unreachable", remediation="check endpoint")

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", side_effect=tier_error),
    ):
        result = _invoke(isolated_config, [])

    assert result.exit_code == 1


def test_build_pipeline_error_exits_1(isolated_config: Path) -> None:
    """BuildPipeline failure propagates BuildPhaseError.exit_code (1)."""
    from maxcompute_semantic.build.errors import BuildPhaseError

    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    build_error = BuildPhaseError("list_tables failed", remediation="check project")

    mock_db_inst = MagicMock()
    mock_db_inst.reindex_vectors.return_value = -1
    mock_db_inst.generate_package_docs.return_value = 0
    mock_db_inst.list_memories.return_value = []
    mock_db_inst.count_annotation_suggestions.return_value = 0

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.PackageDB", return_value=mock_db_inst),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.side_effect = build_error
        mock_pipeline_cls.return_value = mock_pipeline_inst

        result = _invoke(isolated_config, [])

    assert result.exit_code == 1


def test_build_generates_package_docs(isolated_config: Path) -> None:
    """After build, generate_package_docs is called and memory_count appears in output."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = BuildSummary(
        tables_built=2,
        tables_skipped=0,
        tables_new=0,
        tables_changed=0,
        tables_removed=0,
        tables_unchanged=0,
        memory_count=0,
        phases_skipped=["history"],
        errors=[],
        warnings=[],
    )

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
        patch("maxcompute_semantic.commands.build.generate_package_docs") as mock_gen,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst
        mock_gen.return_value = 2

        result = _invoke(isolated_config, ["--no-sampling", "--no-history"])

    assert result.exit_code == 0
    mock_gen.assert_called_once_with(mock_db_inst)
    # memory_count should appear in output (plain text or JSON).
    assert "2" in result.output


def test_build_refresh_flag(isolated_config: Path) -> None:
    """Invoke mcs build --refresh -> verify refresh=True + summary counts."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = BuildSummary(
        tables_built=1,
        tables_skipped=0,
        tables_new=0,
        tables_changed=1,
        tables_removed=0,
        tables_unchanged=2,
        phases_skipped=["history"],
        errors=[],
        warnings=[],
    )

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--refresh"])

    assert result.exit_code == 0
    # Verify BuildOptions had refresh=True.
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.refresh is True
    # Verify summary has refresh-specific fields.
    assert summary.tables_changed == 1
    assert summary.tables_unchanged == 2


def test_build_skips_vector_reindex_by_default(isolated_config: Path) -> None:
    """Without --with-vectors, db.reindex_vectors is not called."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
        patch("maxcompute_semantic.commands.build.generate_package_docs", return_value=0),
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, [])

    assert result.exit_code == 0
    mock_db_inst.reindex_vectors.assert_not_called()


def test_build_with_vectors_reindexes_vectors(isolated_config: Path) -> None:
    """With --with-vectors, db.reindex_vectors is called and vector_count appears in JSON."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
        patch("maxcompute_semantic.commands.build.generate_package_docs", return_value=0),
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = 7
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--with-vectors"], obj={"format": "json"})

    assert result.exit_code == 0
    mock_db_inst.reindex_vectors.assert_called_once()
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["data"]["vector_count"] == 7


def test_build_include_views_flag(isolated_config: Path) -> None:
    """Invoke mcs build --include-views -> verify include_views=True in BuildOptions."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--include-views"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.include_views is True


def test_build_include_views_default_false(isolated_config: Path) -> None:
    """Default (no --include-views flag) -> include_views=False in BuildOptions."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, [])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.include_views is False


def test_build_parallel_flag_plumbs_into_build_options(isolated_config: Path) -> None:
    """``--parallel 8`` -> BuildOptions(parallel=8)."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--parallel", "8"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.parallel == 8


def test_build_parallel_flag_default_is_auto(isolated_config: Path) -> None:
    """No ``--parallel`` flag -> BuildOptions(parallel=None) (auto)."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, [])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.parallel is None


def test_build_parallel_auto_string_accepted(isolated_config: Path) -> None:
    """``--parallel auto`` -> BuildOptions(parallel=None)."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--parallel", "auto"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.parallel is None


def test_build_parallel_invalid_value_exits_nonzero(isolated_config: Path) -> None:
    """``--parallel garbage`` fails fast with a clear error, before any
    profile resolution or client construction."""
    upsert(_ak_profile())
    result = _invoke(isolated_config, ["--parallel", "garbage"])
    assert result.exit_code != 0


def test_build_fresh_flag_plumbs_into_build_options(isolated_config: Path) -> None:
    """``--fresh`` -> BuildOptions(fresh=True); default is False."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--fresh"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.fresh is True


def test_build_refresh_min_age_hours_plumbs_into_build_options(
    isolated_config: Path,
) -> None:
    """``--refresh-min-age-hours 6`` -> BuildOptions(refresh_min_age_hours=6.0);
    default is 24.0."""
    upsert(_ak_profile())
    fake_profile = _ak_profile()
    fake_creds = MagicMock()
    mock_client = MagicMock()
    summary = _make_mock_pipeline_return()

    with (
        patch(_RESOLVE, return_value=fake_profile),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands.build.BuildPipeline") as mock_pipeline_cls,
        patch("maxcompute_semantic.commands.build.PackageDB") as mock_db_cls,
    ):
        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run.return_value = summary
        mock_pipeline_cls.return_value = mock_pipeline_inst
        mock_db_inst = MagicMock()
        mock_db_inst.reindex_vectors.return_value = -1
        mock_db_inst.generate_package_docs.return_value = 0
        mock_db_inst.list_memories.return_value = []
        mock_db_inst.count_annotation_suggestions.return_value = 0
        mock_db_cls.return_value = mock_db_inst

        result = _invoke(isolated_config, ["--refresh-min-age-hours", "6"])

    assert result.exit_code == 0
    call_args = mock_pipeline_cls.call_args
    opts = call_args[0][3] if len(call_args[0]) >= 4 else call_args.kwargs.get("opts")
    assert opts.refresh_min_age_hours == 6.0


def test_build_refresh_min_age_hours_rejects_negative(
    isolated_config: Path,
) -> None:
    upsert(_ak_profile())
    with patch(
        _RESOLVE,
        side_effect=AssertionError("profile resolution should not run"),
    ) as mock_resolve:
        result = _invoke(isolated_config, ["--refresh-min-age-hours=-1"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output
    mock_resolve.assert_not_called()


def test_build_tier_3_multi_source_no_schema_raises_schema_required(
    isolated_config: Path,
) -> None:
    """Tier-3 build + multi-source profile + no ``--schema`` flag
    must hit the unified ``SchemaRequiredError`` (exit 2,
    ``code="SchemaRequired"``) routed through Renderer.error.

    Pre-unification ``mcs build`` plain-text-failed via
    ``click.echo`` + ``sys.exit(2)``; the typed exception lets the
    failure surface in the same envelope the rest of the verbs use,
    with a remediation that names the available source schemas.
    """
    multi_source = Profile(
        name="multi",
        compute_project="test_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(
            DataSource(project="test_project", schema="alpha", tables="*"),
            DataSource(project="test_project", schema="beta", tables="*"),
        ),
    )
    upsert(multi_source)
    fake_creds = MagicMock()
    mock_client = MagicMock()

    with (
        patch(_RESOLVE, return_value=multi_source),
        patch("maxcompute_semantic.commands.build.resolve_credentials", return_value=fake_creds),
        patch("maxcompute_semantic.commands.build.MaxComputeClient", return_value=mock_client),
        patch("maxcompute_semantic.commands.build.get_tier", return_value="3"),
    ):
        result = _invoke(isolated_config, [], obj={"format": "json"})

    assert result.exit_code == 2
    payload = json.loads(result.output.strip().split("\n")[-1])
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "SchemaRequired"
    assert "alpha" in payload["error"]["remediation"]
    assert "beta" in payload["error"]["remediation"]
