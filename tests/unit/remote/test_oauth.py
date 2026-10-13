# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Native SDK OAuth tests; no real identities or remote endpoints are used."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import socket
import stat
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

import httpx2
import pytest
from filelock import FileLock
from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore
from maxcompute_semantic.remote.oauth import (
    FileTokenStorage,
    close_oauth_provider,
    create_oauth_provider,
)

_RESOURCE = "https://resource.example.test/mcp"
_ISSUER = "https://identity.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


class _OAuthServer:
    """Local transport exercising the SDK's real discovery/DCR/PKCE flow."""

    def __init__(self, *, wrong_state: bool = False, denied: bool = False,
                 wrong_issuer: bool = False) -> None:
        self.grants: list[str] = []
        self.registration_count = 0
        self.challenge: str | None = None
        self.callback_responses: list[bytes] = []
        self.browser_count = 0
        self.wrong_state = wrong_state
        self.denied = denied
        self.wrong_issuer = wrong_issuer
        self.metadata_requests: list[str] = []

    def browser(self, url: str) -> bool:
        self.browser_count += 1
        params = parse_qs(urlparse(url).query)
        assert params["code_challenge_method"] == ["S256"]
        self.challenge = params["code_challenge"][0]
        assert params["resource"] == [_RESOURCE]
        callback_params = {
            "code": "test-code", "state": "wrong" if self.wrong_state else params["state"][0],
            "iss": "https://wrong.example.test" if self.wrong_issuer else _ISSUER,
        }
        if self.denied:
            callback_params = {"error": "access_denied", "error_description": "private-error-detail"}
        callback = params["redirect_uri"][0] + "?" + urlencode(callback_params)
        with urlopen(callback, timeout=2.0) as response:
            self.callback_responses.append(response.read())
        return True

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == _RESOURCE:
            if request.headers.get("authorization") in {"Bearer test-access", "Bearer refreshed-access"}:
                return httpx2.Response(200, json={"authenticated": True})
            return httpx2.Response(401, headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://resource.example.test/metadata"',
            })
        if request.url.path == "/metadata":
            self.metadata_requests.append("resource")
            return httpx2.Response(200, json={
                "resource": _RESOURCE, "authorization_servers": [_ISSUER],
            })
        if request.url.path.startswith("/.well-known/oauth-authorization-server"):
            self.metadata_requests.append("issuer")
            return httpx2.Response(200, json={
                "issuer": _ISSUER, "authorization_endpoint": f"{_ISSUER}/authorize",
                "token_endpoint": f"{_ISSUER}/exchange", "registration_endpoint": f"{_ISSUER}/register",
                "code_challenge_methods_supported": ["S256"],
                "authorization_response_iss_parameter_supported": True,
            })
        if request.url.path == "/register":
            self.registration_count += 1
            metadata = json.loads(request.content)
            assert metadata["token_endpoint_auth_method"] == "none"
            return httpx2.Response(201, json={**metadata, "client_id": "test-client"})
        if request.url.path == "/exchange":
            assert request.url.host == "identity.example.test"
            params = parse_qs(request.content.decode())
            grant = params["grant_type"][0]
            self.grants.append(grant)
            if grant == "authorization_code":
                computed = base64.urlsafe_b64encode(
                    hashlib.sha256(params["code_verifier"][0].encode()).digest()
                ).decode().rstrip("=")
                assert computed == self.challenge
                return httpx2.Response(200, json={
                    "access_token": "test-access", "refresh_token": "test-refresh",
                    "token_type": "Bearer", "expires_in": 60,
                })
            assert grant == "refresh_token"
            assert params["refresh_token"] == ["test-refresh"]
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "token_type": "Bearer", "expires_in": 120,
            })
        raise AssertionError(f"unexpected mock OAuth endpoint: {request.url.host}{request.url.path}")


async def _request(provider: OAuthClientProvider, server: _OAuthServer) -> httpx2.Response:
    try:
        async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(server)) as client:
            return await client.get(_RESOURCE)
    finally:
        await close_oauth_provider(provider)


