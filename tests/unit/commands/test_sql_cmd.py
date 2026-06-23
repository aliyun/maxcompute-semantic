# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/sql.py (the ``mcs sql`` verb group of
``execute`` / ``cost`` / ``explain``) and the freestanding
``commands.meta`` module's ``meta_group`` (the eight catalog-
discovery verbs that were promoted out of the ``mcs sql meta``
sub-group hierarchy in the post-v0.4 CLI cleanup). The tests
for the meta verbs still live in this file for now — moving
them to a new ``test_meta_cmd.py`` is the obvious follow-up
but adds churn without a feature-level reason.

Two click-runner helpers below: ``_invoke`` targets the
``sql_group`` and is what the SQL-execution tests use; the
sibling ``_invoke_meta`` targets the new top-level
``meta_group`` and is what every meta-verb test in this file
now uses. Pre-cleanup the meta tests reached the meta sub-group
via ``runner.invoke(sql_group, ["meta", verb, ...])``; that
argv shape returns click's exit-2 ``no such command`` since
``sql_group`` has no ``meta`` child any more, hence the helper
split.


Mocks MaxComputeClient methods so no live MaxCompute needed. Verifies:
  - 3-level SET hints are applied via build_hints
  - 2-level SQL passthrough
  - cost verdict calculation (ok / confirm / blocked)
  - meta list-tables / describe-table JSON output contract
  - schema defaults for 3-level vs 2-level meta commands
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from maxcompute_semantic.cli import cli
from maxcompute_semantic.commands.meta import meta_group
from maxcompute_semantic.commands.sql import sql_group
from maxcompute_semantic.mc_client.envelope import Envelope


def _invoke(args: list[str], obj: dict | None = None) -> object:
    """Click-runner shim for the ``mcs sql`` verb group.

    Targets the ``sql_group`` directly — the meta sub-group that
    sat under ``sql`` pre-cleanup is gone, so the catalog-discovery
    tests use the sibling ``_invoke_meta`` helper below against the
    freestanding ``meta_group``.
    """
    runner = CliRunner()
    return runner.invoke(sql_group, args, obj=obj or {})


def _invoke_cli(args: list[str]) -> object:
    """Click-runner shim for the real root CLI.

    Used when a test needs to assert stdout/stderr behavior with global flags
    like ``-f json``.
    """
    runner = CliRunner()
    return runner.invoke(cli, args)


def _invoke_meta(args: list[str], obj: dict | None = None) -> object:
    """Click-runner shim for the top-level ``mcs meta`` verb group.

    The eight catalog-discovery verbs that pre-v0.4 sat under
    ``mcs sql meta`` as a sub-group and (in the case of
    ``list-projects`` / ``list-schemas``) under the ``mcs profile``
    group as siblings of ``profile show`` / ``profile list``, all
    live under one freestanding top-level group now in
    ``commands.meta``. The post-cleanup test-side argv shape is
    the bare verb name plus its options / args — no leading
    ``"meta"`` element, since the runner-target is the meta-group
    itself, not a parent ``cli`` that would route ``"meta"`` down
    into it.

    Mirrors the existing ``_invoke`` helper above, which still
    targets ``sql_group`` for the ``execute`` / ``cost`` /
    ``explain`` tests.
    """
    runner = CliRunner()
    return runner.invoke(meta_group, args, obj=obj or {})


def _mock_profile(name: str = "my_proj", project: str = "my_proj"):
    """Create a mock Profile."""
    from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile

    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="test_ak", access_key_secret="test_secret"),
        cost_thresholds=CostThresholds(),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def _mock_client(profile=None):
    """Create a mock MaxComputeClient."""
    if profile is None:
        profile = _mock_profile()
    client = MagicMock()
    client.profile = profile
    client._tier = None
    return client


def _patch_profile_and_client(mock_profile, mock_client):
    """Return a single patch for _make_client_for_project."""
    return [
        patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ),
    ]


# ── sql execute ──────────────────────────────────────────────────────────────


class TestSqlExecute:
    """Tests for mcs sql execute."""

    def test_2level_execute_success(self, isolated_config: Path) -> None:
        """2-level project should execute SQL and output Envelope."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        patches = _patch_profile_and_client(mock_profile, mock_client)
        patches.append(patch("maxcompute_semantic.commands.sql.get_tier", return_value="2"))
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"

    def test_3level_execute_applies_hints(self, isolated_config: Path) -> None:
        """3-level project must forward ``schema=`` so the client's
        ``build_hints`` injects the namespace/default-schema pair."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_envelope = Envelope.success({"rows": [], "schema": []})
        mock_client.execute_sql.return_value = mock_envelope

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "my_schema",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        # The CLI must pass schema= through; the client constructs the
        # hints dict from it (verified separately in mc_client tests).
        call_kwargs = mock_client.execute_sql.call_args
        assert call_kwargs.kwargs.get("schema") == "my_schema"
        # Default assume_yes is False — TTY prompt path stays on.
        assert call_kwargs.kwargs.get("assume_yes") is False

    def test_execute_yes_flag_forwards_assume_yes(self, isolated_config: Path) -> None:
        """``--yes`` / ``-y`` must propagate to ``client.execute_sql`` so
        the cost gate bypasses the TTY prompt. The agent/CI surface
        relies on this — otherwise a confirm-verdict job would hang."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_envelope = Envelope.success({"rows": [], "schema": []})
        mock_client.execute_sql.return_value = mock_envelope

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "--yes",
                    "SELECT 1",
                ]
            )

        assert result.exit_code == 0
        call_kwargs = mock_client.execute_sql.call_args
        assert call_kwargs.kwargs.get("assume_yes") is True

    def test_execute_max_rows_forwards_result_cap(self, isolated_config: Path) -> None:
        """``--max-rows`` caps rows returned by the result reader only."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope.success({"rows": [], "schema": []})

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "--max-rows",
                    "250",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        call_kwargs = mock_client.execute_sql.call_args
        assert call_kwargs.kwargs.get("max_rows") == 250

    def test_execute_offset_forwards_result_window_start(self, isolated_config: Path) -> None:
        """``--offset`` starts the returned result window without rewriting SQL."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope.success({"rows": [], "schema": []})

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "--offset",
                    "10000",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        call_kwargs = mock_client.execute_sql.call_args
        assert call_kwargs.kwargs.get("result_offset") == 10000

    def test_execute_timeout_flag_forwards_to_client(self, isolated_config: Path) -> None:
        """``--timeout`` propagates to ``client.execute_sql``."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope.success({"rows": [], "schema": []})

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "--timeout",
                    "5",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        assert mock_client.execute_sql.call_args.kwargs.get("timeout") == 5

    def test_execute_timeout_defaults_to_30s(self, isolated_config: Path) -> None:
        """The synchronous wait defaults to 30s (was 120s) when --timeout is omitted."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope.success({"rows": [], "schema": []})

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT 1"]
            )

        assert result.exit_code == 0
        assert mock_client.execute_sql.call_args.kwargs.get("timeout") == 30

    def test_execute_sync_timeout_hands_off_to_async(self, isolated_config: Path) -> None:
        """When the sync wait elapses, `execute` must not fail: it hands the
        still-running instance off to the async lifecycle, emitting
        ``sync_timed_out`` + ``instance_id`` + ``next_step`` so the agent
        continues with ``sql wait`` / ``sql result`` instead of resubmitting."""
        from maxcompute_semantic.errors import TimeoutError as McsTimeoutError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.side_effect = McsTimeoutError(
            "SQL execution exceeded the synchronous 30s wait; the instance is still running",
            remediation="poll with mcs sql wait",
            sql="SELECT * FROM t",
            instance_id="20260622083000_inst_001",
            logview_url="http://logview/xyz",
        )
        mock_client.get_instance_status.return_value = {
            "instance_id": "20260622083000_inst_001",
            "lifecycle_state": "running",
            "terminal": False,
            "logview_url": "http://logview/xyz",
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT * FROM t"]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        data = output["data"]
        assert data["sync_timed_out"] is True
        assert data["instance_id"] == "20260622083000_inst_001"
        # next_step carries the real instance_id plus the routed --project so it
        # is copy-pasteable even for a multi-source profile.
        assert "20260622083000_inst_001" in data["next_step"]
        assert "mcs sql wait" in data["next_step"]
        assert "--project" in data["next_step"]
        # It must reuse the running instance, not resubmit the query.
        mock_client.get_instance_status.assert_called_once_with("20260622083000_inst_001")

    def test_execute_sync_timeout_fallback_when_status_probe_fails(
        self, isolated_config: Path
    ) -> None:
        """When get_instance_status throws after a sync timeout, the CLI must
        still emit a success envelope with the instance_id from the error
        context (the defensive except branch in execute_cmd)."""
        from maxcompute_semantic.errors import TimeoutError as McsTimeoutError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.side_effect = McsTimeoutError(
            "exceeded 30s",
            remediation="poll",
            sql="SELECT 1",
            instance_id="inst_status_fail",
            logview_url="http://logview/abc",
        )
        mock_client.get_instance_status.side_effect = RuntimeError("network down")

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT 1"]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        data = output["data"]
        assert data["sync_timed_out"] is True
        assert data["instance_id"] == "inst_status_fail"
        assert data["logview_url"] == "http://logview/abc"

    def test_execute_sync_timeout_no_instance_id_falls_through_to_error(
        self, isolated_config: Path
    ) -> None:
        """When the timeout error carries no instance_id, the CLI must fall
        through to the normal error path (line 457 coverage)."""
        from maxcompute_semantic.errors import TimeoutError as McsTimeoutError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.side_effect = McsTimeoutError(
            "exceeded 30s",
            remediation="poll",
            sql="SELECT 1",
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT 1"]
            )

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_execute_failure_outputs_error_envelope(self, isolated_config: Path) -> None:
        """On execution failure, output error envelope and exit 1."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import SyntaxErrorMcs

        mock_client.execute_sql.side_effect = SyntaxErrorMcs(
            "parse error", remediation="check syntax"
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    # --allow-write bypasses the write-op guard so this
                    # test exercises the downstream SyntaxErrorMcs path
                    # (the SQL string is intentionally unparseable to
                    # express test intent; the actual error envelope is
                    # produced by the mocked client.execute_sql).
                    "--allow-write",
                    "INVALID SQL",
                ]
            )

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_execute_profile_resolve_failure(self, isolated_config: Path) -> None:
        """Client/profile setup failures stay on the SQL JSON stdout seam."""
        from maxcompute_semantic.auth.errors import ProfileNotFoundError

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            side_effect=ProfileNotFoundError("profile not found"),
        ):
            result = _invoke(
                [
                    "execute",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT 1",
                ]
            )

        assert result.exit_code == 3
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "ProfileNotFound"

    def test_execute_missing_profile_root_cli_writes_json_to_stdout(
        self, isolated_config: Path
    ) -> None:
        result = _invoke_cli(
            ["-f", "json", "sql", "execute", "--profile", "definitely_missing", "SELECT 1"]
        )

        assert result.exit_code == 3
        assert json.loads(result.stdout)["error"]["code"] == "ProfileNotFound"
        assert result.stderr == ""


