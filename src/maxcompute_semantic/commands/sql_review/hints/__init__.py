"""Hint registry — inferential suggestions, severity info.

Hints are the inferential sibling of issues: they don't gate execution
and they may be wrong. Each hint carries a ``confidence`` band
(``high``/``medium``/``low``) and an optional ``if_misleading`` string
that tells the agent how to dismiss / override the suggestion.

Each hint generator is a callable ``(ctx: ReviewContext) -> list[Hint]``.
The dispatcher iterates ``ALL_HINTS`` and concatenates results.
"""

from __future__ import annotations

from collections.abc import Callable

from maxcompute_semantic.commands.sql_review.hints.aggregation import (
    hint_dimension_aggregated,
)
from maxcompute_semantic.commands.sql_review.hints.join_hints import (
    hint_join_bridge_suggested,
    hint_join_not_declared,
)
from maxcompute_semantic.commands.sql_review.hints.pattern import (
    hint_verified_match,
)
from maxcompute_semantic.commands.sql_review.types import Hint, ReviewContext

HintFn = Callable[[ReviewContext], list[Hint]]

ALL_HINTS: list[HintFn] = [
    hint_join_not_declared,
    hint_join_bridge_suggested,
    hint_dimension_aggregated,
    hint_verified_match,
]