async def _expired_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[RemoteConnection, RemoteStore, _OAuthServer]:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    store = RemoteStore(tmp_path)
    server = _OAuthServer()
    now = [1000.0]
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.time.time", lambda: now[0])
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    assert (await _request(create_oauth_provider(remote, store), server)).status_code == 200
    now[0] = 1100.0
    return remote, store, server


async def _tagged_request(provider: OAuthClientProvider, handler: Any, caller: str) -> httpx2.Response:
    try:
        async with httpx2.AsyncClient(
            auth=provider, transport=httpx2.MockTransport(handler), headers={"X-Fixture-Caller": caller},
        ) as client:
            return await client.get(_RESOURCE)
    finally:
        await close_oauth_provider(provider)


@pytest.mark.anyio
@pytest.mark.parametrize("single_use", [False, True])
async def test_shared_alias_refresh_reloads_latest_cache_before_sending_a_second_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, single_use: bool,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    first_refresh = asyncio.Event()
    release_refresh = asyncio.Event()
    refresh_count = [0]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            refresh_count[0] += 1
            if refresh_count[0] > 1:
                if single_use:
                    return httpx2.Response(400, json={"error": "invalid_grant"})
                return httpx2.Response(200, json={
                    "access_token": "refreshed-access", "refresh_token": "later-generation",
                    "token_type": "Bearer", "expires_in": 600,
                })
            first_refresh.set()
            await release_refresh.wait()
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "refresh_token": "current-generation",
                "token_type": "Bearer", "expires_in": 600,
            })
        return server(request)

    one = create_oauth_provider(remote, store, interactive=False)
    two = create_oauth_provider(remote, store, interactive=False)
    first = asyncio.create_task(_request(one, handler))
    await asyncio.wait_for(first_refresh.wait(), 2)
    second = asyncio.create_task(_request(two, handler))
    try:
        await asyncio.sleep(0.02)
        assert refresh_count[0] == 1
        release_refresh.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert [response.status_code for response in responses] == [200, 200]
        assert refresh_count[0] == 1
        saved = await FileTokenStorage(remote, store.directory).get_tokens()
        assert saved is not None and saved.refresh_token == "current-generation"
        assert server.browser_count == 1
        assert stat.S_IMODE((store.directory / "test.refresh.lock").stat().st_mode) == 0o600
    finally:
        release_refresh.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.anyio
async def test_slow_business_request_and_same_provider_waiter_do_not_hold_alias_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    slow_business_started = asyncio.Event()
    release_business = asyncio.Event()
    refresh_count = [0]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            refresh_count[0] += 1
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "refresh_token": "current-generation",
                "token_type": "Bearer", "expires_in": 600,
            })
        if request.headers.get("X-Fixture-Caller") == "slow":
            slow_business_started.set()
            await release_business.wait()
        return server(request)

    shared = create_oauth_provider(remote, store, interactive=False)
    slow = asyncio.create_task(_tagged_request(shared, handler, "slow"))
    await asyncio.wait_for(slow_business_started.wait(), 2)
    queued = asyncio.create_task(_tagged_request(shared, handler, "queued"))
    try:
        await asyncio.sleep(0.02)
        independent = create_oauth_provider(remote, store, interactive=False)
        response = await asyncio.wait_for(_tagged_request(independent, handler, "independent"), 0.5)
        assert response.status_code == 200
        assert not slow.done() and not queued.done()
        release_business.set()
        completed = await asyncio.wait_for(asyncio.gather(slow, queued), 2)
        assert [response.status_code for response in completed] == [200, 200]
        assert refresh_count[0] == 1
    finally:
        release_business.set()
        for task in (slow, queued):
            if not task.done():
                task.cancel()
        await asyncio.gather(slow, queued, return_exceptions=True)