# ── sql async lifecycle ─────────────────────────────────────────────────────


class TestSqlAsync:
    """Tests for the async MaxCompute instance lifecycle under mcs sql."""

    def test_submit_returns_instance_status_envelope(self, isolated_config: Path) -> None:
        """submit should return an instance id immediately without reading rows."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.run_sql_async.return_value = "inst_123"
        mock_client.get_instance_status.return_value = {
            "instance_id": "inst_123",
            "status": "Running",
            "start_time": None,
            "end_time": None,
            "name": "",
            "logview_url": "http://logview.example.com/inst_123",
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "submit",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "--yes",
                    "SELECT 1",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["instance_id"] == "inst_123"
        assert output["data"]["status"] == "Running"
        mock_client.run_sql_async.assert_called_once_with(
            "SELECT 1", schema="default", hints=None, assume_yes=True, allow_write=False
        )
        mock_client.execute_sql.assert_not_called()

    def test_submit_rejects_write_without_allow_write(self, isolated_config: Path) -> None:
        """submit keeps the same read-only default as execute."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            resolve_profile_for_project=MagicMock(return_value=mock_profile),
        ):
            result = _invoke(
                [
                    "submit",
                    "--project",
                    "my_proj",
                    "INSERT INTO t VALUES (1)",
                ]
            )

        assert result.exit_code == 2
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "WriteOpRejected"
        mock_client.run_sql_async.assert_not_called()

    def test_submit_returns_instance_id_when_status_probe_fails(
        self, isolated_config: Path
    ) -> None:
        """Once submit returns an id, a status probe failure must not hide it."""
        from maxcompute_semantic.mc_client.errors import McsError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.run_sql_async.return_value = "inst_123"
        mock_client.get_instance_status.side_effect = McsError(
            "status reload failed",
            code="Unknown",
            remediation="run `mcs sql status inst_123` later",
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "submit",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT 1",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["instance_id"] == "inst_123"
        assert output["data"]["status"] == "Submitted"
        assert output["data"]["status_probe_error"]["code"] == "Unknown"

    def test_status_returns_instance_status(self, isolated_config: Path) -> None:
        """status should reload and emit the current instance status."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.get_instance_status.return_value = {
            "instance_id": "inst_123",
            "status": "Terminated",
            "start_time": None,
            "end_time": None,
            "name": "sql_task",
            "logview_url": "http://logview.example.com/inst_123",
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(["status", "--project", "my_proj", "inst_123"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["status"] == "Terminated"
        mock_client.get_instance_status.assert_called_once_with("inst_123")

    def test_wait_forwards_timeout_and_interval(self, isolated_config: Path) -> None:
        """wait should poll through the client with caller-provided limits."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.wait_for_instance.return_value = {
            "instance_id": "inst_123",
            "status": "Terminated",
            "start_time": None,
            "end_time": None,
            "name": "",
            "logview_url": "http://logview.example.com/inst_123",
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(
                [
                    "wait",
                    "--project",
                    "my_proj",
                    "--timeout",
                    "30",
                    "--interval",
                    "1",
                    "inst_123",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["status"] == "Terminated"
        mock_client.wait_for_instance.assert_called_once_with("inst_123", timeout=30, interval=1)

    @pytest.mark.parametrize(
        "args",
        [
            ["--timeout", "0", "inst_123"],
            ["--interval", "0", "inst_123"],
        ],
    )
    def test_wait_rejects_non_positive_limits(self, isolated_config: Path, args: list[str]) -> None:
        with patch("maxcompute_semantic.commands.sql.make_client_for_project") as make_client:
            result = _invoke(["wait", "--project", "my_proj", *args])

        assert result.exit_code == 2
        assert "Invalid value" in result.output
        make_client.assert_not_called()

    def test_result_reads_instance_rows(self, isolated_config: Path) -> None:
        """result should emit the stored instance result rows."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.get_instance_result.return_value = {
            "instance_id": "inst_123",
            "schema": [{"name": "cnt", "type": "BIGINT"}],
            "rows": [{"cnt": 1}],
            "row_count": 1,
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(["result", "--project", "my_proj", "inst_123"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["row_count"] == 1
        assert output["data"]["rows"] == [{"cnt": 1}]
        mock_client.get_instance_result.assert_called_once_with(
            "inst_123", max_rows=None, result_offset=0
        )

    def test_result_forwards_max_rows(self, isolated_config: Path) -> None:
        """result should pass --max-rows to the reader path."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.get_instance_result.return_value = {
            "instance_id": "inst_123",
            "schema": [{"name": "cnt", "type": "BIGINT"}],
            "rows": [{"cnt": 1}],
            "row_count": 1,
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(["result", "--project", "my_proj", "--max-rows", "500", "inst_123"])

        assert result.exit_code == 0
        mock_client.get_instance_result.assert_called_once_with(
            "inst_123", max_rows=500, result_offset=0
        )

    def test_result_forwards_offset(self, isolated_config: Path) -> None:
        """result should pass --offset to the reader path."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.get_instance_result.return_value = {
            "instance_id": "inst_123",
            "schema": [{"name": "cnt", "type": "BIGINT"}],
            "rows": [{"cnt": 2}],
            "row_count": 1,
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(["result", "--project", "my_proj", "--offset", "10000", "inst_123"])

        assert result.exit_code == 0
        mock_client.get_instance_result.assert_called_once_with(
            "inst_123", max_rows=None, result_offset=10000
        )

    def test_cancel_stops_instance(self, isolated_config: Path) -> None:
        """cancel should stop the instance and emit the cancellation payload."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cancel_instance.return_value = {
            "instance_id": "inst_123",
            "cancelled": True,
        }

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            return_value=mock_client,
        ):
            result = _invoke(["cancel", "--project", "my_proj", "inst_123"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["cancelled"] is True
        mock_client.cancel_instance.assert_called_once_with("inst_123")


# ── sql cost ─────────────────────────────────────────────────────────────────


class TestSqlCost:
    """Tests for mcs sql cost."""

    def test_ok_verdict(self, isolated_config: Path) -> None:
        """Cost < 10 CNY should give 'ok' verdict."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        # 1 GB = 0.3 CNY → ok
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 1073741824,
            "estimated_cost_cny": 0.3,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM small_table",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        data = output["data"]
        assert data["verdict"] == "ok"
        assert data["estimated_cost_cny"] == 0.3

    def test_confirm_verdict(self, isolated_config: Path) -> None:
        """Cost between 10-100 CNY should give 'confirm' verdict."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 35791394133,
            "estimated_cost_cny": 15.0,
            "verdict": "confirm",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM medium_table",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["verdict"] == "confirm"

    def test_blocked_verdict(self, isolated_config: Path) -> None:
        """Cost >= 100 CNY should give 'blocked' verdict."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 357913941333,
            "estimated_cost_cny": 150.0,
            "verdict": "blocked",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM huge_table",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["verdict"] == "blocked"

    def test_cost_strips_set_and_passes_hints(self, isolated_config: Path) -> None:
        """SET key=val is extracted to a hint; cost_estimate gets the stripped
        SELECT + the SET as hints (so execute_sql_cost never sees the SET)."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SET odps.sql.mapper.split.size = 4096; SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0, result.output
        call = mock_client.cost_estimate.call_args
        assert call.args[0] == "SELECT * FROM t"
        assert call.kwargs.get("hints") == {"odps.sql.mapper.split.size": "4096"}

    def test_3level_cost_applies_hints(self, isolated_config: Path) -> None:
        """3-level project cost must forward ``schema=`` so the client
        builds namespace/default-schema hints internally."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "my_schema",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        call_kwargs = mock_client.cost_estimate.call_args
        assert call_kwargs.kwargs.get("schema") == "my_schema"

    def test_cost_failure_outputs_error_envelope(self, isolated_config: Path) -> None:
        """On cost estimation failure, output error envelope and exit 1."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import ProjectNotFoundError

        mock_client.cost_estimate.side_effect = ProjectNotFoundError(
            "project not found", remediation="check project name"
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_cost_client_setup_failure_outputs_json(self, isolated_config: Path) -> None:
        """Errors before cost_estimate() still use the SQL stdout envelope."""
        from maxcompute_semantic.mc_client.errors import ProjectNotFoundError

        with patch(
            "maxcompute_semantic.commands.sql.make_client_for_project",
            side_effect=ProjectNotFoundError("project not found", remediation="check project"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT 1",
                ],
                obj={"format": "json"},
            )

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "ProjectNotFound"

    def test_zero_bytes_gives_ok(self, isolated_config: Path) -> None:
        """Zero estimated bytes should give 'ok' verdict with 0.0 CNY."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT 1",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["verdict"] == "ok"
        assert output["data"]["estimated_cost_cny"] == 0.0

    def test_json_output_has_all_required_keys(self, isolated_config: Path) -> None:
        """Cost output should have all required keys in the JSON envelope."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 1073741824,
            "estimated_cost_cny": 0.3,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "cost",
                    "--project",
                    "my_proj",
                    "--schema",
                    "default",
                    "SELECT * FROM t",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        data = output["data"]
        assert "estimated_input_bytes" in data
        assert "estimated_cost_cny" in data
        assert "verdict" in data
        assert "thresholds" in data


# ── sql explain ──────────────────────────────────────────────────────────────


class TestSqlExplain:
    """Tests for mcs sql explain."""

    def test_2level_explain_success(self, isolated_config: Path) -> None:
        """2-level project explain returns plan text."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.explain.return_value = {
            "plan": "plan text here",
            "logview_url": "http://logview.example.com/123",
            "elapsed_ms": 500,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["explain", "--project", "my_proj", "SELECT * FROM t"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["plan"] == "plan text here"
        assert output["data"]["logview_url"] == "http://logview.example.com/123"

    def test_3level_explain_applies_hints(self, isolated_config: Path) -> None:
        """3-level project explain must forward ``schema=``."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.explain.return_value = {
            "plan": "plan text",
            "logview_url": "http://logview.example.com/123",
            "elapsed_ms": 500,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(
                ["explain", "--project", "my_proj", "--schema", "my_schema", "SELECT * FROM t"]
            )

        assert result.exit_code == 0
        call_kwargs = mock_client.explain.call_args
        assert call_kwargs.kwargs.get("schema") == "my_schema"

    def test_3level_explain_default_schema(self, isolated_config: Path) -> None:
        """3-level project without --schema resolves via the
        single-source profile's schema (``"default"`` in the
        ``_mock_profile`` fixture). Pre-unification ``explain`` silently
        coerced ``None`` → ``"default"`` regardless of profile shape;
        the unified resolver now lets the profile drive that fallback
        and raises ``SchemaRequiredError`` on multi-source — exercised
        separately in :class:`TestSchemaRequiredErrorPerCommand`."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.explain.return_value = {
            "plan": "plan",
            "logview_url": "http://logview.example.com/123",
            "elapsed_ms": 500,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(["explain", "--project", "my_proj", "SELECT * FROM t"])

        assert result.exit_code == 0
        call_kwargs = mock_client.explain.call_args
        assert call_kwargs.kwargs.get("schema") == "default"

    def test_explain_error_envelope(self, isolated_config: Path) -> None:
        """On explain failure, output error envelope and exit 1."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import SyntaxErrorMcs

        mock_client.explain.side_effect = SyntaxErrorMcs("parse error", remediation="check syntax")

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["explain", "--project", "my_proj", "INVALID SQL"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_explain_with_custom_timeout(self, isolated_config: Path) -> None:
        """Custom --timeout is passed to explain."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.explain.return_value = {
            "plan": "plan",
            "logview_url": "http://logview.example.com/123",
            "elapsed_ms": 500,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["explain", "--project", "my_proj", "--timeout", "30", "SELECT 1"])

        assert result.exit_code == 0
        call_kwargs = mock_client.explain.call_args
        assert call_kwargs.kwargs.get("timeout") == 30

    def test_explain_missing_profile_root_cli_writes_json_to_stdout(
        self, isolated_config: Path
    ) -> None:
        result = _invoke_cli(
            ["-f", "json", "sql", "explain", "--profile", "definitely_missing", "SELECT 1"]
        )

        assert result.exit_code == 3
        assert json.loads(result.stdout)["error"]["code"] == "ProfileNotFound"
        assert result.stderr == ""


# ── sql meta list-tables ────────────────────────────────────────────────────


class TestSqlMetaListTables:
    """Tests for mcs meta list-tables."""

    def test_2level_list_tables(self, isolated_config: Path) -> None:
        """2-level project lists all tables; no schema arg needed."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_tables.return_value = ["t1", "t2", "t3"]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["tables"] == ["t1", "t2", "t3"]
        # 2-level: schema arg NOT passed to client.
        mock_client.list_tables.assert_called_once_with(schema=None, project="my_proj")

    def test_3level_list_tables_with_schema(self, isolated_config: Path) -> None:
        """3-level project passes schema to list_tables."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_tables.return_value = ["s1_t1", "s1_t2"]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj", "--schema", "my_schema"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["tables"] == ["s1_t1", "s1_t2"]
        mock_client.list_tables.assert_called_once_with(schema="my_schema", project="my_proj")

    def test_3level_list_tables_default_schema(self, isolated_config: Path) -> None:
        """3-level project without --schema resolves via the
        single-source profile's schema (``"default"`` in the
        ``_mock_profile`` fixture). Multi-source case is covered in
        :class:`TestSchemaRequiredErrorPerCommand`."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_tables.return_value = ["d1", "d2"]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj"])

        assert result.exit_code == 0
        mock_client.list_tables.assert_called_once_with(schema="default", project="my_proj")

    def test_list_tables_error_envelope(self, isolated_config: Path) -> None:
        """On error, output failure envelope and exit 1."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import ProjectNotFoundError

        mock_client.list_tables.side_effect = ProjectNotFoundError(
            "project not found", remediation="check project name"
        )

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj"])

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_cross_project_uses_target_project_tier(self, isolated_config: Path) -> None:
        """`--project P` (P != compute_project) probes P's tier, not compute's.

        Regression: earlier all meta callsites passed
        ``client.profile.compute_project`` to ``get_tier``, which broke
        cross-project meta calls when the data source's tier diverged
        from the connection's compute project.
        """
        mock_profile = _mock_profile(name="compute", project="compute")
        mock_client = _mock_client(mock_profile)
        mock_client.list_tables.return_value = ["t1"]
        get_tier_mock = MagicMock(return_value="3")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=get_tier_mock,
        ):
            result = _invoke_meta(["list-tables", "--project", "data_proj", "--schema", "s1"])

        assert result.exit_code == 0
        # The first positional arg to get_tier is profile; second is target project.
        args, kwargs = get_tier_mock.call_args
        assert args[1] == "data_proj"

    def test_single_source_auto_fills_project_from_source(self, isolated_config: Path) -> None:
        """Without --project, meta verbs use the source project, not compute_project.

        Regression: earlier meta verbs fell back to ``client.profile.compute_project``
        when ``--project`` was omitted, which breaks profiles where the data source
        lives in a different project than the compute project.
        """
        from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile

        profile = Profile(
            name="test",
            compute_project="compute_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(DataSource(project="data_proj", schema="default", tables="*"),),
        )
        mock_client = _mock_client(profile)
        mock_client.list_tables.return_value = ["t1"]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-tables"])

        assert result.exit_code == 0
        mock_client.list_tables.assert_called_once_with(schema=None, project="data_proj")

    def test_multi_source_auto_fills_from_first_source(self, isolated_config: Path) -> None:
        """Multi-source profile: without --project, use the first source's project."""
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_tables.return_value = ["t1"]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-tables", "--schema", "alpha"])

        assert result.exit_code == 0
        mock_client.list_tables.assert_called_once_with(schema="alpha", project="my_proj")


# ── sql meta describe-table ─────────────────────────────────────────────────


class TestSqlMetaDescribeTable:
    """Tests for mcs meta describe-table."""

    def test_2level_describe_table(self, isolated_config: Path) -> None:
        """2-level project describes a table; no schema arg needed."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.describe_table.return_value = {
            "table": {
                "name": "my_table",
                "comment": "",
                "type": "MANAGED_TABLE",
                "schema": {
                    "columns": [
                        {"name": "id", "type": "BIGINT", "comment": ""},
                        {"name": "name", "type": "STRING", "comment": ""},
                    ],
                    "partition_columns": [],
                },
            }
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "my_table"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["table"]["name"] == "my_table"
        assert len(output["data"]["table"]["schema"]["columns"]) == 2
        # 2-level: schema NOT passed to client.
        mock_client.describe_table.assert_called_once_with(
            "my_table", schema=None, project="my_proj"
        )

    def test_3level_describe_table_with_schema(self, isolated_config: Path) -> None:
        """3-level project passes schema to describe_table."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.describe_table.return_value = {
            "table": {
                "name": "my_table",
                "comment": "",
                "type": "MANAGED_TABLE",
                "schema": {
                    "columns": [{"name": "id", "type": "BIGINT", "comment": ""}],
                    "partition_columns": [
                        {"name": "ds", "type": "STRING", "comment": ""},
                    ],
                },
            }
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(
                [
                    "describe-table",
                    "--project",
                    "my_proj",
                    "--schema",
                    "my_schema",
                    "my_table",
                ]
            )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["table"]["name"] == "my_table"
        assert len(output["data"]["table"]["schema"]["partition_columns"]) == 1
        mock_client.describe_table.assert_called_once_with(
            "my_table", schema="my_schema", project="my_proj"
        )

    def test_3level_describe_table_default_schema(self, isolated_config: Path) -> None:
        """3-level project without --schema resolves via the
        single-source profile's schema (``"default"`` in the
        ``_mock_profile`` fixture). Multi-source case is covered in
        :class:`TestSchemaRequiredErrorPerCommand`."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.describe_table.return_value = {
            "table": {
                "name": "my_table",
                "comment": "",
                "type": "MANAGED_TABLE",
                "schema": {"columns": [], "partition_columns": []},
            }
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "my_table"])

        assert result.exit_code == 0
        mock_client.describe_table.assert_called_once_with(
            "my_table", schema="default", project="my_proj"
        )

    def test_describe_table_error_envelope(self, isolated_config: Path) -> None:
        """On error, output failure envelope and exit 1."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import TableNotFoundError

        mock_client.describe_table.side_effect = TableNotFoundError(
            "table not found", remediation="check table name"
        )

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "nonexistent"])

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_describe_table_output_shape_matches_envelope(self, isolated_config: Path) -> None:
        """Output shape should match mcs meta describe-table envelope."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.describe_table.return_value = {
            "table": {
                "name": "orders",
                "comment": "order data",
                "type": "MANAGED_TABLE",
                "schema": {
                    "columns": [
                        {"name": "order_id", "type": "BIGINT", "comment": "pk"},
                        {"name": "amount", "type": "DOUBLE", "comment": ""},
                    ],
                    "partition_columns": [
                        {"name": "ds", "type": "STRING", "comment": "date partition"},
                    ],
                },
            }
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "orders"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        data = output["data"]
        # Verify the envelope shape.
        assert "table" in data
        assert "schema" in data["table"]
        assert "columns" in data["table"]["schema"]
        assert "partition_columns" in data["table"]["schema"]
        assert "name" in data["table"]
        assert "comment" in data["table"]
        assert "type" in data["table"]


# ── sql meta search-tables ────────────────────────────────────────────────


class TestSqlMetaSearchTables:
    """Tests for mcs meta search-tables."""

    def test_search_tables_success(self, isolated_config: Path) -> None:
        """Search tables returns results with count."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_tables.return_value = [
            {
                "table_name": "orders",
                "description": "Order data",
                "score": 5,
                "matched_columns": [],
            },
        ]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["search-tables", "--project", "my_proj", "order"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["count"] == 1
        assert output["data"]["results"][0]["table_name"] == "orders"

    def test_search_tables_3level_schema(self, isolated_config: Path) -> None:
        """3-level project passes schema to search_tables."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_tables.return_value = []

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(
                ["search-tables", "--project", "my_proj", "--schema", "mys", "test"]
            )

        assert result.exit_code == 0
        mock_client.search_tables.assert_called_once_with("test", schema="mys", project="my_proj")

    def test_search_tables_error(self, isolated_config: Path) -> None:
        """On error, output failure envelope."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        from maxcompute_semantic.mc_client.errors import ProjectNotFoundError

        mock_client.search_tables.side_effect = ProjectNotFoundError("no project")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["search-tables", "--project", "my_proj", "foo"])

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"


