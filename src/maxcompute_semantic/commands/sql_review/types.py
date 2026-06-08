# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Output types and ReviewContext for sql review."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from maxcompute_semantic.build.workload import SqlWorkloadEvidence
from maxcompute_semantic.mc_client.errors import McsError

Severity = Literal["error", "warning"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    rule: str
    message: str
    span: tuple[int, int] | None = None
    fix_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
        }
        if self.span is not None:
            out["span"] = list(self.span)
        if self.fix_hint is not None:
            out["fix_hint"] = self.fix_hint
        return out


@dataclass(frozen=True)
class Hint:
    kind: str
    message: str
    confidence: Confidence
    evidence: dict[str, Any] = field(default_factory=dict)
    if_misleading: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "message": self.message,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
        }
        if self.if_misleading is not None:
            out["if_misleading"] = self.if_misleading
        return out


@dataclass
class ReviewContext:
    """Bundle of everything a rule or hint may need.

    Built once per `mcs sql review` invocation and passed to each
    rule / hint callable. Mutating any field from within a rule is a
    bug — rules must be read-only against this struct.
    """

    sql: str
    evidence: SqlWorkloadEvidence
    profile: Any  # auth.schema.Profile; Any to avoid circular import
    # ``project`` is the routed data project for single-source SQL, or
    # ``None`` when the SQL spans sources (``_route_project`` returns
    # ``None``). Optional so cross-source review still constructs a
    # context — rules don't read this field today (they reach the
    # profile's sources via ``ctx.profile``), but it is forwarded
    # verbatim from the CLI's routing decision for future use.
    project: str | None
    schema_name: str | None
    tier: str  # "2" or "3" (literal strings from get_tier)
    db: Any | None  # build.storage.PackageDB; None in syntax-only mode
    classification: str  # "read" / "write" / "unparseable"

    def to_source_key(self, table_name: str) -> str | None:
        """Resolve a bare table name to its source_key via the profile's sources."""
        if self.db is None:
            return None
        for src in self.profile.sources:
            sk: str | None = self.db.lookup_source_key(src.project, src.schema, table_name)
            if sk:
                return sk
        return None


class ReviewUnsupportedError(McsError):
    """``mcs sql review`` refused to run rules against a write or
    unparseable SQL statement.

    Mirrors :class:`WriteOpRejectedError`'s rejection-by-policy stance
    (exit 2 / usage) but uses the spec'd ``MCS_REVIEW_UNSUPPORTED``
    code so the agent can branch on the review-specific refusal
    without colliding with the execute-time write guard. The
    ``classification`` value (``"write"`` or ``"unparseable"``) lands
    in ``context`` so the agent can craft a recovery prompt without
    re-classifying the SQL.
    """

    code = "MCS_REVIEW_UNSUPPORTED"
    exit_code = 2
