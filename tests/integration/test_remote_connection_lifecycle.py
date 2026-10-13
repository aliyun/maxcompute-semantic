# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""The registered CLI keeps MCP connections separate from MC credentials."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maxcompute_semantic.cli import cli


def test_remote_configuration_does_not_require_or_create_mc_profile(
    isolated_config: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "-f", "json", "remote", "add", "test",
            "--mcp-url", "http://127.0.0.1:18765/mcp",
            "--region", "cn-shanghai", "--token-env", "MCS_TEST_BEARER",
        ],
    )
    assert result.exit_code == 0, result.output
    added = json.loads(result.output)
    assert added["status"] == "success"
    assert added["data"]["token_env"] == "MCS_TEST_BEARER"

    profiles = runner.invoke(cli, ["-f", "json", "profile", "list"])
    assert profiles.exit_code == 0, profiles.output
    assert json.loads(profiles.output)["data"]["profiles"] == []

    shown = runner.invoke(cli, ["-f", "json", "remote", "show", "test"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["data"]["url"] == "http://127.0.0.1:18765/mcp"


def test_remote_missing_connection_is_a_structured_cli_error(
    isolated_config: Path,
) -> None:
    result = CliRunner().invoke(cli, ["-f", "json", "remote", "show", "absent"])
    assert result.exit_code != 0
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] != "Unknown"
    assert result.stderr == ""
