# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Stage knowledge projections and export revision-bound native snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from maxcompute_semantic._internal.artifacts import write_json_artifact
from maxcompute_semantic.commands.package_remote import _emit, _sync, _workspace_option
from maxcompute_semantic.remote.sync import SemanticSyncError, load_section_file


@click.command("stage-projection")
@click.option("--file", "input_file", type=click.Path(path_type=Path, dir_okay=False), required=True)
@_workspace_option
@click.pass_context
def stage_projection_cmd(ctx: click.Context, input_file: Path, workspace: Path) -> None:
    """Merge a complete publishable knowledge preview locally; never push."""
    def action() -> dict[str, Any]:
        report = load_section_file(input_file)
        if (
            not isinstance(report, dict) or report.get("publishable") is not True
            or report.get("unmapped") != [] or report.get("validation_issues") != []
            or not isinstance(report.get("sections"), dict) or not report["sections"]
        ):
            raise SemanticSyncError(
                "projection is incomplete or contains unresolved knowledge",
                code="SemanticProjectionRejected", exit_code=2,
                remediation="resolve unmapped items and evidence issues, then project again",
            )
        return _sync(workspace).stage_sections(report["sections"])

    _emit(ctx, action)


@click.command("export-semantic")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), default=None,
              help="save a raw SemanticSpec snapshot for knowledge ingest-semantic")
@_workspace_option
@click.pass_context
def export_semantic_cmd(ctx: click.Context, output: Path | None, workspace: Path) -> None:
    """Export verified remote base content; local unpublished edits stay separate."""
    def action() -> dict[str, Any]:
        snapshot = _sync(workspace).snapshot()
        if output is not None:
            write_json_artifact(output, snapshot,
                                protected=(workspace / "state.json", workspace / ".sync.lock"))
        return snapshot

    _emit(ctx, action)


def register_package_bridge_commands(group: click.Group) -> None:
    """Attach local projection/export workflows to the package command group."""
    for command in (stage_projection_cmd, export_semantic_cmd):
        group.add_command(command)