@pytest.mark.anyio
async def test_cancelled_same_provider_waiter_cannot_release_another_flows_refresh_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    refresh_count = [0]
    monkeypatch.setattr("maxcompute_semantic.remote.oauth._CREDENTIAL_LOCK_WAIT_SECONDS", 0.04)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            refresh_count[0] += 1
            refresh_started.set()
            await release_refresh.wait()
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "refresh_token": "current-generation",
                "token_type": "Bearer", "expires_in": 600,
            })
        return server(request)

    shared = create_oauth_provider(remote, store, interactive=False)
    owner = asyncio.create_task(_request(shared, handler))
    await asyncio.wait_for(refresh_started.wait(), 2)
    waiter = asyncio.create_task(_request(shared, handler))
    try:
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        with pytest.raises(RemoteError) as caught:
            await _request(create_oauth_provider(remote, store, interactive=False), handler)
        assert str(caught.value.code) == "RemoteAuthBusy"
        assert refresh_count[0] == 1
        release_refresh.set()
        assert (await asyncio.wait_for(owner, 2)).status_code == 200
        assert (await _request(create_oauth_provider(remote, store, interactive=False), handler)).status_code == 200
        assert refresh_count[0] == 1
    finally:
        release_refresh.set()
        for task in (owner, waiter):
            if not task.done():
                task.cancel()
        await asyncio.gather(owner, waiter, return_exceptions=True)


@pytest.mark.anyio
async def test_cancelled_refresh_owner_releases_alias_lock_without_background_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    refresh_started = asyncio.Event()
    never_complete = asyncio.Event()
    refresh_count = [0]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            refresh_count[0] += 1
            if refresh_count[0] == 1:
                refresh_started.set()
                await never_complete.wait()
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "refresh_token": "current-generation",
                "token_type": "Bearer", "expires_in": 600,
            })
        return server(request)

    owner = asyncio.create_task(_request(create_oauth_provider(remote, store, interactive=False), handler))
    await asyncio.wait_for(refresh_started.wait(), 2)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    response = await asyncio.wait_for(_request(create_oauth_provider(remote, store, interactive=False), handler), 1)
    assert response.status_code == 200
    assert refresh_count[0] == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 500])
async def test_refresh_error_releases_alias_lock_for_a_fresh_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    refresh_count = [0]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            refresh_count[0] += 1
            if refresh_count[0] == 1:
                return httpx2.Response(status, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={
                "access_token": "refreshed-access", "refresh_token": "current-generation",
                "token_type": "Bearer", "expires_in": 600,
            })
        return server(request)

    with pytest.raises(RemoteError) as caught:
        await _request(create_oauth_provider(remote, store, interactive=False), handler)
    assert str(caught.value.code) == "RemoteAuthRequired"
    response = await asyncio.wait_for(_request(create_oauth_provider(remote, store, interactive=False), handler), 1)
    assert response.status_code == 200
    assert refresh_count[0] == 2


@pytest.mark.anyio
async def test_bounded_lock_contention_returns_classified_busy_without_refreshing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    monkeypatch.setattr("maxcompute_semantic.remote.oauth._CREDENTIAL_LOCK_WAIT_SECONDS", 0.03)
    sentinel = store.directory / "test.refresh.lock"
    with FileLock(sentinel, mode=0o600), pytest.raises(RemoteError) as caught:
        await _request(create_oauth_provider(remote, store, interactive=False), server)
    assert str(caught.value.code) == "RemoteAuthBusy"
    assert server.grants == ["authorization_code"]
    assert (await _request(create_oauth_provider(remote, store, interactive=False), server)).status_code == 200
    assert server.grants == ["authorization_code", "refresh_token"]
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o600


