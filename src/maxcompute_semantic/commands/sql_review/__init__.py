# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs sql review — stateless SQL conformance check dispatcher."""

from __future__ import annotations

from typing import Any

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.build.workload import extract_sql_evidence
from maxcompute_semantic.commands.sql import _classify_sql
from maxcompute_semantic.commands.sql_review.coverage import compute_model_coverage
from maxcompute_semantic.commands.sql_review.hints import ALL_HINTS
from maxcompute_semantic.commands.sql_review.rules import ALL_RULES, PACKAGE_INDEPENDENT_RULES
from maxcompute_semantic.commands.sql_review.types import ReviewContext


def _syntax_only_coverage(evidence: Any) -> dict[str, int]:
    referenced_columns = {
        *evidence.where_columns,
        *evidence.group_by_columns,
        *((table, col) for _func, table, col in evidence.aggregates),
    }
    return {
        "tables_referenced": len(evidence.tables),
        "tables_with_ai_context": 0,
        "columns_referenced": len(referenced_columns),
        "columns_with_semantic_role": 0,
        "joins_used_in_sql": len(evidence.join_edges),
        "joins_declared": 0,
        "coverage_pct": 0,
    }


def build_review_envelope(
    *,
    sql: str,
    profile: Profile,
    project: str | None,
    schema_name: str | None,
    tier: str,
) -> dict[str, Any]:
    """Run all rules and hints against *sql*; return the envelope dict.

    Returns a dict with the stable review envelope keys:

    - ``sql``: the original input string, echoed unchanged.
    - ``issues``: list of issue dicts (rule outputs serialized via
      ``Issue.to_dict()``); deterministic, schema-/tier-/dialect-class
      rules. Same SQL on the same package always produces the same
      issues.
    - ``hints``: list of hint dicts (``Hint.to_dict()``); inferential,
      may include ``confidence`` levels and ``if_misleading`` strings.
      ``pattern.verified-match`` emits one hint per matching
      ``verified_query`` row by Task 11's contract; the dispatcher does
      NOT dedup or cap.
    - ``model_coverage``: ``dict[str, int]`` with annotation-completeness
      counters; see ``coverage.compute_model_coverage`` for keys. When
      the package is absent, coverage reports referenced SQL surface
      with zero annotation coverage and ``review_mode='syntax_only'``.
    - ``review_mode``: ``"full"`` when package-backed rules and hints ran,
      ``"syntax_only"`` when no package exists.
    - ``semantic_checks_skipped``: true only in syntax-only mode; then
      ``semantic_skip_reason`` is also present.

    *schema_name* is forwarded into the :class:`ReviewContext` for
    tier-rule use (the ``tier.bare-table-in-3level`` rule consults it);
    pass ``None`` for 2-level projects.

    The DB handle is closed in ``finally`` so a rule or hint exception
    can't leak the connection. If no package exists, the dispatcher does
    not create one; it runs only package-independent syntax / dialect /
    tier rules and skips schema rules, semantic hints, and package
    coverage.
    """
    evidence = extract_sql_evidence(sql)
    classification = _classify_sql(sql)
    db_path = profile_data_dir(profile) / "package.db"
    if not db_path.exists():
        ctx = ReviewContext(
            sql=sql,
            evidence=evidence,
            profile=profile,
            project=project,
            schema_name=schema_name,
            tier=tier,
            db=None,
            classification=classification,
        )
        issues = [issue.to_dict() for rule in PACKAGE_INDEPENDENT_RULES for issue in rule(ctx)]
        return {
            "sql": sql,
            "issues": issues,
            "hints": [],
            "model_coverage": _syntax_only_coverage(evidence),
            "review_mode": "syntax_only",
            "semantic_checks_skipped": True,
            "semantic_skip_reason": "package_not_built",
        }
    db = PackageDB(db_path)
    try:
        ctx = ReviewContext(
            sql=sql,
            evidence=evidence,
            profile=profile,
            project=project,
            schema_name=schema_name,
            tier=tier,
            db=db,
            classification=classification,
        )
        # v1 is fail-loud: any rule/hint exception propagates and crashes the
        # review. Per-rule isolation (logging + skip) is deferred to Task 18 —
        # fail-loud surfaces rule bugs faster during this phase.
        issues = []
        for rule in ALL_RULES:
            for issue in rule(ctx):
                issues.append(issue.to_dict())
        # Per-row hint emission is the Task 11 contract — pattern hints
        # may emit one hint per matching verified_query row. Any
        # deduplication or capping is deferred to a future task that has
        # a concrete workload demanding it; v1 appends every emitted
        # hint as-is.
        hints: list[dict[str, Any]] = []
        for hint_fn in ALL_HINTS:
            for h in hint_fn(ctx):
                hints.append(h.to_dict())
        coverage = compute_model_coverage(ctx)
    finally:
        db.close()
    return {
        "sql": sql,
        "issues": issues,
        "hints": hints,
        "model_coverage": coverage,
        "review_mode": "full",
        "semantic_checks_skipped": False,
    }
