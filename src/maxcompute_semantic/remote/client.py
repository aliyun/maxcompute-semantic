# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Synchronous SemanticSpec facade over the official MCP HTTP client."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from contextvars import ContextVar
from typing import Any

from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore

_MAX_TOOL_PAGES = 100
_HTTP_TIMEOUT_SECONDS = 30.0
_HTTP_READ_TIMEOUT_SECONDS = 300.0
_PRIVATE_REMOTE_LOGS: ContextVar[bool] = ContextVar(
    "mcs_private_remote_logs", default=False
)
_SDK_LOGGERS = (
    "mcp.client.streamable_http",
    "mcp.client.session",
    "mcp.client.client",
    "mcp.shared.jsonrpc_dispatcher",
)


class _PrivateRemoteLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK protocol validation errors may include a reflected bearer in
        # their raw Pydantic input. Scope suppression to this caller's tasks.
        if _PRIVATE_REMOTE_LOGS.get():
            record.msg = "MCP SDK transport event"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_PRIVATE_REMOTE_LOG_FILTER = _PrivateRemoteLogFilter()


class SemanticSpecClient:
    """Initialize, discover tools, and preserve Gateway tool envelopes."""

    def __init__(
        self, remote: RemoteConnection, *, store: RemoteStore | None = None
    ) -> None:
        self.connection = remote
        self.store = store or RemoteStore()

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not tool or not isinstance(arguments, dict):
            raise RemoteError(
                "tool name and argument object are required",
                code="RemoteInvalidArgument",
            )
        return self._run(tool, arguments)

    def list_tools(self) -> list[str]:
        return self._run(None, None)["tools"]  # type: ignore[no-any-return]

    def login(
        self, *, open_browser: bool = True, callback_timeout: float = 180.0
    ) -> dict[str, Any]:
        result = self._run(
            None,
            None,
            interactive=True,
            open_browser=open_browser,
            callback_timeout=callback_timeout,
        )
        return {
            "alias": self.connection.alias,
            "authenticated": True,
            "tools": result["tools"],
        }

    def _run(
        self,
        tool: str | None,
        arguments: dict[str, Any] | None,
        *,
        interactive: bool = False,
        open_browser: bool = True,
        callback_timeout: float = 180.0,
    ) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RemoteError(
                "use the remote client outside a running event loop",
                code="RemoteInvalidArgument",
            )
        try:
            return asyncio.run(
                self._request(
                    tool,
                    arguments,
                    interactive=interactive,
                    open_browser=open_browser,
                    callback_timeout=callback_timeout,
                )
            )
        except RemoteError:
            raise
        except Exception as exc:  # noqa: BLE001 — sanitize SDK/network errors at the public boundary
            # SDK/http errors can include response bodies, request URLs and tokens.
            # Keep the public error classified without reproducing those details.
            classified = _remote_error_in_group(exc)
            if classified is not None:
                raise classified from None
            raise RemoteError(
                "MCP request failed; the remote operation outcome may be unknown",
                code="RemoteTransportError",
                remediation="check authentication or connectivity; read back remote state before retrying a write",
            ) from None

    async def _request(
        self,
        tool: str | None,
        arguments: dict[str, Any] | None,
        *,
        interactive: bool,
        open_browser: bool,
        callback_timeout: float,
    ) -> dict[str, Any]:
        # Keep the official SDK and HTTP stack off the local CLI startup path.
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        from maxcompute_semantic.remote.oauth import (
            close_oauth_provider,
            create_oauth_provider,
        )

        provider = None
        headers: dict[str, str] = {}
        if self.connection.token_env is not None:
            token = os.environ.get(self.connection.token_env, "").strip()
            if not token or any(ch.isspace() for ch in token):
                raise RemoteError(
                    "configured bearer environment variable is empty or invalid",
                    code="RemoteAuthRequired",
                    remediation="set the configured token environment variable or use an OAuth connection",
                )
            headers["Authorization"] = f"Bearer {token}"
        else:
            provider = create_oauth_provider(
                self.connection,
                self.store,
                interactive=interactive,
                open_browser=open_browser,
                callback_timeout=callback_timeout,
            )
        logging_scope = _PRIVATE_REMOTE_LOGS.set(True)
        for logger_name in _SDK_LOGGERS:
            logging.getLogger(logger_name).addFilter(_PRIVATE_REMOTE_LOG_FILTER)
        try:
            async with (
                httpx2.AsyncClient(
                    headers=headers,
                    auth=provider,
                    timeout=httpx2.Timeout(
                        _HTTP_TIMEOUT_SECONDS, read=_HTTP_READ_TIMEOUT_SECONDS
                    ),
                ) as http,
                Client(
                    streamable_http_client(self.connection.url, http_client=http),
                    mode="legacy",
                ) as client,
            ):
                tools = await self._tool_names(client)
                if tool is None:
                    return {"tools": tools}
                if tool not in tools:
                    raise RemoteError(
                        "requested SemanticSpec tool is unavailable on this remote",
                        code="RemoteToolUnavailable",
                    )
                result = await client.call_tool(tool, arguments)
                return self._gateway_envelope(result)
        finally:
            try:
                if provider is not None:
                    await close_oauth_provider(provider)
            finally:
                _PRIVATE_REMOTE_LOGS.reset(logging_scope)

    @staticmethod
    async def _tool_names(client: Any) -> list[str]:
        names: list[str] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(_MAX_TOOL_PAGES):
            listing = await client.list_tools(cursor=cursor)
            names.extend(tool.name for tool in listing.tools)
            cursor = listing.next_cursor
            if cursor is None:
                return names
            if cursor in seen:
                break
            seen.add(cursor)
        raise RemoteError(
            "remote tool pagination did not terminate", code="RemoteProtocolError"
        )

    @staticmethod
    def _gateway_envelope(result: Any) -> dict[str, Any]:
        payload = result.structured_content
        text_blocks = [
            block.text
            for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        if payload is None:
            if len(text_blocks) != 1:
                raise RemoteError(
                    "remote tool returned no unambiguous JSON envelope",
                    code="RemoteProtocolError",
                )
            try:
                payload = _parse_json_text(text_blocks[0])
            except (TypeError, ValueError):
                raise RemoteError(
                    "remote tool returned invalid JSON", code="RemoteProtocolError"
                ) from None
        else:
            json_texts: list[Any] = []
            for text in text_blocks:
                try:
                    json_texts.append(_parse_json_text(text))
                except json.JSONDecodeError:
                    # MCP permits explanatory prose alongside structured data.
                    continue
                except (TypeError, ValueError):
                    raise RemoteError(
                        "remote JSON text is not a unique finite representation",
                        code="RemoteProtocolError",
                    ) from None
            if len(json_texts) > 1:
                raise RemoteError(
                    "remote tool returned multiple JSON representations",
                    code="RemoteProtocolError",
                )
            if json_texts:
                if not _same_json_value(payload, json_texts[0]):
                    raise RemoteError(
                        "remote structured and JSON text representations disagree",
                        code="RemoteProtocolError",
                    )
                # Gateway hashes native Go struct JSON before a structured map
                # may reorder its keys. Preserve the original text's order only
                # after both channels agree on every value and JSON type.
                payload = json_texts[0]
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise RemoteError(
                "remote tool did not return a Gateway envelope",
                code="RemoteProtocolError",
            )
        if result.is_error and payload["ok"]:
            raise RemoteError(
                "remote tool returned contradictory success states",
                code="RemoteProtocolError",
            )
        return dict(payload)


def _parse_json_text(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
        parse_float=_finite_json_float,
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number exceeds finite floating point range")
    return number


def _same_json_value(left: Any, right: Any) -> bool:
    """Object ordering is immaterial; types and array ordering must agree."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _remote_error_in_group(exc: BaseException) -> RemoteError | None:
    """AnyIO wraps body errors in ExceptionGroup, including on Python 3.10."""
    if isinstance(exc, RemoteError):
        return exc
    for child in getattr(exc, "exceptions", ()):
        classified = _remote_error_in_group(child)
        if classified is not None:
            return classified
    return None