# ── sql meta search-columns ──────────────────────────────────────────────


class TestSqlMetaSearchColumns:
    """Tests for mcs meta search-columns."""

    def test_search_columns_success(self, isolated_config: Path) -> None:
        """Search columns returns column-level results."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_columns.return_value = [
            {
                "table_name": "orders",
                "column_name": "order_id",
                "type": "BIGINT",
                "comment": "",
                "score": 8,
            },
        ]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["search-columns", "--project", "my_proj", "order_id"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 1
        assert output["data"]["results"][0]["column_name"] == "order_id"


# ── sql meta list-partitions ────────────────────────────────────────────


class TestSqlMetaListPartitions:
    """Tests for mcs meta list-partitions."""

    def test_list_partitions_success(self, isolated_config: Path) -> None:
        """List partitions returns partition info."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_partitions.return_value = {
            "table_name": "orders",
            "partitions": ["ds=20240101", "ds=20240102"],
            "visible_count": 2,
            "has_more": False,
            "limit": 100,
            "latest_partition": "ds=20240102",
            "is_partitioned": True,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-partitions", "--project", "my_proj", "orders"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["is_partitioned"] is True
        assert output["data"]["visible_count"] == 2

    def test_list_partitions_with_limit(self, isolated_config: Path) -> None:
        """Custom --limit is passed to list_partitions."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_partitions.return_value = {
            "table_name": "orders",
            "partitions": ["ds=20240101"],
            "visible_count": 1,
            "has_more": True,
            "limit": 1,
            "latest_partition": None,
            "is_partitioned": True,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(
                ["list-partitions", "--project", "my_proj", "--limit", "1", "orders"]
            )

        assert result.exit_code == 0
        mock_client.list_partitions.assert_called_once_with(
            "orders", schema=None, limit=1, project="my_proj"
        )


# ── sql meta freshness ──────────────────────────────────────────────────


class TestSqlMetaFreshness:
    """Tests for mcs meta freshness."""

    def test_freshness_success(self, isolated_config: Path) -> None:
        """Freshness returns freshness info."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.freshness_info.return_value = {
            "table_name": "orders",
            "is_partitioned": True,
            "latest_partition": "ds=20240115",
            "last_modified_time": "2024-01-15T10:00:00+00:00",
            "freshness_summary": "Data updated 2.0 hours ago (partition: ds=20240115)",
            "stale_warning": None,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["freshness", "--project", "my_proj", "orders"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["is_partitioned"] is True
        assert output["data"]["latest_partition"] == "ds=20240115"


# ---------------------------------------------------------------------------
# env-var auth fallback (no profiles.yaml)
# ---------------------------------------------------------------------------


def test_execute_falls_back_to_env_var_auth(monkeypatch, tmp_path) -> None:
    """When profiles.yaml has no entry for the project, _make_client_for_project
    falls back to creating a MaxComputeClient from AK env vars."""
    # Set AK env vars so the fallback succeeds.
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "env_ak_id")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "env_ak_secret")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "http://service.odps.aliyun.com/api")
    # Isolate HOME so profiles.yaml doesn't exist.
    monkeypatch.setenv("HOME", str(tmp_path))

    mock_envelope = Envelope.success(
        data={"rows": [{"id": 1}], "columns": [{"name": "id", "type": "INT"}]},
    )

    # Patch _make_client_for_project so we don't need live MC access.
    mock_client = MagicMock()
    mock_client.profile = _mock_profile(project="env_proj")
    mock_client._tier = "2"
    mock_client.execute_sql.return_value = mock_envelope

    patch_client = "maxcompute_semantic.commands.sql.make_client_for_project"
    patch_tier = "maxcompute_semantic.commands.sql.get_tier"
    with (
        patch(patch_client, return_value=mock_client),
        patch(patch_tier, return_value="2"),
    ):
        result = _invoke(["execute", "--project", "env_proj", "--schema", "default", "SELECT 1"])

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["status"] == "success"


