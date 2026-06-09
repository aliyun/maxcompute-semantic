# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared column-name regex heuristics for the build pipeline.

Two callers consume these (``profiling.select_profile_columns`` for
column-priority ranking, ``semantic_suggestions._classify_column`` for
role classification). Keeping the patterns in one place avoids the
silent drift that previously had ``profiling.ID_NAME_RE`` carrying an
extra ``id$`` branch — a typo that broadened the match to anything
ending in ``id`` (``paid``, ``void``, ``droid``). The
``semantic_suggestions`` version (without the typo) is the canonical
form preserved here.
"""

from __future__ import annotations

import re

ID_NAME_RE = re.compile(r"(^id$|_id$|uuid$|_key$|code$)", re.IGNORECASE)
TIME_NAME_RE = re.compile(r"(date|time|created|updated|ds|pt)$", re.IGNORECASE)
# Stricter sibling of TIME_NAME_RE used for the time-dimension boost.
# ``date|datetime|timestamp|created|updated|_at`` are unambiguously
# temporal across SQL projects; bare ``time$`` is excluded because it
# also matches duration columns (``laptime``, ``fastestlaptime``,
# ``responsetime``) that are measurements, not temporal moments. The
# partition-key abbreviations ``ds`` / ``pt`` are excluded because
# partition columns are skipped by the suggester anyway, so they
# never reach the dim-boost path. Keeping this regex narrow avoids
# false-positive ``dimension/time`` suggestions on metric-like
# duration columns — the broader TIME_NAME_RE stays in place for the
# identifier carve-out (where over-matching is the safer error).
#
# Pattern covers both snake_case (``release_date``, ``created_at``)
# and camelCase / lowercase compound (``releasedate``,
# ``originalreleasedate``, ``signupdate``) shapes. The trailing
# ``date|datetime|timestamp$`` does over-match a few non-temporal
# English words like ``mandate``/``candidate`` — but those are
# vanishingly rare as bare column names in SQL schemas, and the
# resulting confidence 0.35 ``dimension/time`` suggestion is a soft
# signal the agent reviews against other evidence during
# ``mcs package apply``.
TIME_DIM_NAME_RE = re.compile(
    r"(date|datetime|timestamp|created|updated|_at)$",
    re.IGNORECASE,
)
METRIC_NAME_RE = re.compile(
    r"(amount|price|cost|count|qty|quantity|score|total|sum)", re.IGNORECASE
)
DIM_NAME_RE = re.compile(
    r"(type|status|label|flag|cat|group|code|kind|class|category)$", re.IGNORECASE
)

# Pre-aggregated column-name patterns: the column NAME itself signals
# that the stored value is already an aggregate (an average per group,
# a precomputed count, etc.), not a raw measurement to be aggregated
# further at query time. Anchored at the start of the name; requires a
# trailing separator or letter so bare ``avg`` / ``count`` (which are
# often query-time aliases like ``SELECT SUM(x) AS total``) don't
# match. Matches both snake_case (``avg_score``, ``mean_age``,
# ``num_takers``) and no-separator compound forms for unambiguously
# aggregation-shaped prefixes (``avgScore``, ``countOrders``).
#
# Two prefix tiers with different post-prefix requirements:
#
#   - **Unambiguous aggregation words** (``avg``, ``mean``, ``median``,
#     ``stddev``, ``variance``, ``count``) match with a following ``_``
#     OR another letter (camelCase / glued compound). These words have
#     no common English-noun continuations that would create
#     false-positive column names.
#
#   - **Short / ambiguous abbreviations** (``num``, ``cnt``) require a
#     trailing ``_`` separator. ``num`` is a prefix of common
#     non-aggregate English words (``number``, ``numerator``,
#     ``numerical``, ``numeric``, ``numbers``) — without the ``_``
#     guard the regex would falsely flag a ``qualifying.number``
#     (driver racing number, an identifier) as "already aggregated, do
#     NOT re-aggregate". The ``_`` separator is the universal
#     convention for snake_case aggregation prefixes
#     (``num_takers``, ``cnt_orders``) and is the cleanest available
#     disambiguator.
#
# Deliberately omitted prefixes:
#   - ``total``: ambiguous (``total_amount`` is usually a per-row line-
#     item total, not a SUM across rows)
#   - ``sum``: ambiguous (often a query-time alias)
#   - ``min``/``max``: often dimension thresholds, not aggregates
#
# These name patterns block the type-heuristic metric-default in the
# suggester so the agent's ``mcs package apply`` doesn't write
# ``metrics: - {name: X, agg: AVG}`` for a column whose values are
# already averages — the failure shape regressions where a
# per-school-average column would be wrapped in another AVG by the
# downstream SQL generator.
PRE_AGGREGATED_NAME_RE = re.compile(
    r"^(?:(?:avg|mean|median|stddev|variance|count)[_a-z]|(?:num|cnt)_)",
    re.IGNORECASE,
)
