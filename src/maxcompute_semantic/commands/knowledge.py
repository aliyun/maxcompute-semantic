# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Standalone local knowledge commands; no MaxCompute profile is resolved."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ParamSpec

import click

from maxcompute_semantic._internal.artifacts import (
    sqlite_source_paths,
    write_json_artifact,
)
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.errors.base import McsError
from maxcompute_semantic.knowledge.model import (
    MAX_LIMIT,
    NODE_KINDS,
    STATUSES,
    KnowledgeError,
)

if TYPE_CHECKING:
    from maxcompute_semantic.knowledge import KnowledgeStore

_P = ParamSpec("_P")
DEFAULT_KB = Path(".mcs/knowledge.db")


def _configure_context(ctx: click.Context, kb: Path) -> None:
    """Keep the root's output options while giving this group its own path."""
    ctx.meta["knowledge_path"] = kb


def _override_kb(ctx: click.Context, _param: click.Parameter, value: Path | None) -> None:
    if value is not None:
        _configure_context(ctx, value)


def _kb_option(function: Callable[_P, None]) -> Callable[_P, None]:
    """Accept command-local --kb while retaining the group's default path."""
    return click.option(
        "--kb",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        callback=_override_kb,
        expose_value=False,
        help="override the group's local knowledge database path",
    )(function)


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.find_root().obj or {}
    return Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))