def test_execute_env_var_fallback_no_ak_exits(monkeypatch, tmp_path) -> None:
    """When no profiles.yaml AND no AK env vars, _make_client_for_project
    creates a client with empty AK → ODPS auth error via global exception handler."""
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    # _make_client_for_project will still create a client with empty AK,
    # which will fail at ODPS auth level. We simulate that by patching
    # _make_client_for_project to raise an error.
    from maxcompute_semantic.auth.errors import NoProfilesConfiguredError

    with patch(
        "maxcompute_semantic.commands.meta.make_client_for_project",
        side_effect=NoProfilesConfiguredError(
            "no profiles configured and no AK env vars set",
            remediation="run `mcs profile create` or set ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET",
        ),
    ):
        result = _invoke(
            ["execute", "--project", "missing_proj", "--schema", "default", "SELECT 1"]
        )

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Permission error regression tests — end-to-end error envelope.
#
# After the permission-class collapse, all flavours (table / column /
# meta / info_schema_tenant / info_schema_project / function) fold into
# a single :class:`PermissionDeniedError`. The grid below verifies that
# the same collapsed envelope is emitted from every command entrypoint
# (execute / cost / list-tables / describe-table) rather than per-flavour
# discriminators that used to live here.
# ---------------------------------------------------------------------------


