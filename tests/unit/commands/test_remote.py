# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Public remote CLI configuration lifecycle, with no profile credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic.commands.remote import remote_group


def _invoke(args: list[str]):
    return CliRunner().invoke(remote_group, args, obj={"format": "json"})


def test_remote_cli_add_list_show_remove(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIXTURE_BEARER", "private-fixture-token")
    add = _invoke(
        [
            "add",
            "daily",
            "--mcp-url",
            "https://gateway.example/mcp",
            "--region",
            "cn-shanghai",
            "--token-env",
            "FIXTURE_BEARER",
        ]
    )
    assert add.exit_code == 0
    assert json.loads(add.stdout)["data"]["token_env"] == "FIXTURE_BEARER"
    assert "private-fixture-token" not in add.stdout
    listing = _invoke(["list"])
    assert json.loads(listing.stdout)["data"]["remotes"][0]["alias"] == "daily"
    show = _invoke(["show", "daily"])
    assert json.loads(show.stdout)["data"]["url"] == "https://gateway.example/mcp"
    remove = _invoke(["remove", "daily"])
    assert json.loads(remove.stdout)["data"] == {"removed": "daily"}
    assert json.loads(_invoke(["list"]).stdout)["data"]["remotes"] == []


def test_remote_cli_rejection_has_json_envelope(isolated_config: Path) -> None:
    invalid = _invoke(
        [
            "add",
            "daily",
            "--mcp-url",
            "https://user:SECRET@example/mcp",
            "--region",
            "cn-shanghai",
        ]
    )
    assert invalid.exit_code != 0
    assert json.loads(invalid.stdout)["error"]["code"] == "RemoteInvalidConfig"
    assert "SECRET" not in invalid.stdout + invalid.stderr
    missing = _invoke(["show", "missing"])
    assert missing.exit_code != 0
    assert json.loads(missing.stdout)["error"]["code"] == "RemoteNotFound"


def test_remote_cli_login_delegates_native_auth(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _invoke(
            [
                "add",
                "daily",
                "--mcp-url",
                "https://gateway.example/mcp",
                "--region",
                "cn-shanghai",
            ]
        ).exit_code
        == 0
    )
    calls: list[tuple[bool, float]] = []

    def login(self, *, open_browser: bool, callback_timeout: float):
        calls.append((open_browser, callback_timeout))
        return {"alias": self.connection.alias, "authenticated": True, "tools": []}

    monkeypatch.setattr(
        "maxcompute_semantic.remote.client.SemanticSpecClient.login", login
    )
    result = _invoke(["login", "daily", "--no-browser", "--timeout", "15"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["authenticated"] is True
    assert calls == [(False, 15.0)]
