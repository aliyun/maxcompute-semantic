# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Explicit local imports and reviewed SemanticSpec projection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from maxcompute_semantic._internal.artifacts import (
    sqlite_source_paths,
    write_json_artifact,
)
from maxcompute_semantic.commands.knowledge import (
    _execute,
    _handle_errors,
    _kb_option,
)
from maxcompute_semantic.knowledge.model import MAX_DOCUMENT_BYTES, KnowledgeError


def _read_object(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise KnowledgeError("snapshot exceeds the local import size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise KnowledgeError("cannot read a UTF-8 JSON snapshot", path=str(path)) from exc
    if not isinstance(value, dict):
        raise KnowledgeError("snapshot must be a JSON object")
    return value


@click.command("ingest-semantic")
@click.argument("snapshot", type=click.Path(path_type=Path, dir_okay=False))
@_kb_option
@click.pass_context
@_handle_errors
def ingest_semantic_cmd(ctx: click.Context, snapshot: Path) -> None:
    """Import a versioned SemanticSpec snapshot, keeping its native fields."""
    from maxcompute_semantic.knowledge.semanticspec import import_semanticspec

    fragment = import_semanticspec(_read_object(snapshot))
    _execute(ctx, lambda store: store.import_graph(fragment), create=True)


@click.command("ingest-package")
@click.option("--profile", required=True, help="existing local mcs package profile")
@_kb_option
@click.pass_context
@_handle_errors
def ingest_package_cmd(ctx: click.Context, profile: str) -> None:
    """Read an existing local package without building or contacting MaxCompute."""
    import sqlite3
    import tempfile
    from contextlib import closing

    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.auth.profile_store import get
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.knowledge.semanticspec import import_package

    resolved = get(profile)
    database = profile_data_dir(resolved) / "package.db"
    if not database.is_file():
        raise KnowledgeError("local package is not built", profile=profile)
    try:
        # PackageDB migrates on open. Upgrade a consistent private backup,
        # retaining the original package/fork's schema and contents untouched.
        with tempfile.TemporaryDirectory(prefix="mcs-package-snapshot-") as directory:
            copied = Path(directory) / "package.db"
            with (
                closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(copied)) as destination,
            ):
                source.backup(destination)
            db = PackageDB(copied)
            try:
                # Only public provenance travels; profile auth is not projected.
                fragment = import_package(db, {"name": resolved.name})
            finally:
                db.close()
    except sqlite3.Error as exc:
        raise KnowledgeError("cannot read the local package snapshot", profile=profile) from exc
    _execute(ctx, lambda store: store.import_graph(fragment), create=True)


@click.command("project")
@click.option("--selected", multiple=True, required=True, help="confirmed node ID; repeat for a selection")
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), default=None,
              help="save the projection report as raw JSON")
@_kb_option
@click.pass_context
@_handle_errors
def project_cmd(ctx: click.Context, selected: tuple[str, ...], output: Path | None) -> None:
    """Preview selected query semantics and their dependencies; never publish."""
    from maxcompute_semantic.knowledge.semanticspec import project_semanticspec

    def project(store: Any) -> dict[str, Any]:
        report = project_semanticspec(store.export_graph(), selected)
        if output is not None:
            write_json_artifact(output, report, protected=sqlite_source_paths(store.db_path))
        return report

    _execute(ctx, project)


def register_knowledge_bridge_commands(group: click.Group) -> None:
    """Attach bridge commands to the existing knowledge command surface."""
    for command in (ingest_semantic_cmd, ingest_package_cmd, project_cmd):
        group.add_command(command)
