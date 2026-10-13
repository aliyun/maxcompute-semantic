# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Real Streamable HTTP interoperability through the public remote client."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

from maxcompute_semantic.remote.client import SemanticSpecClient
from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore
from maxcompute_semantic.remote.sync import section_integrity

_BEARER = "fixture-bearer-do-not-log"


@pytest.fixture
def mcp_server(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[tuple[str, list[tuple[str, dict[str, Any]]], list[str]]]:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    calls: list[tuple[str, dict[str, Any]]] = []
    headers: list[str] = []

    async def list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        names = (
            "maxcompute_semanticspec_get",
            "text",
            "gateway_error",
            "invalid",
            "ambiguous",
            "bad_envelope",
            "contradictory",
            "native_order",
            "structured_only",
            "supplementary",
            "dual_mismatch",
            "dual_bool_number",
            "dual_int_float",
            "dual_array_order",
            "dual_ambiguous",
            "duplicate_keys",
            "nonfinite_text",
        )
        if getattr(request, "param", None) == "loop":
            return ListToolsResult(
                tools=[Tool(name="unrelated", input_schema={"type": "object"})],
                next_cursor="cycle",
            )
        cursor = params.cursor if params else None
        return ListToolsResult(
            tools=[
                Tool(name=name, input_schema={"type": "object"})
                for name in (names[:2] if cursor is None else names[2:])
            ],
            next_cursor="second" if cursor is None else None,
        )

    async def call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        arguments = dict(params.arguments or {})
        calls.append((params.name, arguments))
        payload: dict[str, Any] = {
            "ok": True,
            "data": {"name": "spec", "arguments": arguments},
            "metadata": {"revision_id": "rev-1"},
            "future_field": [1, 2],
        }
        if params.name == "nonfinite_text":
            value = arguments["number"]
            return CallToolResult(
                content=[TextContent(text='{"ok":true,"data":{"value":' + value + "}}")]
            )
        if params.name == "native_order":
            native = [
                {
                    "name": "sales",
                    "semanticType": "MEASURE",
                    "baseLogicalTable": "orders",
                    "expr": "amount",
                    "future": {"z": 2, "a": 1},
                }
            ]
            size, digest = section_integrity(native)
            payload["data"]["semantic_spec"] = {
                "sections": [
                    {
                        "section": "metricDefinitions",
                        "content": native,
                        "size_bytes": size,
                        "content_hash": digest,
                    }
                ]
            }
            structured = json.loads(json.dumps(payload, sort_keys=True))
            return CallToolResult(
                content=[TextContent(text=json.dumps(payload))],
                structured_content=structured,
            )
        if params.name == "structured_only":
            return CallToolResult(content=[], structured_content=payload)
        if params.name == "supplementary":
            return CallToolResult(
                content=[
                    TextContent(text="Supplementary explanation"),
                    TextContent(text="Another plain note"),
                ],
                structured_content=payload,
            )
        if params.name.startswith("dual_"):
            structured = json.loads(json.dumps(payload))
            if params.name == "dual_mismatch":
                structured["data"]["name"] = "different"
            elif params.name == "dual_bool_number":
                payload["data"]["typed"] = True
                structured["data"]["typed"] = 1
            elif params.name == "dual_int_float":
                payload["data"]["typed"] = 1
                structured["data"]["typed"] = 1.0
            elif params.name == "dual_array_order":
                structured["future_field"] = [2, 1]
            texts = [TextContent(text=json.dumps(payload))]
            if params.name == "dual_ambiguous":
                texts.append(TextContent(text=json.dumps(payload)))
            return CallToolResult(content=texts, structured_content=structured)
        if params.name == "duplicate_keys":
            return CallToolResult(
                content=[TextContent(text='{"ok":false,"ok":true,"data":{}}')],
                structured_content={"ok": True, "data": {}},
            )
        if params.name == "gateway_error":
            payload = {
                "ok": False,
                "error": {"code": "conflict", "message": "concurrent change"},
                "request_id": "fixture-request",
            }
        if params.name == "invalid":
            return CallToolResult(
                content=[
                    TextContent(
                        text="private upstream detail fixture-bearer-do-not-log"
                    )
                ],
                is_error=True,
            )
        if params.name == "ambiguous":
            return CallToolResult(
                content=[
                    TextContent(text=json.dumps(payload)),
                    TextContent(text=json.dumps(payload)),
                ]
            )
        if params.name == "bad_envelope":
            return CallToolResult(
                content=[TextContent(text=json.dumps({"ok": "true"}))]
            )
        if params.name == "contradictory":
            return CallToolResult(
                content=[TextContent(text=json.dumps(payload))],
                structured_content=payload,
                is_error=True,
            )
        if params.name == "text":
            return CallToolResult(content=[TextContent(text=json.dumps(payload))])
        return CallToolResult(
            content=[TextContent(text=json.dumps(payload))], structured_content=payload
        )

    manager = StreamableHTTPSessionManager(
        app=Server(
            "semantic-spec-fixture", on_list_tools=list_tools, on_call_tool=call_tool
        )
    )

    class HeaderRecorder:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            auth = dict(scope.get("headers", [])).get(b"authorization", b"").decode()
            headers.append(auth)
            if auth != f"Bearer {_BEARER}":
                await JSONResponse({"error": "fixture unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
            if getattr(request, "param", None) == "malformed_wire":
                await JSONResponse({"jsonrpc": _BEARER, "id": 0, "result": {}})(
                    scope, receive, send
                )
                return
            await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with manager.run():
            yield

    app = Starlette(routes=[Mount("/mcp", app=HeaderRecorder())], lifespan=lifespan)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", timeout_graceful_shutdown=1)
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", calls, headers
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def _client(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SemanticSpecClient:
    monkeypatch.setenv("MCS_FIXTURE_BEARER", _BEARER)
    return SemanticSpecClient(
        RemoteConnection("fixture", url, "cn-shanghai", "MCS_FIXTURE_BEARER"),
        store=RemoteStore(tmp_path),
    )


def test_real_http_initialize_discover_call_preserves_gateway_envelope(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, calls, headers = mcp_server
    client = _client(url, tmp_path, monkeypatch)
    result = client.call(
        "maxcompute_semanticspec_get", {"name": "spec", "sections": ["glossary"]}
    )
    assert result["ok"] is True
    assert result["metadata"] == {"revision_id": "rev-1"}
    assert result["future_field"] == [1, 2]
    assert calls == [
        ("maxcompute_semanticspec_get", {"name": "spec", "sections": ["glossary"]})
    ]
    assert headers and all(header == f"Bearer {_BEARER}" for header in headers)
    assert not list(tmp_path.iterdir())


def test_text_envelope_and_gateway_rejection_are_preserved(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(mcp_server[0], tmp_path, monkeypatch)
    assert client.call("text", {})["ok"] is True
    rejection = client.call("gateway_error", {})
    assert rejection["ok"] is False
    assert rejection["error"]["code"] == "conflict"
    assert rejection["request_id"] == "fixture-request"


def test_missing_tool_is_rejected_before_call(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(mcp_server[0], tmp_path, monkeypatch)
    with pytest.raises(RemoteError) as exc:
        client.call("does_not_exist", {})
    assert str(exc.value.code) == "RemoteToolUnavailable"
    assert mcp_server[1] == []


@pytest.mark.parametrize(
    "tool", ["invalid", "ambiguous", "bad_envelope", "contradictory"]
)
def test_protocol_failure_never_echoes_server_detail(
    mcp_server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
) -> None:
    client = _client(mcp_server[0], tmp_path, monkeypatch)
    with pytest.raises(RemoteError) as exc:
        client.call(tool, {})
    assert str(exc.value.code) == "RemoteProtocolError"
    assert _BEARER not in str(exc.value)
    assert _BEARER not in str(exc.value.to_envelope())
    captured = capsys.readouterr()
    assert _BEARER not in captured.out + captured.err


def test_env_bearer_missing_fails_without_network(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = RemoteConnection(
        "fixture", mcp_server[0], "cn-shanghai", "MCS_FIXTURE_BEARER"
    )
    monkeypatch.delenv("MCS_FIXTURE_BEARER", raising=False)
    with pytest.raises(RemoteError) as exc:
        SemanticSpecClient(remote, store=RemoteStore(tmp_path)).list_tools()
    assert str(exc.value.code) == "RemoteAuthRequired"
    assert mcp_server[2] == []


def test_transport_failure_is_classified_without_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    client = _client(url, tmp_path, monkeypatch)
    with pytest.raises(RemoteError) as exc:
        client.call("maxcompute_semanticspec_get", {})
    assert str(exc.value.code) == "RemoteTransportError"
    assert _BEARER not in str(exc.value)


def test_ordinary_call_requires_explicit_login_and_never_opens_browser(
    mcp_server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_calls: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: browser_calls.append(url))
    remote = RemoteConnection("fixture", mcp_server[0], "cn-shanghai")
    with pytest.raises(RemoteError) as exc:
        SemanticSpecClient(remote, store=RemoteStore(tmp_path)).list_tools()
    assert str(exc.value.code) == "RemoteAuthRequired"
    assert "mcs remote login fixture" in exc.value.remediation
    assert browser_calls == []
    # A private coordination sentinel is metadata; unauthenticated reads must
    # still create no OAuth registration or token record.
    assert [path.name for path in tmp_path.iterdir()] == ["fixture.refresh.lock"]
    assert (tmp_path / "fixture.refresh.lock").stat().st_mode & 0o777 == 0o600
    assert mcp_server[1] == []


def test_login_with_existing_bearer_confirms_connection_and_discovers_all_pages(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(mcp_server[0], tmp_path, monkeypatch)
    result = client.login(open_browser=False)
    assert result["authenticated"] is True
    assert result["alias"] == "fixture"
    assert "gateway_error" in result["tools"]
    assert client.list_tools() == result["tools"]
    assert mcp_server[1] == []


@pytest.mark.parametrize("mcp_server", ["loop"], indirect=True)
def test_repeated_tool_cursor_fails_closed_before_call(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(mcp_server[0], tmp_path, monkeypatch)
    with pytest.raises(RemoteError) as exc:
        client.call("maxcompute_semanticspec_get", {})
    assert str(exc.value.code) == "RemoteProtocolError"
    assert mcp_server[1] == []


def test_invalid_arguments_and_event_loop_misuse_are_classified(tmp_path: Path) -> None:
    client = SemanticSpecClient(
        RemoteConnection("fixture", "http://127.0.0.1:1/mcp", "cn-test", "FIXTURE"),
        store=RemoteStore(tmp_path),
    )
    with pytest.raises(RemoteError) as exc:
        client.call("", {})
    assert str(exc.value.code) == "RemoteInvalidArgument"

    async def in_event_loop() -> None:
        with pytest.raises(RemoteError) as loop_error:
            client.list_tools()
        assert str(loop_error.value.code) == "RemoteInvalidArgument"

    asyncio.run(in_event_loop())


def test_dual_channel_preserves_original_native_json_order_for_section_integrity(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = _client(mcp_server[0], tmp_path, monkeypatch).call("native_order", {})
    section = envelope["data"]["semantic_spec"]["sections"][0]
    content = section["content"]
    alphabetized = json.loads(json.dumps(content, sort_keys=True))
    assert section_integrity(alphabetized)[1] != section["content_hash"]
    assert section_integrity(content) == (
        section["size_bytes"],
        section["content_hash"],
    )
    assert list(content[0]) == [
        "name",
        "semanticType",
        "baseLogicalTable",
        "expr",
        "future",
    ]
    assert list(content[0]["future"]) == ["z", "a"]


@pytest.mark.parametrize("tool", ["structured_only", "supplementary"])
def test_structured_only_and_non_json_supplementary_text_remain_supported(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    envelope = _client(mcp_server[0], tmp_path, monkeypatch).call(tool, {})
    assert envelope["ok"] is True
    assert envelope["data"]["name"] == "spec"
    assert envelope["future_field"] == [1, 2]


@pytest.mark.parametrize(
    "tool",
    [
        "dual_mismatch",
        "dual_bool_number",
        "dual_int_float",
        "dual_array_order",
        "dual_ambiguous",
        "duplicate_keys",
    ],
)
def test_dual_channel_values_types_and_array_order_must_agree(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    with pytest.raises(RemoteError) as exc:
        _client(mcp_server[0], tmp_path, monkeypatch).call(tool, {})
    assert str(exc.value.code) == "RemoteProtocolError"


@pytest.mark.parametrize("mcp_server", ["malformed_wire"], indirect=True)
def test_malformed_wire_cannot_echo_bearer_in_sdk_exception_logs(
    mcp_server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(RemoteError) as exc:
        _client(mcp_server[0], tmp_path, monkeypatch).list_tools()
    assert _BEARER not in str(exc.value)
    assert _BEARER not in caplog.text
    logging.getLogger("mcp.client.streamable_http").error("unrelated SDK diagnostic")
    assert "unrelated SDK diagnostic" in caplog.text


@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_numbers_fail_closed_instead_of_becoming_successful_data(
    mcp_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, number: str
) -> None:
    with pytest.raises(RemoteError) as exc:
        _client(mcp_server[0], tmp_path, monkeypatch).call(
            "nonfinite_text", {"number": number}
        )
    assert str(exc.value.code) == "RemoteProtocolError"