@pytest.mark.anyio
@pytest.mark.parametrize("nonregular", ["symlink", "fifo"])
async def test_lock_sentinel_rejects_symlink_and_fifo_without_touching_another_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nonregular: str,
) -> None:
    remote, store, server = await _expired_login(tmp_path, monkeypatch)
    sentinel = store.directory / "test.refresh.lock"
    sentinel.unlink()
    other = tmp_path / "user-file"
    other.write_text("preserved user content")
    if nonregular == "symlink":
        sentinel.symlink_to(other)
    else:
        os.mkfifo(sentinel, 0o600)
    with pytest.raises(RemoteError) as caught:
        await _request(create_oauth_provider(remote, store, interactive=False), server)
    assert str(caught.value.code) == "RemoteAuthFailed"
    assert other.read_text() == "preserved user content"
    assert server.grants == ["authorization_code"]


@pytest.mark.anyio
async def test_sdk_pkce_login_persists_private_state_and_refreshes_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    store = RemoteStore(tmp_path / "remotes")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    now = [1000.0]
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.time.time", lambda: now[0])
    provider = create_oauth_provider(remote, store)
    assert isinstance(provider, OAuthClientProvider)
    assert (await _request(provider, server)).json() == {"authenticated": True}
    assert server.metadata_requests == ["resource", "issuer"]
    assert server.grants == ["authorization_code"]
    assert server.registration_count == server.browser_count == 1
    assert server.callback_responses == [b"You can close this browser tab."]
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    for name in ("test.tokens.json", "test.client.json"):
        assert stat.S_IMODE((store.directory / name).stat().st_mode) == 0o600
    token_record = json.loads((store.directory / "test.tokens.json").read_text())
    assert token_record["expires_at"] == 1060.0
    assert token_record["server_url"] == _RESOURCE

    # A fresh process with a still-valid token needs no browser or registration.
    second = create_oauth_provider(remote, store, interactive=False)
    assert (await _request(second, server)).status_code == 200
    assert server.grants == ["authorization_code"]

    # Persisted expiry must not reset on restart; refresh uses the discovered
    # AS's non-default exchange endpoint, not the MCP resource origin /token.
    now[0] = 1100.0
    third = create_oauth_provider(remote, store, interactive=False)
    assert (await _request(third, server)).status_code == 200
    assert server.grants == ["authorization_code", "refresh_token"]
    assert server.registration_count == server.browser_count == 1
    assert server.metadata_requests == ["resource", "issuer"]
    refreshed = await FileTokenStorage(remote, store.directory).get_tokens()
    assert refreshed is not None and refreshed.refresh_token == "test-refresh"
    assert "test-access" not in capsys.readouterr().out
    assert "test-refresh" not in capsys.readouterr().err


@pytest.mark.anyio
@pytest.mark.parametrize("challenge", [401, 403])
async def test_same_provider_can_authorize_again_after_revocation_or_scope_step_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, challenge: int,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    reject_next = [False]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == _RESOURCE and reject_next[0]:
            reject_next[0] = False
            header = (
                'Bearer resource_metadata="https://resource.example.test/metadata"'
                if challenge == 401 else 'Bearer error="insufficient_scope", scope="semantic:read"'
            )
            return httpx2.Response(challenge, headers={"WWW-Authenticate": header})
        return server(request)

    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    redirect = str(provider.context.client_metadata.redirect_uris[0])
    try:
        async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
            assert (await client.get(_RESOURCE)).status_code == 200
            reject_next[0] = True
            assert (await client.get(_RESOURCE)).status_code == 200
            assert str(provider.context.client_metadata.redirect_uris[0]) == redirect
            if challenge == 403:
                assert provider.context.current_tokens is not None
                assert provider.context.current_tokens.scope == "semantic:read"
    finally:
        await close_oauth_provider(provider)
    assert server.registration_count == 1
    assert server.browser_count == 2
    assert server.grants == ["authorization_code", "authorization_code"]
    for suffix in ("tokens.json", "client.json"):
        assert stat.S_IMODE((tmp_path / f"test.{suffix}").stat().st_mode) == 0o600
    with socket.socket() as available:
        available.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        available.bind(("127.0.0.1", urlparse(redirect).port))