class TestPermissionErrorEnvelopes:
    """Verify PermissionDeniedError produces a uniform error envelope
    across the sql / meta entrypoints."""

    @staticmethod
    def _make_client_with_error(message, remediation=""):
        from maxcompute_semantic.mc_client.errors import PermissionDeniedError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.side_effect = PermissionDeniedError(
            message, remediation=remediation
        )
        mock_client.list_tables.side_effect = PermissionDeniedError(
            message, remediation=remediation
        )
        mock_client.describe_table.side_effect = PermissionDeniedError(
            message, remediation=remediation
        )
        mock_client.cost_estimate.side_effect = PermissionDeniedError(
            message, remediation=remediation
        )
        return mock_profile, mock_client

    def test_execute_permission_denied_envelope(self, isolated_config: Path) -> None:
        """execute → exit 5 + code='PermissionDenied' + raw message verbatim."""
        _, mock_client = self._make_client_with_error(
            "ODPS-0130013: Access Denied on table my_proj.users",
            remediation="request SELECT",
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT * FROM t"]
            )

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "PermissionDenied"
        assert "my_proj.users" in output["error"]["message"]

    def test_cost_permission_denied_envelope(self, isolated_config: Path) -> None:
        _, mock_client = self._make_client_with_error("Access Denied on table")

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["cost", "--project", "my_proj", "--schema", "default", "SELECT * FROM t"]
            )

        assert result.exit_code == 5
        assert json.loads(result.output)["error"]["code"] == "PermissionDenied"

    def test_list_tables_permission_denied_envelope(self, isolated_config: Path) -> None:
        _, mock_client = self._make_client_with_error("Access Denied - list Tables")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj"])

        assert result.exit_code == 5
        assert json.loads(result.output)["error"]["code"] == "PermissionDenied"

    def test_describe_table_permission_denied_envelope(self, isolated_config: Path) -> None:
        _, mock_client = self._make_client_with_error("Access Denied - describe Table")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "restricted_table"])

        assert result.exit_code == 5
        assert json.loads(result.output)["error"]["code"] == "PermissionDenied"

    def test_cross_project_permission_denied_envelope(self, isolated_config: Path) -> None:
        """Cross-project deny lands on the same collapsed envelope; the
        message names the cross-project table directly."""
        _, mock_client = self._make_client_with_error(
            "Access Denied - SELECT on other_project.table",
            remediation="request SELECT access from table owner",
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                ["execute", "--project", "my_proj", "--schema", "default", "SELECT * FROM t"]
            )

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["error"]["code"] == "PermissionDenied"
        assert "other_project" in output["error"]["message"]


# ---------------------------------------------------------------------------
# search_columns / list_partitions / freshness — 3-level + McsError paths
# ---------------------------------------------------------------------------


