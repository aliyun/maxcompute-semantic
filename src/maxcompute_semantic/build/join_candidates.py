# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Ranked JOIN candidates from workload evidence, profile stats, and name heuristics.

Combines three evidence sources — historical SQL workload, profile
statistics (uniqueness_ratio, approx_ndv), and column-name pattern
heuristics — into ranked ``JoinCandidate`` entries stored in the
``join_candidates`` table. Candidates with conflicting column pairs
connecting the same two tables get demoted to ``conflicting`` status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JoinCandidate:
    left_source_key: str
    left_table: str
    left_col: str
    right_source_key: str
    right_table: str
    right_col: str
    confidence: float
    status: str
    evidence: list[dict[str, Any]]
    conflict_group: str
    coverage_ratio: float | None = None
    right_uniqueness_ratio: float | None = None
    cardinality: str | None = None


# Confidence caps by evidence source.
_WORKLOAD_CAP = 0.55
_UNIQUENESS_CAP = 0.20
_COVERAGE_CAP = 0.15
_NAME_HEURISTIC_CAP = 0.10

# Name-heuristic type caps.
_SAME_NAME_CAP = 0.40
_LOOSE_ID_CAP = 0.25

# Cross-source penalty factor.
_CROSS_SOURCE_PENALTY = 0.8

# Join-shape thresholds for FK/PK classification from uniqueness_ratio.
_UNIQUE_THRESHOLD = 0.95
_NON_UNIQUE_THRESHOLD = 0.8

# Multiplier applied when both sides of a name-edge are ~unique. PK↔PK
# joins are coincidental in real schemas — two tables aliasing the same
# entity is rare, and when the candidate exists alongside a true
# FK→PK edge on a different column (e.g. ``cards.id↔legalities.id``
# coexisting with ``cards.uuid↔legalities.uuid`` where ``legalities.uuid``
# is the actual FK), the PK↔PK form must rank below the FK→PK form.
_PK_PK_PENALTY = 0.6


