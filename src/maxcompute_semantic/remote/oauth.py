# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Native MCP SDK OAuth with private storage and a loopback callback.

The SDK owns discovery, registration, PKCE, state/issuer checks and refresh.
This adapter only persists its state and transports the browser callback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import socket
import stat
import tempfile
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from filelock import FileLock, Timeout
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, OAuthFlowError
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyUrl, ValidationError

from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore

_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700
_CALLBACK_PATH = "/callback"
_MAX_HTTP_HEADER_BYTES = 8192
_HTTP_HEADER_TIMEOUT = 5.0
_STORAGE_VERSION = 1
_CREDENTIAL_LOCK_WAIT_SECONDS = 10.0
_CREDENTIAL_LOCK_POLL_SECONDS = 0.05
_PRIVATE_OAUTH_LOGS: ContextVar[bool] = ContextVar("mcs_private_oauth_logs", default=False)
_ACTIVE_CREDENTIAL_LEASE: ContextVar[_CredentialLease | None] = ContextVar(
    "mcs_oauth_credential_lease", default=None
)


class _CredentialLease:
    """One SDK flow's bounded, cancellation-safe cross-process refresh lock."""

    def __init__(self, directory: Path, alias: str) -> None:
        self.directory = directory
        self.path = directory / f"{alias}.refresh.lock"
        self.lock: FileLock | None = None

    async def acquire(self) -> None:
        try:
            self.directory.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
            self.directory.chmod(_PRIVATE_DIRECTORY_MODE)
            # FileLock may truncate its sentinel on open. Validate and create
            # it safely first so a symlink/FIFO cannot damage another file or
            # hang authentication. The private directory protects publication.
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                _PRIVATE_FILE_MODE,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise RemoteError("OAuth credential lock must be a regular file", code="RemoteAuthFailed")
                os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            finally:
                os.close(descriptor)
            self.lock = FileLock(self.path, timeout=0, mode=_PRIVATE_FILE_MODE, thread_local=False)
        except OSError:
            raise RemoteError("could not lock OAuth credentials", code="RemoteAuthFailed") from None
        deadline = time.monotonic() + _CREDENTIAL_LOCK_WAIT_SECONDS
        while True:
            try:
                # Never queue a blocking worker thread: a cancelled waiter must
                # not acquire a lock later with nobody left to release it.
                self.lock.acquire(timeout=0)
                return
            except Timeout:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError(
                        "OAuth credentials are busy in another process", code="RemoteAuthBusy",
                        remediation="retry after the other authentication request completes",
                    ) from None
                await asyncio.sleep(min(_CREDENTIAL_LOCK_POLL_SECONDS, remaining))
            except OSError:
                raise RemoteError("could not lock OAuth credentials", code="RemoteAuthFailed") from None

    def release(self) -> None:
        if self.lock is not None:
            self.lock.release()
            self.lock = None


class _PrivateOAuthLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # The SDK includes token-endpoint bodies and Pydantic input values in
        # some exception traces. Scope redaction to this adapter's own flow;
        # unrelated SDK clients retain their normal logging behavior.
        if _PRIVATE_OAUTH_LOGS.get():
            record.msg = "MCP OAuth SDK authentication event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_PRIVATE_LOG_FILTER = _PrivateOAuthLogFilter()