class TestSearchColumnsCmd:
    """Tests for mcs meta search-columns."""

    def test_3level_search_columns_success(self, isolated_config: Path) -> None:
        """3-level project passes schema to search_columns."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_columns.return_value = [
            {"table_name": "t1", "column_name": "id", "type": "INT", "comment": "", "score": 0.9},
        ]

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["search-columns", "--project", "my_proj", "id"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["count"] == 1
        mock_client.search_columns.assert_called_once_with(
            "id", schema="default", project="my_proj"
        )

    def test_3level_search_columns_with_schema(self, isolated_config: Path) -> None:
        """3-level project with explicit schema passes it to search_columns."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_columns.return_value = []

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(
                ["search-columns", "--project", "my_proj", "--schema", "myschema", "x"]
            )

        assert result.exit_code == 0
        mock_client.search_columns.assert_called_once_with(
            "x", schema="myschema", project="my_proj"
        )

    def test_2level_search_columns_no_schema(self, isolated_config: Path) -> None:
        """2-level project passes None schema to search_columns."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_columns.return_value = []

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["search-columns", "--project", "my_proj", "x"])

        assert result.exit_code == 0
        mock_client.search_columns.assert_called_once_with("x", schema=None, project="my_proj")

    def test_search_columns_mcs_error(self, isolated_config: Path) -> None:
        """search_columns McsError → failure envelope + exit 1."""
        from maxcompute_semantic.mc_client.errors import McsError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.search_columns.side_effect = McsError("search failed", remediation="retry")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["search-columns", "--project", "my_proj", "x"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"


class TestListPartitionsCmd:
    """Tests for mcs meta list-partitions."""

    def test_3level_list_partitions_success(self, isolated_config: Path) -> None:
        """3-level project passes schema to list_partitions."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_partitions.return_value = {
            "table_name": "t",
            "partitions": ["ds=20260101"],
            "visible_count": 1,
            "has_more": False,
            "is_partitioned": True,
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-partitions", "--project", "my_proj", "t"])

        assert result.exit_code == 0
        mock_client.list_partitions.assert_called_once_with(
            "t", schema="default", limit=100, project="my_proj"
        )

    def test_list_partitions_mcs_error(self, isolated_config: Path) -> None:
        """list_partitions McsError → failure envelope + exit 1."""
        from maxcompute_semantic.mc_client.errors import McsError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.list_partitions.side_effect = McsError("partitions error", remediation="retry")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["list-partitions", "--project", "my_proj", "t"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"


class TestFreshnessCmd:
    """Tests for mcs meta freshness."""

    def test_3level_freshness_success(self, isolated_config: Path) -> None:
        """3-level project passes schema to freshness_info."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.freshness_info.return_value = {
            "table_name": "t",
            "is_partitioned": True,
            "freshness_summary": "fresh",
        }

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["freshness", "--project", "my_proj", "t"])

        assert result.exit_code == 0
        mock_client.freshness_info.assert_called_once_with("t", schema="default", project="my_proj")

    def test_freshness_mcs_error(self, isolated_config: Path) -> None:
        """freshness McsError → failure envelope + exit 1."""
        from maxcompute_semantic.mc_client.errors import McsError

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.freshness_info.side_effect = McsError("freshness error", remediation="retry")

        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke_meta(["freshness", "--project", "my_proj", "t"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "error"


class TestSchemaValidationForTier:
    """Verify _validate_schema_for_tier rejects non-default schema on 2-level projects."""

    def test_2level_schema_default_accepted(self, isolated_config: Path) -> None:
        """2-level project with --schema default succeeds."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope(
            status="success", data={"rows": [], "row_count": 0, "elapsed_ms": 100}
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["execute", "--project", "my_proj", "--schema", "default", "SELECT 1"])

        assert result.exit_code == 0

    def test_2level_schema_non_default_rejected(self, isolated_config: Path) -> None:
        """2-level project with --schema custom is rejected."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["execute", "--project", "my_proj", "--schema", "custom", "SELECT 1"])

        assert result.exit_code == 2
        assert "--schema must be 'default'" in result.stderr

    def test_2level_no_schema_defaults_to_default(self, isolated_config: Path) -> None:
        """2-level project without --schema defaults to 'default' and succeeds."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope(
            status="success", data={"rows": [], "row_count": 0, "elapsed_ms": 100}
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["execute", "--project", "my_proj", "SELECT 1"])

        assert result.exit_code == 0

    def test_2level_cost_schema_non_default_rejected(self, isolated_config: Path) -> None:
        """2-level project cost command rejects non-default schema."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["cost", "--project", "my_proj", "--schema", "extra", "SELECT 1"])

        assert result.exit_code == 2
        assert "--schema must be 'default'" in result.stderr

    def test_3level_schema_default_accepted(self, isolated_config: Path) -> None:
        """3-level project: explicit --schema default is honored, not treated as 'unspecified'.

        'default' is a valid 3-level schema name (where MaxCompute parks
        flat tables after a 2→3 upgrade). The CI connectivity probe
        (`mcs sql execute --project P --schema default 'SELECT 1'`)
        relies on this — regression guard.
        """
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_client.execute_sql.return_value = Envelope(
            status="success", data={"rows": [], "row_count": 0, "elapsed_ms": 100}
        )

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(["execute", "--project", "my_proj", "--schema", "default", "SELECT 1"])

        assert result.exit_code == 0
        # The CLI forwards the explicit "default" schema; the client
        # builds the hints dict from it.
        call_kwargs = mock_client.execute_sql.call_args.kwargs
        assert call_kwargs["schema"] == "default"


# ── SchemaRequiredError per-command coverage ─────────────────────────────────


def _multi_source_profile():
    """Profile with two sources so ``resolve_schema_for_tier``
    can't auto-pick — exercises the SchemaRequiredError path that
    used to be Policy A on execute/cost and Policy B (silent
    coerce → "default") on explain + every meta verb."""
    from maxcompute_semantic.auth.schema import (
        AkAuth,
        CostThresholds,
        DataSource,
        Profile,
    )

    return Profile(
        name="multi",
        compute_project="my_proj",
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=(
            DataSource(project="my_proj", schema="alpha", tables="*"),
            DataSource(project="my_proj", schema="beta", tables="*"),
        ),
    )


class TestSchemaRequiredErrorPerCommand:
    """Every verb that takes ``--project`` / ``--schema`` MUST surface
    the missing-schema-on-tier-3 failure as a classified
    ``SchemaRequired`` McsError (exit 2, JSON envelope) when the
    profile can't auto-fill. Previously ``execute`` / ``cost`` /
    ``build`` hard-failed with plain-text stderr + exit 2 and the
    rest of the verbs (``explain`` + the six meta verbs) silently
    coerced ``None`` → ``"default"`` — masking misconfigured profiles
    by hitting the upgrade-synthetic ``default`` slot. The unified
    resolver in ``commands._schema_resolve`` makes every verb fail
    the same way; this class is the per-verb gate.
    """

    def _assert_schema_required_envelope(self, result) -> None:
        assert result.exit_code == 2
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "SchemaRequired"
        assert payload["error"]["remediation"]
        assert "alpha" in payload["error"]["remediation"]
        assert "beta" in payload["error"]["remediation"]

    def test_execute_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(["execute", "--project", "my_proj", "SELECT 1"])
        self._assert_schema_required_envelope(result)
        mock_client.execute_sql.assert_not_called()

    def test_cost_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(["cost", "--project", "my_proj", "SELECT 1"])
        self._assert_schema_required_envelope(result)
        mock_client.cost_estimate.assert_not_called()

    def test_explain_raises_schema_required(self, isolated_config: Path) -> None:
        # Behavior change: pre-unification ``explain`` silently
        # coerced None → "default" on tier-3 (Policy B). Now it
        # fails fast like execute/cost.
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke(["explain", "--project", "my_proj", "SELECT 1"])
        self._assert_schema_required_envelope(result)
        mock_client.explain.assert_not_called()

    def test_list_tables_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-tables", "--project", "my_proj"])
        self._assert_schema_required_envelope(result)
        mock_client.list_tables.assert_not_called()

    def test_describe_table_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["describe-table", "--project", "my_proj", "t"])
        self._assert_schema_required_envelope(result)
        mock_client.describe_table.assert_not_called()

    def test_search_tables_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["search-tables", "--project", "my_proj", "kw"])
        self._assert_schema_required_envelope(result)
        mock_client.search_tables.assert_not_called()

    def test_search_columns_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["search-columns", "--project", "my_proj", "kw"])
        self._assert_schema_required_envelope(result)
        mock_client.search_columns.assert_not_called()

    def test_list_partitions_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["list-partitions", "--project", "my_proj", "t"])
        self._assert_schema_required_envelope(result)
        mock_client.list_partitions.assert_not_called()

    def test_freshness_raises_schema_required(self, isolated_config: Path) -> None:
        mock_profile = _multi_source_profile()
        mock_client = _mock_client(mock_profile)
        with patch.multiple(
            "maxcompute_semantic.commands.meta",
            make_client_for_project=MagicMock(return_value=mock_client),
            get_tier=MagicMock(return_value="3"),
        ):
            result = _invoke_meta(["freshness", "--project", "my_proj", "t"])
        self._assert_schema_required_envelope(result)
        mock_client.freshness_info.assert_not_called()


# ── sql project auto-routing ─────────────────────────────────────────────────


def _dev_prod_profile(*, dev: str = "dev_proj", prod: str = "prod_proj", schema: str = "default"):
    """Profile with ``compute_project = dev``, single source under ``prod``.

    The DataWorks standard-mode shape: AK owns the dev sandbox for
    writes; data lives in a separate prod project. Pre-fix, bare SQL
    against tables in *prod* failed because the client defaulted to
    *dev*.
    """
    from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile

    return Profile(
        name="dev_prod",
        compute_project=dev,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=(DataSource(project=prod, schema=schema, tables="*"),),
    )


def _two_source_profile(
    *,
    compute: str = "dev_proj",
    a_proj: str = "alpha_proj",
    b_proj: str = "beta_proj",
):
    """Multi-source profile spanning two distinct data-side projects.

    Used to verify cross-source SQL keeps ``compute_project`` rather
    than picking one source arbitrarily.
    """
    from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile

    return Profile(
        name="two_sources",
        compute_project=compute,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=(
            DataSource(project=a_proj, schema="default", tables="*"),
            DataSource(project=b_proj, schema="default", tables="*"),
        ),
    )


def _make_package(profile, *triples: tuple[str, str, str]) -> None:
    """Materialize a minimal ``package.db`` for *profile* with the given
    ``(project, schema, table)`` triples so ``lookup_source_key``
    resolves them during routing.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    db = PackageDB(pdir / "package.db")
    for proj, schema, table in triples:
        db.upsert_table(f"{proj}__{schema}", table, "hash")
    db.close()


class TestResolveTargetProject:
    """Direct tests for the routing helper. The CLI verbs delegate to
    this via ``_route_project``; covering it here lets the integration
    tests stay focused on wiring rather than every edge case.
    """

    def test_single_source_matches_compute(self, isolated_config: Path) -> None:
        """Source project == compute project: routing is a no-op."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile(dev="same", prod="same")
        _make_package(profile, ("same", "default", "orders"))
        assert _resolve_target_project("SELECT * FROM orders", profile) == "same"

    def test_single_source_routes_to_prod_when_compute_differs(self, isolated_config: Path) -> None:
        """Standard dev/prod: bare name routes to source project, not compute."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        assert _resolve_target_project("SELECT * FROM orders", profile) == "prod_proj"

    def test_multi_source_all_in_one_source(self, isolated_config: Path) -> None:
        """Bare names all in one source route to that source."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _two_source_profile()
        _make_package(
            profile,
            ("alpha_proj", "default", "orders"),
            ("alpha_proj", "default", "items"),
        )
        sql = "SELECT * FROM orders JOIN items ON orders.id = items.order_id"
        assert _resolve_target_project(sql, profile) == "alpha_proj"

    def test_multi_source_split_keeps_compute(self, isolated_config: Path) -> None:
        """Bare names spanning two sources return None — caller keeps
        ``compute_project`` and the engine errors with its own
        multi-project diagnostic."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _two_source_profile()
        _make_package(
            profile,
            ("alpha_proj", "default", "orders"),
            ("beta_proj", "default", "users"),
        )
        sql = "SELECT * FROM orders JOIN users ON orders.uid = users.id"
        assert _resolve_target_project(sql, profile) is None

    def test_unknown_table_falls_back_to_first_source(self, isolated_config: Path) -> None:
        """Bare name not in package falls back to ``sources[0].project``."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        assert _resolve_target_project("SELECT * FROM never_built", profile) == "prod_proj"

    def test_select_one_no_tables_falls_back(self, isolated_config: Path) -> None:
        """``SELECT 1`` (no tables) falls back to ``sources[0].project``."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        assert _resolve_target_project("SELECT 1", profile) == "prod_proj"

    def test_ddl_create_falls_back(self, isolated_config: Path) -> None:
        """DDL touching a name not in the package routes to the data side."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        assert _resolve_target_project("CREATE TABLE foo (x BIGINT)", profile) == "prod_proj"

    def test_unparseable_sql_falls_back(self, isolated_config: Path) -> None:
        """sqlglot can't parse → fall back rather than crash."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        assert _resolve_target_project("not even sql @@@ {{ ?? }}", profile) == "prod_proj"

    def test_explicit_fqn_uses_catalog_project(self, isolated_config: Path) -> None:
        """3-segment FQN routes to the catalog project even when it's
        not a profile source."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        sql = "SELECT * FROM other_proj.default.misc"
        assert _resolve_target_project(sql, profile) == "other_proj"

    def test_no_package_db_falls_back(self, isolated_config: Path) -> None:
        """Fresh profile, never built — fall back to
        ``sources[0].project`` so routing works on cold-start."""
        from maxcompute_semantic.commands.sql import _resolve_target_project

        profile = _dev_prod_profile()
        assert _resolve_target_project("SELECT * FROM orders", profile) == "prod_proj"

    def test_empty_sources_returns_none(self, isolated_config: Path) -> None:
        """Profile with no sources at all → no routing decision possible."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            Profile,
        )
        from maxcompute_semantic.commands.sql import _resolve_target_project

        # Profile validator requires at least one source; bypass via
        # the __new__ + __setattr__ pattern to construct the edge case.
        profile = Profile.__new__(Profile)
        object.__setattr__(profile, "name", "empty")
        object.__setattr__(profile, "compute_project", "x")
        object.__setattr__(profile, "endpoint", "http://x")
        object.__setattr__(profile, "auth", AkAuth(access_key_id="a", access_key_secret="b"))
        object.__setattr__(profile, "cost_thresholds", CostThresholds())
        object.__setattr__(profile, "sources", ())
        object.__setattr__(profile, "package_path", None)
        object.__setattr__(profile, "tags", ())
        assert _resolve_target_project("SELECT * FROM t", profile) is None


