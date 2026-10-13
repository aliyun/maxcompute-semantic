# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Bounded local graph queries over recorded relations and pinned evidence."""

from __future__ import annotations

from pathlib import Path

import click

from maxcompute_semantic.commands.knowledge import (
    DEFAULT_KB,
    _configure_context,
    _execute,
    _handle_errors,
    _kb_option,
)
from maxcompute_semantic.knowledge.model import MAX_DEPTH, MAX_LIMIT, STATUSES


@click.group(name="graph")
@click.option(
    "--kb",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_KB,
    show_default=True,
    help="local knowledge database; independent of MaxCompute profiles",
)
@click.pass_context
def graph_group(ctx: click.Context, kb: Path) -> None:
    """Explore recorded relations; a path is an association, not factual proof."""
    _configure_context(ctx, kb)


@graph_group.command("neighbors")
@click.argument("node_id")
@click.option("--direction", type=click.Choice(["both", "in", "out"]), default="both")
@click.option(
    "--status",
    "statuses",
    type=click.Choice(sorted(STATUSES)),
    multiple=True,
    help="include this review state (repeatable; default: confirmed)",
)
@click.option("--limit", type=click.IntRange(1, MAX_LIMIT), default=50, show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def neighbors_cmd(
    ctx: click.Context, node_id: str, direction: str, statuses: tuple[str, ...], limit: int
) -> None:
    """List the node's immediate relations with their evidence and review state."""
    _execute(
        ctx,
        lambda store: store.neighbors(
            node_id, direction=direction, statuses=statuses or ("confirmed",), limit=limit
        ),
    )


@graph_group.command("path")
@click.argument("start")
@click.argument("end")
@click.option("--max-depth", type=click.IntRange(1, MAX_DEPTH), default=4, show_default=True)
@click.option("--limit", type=click.IntRange(1, MAX_LIMIT), default=100, show_default=True)
@click.option(
    "--status",
    "statuses",
    type=click.Choice(sorted(STATUSES)),
    multiple=True,
    help="include this review state (repeatable; default: confirmed)",
)
@_kb_option
@click.pass_context
@_handle_errors
def path_cmd(
    ctx: click.Context, start: str, end: str, max_depth: int, limit: int, statuses: tuple[str, ...]
) -> None:
    """Find bounded relation paths between node IDs, retaining each assertion."""
    _execute(
        ctx,
        lambda store: store.path(
            start, end, max_depth=max_depth, limit=limit, statuses=statuses or ("confirmed",)
        ),
    )


@graph_group.command("impact")
@click.argument("node_id")
@click.option("--max-depth", type=click.IntRange(1, MAX_DEPTH), default=4, show_default=True)
@click.option("--limit", type=click.IntRange(1, MAX_LIMIT), default=100, show_default=True)
@_kb_option
@click.pass_context
@_handle_errors
def impact_cmd(ctx: click.Context, node_id: str, max_depth: int, limit: int) -> None:
    """Find dependents through confirmed dependency relations."""
    _execute(ctx, lambda store: store.impact(node_id, max_depth=max_depth, limit=limit))


@graph_group.command("check")
@_kb_option
@click.pass_context
@_handle_errors
def check_cmd(ctx: click.Context) -> None:
    """Check local graph integrity and evidence freshness."""
    report = _execute(ctx, lambda store: store.check())
    if report.get("ok") is False:
        ctx.exit(1)
