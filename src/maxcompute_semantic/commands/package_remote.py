# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Explicit remote SemanticSpec workflows under ``mcs package``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.errors import McsError
from maxcompute_semantic.remote.client import SemanticSpecClient
from maxcompute_semantic.remote.config import RemoteStore
from maxcompute_semantic.remote.sync import (
    SECTIONS,
    SOURCES,
    SemanticPackageSync,
    SemanticSyncError,
    load_section_file,
)

_DEFAULT_WORKSPACE = ".mcs-remote"


def _workspace_option(function: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--workspace", type=click.Path(file_okay=False, path_type=Path),
        default=_DEFAULT_WORKSPACE, show_default=True,
        help="native JSON sidecar directory; independent of MaxCompute profiles",
    )(function)


def _sync(workspace: Path, *, remote: str | None = None) -> SemanticPackageSync:
    if remote is None:
        state_path = workspace / "state.json"
        if not state_path.exists():
            raise SemanticSyncError("bind this workspace first", code="SemanticNotBound", exit_code=2)
        state = load_section_file(state_path)
        binding = state.get("binding") if isinstance(state, dict) else None
        if not isinstance(binding, dict) or not isinstance(binding.get("remote_alias"), str):
            raise SemanticSyncError("workspace binding is invalid", code="SemanticStateInvalid", exit_code=2)
        remote = binding["remote_alias"]
    connection = RemoteStore().get(remote)
    return SemanticPackageSync(workspace, SemanticSpecClient(connection))


def _emit(ctx: click.Context, action: Callable[[], dict[str, Any]]) -> None:
    options = ctx.obj or {}
    renderer = Renderer(format=options.get("format", "plain"), quiet=options.get("quiet", False))
    try:
        result = action()
    except McsError as exc:
        renderer.error(exc)
        ctx.exit(exc.exit_code)
    renderer.success(result)


@click.command("bind-remote")
@click.argument("remote")
@click.option("--name", required=True, help="exact existing SemanticSpec name")
@click.option("--region", default=None, help="routing region; defaults to the named connection")
@_workspace_option
@click.pass_context
def bind_remote_cmd(ctx: click.Context, remote: str, name: str, region: str | None, workspace: Path) -> None:
    """Bind native semantic JSON to a remote account/object; no MC credentials."""
    def action() -> dict[str, Any]:
        sync = _sync(workspace, remote=remote)
        return sync.bind(name, region or sync.client.connection.region)
    _emit(ctx, action)


@click.command("pull")
@click.option("--source", type=click.Choice(SOURCES), default="PUBLISHED", show_default=True)
@click.option("--section", "sections", type=click.Choice(SECTIONS), multiple=True, help="complete section; repeat to select several (default: all seven)")
@click.option("--revision", default=None, help="explicit immutable PUBLISHED revision")
@_workspace_option
@click.pass_context
def pull_cmd(ctx: click.Context, source: str, sections: tuple[str, ...], revision: str | None, workspace: Path) -> None:
    """Pull native sections with verified hashes; preserve divergent local work."""
    _emit(ctx, lambda: _sync(workspace).pull(source, sections or SECTIONS, revision))


@click.command("diff")
@_workspace_option
@click.pass_context
def diff_cmd(ctx: click.Context, workspace: Path) -> None:
    """Compare base/local/remote and show the exact approval hash."""
    _emit(ctx, lambda: _sync(workspace).diff())


@click.command("push")
@click.option("--apply", is_flag=True, help="write the exact reviewed plan to USER_DRAFT")
@click.option("--dry-run", is_flag=True, help="show the plan without writes (the default)")
@click.option("--plan-hash", "approved_plan_hash", default=None, help="approval hash returned by diff or dry-run; required with --apply")
@_workspace_option
@click.pass_context
def push_cmd(ctx: click.Context, apply: bool, dry_run: bool, approved_plan_hash: str | None, workspace: Path) -> None:
    """Preview by default; apply preserves complete sections and never publishes."""
    if apply and dry_run:
        raise click.UsageError("--apply and --dry-run are mutually exclusive")
    _emit(ctx, lambda: _sync(workspace).push(dry_run=not apply, approved_plan_hash=approved_plan_hash))


@click.command("edit-section")
@click.argument("section", type=click.Choice(SECTIONS))
@click.option("--file", "input_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True, help="native section JSON (not a package database)")
@click.option("--replace", is_flag=True, help="explicitly replace the whole local section, including removals")
@click.option("--remove", "removals", multiple=True, help="explicit existing entry key to remove; repeat as needed")
@_workspace_option
@click.pass_context
def edit_section_cmd(ctx: click.Context, section: str, input_file: Path, replace: bool, removals: tuple[str, ...], workspace: Path) -> None:
    """Merge local named entries; retain unrelated entries and unknown fields."""
    _emit(ctx, lambda: _sync(workspace).edit_section(section, load_section_file(input_file), replace=replace, removals=removals))


@click.command("publish")
@click.option("--revision", "expected_revision", required=True, help="exact USER_DRAFT revision reviewed across all seven sections")
@click.option("--confirm", is_flag=True, help="explicitly authorize publication of that reviewed revision")
@_workspace_option
@click.pass_context
def publish_cmd(ctx: click.Context, expected_revision: str, confirm: bool, workspace: Path) -> None:
    """Publish an explicitly confirmed complete draft; require read-back evidence."""
    _emit(ctx, lambda: _sync(workspace).publish(expected_revision, confirm=confirm))


def register_package_remote_commands(group: click.Group) -> None:
    """Attach workflows without changing existing profile-backed commands."""
    for command in (bind_remote_cmd, pull_cmd, diff_cmd, push_cmd, edit_section_cmd, publish_cmd):
        group.add_command(command)
