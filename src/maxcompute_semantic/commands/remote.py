# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Manage MCP remotes independently from local MaxCompute profiles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.remote.config import RemoteConnection, RemoteError, RemoteStore


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))


def _perform(ctx: click.Context, operation: Callable[[], dict[str, Any]]) -> None:
    renderer = _renderer(ctx)
    try:
        data = operation()
    except RemoteError as exc:
        renderer.error(exc)
        ctx.exit(exc.exit_code)
    else:
        renderer.success(data)


@click.group(name="remote")
def remote_group() -> None:
    """Configure and authenticate SemanticSpec MCP connections."""


@remote_group.command("add")
@click.argument("alias")
@click.option("--mcp-url", required=True, help="MCP Streamable HTTP endpoint")
@click.option(
    "--region", required=True, help="remote region, stored with sync bindings"
)
@click.option(
    "--token-env",
    default=None,
    help="environment variable containing an existing bearer token",
)
@click.pass_context
def add_cmd(
    ctx: click.Context, alias: str, mcp_url: str, region: str, token_env: str | None
) -> None:
    """Add a connection; secrets are never written into its configuration."""

    def operation() -> dict[str, Any]:
        remote = RemoteConnection(alias, mcp_url, region, token_env)
        RemoteStore().add(remote)
        return remote.to_dict()

    _perform(ctx, operation)


@remote_group.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List connections without reading credentials."""
    _perform(
        ctx, lambda: {"remotes": [remote.to_dict() for remote in RemoteStore().list()]}
    )


@remote_group.command("show")
@click.argument("alias")
@click.pass_context
def show_cmd(ctx: click.Context, alias: str) -> None:
    """Show public connection configuration."""
    _perform(ctx, lambda: RemoteStore().get(alias).to_dict())


@remote_group.command("login")
@click.argument("alias")
@click.option(
    "--no-browser", is_flag=True, help="print the authorization URL for manual opening"
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=1),
    default=180.0,
    help="loopback callback timeout in seconds",
)
@click.pass_context
def login_cmd(ctx: click.Context, alias: str, no_browser: bool, timeout: float) -> None:
    """Log in with native OAuth and confirm the MCP connection."""

    def operation() -> dict[str, Any]:
        from maxcompute_semantic.remote.client import SemanticSpecClient

        remote = RemoteStore().get(alias)
        return SemanticSpecClient(remote).login(
            open_browser=not no_browser, callback_timeout=timeout
        )

    _perform(ctx, operation)


@remote_group.command("remove")
@click.argument("alias")
@click.pass_context
def remove_cmd(ctx: click.Context, alias: str) -> None:
    """Remove one connection and its saved OAuth credentials."""

    def operation() -> dict[str, Any]:
        RemoteStore().remove(alias)
        return {"removed": alias}

    _perform(ctx, operation)
