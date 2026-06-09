# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared ``--schema`` / project-tier resolution for the CLI verbs.

Single entry point :func:`resolve_schema_for_tier` is the unified policy
the ``mcs sql`` (execute/cost/explain), ``mcs meta`` (six verbs), and
``mcs build`` commands share. The contract:

- 2-level: ``--schema`` must be unset or exactly ``"default"``; returns
  ``"default"``. Non-default value is a usage error — same
  ``click.echo`` + ``sys.exit(2)`` shape we've always had (kept as a
  raw Click exit because the message is for a human, the agent doesn't
  reach this path).
- 3-level + ``--schema`` set: returns the value verbatim.
- 3-level + ``--schema`` unset + single-source profile: returns that
  source's schema (the cwd-link / 1-source common case — every
  ``mcs profile create`` produces this shape).
- 3-level + ``--schema`` unset + multi-source / env-var fallback:
  raises :class:`SchemaRequiredError`, which the CLI boundary turns
  into the McsError JSON envelope with a remediation that names the
  available schemas when a multi-source profile is in play.

Previously the policy split into three variants across the CLI: the
SQL ``execute`` / ``cost`` and ``build`` paths hard-failed with a
plain-text "Error: 3-level project needs a schema" + exit 2; ``explain``
and the six ``mcs meta`` verbs silently coerced ``None`` → ``"default"``
which masked misconfigured profiles by hitting the upgrade-synthetic
``default`` slot. Unifying through :class:`SchemaRequiredError` keeps
the failure visible, classified, and machine-parseable.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from maxcompute_semantic.mc_client.errors import SchemaRequiredError

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile


def resolve_schema_for_tier(
    tier: str,
    schema: str | None,
    *,
    profile: Profile | None = None,
) -> str:
    """Resolve the effective schema for a CLI invocation.

    See module docstring for the full policy. Returns the schema string
    every caller should pass downstream. Raises
    :class:`SchemaRequiredError` on the tier-3 + no-schema +
    not-auto-resolvable case; the caller's CLI boundary should let that
    propagate through ``emit_mcs_error`` so it lands as the standard
    McsError JSON envelope.
    """
    if tier == "2":
        if schema is not None and schema != "default":
            click.echo("Error: --schema must be 'default' for 2-level projects", err=True)
            sys.exit(2)
        return "default"

    # 3-level from here on.
    if schema is not None:
        return schema
    if profile is not None and len(profile.sources) == 1:
        return profile.sources[0].schema

    raise SchemaRequiredError(
        "3-level project requires a schema",
        remediation=_remediation_for_missing_schema(profile),
        available_schemas=_available_schemas(profile),
    )


def resolve_project_for_profile(
    project: str | None,
    *,
    profile: Profile | None = None,
) -> str:
    """Resolve the effective project, with auto-fill from profile sources.

    Mirrors the schema-resolution pattern: when a profile has data
    sources, use the first source's project as the default. Explicit
    ``--project`` always wins. Falls back to empty string when no
    profile or no sources are available.
    """
    if project is not None:
        return project
    if profile is not None and profile.sources:
        return profile.sources[0].project
    return ""


def _available_schemas(profile: Profile | None) -> list[str]:
    """Distinct schema names attached to *profile*'s sources, sorted."""
    if profile is None or not profile.sources:
        return []
    return sorted({s.schema for s in profile.sources})


def _remediation_for_missing_schema(profile: Profile | None) -> str:
    """Human + agent-actionable next step for the missing-schema failure.

    When *profile* has multiple sources, name them so the agent doesn't
    have to round-trip through ``mcs meta list-schemas`` just to discover
    what it should have passed. When *profile* is None or empty, point
    at the env-var fallback case.
    """
    schemas = _available_schemas(profile)
    if len(schemas) > 1:
        return (
            "pass --schema NAME (choices: "
            + ", ".join(schemas)
            + "), or bind a single-source profile in the working "
            "directory with `mcs link bind <NAME>`"
        )
    return (
        "pass --schema NAME, or bind a profile in the working directory "
        "with `mcs link bind <NAME>` so the schema is auto-resolved"
    )