def rank_join_candidates(
    *,
    tables: dict[tuple[str, str], dict[str, dict[str, Any]]],
    workload_summary: dict[str, Any],
    name_edges: list[dict[str, Any]],
    limit_per_table: int = 5,
) -> list[JoinCandidate]:
    """Combine evidence sources into ranked join candidates.

    ``tables`` maps ``(source_key, table_name)`` to column-name→stats dicts.
    ``workload_summary`` is a ``WorkloadSummary.to_jsonable()`` dict.
    ``name_edges`` is the list of dicts from
    ``phase_infer_joins_heuristic`` (left/right source/table/col,
    kind, confidence).
    """
    candidates: list[JoinCandidate] = []

    # Build workload-evidence candidates from join_counts.
    # Workload edges are stored with bare table names (no source_key),
    # so each side gets resolved against ``tables`` to recover the
    # source_key when exactly one source carries that bare name. This
    # is what lets the per-table markdown view (which filters
    # ``join_candidates`` by ``left_source_key``) actually surface
    # workload-discovered joins to the agent — empty source_key would
    # silently drop them in any non-empty-source-key profile.
    join_counts = workload_summary.get("join_counts", {})
    for edge_key, frequency in join_counts.items():
        parts = edge_key.split("=")
        if len(parts) != 2:
            continue
        left_part, right_part = parts[0].strip(), parts[1].strip()
        left_tc = left_part.split(".") if "." in left_part else ("", left_part)
        right_tc = right_part.split(".") if "." in right_part else ("", right_part)
        left_table, left_col = left_tc[0].strip(), left_tc[1].strip()
        right_table, right_col = right_tc[0].strip(), right_tc[1].strip()

        left_sk = _resolve_unique_source_key(tables, left_table)
        right_sk = _resolve_unique_source_key(tables, right_table)
        left_uniqueness = (
            tables.get((left_sk, left_table), {}).get(left_col, {}).get("uniqueness_ratio")
            if left_sk is not None and left_table
            else None
        )
        right_uniqueness = (
            tables.get((right_sk, right_table), {}).get(right_col, {}).get("uniqueness_ratio")
            if right_sk is not None and right_table
            else None
        )

        # Compute shape/cardinality for the agent. We don't reuse the
        # boosted confidence — workload frequency is the authoritative
        # signal for "this join is real", so PK-PK penalties and
        # uniqueness boosts don't apply here.
        shape, _ = _apply_join_shape_adjustment(
            base_conf=0.0,
            left_uniqueness=left_uniqueness,
            right_uniqueness=right_uniqueness,
        )

        workload_conf = min(frequency / 10, 1.0) * _WORKLOAD_CAP
        evidence: list[dict[str, Any]] = [
            {"source": "history_sql", "frequency": frequency, "edge": edge_key},
        ]
        if left_uniqueness is not None or right_uniqueness is not None:
            evidence.append(
                {
                    "source": "profile_stats",
                    "left_uniqueness_ratio": left_uniqueness,
                    "right_uniqueness_ratio": right_uniqueness,
                    "join_shape": shape,
                }
            )

        resolved_left_sk = left_sk or ""
        resolved_right_sk = right_sk or ""
        cg = (
            f"{resolved_left_sk}.{left_table}->{resolved_right_sk}.{right_table}"
            if left_table and right_table
            else ""
        )

        candidates.append(
            JoinCandidate(
                left_source_key=resolved_left_sk,
                left_table=left_table,
                left_col=left_col,
                right_source_key=resolved_right_sk,
                right_table=right_table,
                right_col=right_col,
                confidence=workload_conf,
                status="suggested",
                evidence=evidence,
                conflict_group=cg,
                right_uniqueness_ratio=right_uniqueness,
                cardinality=_infer_cardinality("history_sql", shape, right_uniqueness),
            )
        )

    # Process name-heuristic edges.
    for edge in name_edges:
        kind = edge.get("kind", "same_name")
        name_conf = edge.get("confidence", 0.5)

        if kind == "same_name":
            name_conf = min(name_conf, _SAME_NAME_CAP)
        elif kind == "loose_id":
            name_conf = min(name_conf, _LOOSE_ID_CAP)
        else:
            name_conf = min(name_conf, _SAME_NAME_CAP)

        # Look up uniqueness on BOTH sides so the ranker can distinguish
        # FK→PK (right unique, left non-unique — the classic n:1 child
        # ref to parent) from PK↔PK (both unique — usually coincidental
        # shared PK names that should not outrank a real FK candidate).
        left_key = (edge.get("left_source_key", ""), edge.get("left_table", ""))
        left_col = edge.get("left_col", "")
        right_key = (edge.get("right_source_key", ""), edge.get("right_table", ""))
        right_col = edge.get("right_col", "")
        left_uniqueness = tables.get(left_key, {}).get(left_col, {}).get("uniqueness_ratio")
        right_uniqueness = tables.get(right_key, {}).get(right_col, {}).get("uniqueness_ratio")

        join_shape, name_conf = _apply_join_shape_adjustment(
            base_conf=name_conf,
            left_uniqueness=left_uniqueness,
            right_uniqueness=right_uniqueness,
        )

        lsk = edge.get("left_source_key", "")
        rsk = edge.get("right_source_key", "")

        # Cross-source penalty.
        if lsk and rsk and lsk != rsk:
            name_conf *= _CROSS_SOURCE_PENALTY

        cg = f"{lsk}.{edge.get('left_table', '')}->{rsk}.{edge.get('right_table', '')}"

        evidence: list[dict[str, Any]] = [{"source": "name_heuristic", "kind": kind}]
        if left_uniqueness is not None or right_uniqueness is not None:
            evidence.append(
                {
                    "source": "profile_stats",
                    "left_uniqueness_ratio": left_uniqueness,
                    "right_uniqueness_ratio": right_uniqueness,
                    "join_shape": join_shape,
                }
            )

        candidates.append(
            JoinCandidate(
                left_source_key=lsk,
                left_table=edge.get("left_table", ""),
                left_col=edge.get("left_col", ""),
                right_source_key=rsk,
                right_table=edge.get("right_table", ""),
                right_col=right_col,
                confidence=min(name_conf, 0.85),
                status="suggested",
                evidence=evidence,
                conflict_group=cg,
                right_uniqueness_ratio=right_uniqueness,
                cardinality=_infer_cardinality(kind, join_shape, right_uniqueness),
            )
        )

    # Resolve conflicts.
    _resolve_conflicts(candidates)

    # Sort by confidence descending and limit per table.
    candidates.sort(key=lambda c: -c.confidence)
    result: list[JoinCandidate] = []
    seen_per_table: dict[str, int] = {}
    for c in candidates:
        key = f"{c.left_source_key}.{c.left_table}"
        count = seen_per_table.get(key, 0)
        if count < limit_per_table:
            result.append(c)
            seen_per_table[key] = count + 1

    return result


