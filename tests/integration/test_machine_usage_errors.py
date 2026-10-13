# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Structured callers can recover from argument errors without parsing stderr."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from ruamel.yaml import YAML

from maxcompute_semantic.cli import cli


@pytest.mark.parametrize("output_format", ["json", "yaml"])
@pytest.mark.parametrize("arguments", [
    ["remote", "add", "test"],
    ["graph", "path"],
    ["package", "stage-projection"],
    ["knowledge", "project", "--unknown", "sensitive-test-value"],
])
def test_usage_errors_are_structured_and_do_not_echo_values(
    isolated_config: Path, output_format: str, arguments: list[str],
) -> None:
    result = CliRunner().invoke(cli, ["-f", output_format, *arguments])
    assert result.exit_code == 2
    envelope = json.loads(result.stdout) if output_format == "json" else YAML(typ="safe").load(result.stdout)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "UsageError"
    assert result.stderr == ""
    assert "sensitive-test-value" not in result.output