@pytest.mark.anyio
async def test_reauthorization_port_conflict_is_classified_without_replacing_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    reject_next = [False]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == _RESOURCE and reject_next[0]:
            reject_next[0] = False
            return httpx2.Response(401, headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://resource.example.test/metadata"',
            })
        return server(request)

    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    redirect = str(provider.context.client_metadata.redirect_uris[0])
    try:
        async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
            assert (await client.get(_RESOURCE)).status_code == 200
            with socket.socket() as occupied:
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                occupied.bind(("127.0.0.1", urlparse(redirect).port))
                occupied.listen()
                reject_next[0] = True
                with pytest.raises(RemoteError, match="port is unavailable"):
                    await client.get(_RESOURCE)
            assert str(provider.context.client_metadata.redirect_uris[0]) == redirect
    finally:
        await close_oauth_provider(provider)
    assert server.browser_count == 1
    assert server.grants == ["authorization_code"]
    assert "test-access" not in caplog.text
    assert "test-refresh" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["state", "issuer"])
async def test_sdk_rejects_mismatched_callback_without_exchanging_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer(wrong_state=invalid == "state", wrong_issuer=invalid == "issuer")
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    with pytest.raises(OAuthFlowError):
        await _request(provider, server)
    assert server.grants == []
    assert not (tmp_path / "test.tokens.json").exists()


@pytest.mark.anyio
async def test_denied_login_redacts_callback_error_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer(denied=True)
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    with pytest.raises(RemoteError, match="authorization was denied") as caught:
        await _request(provider, server)
    assert "private-error-detail" not in str(caught.value)
    assert server.grants == []


@pytest.mark.anyio
async def test_login_timeout_closes_callback_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", lambda _url: True)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path), callback_timeout=0.02)
    redirect = str(provider.context.client_metadata.redirect_uris[0])
    with pytest.raises(RemoteError, match="timed out"):
        await _request(provider, _OAuthServer())
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", urlparse(redirect).port))


@pytest.mark.anyio
async def test_noninteractive_requests_require_explicit_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    browser_urls: list[str] = []
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", browser_urls.append)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path), interactive=False)
    with pytest.raises(RemoteError) as caught:
        await _request(provider, server)
    assert str(caught.value.code) == "RemoteAuthRequired"
    assert caught.value.remediation == "run mcs remote login test"
    assert browser_urls == []
    assert server.grants == []
    assert server.registration_count == 0
    assert not (tmp_path / "test.client.json").exists()


@pytest.mark.anyio
async def test_no_browser_prints_only_authorization_url_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    provider = create_oauth_provider(remote, RemoteStore(tmp_path), open_browser=False, callback_timeout=0.02)
    with pytest.raises(RemoteError, match="timed out"):
        await _request(provider, _OAuthServer())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "https://identity.example.test/authorize?" in captured.err
    assert "test-access" not in captured.err
    assert "test-refresh" not in captured.err


@pytest.mark.anyio
async def test_loopback_ignores_unknown_path_and_duplicate_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    statuses: list[int] = []

    def browse(url: str) -> bool:
        from urllib.error import HTTPError

        uri = parse_qs(urlparse(url).query)["redirect_uri"][0]
        for target in (uri.replace("/callback", "/favicon.ico"), uri + "?code=x&state=a&state=b"):
            try:
                urlopen(target, timeout=2.0)
            except HTTPError as error:
                statuses.append(error.code)
        return server.browser(url)

    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", browse)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    assert (await _request(provider, server)).status_code == 200
    assert statuses == [404, 400]


@pytest.mark.anyio
async def test_private_storage_rejects_url_drift_without_returning_token(tmp_path: Path) -> None:
    original = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(original, tmp_path)
    await storage.set_tokens(OAuthToken(access_token="private-test-token", expires_in=60))
    changed = RemoteConnection("test", "https://another.example.test/mcp", "test-region")
    with pytest.raises(RemoteError) as caught:
        await FileTokenStorage(changed, tmp_path).get_tokens()
    assert "private-test-token" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize("payload", ["not json with private-test-token", '["private-test-token"]'])
