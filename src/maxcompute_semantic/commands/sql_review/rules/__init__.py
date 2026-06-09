# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Rule registry — populated incrementally by category modules.

Each rule is a callable `(ctx: ReviewContext) -> list[Issue]`.
The dispatcher iterates ALL_RULES and concatenates results.
"""

from __future__ import annotations

from collections.abc import Callable

from maxcompute_semantic.commands.sql_review.rules.dialect import (
    check_sqlite_iif,
    check_sqlite_julianday,
    check_sqlite_strftime,
    check_sqlite_substr_neg,
)
from maxcompute_semantic.commands.sql_review.rules.projection import (
    check_intermediate_values_in_projection,
    check_ranking_key_in_projection,
)
from maxcompute_semantic.commands.sql_review.rules.schema import (
    check_column_not_found,
    check_table_not_found,
)
from maxcompute_semantic.commands.sql_review.rules.tier import (
    check_bare_table_in_3level,
)
from maxcompute_semantic.commands.sql_review.rules.type_check import (
    check_string_date_compare,
)
from maxcompute_semantic.commands.sql_review.types import Issue, ReviewContext

RuleFn = Callable[[ReviewContext], list[Issue]]

PACKAGE_INDEPENDENT_RULES: list[RuleFn] = [
    check_sqlite_iif,
    check_sqlite_strftime,
    check_sqlite_julianday,
    check_sqlite_substr_neg,
    check_bare_table_in_3level,
    check_ranking_key_in_projection,
    check_intermediate_values_in_projection,
]

PACKAGE_RULES: list[RuleFn] = [
    check_table_not_found,
    check_column_not_found,
    check_string_date_compare,
]

ALL_RULES: list[RuleFn] = [*PACKAGE_INDEPENDENT_RULES, *PACKAGE_RULES]
