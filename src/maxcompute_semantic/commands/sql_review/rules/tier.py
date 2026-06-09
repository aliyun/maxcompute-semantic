"""Tier rule — flag bare table refs in multi-source 3-level projects."""

from __future__ import annotations

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    cte_names,
    parse_statements,
)
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext


def check_bare_table_in_3level(ctx: ReviewContext) -> list[Issue]:
    """Flag bare table references in multi-source 3-level projects.

    **Single-source profiles are exempt**: ``mcs sql execute`` auto-injects
    ``odps.default.schema`` via ``build_hints``, so bare names resolve
    correctly. Emitting this warning in the single-source case wastes
    agent turns on unnecessary FQN qualification.

    **Multi-source profiles** have tables across multiple schemas (and
    possibly projects). A bare name is ambiguous — only the 3-segment
    FQN ``project.schema.table`` is unambiguous.

    CTE references are skipped (sqlglot yields a Table node for every
    CTE reference with bare name, which would otherwise false-positive).
    """
    if ctx.tier != "3":
        return []
    if hasattr(ctx.profile, "sources") and len(ctx.profile.sources) <= 1:
        return []

    schemas = sorted({s.schema for s in ctx.profile.sources})
    issues: list[Issue] = []
    for stmt in parse_statements(ctx.sql):
        ctes = cte_names(stmt)
        for table in stmt.find_all(exp.Table):
            if table.catalog:
                continue
            if table.name.lower() in ctes:
                continue
            example_fqn = f"`{ctx.profile.sources[0].project}.{schemas[0]}.{table.name}`"
            issues.append(
                Issue(
                    severity="warning",
                    rule="tier.bare-table-in-3level",
                    message=(
                        f"Table `{table.name}` needs a fully-qualified name "
                        f"(`project.schema.table`) — this profile has "
                        f"{len(ctx.profile.sources)} sources across schemas "
                        f"{', '.join(schemas)}"
                    ),
                    fix_hint=f"use 3-segment FQN, e.g. {example_fqn}",
                )
            )
    return issues