class TestSqlAutoRoutingIntegration:
    """Verify the CLI verbs (execute/cost/explain) actually call
    ``_make_client_for_project`` with the routed project.
    """

    def test_execute_routes_bare_name_to_source_project(self, isolated_config: Path) -> None:
        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        mock_client = _mock_client(profile)
        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        make_client_mock = MagicMock(return_value=mock_client)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=make_client_mock,
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["execute", "SELECT * FROM orders"])

        assert result.exit_code == 0, result.output
        make_client_mock.assert_called_once()
        assert make_client_mock.call_args.args[0] == "prod_proj"

    def test_explicit_project_flag_wins(self, isolated_config: Path) -> None:
        """``--project X`` short-circuits routing — no profile lookup."""
        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        mock_client = _mock_client(profile)
        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        make_client_mock = MagicMock(return_value=mock_client)
        resolve_mock = MagicMock(return_value=profile)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=make_client_mock,
            resolve_profile_for_project=resolve_mock,
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["execute", "--project", "explicit_proj", "SELECT * FROM orders"])

        assert result.exit_code == 0, result.output
        resolve_mock.assert_not_called()
        assert make_client_mock.call_args.args[0] == "explicit_proj"

    def test_cost_uses_same_routing(self, isolated_config: Path) -> None:
        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        mock_client = _mock_client(profile)
        mock_client.cost_estimate.return_value = {
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }

        make_client_mock = MagicMock(return_value=mock_client)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=make_client_mock,
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["cost", "SELECT * FROM orders"])

        assert result.exit_code == 0, result.output
        assert make_client_mock.call_args.args[0] == "prod_proj"

    def test_explain_uses_same_routing(self, isolated_config: Path) -> None:
        profile = _dev_prod_profile()
        _make_package(profile, ("prod_proj", "default", "orders"))
        mock_client = _mock_client(profile)
        mock_client.explain.return_value = {"plan": "ok"}

        make_client_mock = MagicMock(return_value=mock_client)
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=make_client_mock,
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(["explain", "SELECT * FROM orders"])

        assert result.exit_code == 0, result.output
        assert make_client_mock.call_args.args[0] == "prod_proj"


# ── _classify_sql (pure-function tests) ──────────────────────────────────────


