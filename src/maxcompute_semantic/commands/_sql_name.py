"""Compute the shortest unambiguous table name for use in SQL."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile


def sql_name(table: str, source_key: str, profile: Profile) -> str:
    """Return the shortest table reference an agent can paste into SQL.

    Single-source profiles (including zero-source env-var fallback):
    bare ``table`` — ``mcs sql execute`` auto-injects
    ``odps.default.schema`` so bare names resolve.

    Multi-source profiles: 3-segment ``project.schema.table`` FQN
    extracted from the ``source_key`` (which encodes
    ``<project>__<schema>``).
    """
    if len(profile.sources) <= 1:
        return table
    if "__" in source_key:
        project, schema = source_key.split("__", 1)
        return f"{project}.{schema}.{table}"
    return table
