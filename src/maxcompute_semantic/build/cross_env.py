# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cross-environment duplicate source detection.

A profile carrying two ``DataSource`` entries that point at the same
logical schema in different environments (commonly ``acme__prod`` /
``acme__staging``, ``warehouse__main`` / ``warehouse__dev``, ...) is
a valid configuration — operators may want metadata from prod and
query execution against dev. But the JOIN inference heuristics treat
every pair of (source, table) as peers, so a same-named ``users``
table in both sources trips ``same_name`` matches (and where
uniqueness lines up, ``link_to`` matches too), surfacing cross-env
edges that are never real joins.
``phases.CROSS_SOURCE_CONFIDENCE_PENALTY`` (0.8) softens the
confidence rank but doesn't drop the edges, so the agent still sees
them in ``mcs show`` output.

This module identifies source pairs whose table-name sets overlap
heavily enough to be confident they're the same schema, and the
build pipeline uses the result to: (1) warn the user during build,
and (2) suppress JOIN inference between the suspect pair entirely
(no edge in the joins table — no penalty rank to wade through).

The detector is name-based only — no column comparison, no row
sampling. The default overlap threshold is intentionally conservative
(70% of the smaller side, with a 3-table floor) so unrelated sources
that happen to share a couple of common names like ``users`` or
``events`` don't get flagged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrossEnvDuplicatePair:
    """One detected pair of likely-duplicate sources.

    ``source_a`` < ``source_b`` (alphabetical) so emitted pairs are
    deterministic regardless of input ordering.
    """

    source_a: str
    source_b: str
    shared_tables: tuple[str, ...]
    shared_count: int
    smaller_size: int
    overlap_ratio: float


def detect_cross_env_duplicate_sources(
    tables_by_source: dict[str, set[str]],
    *,
    min_overlap_ratio: float = 0.7,
    min_shared_tables: int = 3,
) -> list[CrossEnvDuplicatePair]:
    """Return source pairs whose table-name sets overlap above the
    threshold, sorted alphabetically by ``(source_a, source_b)``.

    ``min_overlap_ratio`` is measured against the *smaller* source's
    table count, so a tiny dev source mirroring a large prod source
    still flags. ``min_shared_tables`` is a floor that prevents
    flagging two 3-table sources that share 3 common names like
    ``users`` / ``events`` / ``products``.
    """
    keys = sorted(tables_by_source)
    out: list[CrossEnvDuplicatePair] = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1 :]:
            ta = tables_by_source[ka]
            tb = tables_by_source[kb]
            shared = ta & tb
            if len(shared) < min_shared_tables:
                continue
            smaller = min(len(ta), len(tb))
            if smaller == 0:
                continue
            ratio = len(shared) / smaller
            if ratio < min_overlap_ratio:
                continue
            out.append(
                CrossEnvDuplicatePair(
                    source_a=ka,
                    source_b=kb,
                    shared_tables=tuple(sorted(shared)),
                    shared_count=len(shared),
                    smaller_size=smaller,
                    overlap_ratio=ratio,
                )
            )
    return out
