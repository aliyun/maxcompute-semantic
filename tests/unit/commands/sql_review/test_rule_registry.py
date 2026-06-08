# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from maxcompute_semantic.commands.sql_review.rules import ALL_RULES


def test_registry_is_a_list_of_callables() -> None:
    assert isinstance(ALL_RULES, list)
    for r in ALL_RULES:
        assert callable(r), f"non-callable in ALL_RULES: {r!r}"


def test_registry_has_expected_rule_count() -> None:
    # Tracks the catalog as it grows: 4 dialect + 2 schema + 1 tier +
    # 1 type + 2 projection rules.
    assert [rule.__name__ for rule in ALL_RULES] == [
        "check_sqlite_iif",
        "check_sqlite_strftime",
        "check_sqlite_julianday",
        "check_sqlite_substr_neg",
        "check_bare_table_in_3level",
        "check_ranking_key_in_projection",
        "check_intermediate_values_in_projection",
        "check_table_not_found",
        "check_column_not_found",
        "check_string_date_compare",
    ]
