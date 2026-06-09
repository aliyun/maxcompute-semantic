# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs package`` -- semantic proposal review workflow."""

from __future__ import annotations

from typing import Any

import click

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.markdown import MarkdownRenderer
from maxcompute_semantic.build.proposals import (
    SemanticProposalError,
    apply_semantic_proposal,
    create_annotation_promotion_proposals,
    proposal_payload,
)
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning import (
    ACTION_PACKAGE_PREFIX,
    commit_after_command,
)


def _open_db_existing(profile: Profile) -> PackageDB:
    """Open an existing PackageDB; raise if it doesn't exist yet."""
    db_path = profile_data_dir(profile) / "package.db"
    if not db_path.exists():
        raise SemanticProposalError(
            f"no semantic package for profile {profile.name!r}",
            remediation="run `mcs build` first",
        )
    return PackageDB(db_path)


def _review_marker(row: dict[str, Any] | None) -> tuple[Any, Any, Any] | None:
    if row is None:
        return None
    return (row.get("reviewed_by"), row.get("reviewed_at"), row.get("validation_json"))


def _missing_proposal(proposal_id: int) -> SemanticProposalError:
    return SemanticProposalError(
        f"semantic proposal {proposal_id} not found",
        remediation="run `mcs package list-proposals` and choose an existing id",
    )


def _rerender_applied_patch(
    db: PackageDB,
    profile: Profile,
    patch: dict[str, Any],
) -> list[str]:
    kind = patch.get("kind", "")
    if kind == "metric":
        pdir = profile_data_dir(profile)
        renderer = MarkdownRenderer(db, profile, pdir)
        renderer.render_overview()
        renderer.render_state()
        return ["_overview.md", "_state.json"]
    source_key = patch["source_key"]
    table_name = patch["table"]
    pdir = profile_data_dir(profile)
    renderer = MarkdownRenderer(db, profile, pdir)
    renderer.render_table(source_key, table_name)
    renderer.render_overview()
    renderer.render_state()
    return [f"{source_key}/{table_name}.md", "_overview.md", "_state.json"]


@click.group(name="package")
def package_group() -> None:
    """Semantic package proposal workflow."""


@profile_command(
    package_group,
    "propose",
    action=ACTION_PACKAGE_PREFIX,
    accepts_schema=False,
)
@click.option(
    "--from-suggestions",
    is_flag=True,
    help="promote build-time annotation suggestions into semantic proposals",
)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="create proposals from YAML on stdin (tables + columns + metrics)",
)
@click.option(
    "--min-confidence",
    type=float,
    default=0.7,
    show_default=True,
    help="minimum suggestion confidence to promote",
)
def propose_cmd(
    pctx: ProfileContext,
    from_suggestions: bool,
    from_stdin: bool,
    min_confidence: float,
) -> None:
    """Create semantic proposals from package-derived suggestions or agent YAML."""
    if not from_suggestions and not from_stdin:
        raise click.UsageError("pass --from-suggestions or --from-stdin")
    if from_suggestions and from_stdin:
        raise click.UsageError("--from-suggestions and --from-stdin are mutually exclusive")

    p = pctx.profile
    db = _open_db_existing(p)
    try:
        if from_suggestions:
            counts = create_annotation_promotion_proposals(
                db,
                min_confidence=min_confidence,
            )
        else:
            import sys as _sys

            _STDIN_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
            yaml_content = _sys.stdin.read(_STDIN_MAX_BYTES)
            if len(yaml_content) == _STDIN_MAX_BYTES:
                raise click.UsageError("stdin input exceeds 10 MB limit")
            if not yaml_content.strip():
                raise click.UsageError("no YAML on stdin")
            from maxcompute_semantic.build.proposals import create_proposals_from_yaml

            counts = create_proposals_from_yaml(
                db,
                yaml_content,
                created_by="agent",
                profile=p,
            )
        commit_summary = None
        if counts.get("created") or counts.get("updated") or counts.get("reopened"):
            commit_summary = "propose from stdin" if from_stdin else "propose from suggestions"
        pctx.success({"profile": p.name, **counts}, commit_summary=commit_summary)
    finally:
        db.close()


