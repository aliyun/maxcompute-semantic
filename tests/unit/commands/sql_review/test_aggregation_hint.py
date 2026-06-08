# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for aggregation hint generator (`hints/aggregation.py`).

The hint generator skips aggregates over unqualified columns (the
``extract_sql_evidence`` shape carries an empty string for the table
side when the SQL doesn't qualify the column reference). All positive
tests therefore use qualified ``table.column`` form so the rule has a
table to look up against the package — this mirrors the v1 design
("skip rather than infer the single-real-table FROM target") rather
than the plan-doc test snippets that wrote unqualified SQL.
"""

from __future__ import annotations

from maxcompute_semantic.commands.sql_review.hints.aggregation import (
    hint_dimension_aggregated,
)


class TestDimensionAggregated:
    def test_sum_on_dimension_emits_hint(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "status",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT SUM(orders.status) FROM orders", profile, db_path)
        try:
            hints = hint_dimension_aggregated(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "aggregation.dimension-aggregated"
            assert "status" in hints[0].message
            assert "SUM" in hints[0].message
            assert hints[0].confidence == "medium"
            assert hints[0].evidence["function"] == "SUM"
            assert hints[0].evidence["table"] == "orders"
            assert hints[0].evidence["column"] == "status"
            assert hints[0].evidence["declared_role"] == "dimension"
        finally:
            db.close()

    def test_sum_on_measure_no_hint(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "amount",
                            "semantic_role": "measure",
                            "agg": "sum",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT SUM(orders.amount) FROM orders", profile, db_path)
        try:
            assert hint_dimension_aggregated(ctx) == []
        finally:
            db.close()

    def test_count_on_unannotated_no_hint(self, make_review_package, make_review_ctx) -> None:
        """Without an annotation we don't know — don't emit a hint."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT COUNT(orders.id) FROM orders", profile, db_path)
        try:
            assert hint_dimension_aggregated(ctx) == []
        finally:
            db.close()

    def test_avg_on_dimension_emits_hint(self, make_review_package, make_review_ctx) -> None:
        """The rule covers AVG (and every aggregate), not just SUM."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "region",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT AVG(orders.region) FROM orders", profile, db_path)
        try:
            hints = hint_dimension_aggregated(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "aggregation.dimension-aggregated"
            assert "AVG" in hints[0].message
            assert "region" in hints[0].message
        finally:
            db.close()

    def test_aggregate_in_cte_still_fires(self, make_review_package, make_review_ctx) -> None:
        """Aggregates inside a CTE body are surfaced by extract_sql_evidence."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "status",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "WITH cte AS (SELECT SUM(orders.status) AS s FROM orders) SELECT * FROM cte",
            profile,
            db_path,
        )
        try:
            hints = hint_dimension_aggregated(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "aggregation.dimension-aggregated"
        finally:
            db.close()

    def test_fqn_resolves_to_named_source(self, make_review_package, make_review_ctx) -> None:
        """3-segment FQN must resolve the annotation against the
        explicit ``catalog.db.table`` source. Regression: the bare-name
        path (`ctx.evidence.aggregates` carries only the bare name)
        used to pick whichever source was listed first in
        ``profile.sources``, firing a hint against the wrong source's
        annotation. Here proj_a.amount is annotated as ``dimension``
        and proj_b.amount as ``measure``; SQL selects from proj_b so
        no hint should fire."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_b", schema="default", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__default",
                    "name": "orders",
                    "columns": [
                        {"name": "amount", "semantic_role": "dimension", "dim_type": "categorical"}
                    ],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "semantic_role": "measure", "agg": "sum"}],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(orders.amount) FROM proj_b.default.orders",
            profile,
            db_path,
        )
        try:
            assert hint_dimension_aggregated(ctx) == []
        finally:
            db.close()

    def test_dedup_same_func_table_col(self, make_review_package, make_review_ctx) -> None:
        """Repeated `SUM(status)` in the same SELECT must dedup to one hint."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "status",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(orders.status), SUM(orders.status) FROM orders",
            profile,
            db_path,
        )
        try:
            hints = hint_dimension_aggregated(ctx)
            assert len(hints) == 1
        finally:
            db.close()

    def test_nested_subquery_alias_shadowing_does_not_mis_route(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: an inner subquery reusing an outer alias for a
        different table must not pull the outer aggregate's column
        resolution into the inner scope.

        Pre-fix the statement-wide ``alias_to_table`` collapsed both
        ``o`` entries; the outer ``SUM(o.amount)`` over the measure
        column ``orders.amount`` ended up resolved against
        ``other.amount`` (dimension), firing a false hint. The fix
        scopes the alias map to the column's enclosing Select.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {"name": "amount", "semantic_role": "measure", "agg": "sum"},
                        {"name": "id"},
                    ],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "other",
                    "columns": [
                        {
                            "name": "amount",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        },
                        {"name": "id"},
                    ],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(o.amount) FROM orders o "
            "WHERE EXISTS (SELECT 1 FROM other o WHERE o.id = 1)",
            profile,
            db_path,
        )
        try:
            # Outer ``o.amount`` is orders.amount (metric, no hint).
            # The inner subquery has no aggregate. Pre-fix this fired
            # against other.amount (dimension).
            assert hint_dimension_aggregated(ctx) == []
        finally:
            db.close()

    def test_uppercase_alias_resolves_case_insensitively(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression for the Round 6 Codex P2 #3 finding.

        ``SELECT SUM(O.status) FROM orders O`` must resolve ``O.status``
        against the lower-cased alias key ``o``. Pre-fix the alias map
        stored the raw alias ``O`` but the lookup lower-cased to ``o``,
        so the hint silently dropped on an annotated-as-dimension column.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {
                            "name": "status",
                            "semantic_role": "dimension",
                            "dim_type": "categorical",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(O.status) FROM orders O",
            profile,
            db_path,
        )
        try:
            hints = hint_dimension_aggregated(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "aggregation.dimension-aggregated"
            assert hints[0].evidence["table"] == "orders"
            assert hints[0].evidence["column"] == "status"
        finally:
            db.close()

    def test_fqn_dedup_checks_both_same_bare_name_refs(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Same bare-name tables from different sources must each be checked.

        Regression for the Round 3 Codex finding: the dedup tuple used
        ``(func, table, col)`` and collapsed
        ``proj_a.default.orders.amount`` and
        ``proj_b.default.orders.amount`` to one key, so the first
        aggregate marked the bare triple as seen and the second one
        silently skipped — meaning a real wrong-role hint against
        ``proj_a.amount`` (annotated as dimension) was dropped when
        ``proj_b.amount`` (measure) was aggregated first.
        """
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_b", schema="default", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__default",
                    "name": "orders",
                    "columns": [
                        {"name": "amount", "semantic_role": "dimension", "dim_type": "categorical"}
                    ],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "semantic_role": "measure", "agg": "sum"}],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(b.amount), SUM(a.amount) "
            "FROM proj_b.default.orders b JOIN proj_a.default.orders a ON a.id = b.id",
            profile,
            db_path,
        )
        try:
            hints = hint_dimension_aggregated(ctx)
            # proj_b.amount is measure (no hint), proj_a.amount is
            # dimension (hint must fire). Pre-fix the dimension hint
            # was masked because the measure aggregate landed first and
            # claimed the bare ("SUM","orders","amount") triple.
            assert len(hints) == 1
            assert hints[0].evidence["table"] == "orders"
            assert hints[0].evidence["column"] == "amount"
            assert hints[0].evidence["declared_role"] == "dimension"
        finally:
            db.close()