def _handle_errors(function: Callable[_P, None]) -> Callable[_P, None]:
    """Render classified local errors in the same format as successful results."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> None:
        try:
            function(*args, **kwargs)
        except McsError as exc:
            ctx = click.get_current_context()
            _renderer(ctx).error(exc)
            ctx.exit(exc.exit_code)

    return wrapped


def _execute(
    ctx: click.Context,
    action: Callable[[KnowledgeStore], dict[str, Any]],
    *,
    create: bool = False,
) -> dict[str, Any]:
    # Keep storage off the CLI import chain and out of profile lifecycle hooks.
    from maxcompute_semantic.knowledge import KnowledgeStore

    with KnowledgeStore(ctx.meta["knowledge_path"], create=create) as store:
        result = action(store)
    renderer = _renderer(ctx)
    renderer.success(result)
    identifier = result.get("id")
    if identifier is None:
        for key in ("proposal", "edge"):
            item = result.get(key)
            if isinstance(item, dict) and item.get("id"):
                identifier = item["id"]
                break
    renderer.quiet_essential({"id": identifier or result.get("kb_id")}, "id")
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise KnowledgeError(
            f"cannot read JSON file {path}: {exc}",
            remediation="use a readable UTF-8 JSON file",
        ) from exc


def _load_object(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise KnowledgeError("JSON input must be an object")
    return value


def _before_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise KnowledgeError("before-value must be valid JSON") from exc


@click.group(name="knowledge")
@click.option(
    "--kb",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_KB,
    show_default=True,
    help="local knowledge database; independent of MaxCompute profiles",
)
@click.pass_context
def knowledge_group(ctx: click.Context, kb: Path) -> None:
    """Ingest, review and share local evidence-backed knowledge."""
    _configure_context(ctx, kb)


@knowledge_group.command("init")
@_kb_option
@click.pass_context
@_handle_errors
def init_cmd(ctx: click.Context) -> None:
    """Create a local knowledge database, or show its existing identity."""
    _execute(ctx, lambda store: store.meta(), create=True)


@knowledge_group.command("ingest")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--scope", default="", help="namespace for this document")
@_kb_option
@click.pass_context
@_handle_errors
def ingest_cmd(ctx: click.Context, path: Path, scope: str) -> None:
    """Ingest UTF-8 Markdown from a file or directory, entirely locally."""
    _execute(ctx, lambda store: store.ingest_markdown(path, scope=scope), create=True)


@knowledge_group.command("search")
@click.argument("query")
@click.option("--scope", default=None, help="search only this namespace")
@click.option("--limit", type=click.IntRange(1, MAX_LIMIT), default=20, show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def search_cmd(ctx: click.Context, query: str, scope: str | None, limit: int) -> None:
    """Search local documents and knowledge nodes."""
    _execute(ctx, lambda store: {"results": store.search(query, scope=scope, limit=limit)})


@knowledge_group.command("list")
@click.option("--kind", type=click.Choice(sorted(NODE_KINDS)), default=None)
@click.option("--scope", default=None, help="list only this namespace")
@click.option("--status", type=click.Choice(sorted(STATUSES)), default=None)
@click.option("--limit", type=click.IntRange(1, MAX_LIMIT), default=50, show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def list_cmd(
    ctx: click.Context,
    kind: str | None,
    scope: str | None,
    status: str | None,
    limit: int,
) -> None:
    """List knowledge nodes, optionally filtered by kind, scope or state."""
    _execute(
        ctx,
        lambda store: {"nodes": store.list(kind=kind, scope=scope, status=status, limit=limit)},
    )


@knowledge_group.command("show")
@click.argument("identifier")
@click.option("--scope", default=None, help="disambiguate a name within this namespace")
@_kb_option
@click.pass_context
@_handle_errors
def show_cmd(ctx: click.Context, identifier: str, scope: str | None) -> None:
    """Show a node, document, evidence, relation or proposal by ID."""
    _execute(ctx, lambda store: store.show(identifier, scope=scope))


@knowledge_group.command("propose")
@click.argument("path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--expected-revision", type=click.IntRange(min=0), default=None)
@click.option("--before-value", default=None, help="expected prior value as JSON")
@click.option("--created-by", default="user", show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def propose_cmd(
    ctx: click.Context,
    path: Path,
    expected_revision: int | None,
    before_value: str | None,
    created_by: str,
) -> None:
    """Submit a JSON node/relation patch for explicit review."""
    patch = _load_object(path)
    options: dict[str, Any] = {
        "expected_revision": expected_revision,
        "created_by": created_by,
    }
    if before_value is not None:
        options["before_value"] = _before_value(before_value)
    _execute(
        ctx,
        lambda store: store.propose(patch, **options),
    )


@knowledge_group.command("apply")
@click.argument("proposal_id")
@click.option("--reviewed-by", default="user", show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def apply_cmd(ctx: click.Context, proposal_id: str, reviewed_by: str) -> None:
    """Apply a proposal after checking its revision and prior value."""
    _execute(ctx, lambda store: store.apply(proposal_id, reviewed_by=reviewed_by))


@knowledge_group.command("reject")
@click.argument("proposal_id")
@click.option("--reason", default="", help="review rationale")
@click.option("--reviewed-by", default="user", show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def reject_cmd(
    ctx: click.Context, proposal_id: str, reason: str, reviewed_by: str
) -> None:
    """Reject a proposal while retaining its review history."""
    _execute(
        ctx, lambda store: store.reject(proposal_id, reason=reason, reviewed_by=reviewed_by)
    )


@knowledge_group.command("retract")
@click.argument("relation_id")
@click.option("--reason", default="", help="why this assertion is withdrawn")
@_kb_option
@click.pass_context
@_handle_errors
def retract_cmd(ctx: click.Context, relation_id: str, reason: str) -> None:
    """Withdraw a recorded relation without deleting its history."""
    _execute(ctx, lambda store: store.retract(relation_id, reason=reason))


@knowledge_group.command("export")
@click.argument("path", required=False, type=click.Path(path_type=Path, dir_okay=False))
@_kb_option
@click.pass_context
@_handle_errors
def export_cmd(ctx: click.Context, path: Path | None) -> None:
    """Export the full graph to stdout, or as raw JSON to PATH."""

    def export(store: KnowledgeStore) -> dict[str, Any]:
        graph = store.export_graph()
        if path is None:
            return graph
        try:
            write_json_artifact(path, graph, protected=sqlite_source_paths(store.db_path))
        except McsError as exc:
            raise KnowledgeError(exc.message, **exc.context) from exc
        return {"path": str(path), "kb_id": graph["kb_id"], "revision": graph["revision"]}

    _execute(ctx, export)


@knowledge_group.command("import")
@click.argument("path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--replace", is_flag=True, help="replace an existing graph with this full export")
@_kb_option
@click.pass_context
@_handle_errors
def import_cmd(ctx: click.Context, path: Path, replace: bool) -> None:
    """Import a graph fragment or a full identity-preserving JSON export."""
    graph = _load_object(path)
    _execute(ctx, lambda store: store.import_graph(graph, replace=replace), create=True)
