# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs metric -- CRUD over top-level (profile-global) named metrics.

A *metric* is a named MaxCompute SQL expression with optional
``description`` and ``ai_context``. The expression is **copied** into
generated SQL, not **compiled** -- mcs has no Metric Query Language.
See the top-level-metrics design spec for the why.

This module mirrors the verb-group conventions used by
``commands/udf.py`` (CRUD-over-PackageDB with the
``commit_after_command`` versioning hook on every write verb).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.errors.annotate import AnnotateValidationError
from maxcompute_semantic.errors.build import (
    MetricNotFoundError,
    MetricValidationError,
)
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning import (
    ACTION_METRIC_PREFIX,
)

if TYPE_CHECKING:
    from maxcompute_semantic.metric_validator import ValidationResult

# ── helpers ────────────────────────────────────────────────────────────────


def _open_existing_db(profile: Profile) -> PackageDB | None:
    db_path = profile_data_dir(profile) / "package.db"
    if not db_path.exists():
        return None
    return PackageDB(db_path)


def _reject_unparseable(r: Renderer, lint: ValidationResult) -> None:
    """Surface a :class:`MetricValidationError` envelope and exit when
    sqlglot rejects a metric expression.

    No-op when ``lint.ok`` is True so callers can call this unconditionally
    and let the helper decide.
    """
    if lint.ok:
        return
    err = MetricValidationError(
        f"metric expression rejected: {lint.error}",
        remediation="fix the SQL fragment and retry",
    )
    r.error(err)
    sys.exit(err.exit_code)


def _require_row(row: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """Defensive read-back guard for ``add`` / ``edit`` re-reads.

    The preceding ``add_metric`` / ``update_metric`` call holds the write
    lock and committed; ``get_metric(name)`` on the same connection should
    always return the row. The guard exists so the impossible path
    surfaces as a classified :class:`McsError` envelope instead of an
    unwrapped ``AssertionError`` traceback from a bare ``assert``.
    """
    if row is None:
        raise McsError(
            f"metric {name!r} disappeared between write and read-back",
            remediation="rerun the command; if it persists, file an issue",
        )
    return row


# ── metric group ──────────────────────────────────────────────────────────


@click.group(name="metric")
def metric_group() -> None:
    """Manage profile-level (top-level) named metrics."""


# ── metric add ────────────────────────────────────────────────────────────


@profile_command(
    metric_group,
    "add",
    action=ACTION_METRIC_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
@click.option("--expression", required=True, help="MaxCompute SQL fragment")
@click.option("--description", default=None, help="one-line business description")
@click.option(
    "--ai-context",
    default=None,
    help="longer NL paragraph for downstream LLMs",
)
def metric_add_cmd(
    pctx: ProfileContext,
    name: str,
    expression: str,
    description: str | None,
    ai_context: str | None,
) -> None:
    """Add a new top-level metric. Profile-global UNIQUE(name)."""
    # Lazy: the validator pulls sqlglot + the dialect package, which must
    # stay off the CLI startup chain.
    from maxcompute_semantic.metric_validator import validate_metric_expression

    db = pctx.open_db()
    try:
        lint = validate_metric_expression(expression, db)
        _reject_unparseable(pctx.renderer, lint)
        db.add_metric(
            name=name,
            expression=expression,
            description=description,
            ai_context=ai_context,
        )
        row = _require_row(db.get_metric(name), name)
        payload = dict(row)
        payload["warnings"] = lint.warnings
    finally:
        db.close()

    pctx.success(payload, commit_summary=f"add {name}")


# ── metric list ───────────────────────────────────────────────────────────


@profile_command(metric_group, "list", accepts_schema=False)
def metric_list_cmd(pctx: ProfileContext) -> None:
    """List all top-level metrics defined in this profile."""
    p = pctx.profile
    db = _open_existing_db(p)
    if db is None:
        rows: list[dict[str, Any]] = []
    else:
        try:
            rows = db.list_metrics()
        finally:
            db.close()
    pctx.renderer.success({"metrics": rows})


# ── metric show ───────────────────────────────────────────────────────────


@profile_command(metric_group, "show", accepts_schema=False)
@click.argument("name")
def metric_show_cmd(pctx: ProfileContext, name: str) -> None:
    """Show one metric's full payload, re-running the validator."""
    from maxcompute_semantic.metric_validator import validate_metric_expression

    p = pctx.profile
    db = _open_existing_db(p)
    if db is None:
        raise MetricNotFoundError(name)
    try:
        row = db.get_metric(name)
        if row is None:
            raise MetricNotFoundError(name)
        lint = validate_metric_expression(row["expression"], db)
    finally:
        db.close()
    payload = dict(row)
    payload["warnings"] = lint.warnings
    pctx.renderer.success(payload)


# ── metric edit ───────────────────────────────────────────────────────────


@profile_command(
    metric_group,
    "edit",
    action=ACTION_METRIC_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
@click.option("--expression", default=None, help="new expression")
@click.option("--description", default=None, help="new description")
@click.option("--ai-context", default=None, help="new ai_context")
def metric_edit_cmd(
    pctx: ProfileContext,
    name: str,
    expression: str | None,
    description: str | None,
    ai_context: str | None,
) -> None:
    """Partial-update a metric. Only the supplied fields are written."""
    from maxcompute_semantic.metric_validator import validate_metric_expression

    p = pctx.profile
    db = _open_existing_db(p)
    if db is None:
        raise MetricNotFoundError(name)
    try:
        if expression is not None:
            _reject_unparseable(pctx.renderer, validate_metric_expression(expression, db))
        db.update_metric(
            name,
            expression=expression,
            description=description,
            ai_context=ai_context,
        )
        row = _require_row(db.get_metric(name), name)
        lint = validate_metric_expression(row["expression"], db)
        payload = dict(row)
        payload["warnings"] = lint.warnings
    finally:
        db.close()
    pctx.success(payload, commit_summary=f"edit {name}")


# ── metric remove ─────────────────────────────────────────────────────────


@profile_command(
    metric_group,
    "remove",
    action=ACTION_METRIC_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
@click.option(
    "--force",
    is_flag=True,
    help="skip confirmation (required in non-TTY)",
)
def metric_remove_cmd(pctx: ProfileContext, name: str, force: bool) -> None:
    """Remove a metric from this profile."""
    p = pctx.profile
    if not force:
        if not sys.stdin.isatty():
            raise AnnotateValidationError(
                "non-TTY removal requires --force",
                remediation=(f"re-run as `mcs metric remove {name} --force`"),
            )
        if not click.confirm(f"Remove metric '{name}'?", default=False):
            click.echo("aborted")
            return
    db = _open_existing_db(p)
    if db is None:
        raise MetricNotFoundError(name)
    try:
        db.remove_metric(name)
    finally:
        db.close()
    pctx.success({"removed": name}, commit_summary=f"remove {name}")