class TestClassifySql:
    """Pure-function matrix for the read/write/unparseable classifier.
    Acts as the version-drift belt: a future sqlglot upgrade that
    renames an expression node flips its row from "write"/"read" to
    "unparseable" and the matrix fails loudly."""

    def test_select_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT * FROM t") == "read"

    def test_select_one_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT 1") == "read"

    def test_with_cte_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("WITH x AS (SELECT 1) SELECT * FROM x") == "read"

    def test_union_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT 1 UNION SELECT 2") == "read"

    def test_desc_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("DESC TABLE foo") == "read"

    def test_use_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("USE my_db") == "read"

    def test_set_is_write(self) -> None:
        """SET mutates session state — requires --allow-write.

        This stays 'write' on purpose: classify_sql is NOT modified by
        the SET-extraction feature (see
        docs/superpowers/specs/2026-06-23-set-statement-extraction-design.md).
        Extraction happens in the verbs before classification, so
        extractable SETs never reach classify_sql; non-extractable SETs
        (SET LABEL, SETPROJECT) still do and must stay gated as write.
        Do not remove this assertion.
        """
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SET odps.sql.allow.fullscan=true") == "write"

    def test_show_tables_via_command_keyword_whitelist_is_read(self) -> None:
        # SHOW TABLES parses to Command(name="SHOW") in current sqlglot;
        # the Command-keyword whitelist makes it a read.
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SHOW TABLES") == "read"

    def test_show_partitions_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SHOW PARTITIONS foo") == "read"

    def test_explain_select_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("EXPLAIN SELECT 1") == "read"

    def test_insert_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("INSERT INTO t VALUES (1)") == "write"

    def test_update_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("UPDATE t SET x=1 WHERE y=2") == "write"

    def test_delete_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("DELETE FROM t WHERE y=2") == "write"

    def test_merge_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        sql = "MERGE INTO t USING s ON t.k = s.k WHEN MATCHED THEN UPDATE SET a=1"
        assert _classify_sql(sql) == "write"

    def test_create_table_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("CREATE TABLE t (a int)") == "write"

    def test_ctas_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("CREATE TABLE t AS SELECT * FROM s") == "write"

    def test_drop_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("DROP TABLE t") == "write"

    def test_alter_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("ALTER TABLE t ADD COLUMN x int") == "write"

    def test_alter_rename_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("ALTER TABLE t RENAME TO u") == "write"

    def test_truncate_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("TRUNCATE TABLE t") == "write"

    def test_grant_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("GRANT SELECT ON t TO USER u") == "write"

    def test_revoke_is_write(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("REVOKE SELECT ON t FROM USER u") == "write"

    def test_multi_statement_any_write_is_write(self) -> None:
        # Defensive: even though ODPS run_sql is one-statement-at-a-
        # time in practice, a multi-statement input with any write
        # classifies as write.
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT 1; DROP TABLE t") == "write"

    def test_multi_statement_all_read_is_read(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT 1; SELECT 2") == "read"

    def test_add_jar_top_level_alias_is_unparseable(self) -> None:
        # ADD JAR parses to top-level Alias in the default sqlglot
        # dialect; we treat any unrecognized top-level node as
        # unparseable so the operator must opt-in via --allow-write.
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("ADD JAR foo.jar") == "unparseable"

    def test_unknown_command_keyword_is_unparseable(self) -> None:
        # Command nodes whose first keyword isn't in the read whitelist
        # classify as unparseable (e.g. LIST FUNCTIONS).
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("LIST FUNCTIONS") == "unparseable"

    def test_empty_string_is_unparseable(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("") == "unparseable"

    def test_garbage_is_unparseable(self) -> None:
        from maxcompute_semantic.commands.sql import _classify_sql

        # Should not raise; ErrorLevel.RAISE swallows the ParseError
        # inside the helper and falls back to unparseable.
        assert _classify_sql("###not sql###") == "unparseable"

    def test_incomplete_where_is_unparseable(self) -> None:
        """Regression for the Round 6 Codex P2 #5 finding.

        ``SELECT * FROM orders WHERE`` is syntactically incomplete;
        pre-fix ``error_level=IGNORE`` made sqlglot silently emit a
        partial AST containing an exp.Select node, so the classifier
        returned ``"read"`` and the write-guard let the broken SQL
        through to the server. Post-fix the classifier uses
        ``error_level=RAISE`` and falls back to ``"unparseable"`` so
        the write-guard rejects it via the ``MCS_REVIEW_UNSUPPORTED``
        path.
        """
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT * FROM orders WHERE") == "unparseable"

    def test_unclosed_paren_is_unparseable(self) -> None:
        """Sibling: an unclosed paren in a subquery is also a parse
        error under RAISE — under IGNORE sqlglot would return a
        partial Select AST and the classifier would call it ``read``,
        leaking past the write-guard."""
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT * FROM (SELECT id FROM orders") == "unparseable"


# ── mcs sql execute write-op guard (CLI integration) ─────────────────────────


class TestSqlExecuteWriteGuard:
    """End-to-end CLI behavior for the write-op guard. The classifier
    itself is tested under TestClassifySql; this class only verifies
    the guard wiring: rejection envelope shape, exit code 2,
    --allow-write override, and that the guard runs BEFORE the cost
    gate so client.execute_sql / client.cost_estimate are never
    called on a reject."""

    def _run(self, args: list[str]):
        """Invoke `mcs sql execute` with mocked profile + client.

        The reject path uses `_resolve_profile_for_project` directly
        for source-tag resolution, so that's mocked separately from
        the success-path `_make_client_for_project` mock.
        """
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            make_client_for_project=MagicMock(return_value=mock_client),
            resolve_profile_for_project=MagicMock(return_value=mock_profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(args)
        return result, mock_client

    def test_select_default_succeeds(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default", "SELECT 1"]
        )
        assert result.exit_code == 0
        assert mock_client.execute_sql.called

    def test_show_tables_default_succeeds(self, isolated_config: Path) -> None:
        # SHOW TABLES → Command(name="SHOW") → allowed.
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default", "SHOW TABLES"]
        )
        assert result.exit_code == 0
        assert mock_client.execute_sql.called

    def test_set_then_select_runs_without_allow_write(self, isolated_config: Path) -> None:
        # SET key=val is extracted to a hint; the remaining SELECT is a read,
        # so --allow-write is NOT required.
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET odps.sql.mapper.split.size = 4096; SELECT 1"]
        )
        assert result.exit_code == 0, result.output
        assert mock_client.execute_sql.called
        call = mock_client.execute_sql.call_args
        assert call.args[0] == "SELECT 1"
        assert call.kwargs["hints"] == {"odps.sql.mapper.split.size": "4096"}

    def test_set_label_still_requires_allow_write(self, isolated_config: Path) -> None:
        # SET LABEL is not key=val -> not extracted -> stays -> rejected.
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET LABEL tbl TO user; SELECT 1"]
        )
        assert result.exit_code == 2
        assert not mock_client.execute_sql.called

    def test_standalone_set_is_rejected(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            ["execute", "--project", "p", "--schema", "default",
             "SET odps.sql.mapper.split.size = 4096"]
        )
        assert result.exit_code == 2
        assert not mock_client.execute_sql.called
        assert "no query" in result.output

    def test_insert_default_rejected(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "INSERT INTO t VALUES (1)",
            ]
        )
        assert result.exit_code == 2
        output = json.loads(result.output)
        assert output["status"] == "error"
        assert output["error"]["code"] == "WriteOpRejected"
        assert "--allow-write" in output["error"]["remediation"]
        # Guard MUST precede client construction and cost gate.
        assert not mock_client.execute_sql.called
        assert not mock_client.cost_estimate.called

    def test_drop_default_rejected(self, isolated_config: Path) -> None:
        result, _ = self._run(["execute", "--project", "p", "--schema", "default", "DROP TABLE t"])
        assert result.exit_code == 2
        assert json.loads(result.output)["error"]["code"] == "WriteOpRejected"

    def test_ctas_default_rejected(self, isolated_config: Path) -> None:
        result, _ = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "CREATE TABLE t AS SELECT * FROM s",
            ]
        )
        assert result.exit_code == 2
        assert json.loads(result.output)["error"]["code"] == "WriteOpRejected"

    def test_multi_statement_with_write_rejected(self, isolated_config: Path) -> None:
        result, _ = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "SELECT 1; DROP TABLE t",
            ]
        )
        assert result.exit_code == 2
        assert json.loads(result.output)["error"]["code"] == "WriteOpRejected"

    def test_add_jar_default_rejected_as_unparseable(self, isolated_config: Path) -> None:
        # ADD JAR → top-level Alias → unparseable → reject. The
        # remediation message must mention the unparseable branch so
        # the operator knows --allow-write is the right escape hatch.
        result, _ = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "ADD JAR foo.jar",
            ]
        )
        assert result.exit_code == 2
        env = json.loads(result.output)["error"]
        assert env["code"] == "WriteOpRejected"
        assert "could not be parsed" in env["message"]
        assert "--allow-write" in env["remediation"]

    def test_unknown_command_rejection_does_not_reuse_stale_parse_error(
        self,
        isolated_config: Path,
    ) -> None:
        """Non-exception unparseable SQL must not inherit a previous parse error."""
        from maxcompute_semantic.commands.sql import _classify_sql

        assert _classify_sql("SELECT * FROM t WHERE name = 'Women's Soccer'") == "unparseable"

        result, _ = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "LIST FUNCTIONS",
            ]
        )

        assert result.exit_code == 2
        env = json.loads(result.output)["error"]
        assert env["code"] == "WriteOpRejected"
        assert "Women's Soccer" not in env["message"]
        assert "Parse error:" not in env["message"]

    def test_apostrophe_parse_rejection_mentions_doubled_quote_fix(
        self,
        isolated_config: Path,
    ) -> None:
        result, _ = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "SELECT * FROM t WHERE name = 'Women's Soccer'",
            ]
        )

        assert result.exit_code == 2
        env = json.loads(result.output)["error"]
        assert env["code"] == "WriteOpRejected"
        assert "Parse error:" in env["message"]
        assert "double" in env["remediation"].lower()
        assert "single quote" in env["remediation"].lower()

    def test_insert_with_allow_write_succeeds(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "--allow-write",
                "INSERT INTO t VALUES (1)",
            ]
        )
        assert result.exit_code == 0
        assert mock_client.execute_sql.called

    def test_add_jar_with_allow_write_succeeds(self, isolated_config: Path) -> None:
        result, mock_client = self._run(
            [
                "execute",
                "--project",
                "p",
                "--schema",
                "default",
                "--allow-write",
                "ADD JAR foo.jar",
            ]
        )
        assert result.exit_code == 0
        assert mock_client.execute_sql.called


class TestResolveSourceKeysTwoSegment:
    """Regression (round 7 P1 #1): _resolve_source_keys must honor
    table.db for two-segment names so ``schema_b.orders`` does not
    resolve against a different schema's source."""

    def test_two_segment_routes_to_correct_source(self, tmp_path, monkeypatch):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )
        from maxcompute_semantic.build.storage import PackageDB
        from maxcompute_semantic.commands.sql import _resolve_source_keys

        profile = Profile(
            name="multi",
            compute_project="proj_a",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_a", schema="schema_b", tables="*"),
            ),
        )
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.upsert_table("proj_a__default", "orders", schema_hash="h1")
        db.upsert_table("proj_a__schema_b", "orders", schema_hash="h2")
        db.close()

        monkeypatch.setattr(
            "maxcompute_semantic._internal.paths.profile_data_dir",
            lambda _: tmp_path,
        )

        keys = _resolve_source_keys("SELECT * FROM schema_b.orders", profile)
        assert keys == {"proj_a__schema_b"}

    def test_bare_name_falls_through_all_sources(self, tmp_path, monkeypatch):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )
        from maxcompute_semantic.build.storage import PackageDB
        from maxcompute_semantic.commands.sql import _resolve_source_keys

        profile = Profile(
            name="multi",
            compute_project="proj_a",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_a", schema="schema_b", tables="*"),
            ),
        )
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.upsert_table("proj_a__default", "orders", schema_hash="h1")
        db.close()

        monkeypatch.setattr(
            "maxcompute_semantic._internal.paths.profile_data_dir",
            lambda _: tmp_path,
        )

        keys = _resolve_source_keys("SELECT * FROM orders", profile)
        assert keys == {"proj_a__default"}

    def test_two_segment_prefers_compute_project_when_schema_shared(self, tmp_path, monkeypatch):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )
        from maxcompute_semantic.build.storage import PackageDB
        from maxcompute_semantic.commands.sql import (
            _resolve_source_keys,
            _resolve_target_project,
        )

        profile = Profile(
            name="multi",
            compute_project="proj_b",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="schema_x", tables="*"),
                DataSource(project="proj_b", schema="schema_x", tables="*"),
            ),
        )
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.upsert_table("proj_a__schema_x", "orders", schema_hash="h1")
        db.upsert_table("proj_b__schema_x", "orders", schema_hash="h2")
        db.close()

        monkeypatch.setattr(
            "maxcompute_semantic._internal.paths.profile_data_dir",
            lambda _: tmp_path,
        )

        sql = "SELECT * FROM schema_x.orders"
        assert _resolve_source_keys(sql, profile) == {"proj_b__schema_x"}
        assert _resolve_target_project(sql, profile) == "proj_b"

    def test_project_table_does_not_match_non_default_schema(self, tmp_path, monkeypatch):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )
        from maxcompute_semantic.build.storage import PackageDB
        from maxcompute_semantic.commands.sql import _resolve_source_keys

        profile = Profile(
            name="multi",
            compute_project="proj_a",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(DataSource(project="proj_a", schema="schema_x", tables="*"),),
        )
        db_path = tmp_path / "package.db"
        db = PackageDB(db_path)
        db.upsert_table("proj_a__schema_x", "orders", schema_hash="h1")
        db.close()

        monkeypatch.setattr(
            "maxcompute_semantic._internal.paths.profile_data_dir",
            lambda _: tmp_path,
        )

        assert _resolve_source_keys("SELECT * FROM proj_a.orders", profile) == set()