def _resolve_unique_source_key(
    tables: dict[tuple[str, str], dict[str, dict[str, Any]]],
    table_name: str,
) -> str | None:
    """Return the source_key for *table_name* iff exactly one source
    carries that bare name. Returns ``None`` for absent or ambiguous
    cases — the caller should keep the source_key empty and skip
    profile-stat enrichment rather than guess wrong.
    """
    if not table_name:
        return None
    matches = [sk for (sk, tname) in tables if tname == table_name]
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_conflicts(candidates: list[JoinCandidate]) -> None:
    """Mark lower-confidence candidates connecting the same table pair
    as ``conflicting``.
    """
    groups: dict[str, list[JoinCandidate]] = {}
    for c in candidates:
        gk = f"{c.left_source_key}.{c.left_table}->{c.right_source_key}.{c.right_table}"
        groups.setdefault(gk, []).append(c)

    for _gk, group in groups.items():
        if len(group) <= 1:
            continue
        best = max(c.confidence for c in group)
        for c in group:
            if c.confidence < best and c.status == "suggested":
                idx = candidates.index(c)
                candidates[idx] = JoinCandidate(
                    **{k: v for k, v in c.__dict__.items() if k != "status"},
                    status="conflicting",
                )


def _apply_join_shape_adjustment(
    *,
    base_conf: float,
    left_uniqueness: float | None,
    right_uniqueness: float | None,
) -> tuple[str, float]:
    """Classify the join shape from per-side uniqueness and adjust confidence.

    Returns ``(shape_label, adjusted_confidence)``. Shapes:

    - ``fk-pk`` — right side unique, left side non-unique. Classic n:1
      child→parent reference. Boost by right uniqueness.
    - ``pk-fk`` — left side unique, right side non-unique. 1:n parent→child.
      Boost by left uniqueness.
    - ``pk-pk`` — both sides unique. Usually coincidental same-PK-name
      collisions; penalize so the FK→PK candidate on a different column
      pair wins.
    - ``fk-fk`` — both sides non-unique. Likely shared FK to an absent
      parent table (e.g. ``team.team_api_id↔team_attributes.team_api_id``
      with no ``team_api`` table). Confidence unchanged — the name match
      itself is the only evidence we have.
    - ``unknown`` — one or both stats missing; partial boost on whichever
      side is present.
    """
    left_known = left_uniqueness is not None
    right_known = right_uniqueness is not None
    left_unique = left_known and left_uniqueness >= _UNIQUE_THRESHOLD
    right_unique = right_known and right_uniqueness >= _UNIQUE_THRESHOLD
    left_non_unique = left_known and left_uniqueness < _NON_UNIQUE_THRESHOLD
    right_non_unique = right_known and right_uniqueness < _NON_UNIQUE_THRESHOLD

    if left_unique and right_unique:
        return "pk-pk", base_conf * _PK_PK_PENALTY
    if right_unique and left_non_unique:
        assert right_uniqueness is not None
        return "fk-pk", base_conf + _UNIQUENESS_CAP * right_uniqueness
    if left_unique and right_non_unique:
        assert left_uniqueness is not None
        return "pk-fk", base_conf + _UNIQUENESS_CAP * left_uniqueness
    if right_unique:
        assert right_uniqueness is not None
        return "fk-pk", base_conf + _UNIQUENESS_CAP * right_uniqueness
    if left_unique:
        assert left_uniqueness is not None
        return "pk-fk", base_conf + _UNIQUENESS_CAP * left_uniqueness
    if left_non_unique and right_non_unique:
        return "fk-fk", base_conf
    return "unknown", base_conf


def _infer_cardinality(kind: str, shape: str, right_uniqueness: float | None) -> str | None:
    """Heuristic cardinality from edge kind plus inferred join shape."""
    if kind == "link_to":
        return "n:1"
    if shape == "fk-pk":
        return "n:1"
    if shape == "pk-fk":
        return "1:n"
    if shape == "pk-pk":
        return "1:1"
    if shape == "fk-fk":
        return "n:m"
    if right_uniqueness is not None and right_uniqueness >= 0.98:
        return "n:1"
    return None


def build_overlap_validation_sql(
    *,
    left_fq_name: str,
    left_col: str,
    right_fq_name: str,
    right_col: str,
    left_where_clause: str,
    right_where_clause: str,
) -> str:
    """Build SQL for value-overlap validation of one join candidate.

    Returns a query that counts matched rows between left and right
    tables to compute coverage_ratio.
    """
    left_where = f" {left_where_clause}" if left_where_clause else ""
    right_where = f" {right_where_clause}" if right_where_clause else ""

    return (
        f"SELECT "
        f"COUNT(1) AS left_non_null, "
        f"COUNT(r.{right_col}) AS matched_rows "
        f"FROM (SELECT {left_col} FROM {left_fq_name}{left_where}) l "
        f"LEFT JOIN (SELECT DISTINCT {right_col} FROM {right_fq_name}{right_where}) r "
        f"ON l.{left_col} = r.{right_col}"
    )