@profile_command(package_group, "list-proposals", accepts_schema=False)
@click.option(
    "--status",
    default="suggested",
    show_default=True,
    help="filter by proposal status",
)
@click.option("--target-type", default=None, help="filter by target type")
@click.option("--limit", type=int, default=50, show_default=True, help="maximum rows")
def list_proposals_cmd(
    pctx: ProfileContext,
    status: str | None,
    target_type: str | None,
    limit: int,
) -> None:
    """List semantic proposals."""
    p = pctx.profile
    db = _open_db_existing(p)
    try:
        rows = db.list_semantic_proposals(
            status=status,
            target_type=target_type,
            limit=limit,
        )
        proposals = [proposal_payload(row) for row in rows]
        pctx.renderer.success(
            {
                "profile": p.name,
                "count": len(proposals),
                "proposals": proposals,
            }
        )
    finally:
        db.close()


@profile_command(package_group, "show-proposal", accepts_schema=False)
@click.argument("proposal_id", type=int)
def show_proposal_cmd(pctx: ProfileContext, proposal_id: int) -> None:
    """Show one semantic proposal."""
    p = pctx.profile
    db = _open_db_existing(p)
    try:
        row = db.get_semantic_proposal(proposal_id)
        if row is None:
            raise _missing_proposal(proposal_id)
        pctx.renderer.success({"profile": p.name, "proposal": proposal_payload(row)})
    finally:
        db.close()


@profile_command(
    package_group,
    "apply",
    action=ACTION_PACKAGE_PREFIX,
    accepts_schema=False,
)
@click.argument("proposal_id", type=int)
@click.option("--reviewed-by", default=None, help="reviewer identifier")
def apply_cmd(pctx: ProfileContext, proposal_id: int, reviewed_by: str | None) -> None:
    """Apply one semantic proposal."""
    p = pctx.profile
    db: PackageDB | None = None
    db = _open_db_existing(p)
    before_marker = _review_marker(db.get_semantic_proposal(proposal_id))
    try:
        result = apply_semantic_proposal(
            db,
            proposal_id,
            reviewed_by=reviewed_by,
        )
    except McsError:
        after_marker = _review_marker(db.get_semantic_proposal(proposal_id))
        validation_written = after_marker != before_marker
        db.close()
        db = None
        if validation_written:
            commit_after_command(
                p,
                action=ACTION_PACKAGE_PREFIX,
                summary=f"apply proposal {proposal_id} failed",
            )
        raise
    try:
        rerendered = _rerender_applied_patch(db, p, result["patch"])
        pctx.success(
            {"profile": p.name, **result, "rerendered_files": rerendered},
            commit_summary=f"apply proposal {proposal_id}",
        )
    finally:
        if db is not None:
            db.close()


@profile_command(
    package_group,
    "reject",
    action=ACTION_PACKAGE_PREFIX,
    accepts_schema=False,
)
@click.argument("proposal_id", type=int)
@click.option("--reason", default=None, help="review rejection reason")
@click.option("--reviewed-by", default=None, help="reviewer identifier")
def reject_cmd(
    pctx: ProfileContext,
    proposal_id: int,
    reason: str | None,
    reviewed_by: str | None,
) -> None:
    """Reject one semantic proposal."""
    reason_text = "" if reason is None else reason.strip()
    if reason is not None and not reason_text:
        raise SemanticProposalError(
            "reject reason cannot be blank",
            remediation="omit --reason or pass a non-blank reason",
        )

    p = pctx.profile
    db = _open_db_existing(p)
    try:
        row = db.get_semantic_proposal(proposal_id)
        if row is None:
            raise _missing_proposal(proposal_id)
        if row.get("status") != "suggested":
            raise SemanticProposalError(
                f"semantic proposal {proposal_id} has status {row.get('status')!r}",
                remediation="only proposals with status='suggested' can be rejected",
            )
        updated = db.update_semantic_proposal_status(
            proposal_id,
            status="rejected",
            reviewed_by=reviewed_by,
            validation={
                "ok": True,
                "decision": "rejected",
                "reason": reason_text,
            },
        )
        if not updated:
            raise _missing_proposal(proposal_id)
        row = db.get_semantic_proposal(proposal_id)
        pctx.success(
            {
                "profile": p.name,
                "rejected": True,
                "id": proposal_id,
                "proposal": proposal_payload(row) if row is not None else None,
            },
            commit_summary=f"reject proposal {proposal_id}",
        )
    finally:
        db.close()
