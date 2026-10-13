# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Public connection-store behavior, without profile or token access."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore


def test_connection_roundtrip_is_independent_and_private(tmp_path: Path) -> None:
    store = RemoteStore(tmp_path / "remotes")
    remote = RemoteConnection(
        "daily", "https://gateway.example/mcp", "cn-shanghai", "MCS_TEST_BEARER"
    )
    store.add(remote)
    assert store.get("daily") == remote
    assert store.list() == [remote]
    assert stat.S_IMODE((store.directory / "daily.remote.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert (
        json.loads((store.directory / "daily.remote.json").read_text())
        == remote.to_dict()
    )


def test_listing_absent_store_does_not_write(tmp_path: Path) -> None:
    store = RemoteStore(tmp_path / "absent")
    assert store.list() == []
    assert not store.directory.exists()


def test_duplicate_alias_preserves_original(tmp_path: Path) -> None:
    store = RemoteStore(tmp_path)
    remote = RemoteConnection("daily", "https://gateway.example/mcp", "cn-shanghai")
    store.add(remote)
    with pytest.raises(RemoteError, match="already exists"):
        store.add(RemoteConnection("daily", "https://other.example/mcp", "cn-hangzhou"))
    assert store.get("daily") == remote


@pytest.mark.parametrize("alias", ["../tokens", "/tmp/token", "a.b", "", "a" * 65])
def test_alias_cannot_escape_store(tmp_path: Path, alias: str) -> None:
    with pytest.raises(RemoteError):
        RemoteStore(tmp_path).get(alias)


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://user:SECRET@example/mcp",
        "https://example/mcp?token=SECRET",
        "https://example/mcp#SECRET",
        "https://example:invalid/mcp",
    ],
)
def test_url_credentials_and_invalid_urls_are_rejected_without_echo(url: str) -> None:
    with pytest.raises(RemoteError) as exc:
        RemoteConnection("daily", url, "cn-shanghai")
    assert "SECRET" not in str(exc.value)


def test_remove_deletes_only_selected_connection_and_oauth(tmp_path: Path) -> None:
    store = RemoteStore(tmp_path)
    for alias in ("daily", "prod"):
        store.add(RemoteConnection(alias, "https://gateway.example/mcp", "cn-shanghai"))
        (tmp_path / f"{alias}.tokens.json").write_text("not read by remove")
        (tmp_path / f"{alias}.client.json").write_text("not read by remove")
    store.remove("daily")
    assert [remote.alias for remote in store.list()] == ["prod"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "prod.client.json",
        "prod.remote.json",
        "prod.tokens.json",
    ]


def test_get_rejects_corrupt_config_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "daily.remote.json").write_text(
        '{"alias":"daily","unexpected":"SECRET"}'
    )
    with pytest.raises(RemoteError) as exc:
        RemoteStore(tmp_path).get("daily")
    assert "SECRET" not in str(exc.value)
    (tmp_path / "daily.remote.json").unlink()
    (tmp_path / "daily.remote.json").symlink_to(tmp_path / "outside")
    with pytest.raises(RemoteError, match="symlink"):
        RemoteStore(tmp_path).get("daily")


@pytest.mark.parametrize(
    "region,token_env", [("", None), ("cn-shanghai", "TOKEN=value")]
)
def test_region_and_token_variable_are_explicit_typed_configuration(
    region: str, token_env: str | None
) -> None:
    with pytest.raises(RemoteError) as exc:
        RemoteConnection("daily", "https://gateway.example/mcp", region, token_env)
    assert str(exc.value.code) == "RemoteInvalidConfig"


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        '{"alias":"daily"}',
        '{"alias":"daily","url":7,"region":"cn-shanghai"}',
        '{"alias":"other","url":"https://gateway.example/mcp","region":"cn-shanghai"}',
    ],
)
def test_untrusted_config_file_is_rejected_without_fallback(
    tmp_path: Path, payload: str
) -> None:
    (tmp_path / "daily.remote.json").write_text(payload)
    with pytest.raises(RemoteError) as exc:
        RemoteStore(tmp_path).get("daily")
    assert str(exc.value.code) == "RemoteInvalidConfig"


def test_config_write_failure_is_classified(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("existing user file")
    with pytest.raises(RemoteError) as exc:
        RemoteStore(occupied).add(
            RemoteConnection("daily", "https://gateway.example/mcp", "cn-shanghai")
        )
    assert str(exc.value.code) == "RemoteConfigWrite"
    assert occupied.read_text() == "existing user file"