async def test_corrupt_storage_errors_never_include_file_contents(tmp_path: Path, payload: str) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    (tmp_path / "test.tokens.json").write_text(payload)
    with pytest.raises(RemoteError) as caught:
        await FileTokenStorage(remote, tmp_path).get_tokens()
    assert "private-test-token" not in str(caught.value)


@pytest.mark.anyio
async def test_storage_read_restricts_permissions_and_rejects_symlinks(tmp_path: Path) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    await storage.set_tokens(OAuthToken(access_token="private-test-token"))
    storage.tokens_path.chmod(0o644)
    assert (await storage.get_tokens()) is not None
    assert stat.S_IMODE(storage.tokens_path.stat().st_mode) == 0o600
    actual = tmp_path / "elsewhere"
    storage.tokens_path.rename(actual)
    storage.tokens_path.symlink_to(actual)
    with pytest.raises(RemoteError):
        await storage.get_tokens()


@pytest.mark.anyio
async def test_nonregular_credential_file_is_rejected_without_waiting_for_a_writer(tmp_path: Path) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    os.mkfifo(storage.tokens_path)
    with pytest.raises(RemoteError):
        await storage.get_tokens()


@pytest.mark.anyio
async def test_client_registration_reuses_the_same_loopback_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    redirect = str(provider.context.client_metadata.redirect_uris[0])
    await _request(provider, server)
    again = create_oauth_provider(remote, RemoteStore(tmp_path))
    try:
        assert str(again.context.client_metadata.redirect_uris[0]) == redirect
    finally:
        await close_oauth_provider(again)


@pytest.mark.anyio
async def test_cancelled_login_releases_reserved_and_listening_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    started = asyncio.Event()

    def browser(_url: str) -> bool:
        return True

    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", browser)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    redirect = str(provider.context.client_metadata.redirect_uris[0])
    original = provider.context.redirect_handler

    async def on_redirect(url: str) -> None:
        assert original is not None
        await original(url)
        started.set()

    provider.context.redirect_handler = on_redirect
    task = asyncio.create_task(_request(provider, _OAuthServer()))
    await asyncio.wait_for(started.wait(), 2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", urlparse(redirect).port))


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected_without_opening_a_listener(tmp_path: Path, timeout: float) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    with pytest.raises(RemoteError, match="positive and finite"):
        create_oauth_provider(remote, RemoteStore(tmp_path), callback_timeout=timeout)


@pytest.mark.anyio
async def test_client_information_is_stored_independently_and_privately(tmp_path: Path) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    client = OAuthClientInformationFull(
        client_id="registered-test-client", client_secret="private-test-secret",
        redirect_uris=["http://127.0.0.1:54321/callback"],
        token_endpoint_auth_method="none",
    )
    await storage.set_client_info(client)
    assert not storage.tokens_path.exists()
    assert stat.S_IMODE(storage.client_path.stat().st_mode) == 0o600
    restored = await storage.get_client_info()
    assert restored is not None and restored.client_id == "registered-test-client"


@pytest.mark.anyio
async def test_tampered_discovery_issuer_is_not_used_for_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    await _request(provider, server)
    path = tmp_path / "test.client.json"
    record: dict[str, Any] = json.loads(path.read_text())
    record["discovery"]["oauth_metadata"]["issuer"] = "https://different.example.test"
    path.write_text(json.dumps(record))
    again = create_oauth_provider(remote, RemoteStore(tmp_path), interactive=False)
    with pytest.raises(RemoteError, match="invalid or belong"):
        await _request(again, server)
    assert server.grants == ["authorization_code"]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 400])
