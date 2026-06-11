# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for SQL review projection rules."""

from __future__ import annotations

from maxcompute_semantic.commands.sql_review.rules.projection import (
    check_intermediate_values_in_projection,
    check_ranking_key_in_projection,
)
from maxcompute_semantic.commands.sql_review.types import ReviewContext


class _Profile:
    sources = ()


def _ctx(sql: str) -> ReviewContext:
    return ReviewContext(
        sql=sql,
        evidence=None,
        profile=_Profile(),
        project="p",
        schema_name=None,
        tier="2",
        db=None,
        classification="read",
    )


class TestRankingKeyProjection:
    def test_flags_projected_order_by_key_in_top_n_query(self) -> None:
        issues = check_ranking_key_in_projection(
            _ctx("SELECT name, score FROM users ORDER BY score DESC LIMIT 1")
        )

        assert len(issues) == 1
        assert issues[0].rule == "projection.ranking-key-in-select"
        assert "`score`" in issues[0].message
        assert issues[0].fix_hint is not None

    def test_ignores_order_key_not_projected(self) -> None:
        assert (
            check_ranking_key_in_projection(
                _ctx("SELECT name FROM users ORDER BY score DESC LIMIT 1")
            )
            == []
        )

    def test_requires_limit_and_order_by(self) -> None:
        assert check_ranking_key_in_projection(_ctx("SELECT score FROM users ORDER BY score")) == []
        assert check_ranking_key_in_projection(_ctx("SELECT score FROM users LIMIT 1")) == []

    def test_ignores_grouping_key_and_aggregate_projection(self) -> None:
        issues = check_ranking_key_in_projection(
            _ctx(
                "SELECT region, SUM(amount) AS total "
                "FROM orders GROUP BY region ORDER BY total DESC LIMIT 1"
            )
        )

        assert issues == []


class TestIntermediateProjection:
    def test_flags_raw_operands_projected_with_difference(self) -> None:
        issues = check_intermediate_values_in_projection(
            _ctx("SELECT revenue, cost, revenue - cost AS profit FROM orders")
        )

        assert len(issues) == 1
        assert issues[0].rule == "projection.intermediate-values"
        assert "revenue" in issues[0].message
        assert "cost" in issues[0].message

    def test_flags_case_operand_projected_with_derived_result(self) -> None:
        issues = check_intermediate_values_in_projection(
            _ctx("SELECT CASE WHEN a > b THEN a ELSE b END AS max_value, a FROM t")
        )

        assert len(issues) == 1
        assert issues[0].rule == "projection.intermediate-values"
        assert "a" in issues[0].message

    def test_ignores_derived_expression_without_raw_operands(self) -> None:
        assert (
            check_intermediate_values_in_projection(
                _ctx("SELECT revenue - cost AS profit FROM orders")
            )
            == []
        )

    def test_ignores_plain_projection_without_derived_expression(self) -> None:
        assert check_intermediate_values_in_projection(_ctx("SELECT revenue, cost FROM orders")) == []