class FileTokenStorage:
    """The SDK TokenStorage protocol, scoped to one remote URL and alias."""

    def __init__(self, remote: RemoteConnection, directory: Path) -> None:
        self.remote = remote
        self.directory = directory
        self.tokens_path = directory / f"{remote.alias}.tokens.json"
        self.client_path = directory / f"{remote.alias}.client.json"
        self.provider: OAuthClientProvider | None = None
        self.expires_at: float | None = None

    def read_client_record(self) -> dict[str, Any] | None:
        return self._read(self.client_path)

    async def get_tokens(self) -> OAuthToken | None:
        record = self._read(self.tokens_path)
        if record is None:
            return None
        try:
            expiry = record["expires_at"]
            if expiry is not None and (
                isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry)
            ):
                raise ValueError("invalid expiry")
            self.expires_at = expiry
            return OAuthToken.model_validate(record["tokens"])
        except (KeyError, ValueError, TypeError):
            raise self._invalid_storage() from None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.expires_at = (
            time.time() + tokens.expires_in if tokens.expires_in is not None else None
        )
        self._write(self.tokens_path, {
            "tokens": tokens.model_dump(mode="json"),
            "expires_at": self.expires_at,
        })
        # Discovery state is needed to refresh against a separate AS after a
        # process restart. Only snapshot the SDK's already-validated context.
        if self.provider is not None and self.provider.context.client_info is not None:
            await self.set_client_info(self.provider.context.client_info)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        record = self.read_client_record()
        if record is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(record["client_info"])
        except (KeyError, ValidationError):
            raise self._invalid_storage() from None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        snapshot: dict[str, Any] = {"client_info": client_info.model_dump(mode="json")}
        if self.provider is not None:
            context = self.provider.context
            snapshot["discovery"] = {
                "oauth_metadata": (
                    context.oauth_metadata.model_dump(mode="json")
                    if context.oauth_metadata is not None else None
                ),
                "protected_resource_metadata": (
                    context.protected_resource_metadata.model_dump(mode="json")
                    if context.protected_resource_metadata is not None else None
                ),
                "auth_server_url": context.auth_server_url,
            }
        self._write(self.client_path, snapshot)

    def restore_discovery(self, provider: OAuthClientProvider) -> None:
        record = self.read_client_record()
        discovery = record.get("discovery") if record is not None else None
        if discovery is None:
            return
        try:
            metadata_raw = discovery["oauth_metadata"]
            resource_raw = discovery["protected_resource_metadata"]
            metadata = OAuthMetadata.model_validate(metadata_raw) if metadata_raw else None
            resource = ProtectedResourceMetadata.model_validate(resource_raw) if resource_raw else None
            server = discovery["auth_server_url"]
            expected = server or _origin(self.remote.url)
            if metadata is not None and str(metadata.issuer).rstrip("/") != expected.rstrip("/"):
                raise ValueError("issuer mismatch")
            if resource is not None and server not in [str(url) for url in resource.authorization_servers]:
                raise ValueError("authorization server mismatch")
            provider.context.oauth_metadata = metadata
            provider.context.protected_resource_metadata = resource
            provider.context.auth_server_url = server
        except (KeyError, TypeError, ValueError, AttributeError):
            raise self._invalid_storage() from None

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            fd = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise self._invalid_storage() from None
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise self._invalid_storage()
                os.fchmod(stream.fileno(), _PRIVATE_FILE_MODE)
                record = json.load(stream)
            if (
                not isinstance(record, dict)
                or record.get("version") != _STORAGE_VERSION
                or record.get("server_url") != self.remote.url
            ):
                raise self._invalid_storage()
            return record
        except (OSError, ValueError):
            raise self._invalid_storage() from None

    def _write(self, path: Path, values: dict[str, Any]) -> None:
        temporary: str | None = None
        try:
            self.directory.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
            self.directory.chmod(_PRIVATE_DIRECTORY_MODE)
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), _PRIVATE_FILE_MODE)
                json.dump({"version": _STORAGE_VERSION, "server_url": self.remote.url, **values}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            raise RemoteError("could not save private OAuth credentials") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _invalid_storage(self) -> RemoteError:
        return RemoteError(
            "stored OAuth credentials are invalid or belong to a different remote",
            remediation=f"remove the saved login and run mcs remote login {self.remote.alias}",
        )


class _LoopbackCallback:
    def __init__(self, redirect_uri: str | None, timeout: float, open_browser: bool) -> None:
        port = 0
        if redirect_uri is not None:
            try:
                parsed = urlparse(redirect_uri)
                port = parsed.port or 0
            except ValueError:
                raise RemoteError("stored OAuth client has an invalid loopback redirect") from None
            if (
                parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.path != _CALLBACK_PATH or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None
                or port == 0
            ):
                raise RemoteError("stored OAuth client has an invalid loopback redirect")
        self.socket: socket.socket | None = _reserve_callback_socket(port)
        self.port = self.socket.getsockname()[1]
        self.redirect_uri = f"http://127.0.0.1:{self.port}{_CALLBACK_PATH}"
        self.timeout = timeout
        self.open_browser = open_browser
        self.server: asyncio.Server | None = None
        self.result: asyncio.Future[AuthorizationCodeResult] | None = None
        self.tasks: set[asyncio.Task[None]] = set()

    async def redirect(self, authorization_url: str) -> None:
        self.result = asyncio.get_running_loop().create_future()
        # The SDK can authorize again in the same session after a revoked
        # token or scope step-up. Reuse the registered redirect, never a new
        # ephemeral port after the previous flow closed its listener.
        if self.socket is None:
            self.socket = _reserve_callback_socket(self.port)
        try:
            self.server = await asyncio.start_server(
                self._receive, sock=self.socket, limit=_MAX_HTTP_HEADER_BYTES
            )
        except OSError:
            raise RemoteError("OAuth callback port is unavailable; retry when it is free") from None
        self.socket = None  # asyncio now owns it.
        opened = False
        if self.open_browser:
            try:
                opened = await asyncio.to_thread(webbrowser.open, authorization_url)
            except (webbrowser.Error, OSError):
                opened = False
        if not opened:
            import click

            click.echo(f"Open this URL to log in:\n{authorization_url}", err=True)

    async def callback(self) -> AuthorizationCodeResult:
        try:
            if self.result is None:
                raise RemoteError("OAuth callback listener was not started")
            return await asyncio.wait_for(self.result, timeout=self.timeout)
        except asyncio.TimeoutError:
            raise RemoteError("OAuth login timed out waiting for the browser callback") from None
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        result: AuthorizationCodeResult | RemoteError | None = None
        status = "400 Bad Request"
        try:
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HTTP_HEADER_TIMEOUT)
            method, target, _version = headers.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            parsed = urlparse(target)
            if method != "GET" or parsed.path != _CALLBACK_PATH or not target.startswith("/"):
                status = "404 Not Found"
            else:
                params = parse_qs(parsed.query, keep_blank_values=True)
                if any(len(params.get(key, [])) > 1 for key in ("code", "state", "iss", "error")):
                    status = "400 Bad Request"
                elif "error" in params:
                    result = RemoteError("OAuth authorization was denied")
                    status = "200 OK"
                elif params.get("code", [""])[0] and params.get("state", [""])[0]:
                    result = AuthorizationCodeResult(
                        code=params["code"][0], state=params["state"][0],
                        iss=params.get("iss", [None])[0],
                    )
                    status = "200 OK"
            body = b"You can close this browser tab."
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                "Referrer-Policy: no-referrer\r\nConnection: close\r\n\r\n".encode("ascii") + body
            )
            await writer.drain()
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if task is not None:
                self.tasks.discard(task)
        if result is not None and self.result is not None and not self.result.done():
            if isinstance(result, RemoteError):
                self.result.set_exception(result)
            else:
                self.result.set_result(result)