async def test_token_errors_redact_endpoint_body_in_exception_and_sdk_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, status: int,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    caplog.set_level(logging.DEBUG, logger="mcp.client.auth.oauth2")

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            return httpx2.Response(status, json={
                "access_token": "private-error-token", "expires_in": "private-error-token",
            })
        return server(request)

    provider = create_oauth_provider(remote, RemoteStore(tmp_path))
    try:
        async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(RemoteError, match="token exchange failed") as caught:
                await client.get(_RESOURCE)
    finally:
        await close_oauth_provider(provider)
    assert "private-error-token" not in str(caught.value)
    assert "private-error-token" not in caplog.text
    assert not (tmp_path / "test.tokens.json").exists()


@pytest.mark.anyio
async def test_refresh_validation_error_does_not_log_token_or_reauthorize_implicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", server.browser)
    now = [1000.0]
    monkeypatch.setattr("maxcompute_semantic.remote.oauth.time.time", lambda: now[0])
    await _request(create_oauth_provider(remote, RemoteStore(tmp_path)), server)
    now[0] = 1100.0
    caplog.set_level(logging.DEBUG, logger="mcp.client.auth.oauth2")

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/exchange":
            return httpx2.Response(200, json={
                "access_token": "private-refresh-token", "expires_in": "private-refresh-token",
            })
        return server(request)

    provider = create_oauth_provider(remote, RemoteStore(tmp_path), interactive=False)
    try:
        async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(RemoteError) as caught:
                await client.get(_RESOURCE)
    finally:
        await close_oauth_provider(provider)
    assert str(caught.value.code) == "RemoteAuthRequired"
    assert "private-refresh-token" not in caplog.text
    assert server.browser_count == 1


@pytest.mark.anyio
async def test_atomic_write_failure_preserves_previous_credentials_and_cleans_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    await storage.set_tokens(OAuthToken(access_token="old-private-token"))

    def failed_replace(*_args: object) -> None:
        raise OSError("private-filesystem-error")

    monkeypatch.setattr("maxcompute_semantic.remote.oauth.os.replace", failed_replace)
    with pytest.raises(RemoteError, match="could not save") as caught:
        await storage.set_tokens(OAuthToken(access_token="new-private-token"))
    assert "private-filesystem-error" not in str(caught.value)
    restored = await storage.get_tokens()
    assert restored is not None and restored.access_token == "old-private-token"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["test.tokens.json"]


@pytest.mark.anyio
async def test_browser_failure_prints_manual_url_and_still_receives_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import webbrowser

    remote = RemoteConnection("test", _RESOURCE, "test-region")
    server = _OAuthServer()

    def broken_browser(url: str) -> bool:
        server.browser(url)
        raise webbrowser.Error("no desktop browser")

    monkeypatch.setattr("maxcompute_semantic.remote.oauth.webbrowser.open", broken_browser)
    assert (await _request(create_oauth_provider(remote, RemoteStore(tmp_path)), server)).status_code == 200
    assert "Open this URL" in capsys.readouterr().err


@pytest.mark.anyio
async def test_reserved_registration_port_conflict_is_explicit_and_private(tmp_path: Path) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        await storage.set_client_info(OAuthClientInformationFull(
            client_id="test-client", redirect_uris=[f"http://127.0.0.1:{port}/callback"],
            token_endpoint_auth_method="none",
        ))
        with pytest.raises(RemoteError, match="port is unavailable"):
            create_oauth_provider(remote, RemoteStore(tmp_path))


@pytest.mark.anyio
@pytest.mark.parametrize("uri", ["https://remote.example.test/callback", "http://127.0.0.1:0/callback"])
async def test_stored_callback_cannot_redirect_to_an_external_host(tmp_path: Path, uri: str) -> None:
    remote = RemoteConnection("test", _RESOURCE, "test-region")
    storage = FileTokenStorage(remote, tmp_path)
    await storage.set_client_info(OAuthClientInformationFull(
        client_id="test-client", redirect_uris=[uri], token_endpoint_auth_method="none",
    ))
    with pytest.raises(RemoteError, match="invalid loopback redirect"):
        create_oauth_provider(remote, RemoteStore(tmp_path))
