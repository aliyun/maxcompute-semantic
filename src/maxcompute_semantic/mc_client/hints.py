# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Build pyodps `hints` dict (= SET-equivalents) from profile tier + user input."""

from __future__ import annotations


def build_hints(
    *,
    tier: str | None,
    schema: str | None,
    user_hints: dict[str, str] | None = None,
    source_key: str | None = None,
) -> dict[str, str]:
    """Build hints dict for pyodps execute_sql.

    3-level project -> always inject ``odps.namespace.schema=true`` so
    3-segment ``project.schema.table`` references parse correctly,
    independent of whether a session schema was supplied. When *schema*
    is also set, ``odps.default.schema=<schema>`` is added so bare
    table names can resolve via the session default.

    User hints win over tier-derived hints (setdefault semantics).
    *source_key* is accepted for symmetry with other mc_client helpers
    that prefix log messages, but is not used by this function.
    """
    hints = dict(user_hints or {})
    if tier == "3":
        hints.setdefault("odps.namespace.schema", "true")
        if schema:
            hints.setdefault("odps.default.schema", schema)
    return hints


def namespace_schema_hints(enabled: bool) -> dict[str, str] | None:
    """Return ``{"odps.namespace.schema": "true"}`` when *enabled*, else None.

    Used by callers that submit fully-qualified 3-part SQL whose project
    tier isn't the connection's compute_project tier (so the standard
    ``schema=`` kwarg path in :func:`build_hints` won't fire). Returning
    ``None`` lets callers pass the result straight to
    ``execute_sql(hints=...)``.
    """
    return {"odps.namespace.schema": "true"} if enabled else None