class _PersistentOAuthProvider(OAuthClientProvider):
    """Restore SDK context omitted by its in-memory initialization hook."""

    def __init__(self, *, file_storage: FileTokenStorage, callback: _LoopbackCallback | None,
                 interactive: bool,
                 **kwargs: Any) -> None:
        super().__init__(storage=file_storage, **kwargs)
        self.file_storage = file_storage
        self.callback = callback
        self.interactive = interactive
        self._startup_lock = asyncio.Lock()
        file_storage.provider = self
        logging.getLogger("mcp.client.auth.oauth2").addFilter(_PRIVATE_LOG_FILTER)

    async def _initialize(self) -> None:
        # The SDK already owns context.lock here. Acquiring the alias lock
        # earlier could hold it while a same-provider caller waits for an
        # unrelated business request to complete.
        lease = _ACTIVE_CREDENTIAL_LEASE.get()
        if lease is None:
            raise RemoteError("OAuth credential lock state is unavailable", code="RemoteAuthFailed")
        await lease.acquire()
        await super()._initialize()
        self.context.token_expiry_time = self.file_storage.expires_at
        self.file_storage.restore_discovery(self)
        if self.context.is_token_valid() or not self.context.can_refresh_token():
            lease.release()

    async def aclose(self) -> None:
        if self.callback is not None:
            await self.callback.aclose()

    async def _handle_token_response(self, response: httpx2.Response) -> None:
        try:
            await super()._handle_token_response(response)
        except (OAuthFlowError, ValidationError):
            raise RemoteError("OAuth token exchange failed", code="RemoteAuthFailed") from None

    async def _handle_refresh_response(self, response: httpx2.Response) -> bool:
        try:
            return await super()._handle_refresh_response(response)
        finally:
            lease = _ACTIVE_CREDENTIAL_LEASE.get()
            if lease is not None:
                lease.release()

    async def _auth_flow(
        self, request: httpx2.Request,
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        # Forward the SDK flow unchanged, except for the product's explicit
        # login boundary. An ordinary call may refresh, but cannot register a
        # browser client or wait for a human after an authentication challenge.
        flow = super()._auth_flow(request)
        log_context = _PRIVATE_OAUTH_LOGS.set(True)
        lease = _CredentialLease(self.file_storage.directory, self.file_storage.remote.alias)
        lease_context = _ACTIVE_CREDENTIAL_LEASE.set(lease)
        try:
            # Reset inside a per-provider startup gate so multiple queued
            # callers each reload disk after obtaining the SDK context lock.
            async with self._startup_lock:
                self._initialized = False
                outgoing = await anext(flow)
            while True:
                response = yield outgoing
                if (
                    not self.interactive and response.status_code == 401
                    and response.request.url == request.url
                ):
                    raise _login_required(self.file_storage.remote)
                outgoing = await flow.asend(response)
        except StopAsyncIteration:
            return
        finally:
            try:
                await flow.aclose()
            finally:
                try:
                    lease.release()
                finally:
                    _ACTIVE_CREDENTIAL_LEASE.reset(lease_context)
                    _PRIVATE_OAUTH_LOGS.reset(log_context)


def create_oauth_provider(
    remote: RemoteConnection,
    store: RemoteStore | None = None,
    *,
    open_browser: bool = True,
    callback_timeout: float = 180.0,
    interactive: bool = True,
) -> OAuthClientProvider:
    """Create SDK auth; interactive login is explicitly selected by callers."""
    if not math.isfinite(callback_timeout) or callback_timeout <= 0:
        raise RemoteError("OAuth callback timeout must be positive and finite")
    storage = FileTokenStorage(remote, (store or RemoteStore()).directory)
    record = storage.read_client_record()
    redirect_uri: str | None = None
    if record is not None:
        try:
            client = OAuthClientInformationFull.model_validate(record["client_info"])
            redirect_uri = str(client.redirect_uris[0]) if client.redirect_uris else None
        except (ValidationError, KeyError):
            raise storage._invalid_storage() from None
    callback = _LoopbackCallback(redirect_uri, callback_timeout, open_browser) if interactive else None

    async def require_login(_authorization_url: str) -> None:
        raise _login_required(remote)

    async def require_callback() -> AuthorizationCodeResult:
        raise _login_required(remote)

    return _PersistentOAuthProvider(
        server_url=remote.url,
        client_metadata=OAuthClientMetadata(
            client_name="mcs", token_endpoint_auth_method="none",
            redirect_uris=[AnyUrl(callback.redirect_uri if callback else (
                redirect_uri or "http://127.0.0.1:0/callback"
            ))],
        ),
        file_storage=storage, callback=callback, interactive=interactive,
        redirect_handler=callback.redirect if callback is not None else require_login,
        callback_handler=callback.callback if callback is not None else require_callback,
    )


async def close_oauth_provider(provider: OAuthClientProvider) -> None:
    """Release reserved/listening loopback sockets, also on failed login."""
    if isinstance(provider, _PersistentOAuthProvider):
        await provider.aclose()


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _login_required(remote: RemoteConnection) -> RemoteError:
    return RemoteError(
        "remote authentication requires login", code="RemoteAuthRequired",
        remediation=f"run mcs remote login {remote.alias}",
    )


def _reserve_callback_socket(port: int) -> socket.socket:
    callback_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    callback_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        callback_socket.bind(("127.0.0.1", port))
    except OSError:
        callback_socket.close()
        raise RemoteError("OAuth callback port is unavailable; retry when it is free") from None
    return callback_socket
