"""Tests for ``compute_model_coverage`` (`coverage.py`).

The calculator quantifies how much of the SQL's surface — referenced
tables, projected columns, and declared joins — is backed by
annotations in the package. The result populates the envelope's
``model_coverage`` field; high coverage signals the agent should trust
hints, low coverage signals more guesswork.

Adaptation pin notes (mirroring the plan):

- CTE references must NOT count as referenced tables (adaptation E)
- Unqualified columns inside a CTE body must resolve to the single
  real (non-CTE) table the same way ``rules/schema.py`` resolves them
  (adaptation D)
"""

from __future__ import annotations

from maxcompute_semantic.commands.sql_review.coverage import (
    compute_model_coverage,
)


class TestComputeModelCoverage:
    def test_fully_annotated_high_coverage(self, make_review_package, make_review_ctx) -> None:
        """A SQL whose every table + projected column is annotated → 100%."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "ai_context": "Customer order facts.",
                    "columns": [
                        {
                            "name": "id",
                            "semantic_role": "identifier",
                            "id_type": "primary_key",
                        },
                        {
                            "name": "amount",
                            "semantic_role": "measure",
                            "agg": "sum",
                        },
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT orders.id, orders.amount FROM orders", profile, db_path)
        try:
            cov = compute_model_coverage(ctx)
            assert cov["tables_referenced"] == 1
            assert cov["tables_with_ai_context"] == 1
            assert cov["columns_referenced"] == 2
            assert cov["columns_with_semantic_role"] == 2
            assert cov["coverage_pct"] == 100
        finally:
            db.close()

    def test_unannotated_zero_coverage(self, make_review_package, make_review_ctx) -> None:
        """Tables exist but carry no ai_context / semantic_role → 0%."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "amount"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT orders.id, orders.amount FROM orders", profile, db_path)
        try:
            cov = compute_model_coverage(ctx)
            assert cov["tables_referenced"] == 1
            assert cov["tables_with_ai_context"] == 0
            assert cov["columns_referenced"] == 2
            assert cov["columns_with_semantic_role"] == 0
            assert cov["coverage_pct"] == 0
        finally:
            db.close()

    def test_cte_table_does_not_count(self, make_review_package, make_review_ctx) -> None:
        """A CTE name (`ev`) must not inflate the referenced-table count."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "ai_context": "Event log.",
                    "columns": [{"name": "id"}],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "WITH ev AS (SELECT id FROM events) SELECT * FROM ev",
            profile,
            db_path,
        )
        try:
            cov = compute_model_coverage(ctx)
            # CTE `ev` is filtered out — only `events` counts.
            assert cov["tables_referenced"] == 1
            assert cov["tables_with_ai_context"] == 1
            # 100% on tables side; columns side has only `id` (unannotated)
            # → table_pct=100, col_pct=0, average=50.
            assert cov["coverage_pct"] == 50
        finally:
            db.close()

    def test_cte_unqualified_column_resolves(self, make_review_package, make_review_ctx) -> None:
        """Inside a CTE body, unqualified columns must resolve to the single
        real table — mirrors ``rules/schema.py:check_column_not_found``."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "ai_context": "Event log.",
                    "columns": [
                        {
                            "name": "id",
                            "semantic_role": "identifier",
                            "id_type": "primary_key",
                        },
                        {
                            "name": "amount",
                            "semantic_role": "measure",
                            "agg": "sum",
                        },
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "WITH ev AS (SELECT id, amount FROM events) SELECT * FROM ev",
            profile,
            db_path,
        )
        try:
            cov = compute_model_coverage(ctx)
            # The two unqualified columns inside the CTE body must
            # resolve to `events` — not be silently dropped because
            # `find_all(exp.Table)` returned 2 nodes (events + ev).
            assert cov["columns_referenced"] == 2
            assert cov["columns_with_semantic_role"] == 2
            assert cov["tables_referenced"] == 1
            assert cov["coverage_pct"] == 100
        finally:
            db.close()

    def test_joins_declared_and_used(self, make_review_package, make_review_ctx) -> None:
        """``joins_declared`` counts joins where either side is referenced;
        ``joins_used_in_sql`` is the count of join_edges from the evidence."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "user_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "orders",
                    "left_col": "user_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "users",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders o JOIN users u ON o.user_id = u.id",
            profile,
            db_path,
        )
        try:
            cov = compute_model_coverage(ctx)
            assert cov["joins_declared"] == 1
            assert cov["joins_used_in_sql"] == 1
        finally:
            db.close()

    def test_fqn_dedup_counts_both_same_bare_name_refs(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Two ``orders`` from different sources in one SQL must each
        contribute to the referenced-tables / columns counters. Bare-name
        dedup would collapse them and report 100% coverage on a SQL
        whose second source is wholly un-annotated."""
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
                    "ai_context": "annotated",
                    "columns": [
                        {"name": "id", "semantic_role": "identifier", "id_type": "primary_key"},
                    ],
                },
                # proj_b.orders: no ai_context, no semantic_role.
                {"source_key": "proj_b__default", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT a.id, b.id FROM proj_a.default.orders a "
            "JOIN proj_b.default.orders b ON a.id = b.id",
            profile,
            db_path,
        )
        try:
            cov = compute_model_coverage(ctx)
            # Two FQN-distinct tables — pre-fix would report 1.
            assert cov["tables_referenced"] == 2
            assert cov["tables_with_ai_context"] == 1
            # Two FQN-distinct (table, col) pairs — pre-fix would report 1.
            assert cov["columns_referenced"] == 2
            assert cov["columns_with_semantic_role"] == 1
            # table_pct=50, col_pct=50 → 50%, not the pre-fix 100%.
            assert cov["coverage_pct"] == 50
        finally:
            db.close()

    def test_nested_subquery_alias_shadowing_does_not_mis_count(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: a subquery reusing an outer alias for a different
        table must count the outer column against the OUTER table's
        annotation, not the inner subquery's.

        Pre-fix the alias map was statement-wide so the inner ``other o``
        overwrote the outer ``orders o`` and the outer ``o.id`` was
        counted against ``other`` (no semantic_role) — under-counting
        when ``orders.id`` is annotated.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "ai_context": "orders fact",
                    "columns": [
                        {
                            "name": "id",
                            "semantic_role": "identifier",
                            "id_type": "primary_key",
                        }
                    ],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "other",
                    "columns": [{"name": "x"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM other o WHERE o.x = 1)",
            profile,
            db_path,
        )
        try:
            cov = compute_model_coverage(ctx)
            # The outer ``o.id`` must count against orders.id (annotated);
            # the inner ``o.x`` must count against other.x (unannotated).
            assert cov["columns_referenced"] == 2
            assert cov["columns_with_semantic_role"] == 1
        finally:
            db.close()

    def test_uppercase_alias_resolves_case_insensitively(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression for the Round 6 Codex P2 #3 finding.

        ``SELECT O.id FROM orders O`` must count ``O.id`` against
        ``orders.id``. Pre-fix the alias map stored the raw alias
        ``O`` but the lookup lower-cased to ``o`` — so the column
        was silently dropped from the referenced-columns count,
        under-counting coverage and skipping the annotation check.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "ai_context": "Orders fact.",
                    "columns": [
                        {
                            "name": "id",
                            "semantic_role": "identifier",
                            "id_type": "primary_key",
                        }
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT O.id FROM orders O", profile, db_path)
        try:
            cov = compute_model_coverage(ctx)
            assert cov["tables_referenced"] == 1
            assert cov["columns_referenced"] == 1
            assert cov["columns_with_semantic_role"] == 1
            assert cov["coverage_pct"] == 100
        finally:
            db.close()

    def test_fqn_coverage_counts_named_source(self, make_review_package, make_review_ctx) -> None:
        """3-segment FQN must count annotation coverage against the
        explicit ``catalog.db.table`` source. Regression: bare-name
        ``ctx.to_source_key`` returns proj_a (listed first) and counts
        its annotations even when the SQL targets proj_b.
        proj_a.orders has ai_context + semantic_role; proj_b.orders is
        un-annotated. The FQN-form SQL must report 0% coverage."""
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
                    "ai_context": "annotated",
                    "columns": [
                        {"name": "id", "semantic_role": "identifier", "id_type": "primary_key"}
                    ],
                },
                {"source_key": "proj_b__default", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx("SELECT id FROM proj_b.default.orders", profile, db_path)
        try:
            cov = compute_model_coverage(ctx)
            assert cov["tables_referenced"] == 1
            assert cov["tables_with_ai_context"] == 0
            assert cov["columns_referenced"] == 1
            assert cov["columns_with_semantic_role"] == 0
            assert cov["coverage_pct"] == 0
        finally:
            db.close()
