# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from maxcompute_semantic.commands.sql_review.rules.schema import (
    check_column_not_found,
    check_table_not_found,
)


class TestTableNotFound:
    def test_known_table_passes(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id", "type": "BIGINT"}],
                },
            ]
        )
        db, ctx = make_review_ctx("SELECT id FROM orders", profile, db_path)
        try:
            assert check_table_not_found(ctx) == []
        finally:
            db.close()

    def test_unknown_table_emits_issue(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(tables=[])
        db, ctx = make_review_ctx("SELECT * FROM missing_table", profile, db_path)
        try:
            issues = check_table_not_found(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "schema.table-not-found"
            assert "missing_table" in issues[0].message
        finally:
            db.close()

    def test_cte_reference_does_not_emit(self, make_review_package, make_review_ctx) -> None:
        """`WITH cte AS (...) SELECT * FROM cte` — `cte` isn't a real table,
        so the rule must skip the CTE reference even though sqlglot's
        find_all(exp.Table) yields a Table node for it."""
        profile, db_path = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "orders", "columns": [{"name": "id"}]},
            ]
        )
        db, ctx = make_review_ctx(
            "WITH recent AS (SELECT id FROM orders) SELECT * FROM recent",
            profile,
            db_path,
        )
        try:
            assert check_table_not_found(ctx) == []
        finally:
            db.close()


class TestColumnNotFound:
    def test_known_column_passes(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "amount"}],
                },
            ]
        )
        db, ctx = make_review_ctx("SELECT id, amount FROM orders", profile, db_path)
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_unknown_column_emits_issue(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "orders", "columns": [{"name": "id"}]},
            ]
        )
        db, ctx = make_review_ctx("SELECT bogus FROM orders", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "schema.column-not-found"
            assert "bogus" in issues[0].message
            assert "orders" in issues[0].message
        finally:
            db.close()

    def test_case_insensitive_match(self, make_review_package, make_review_ctx) -> None:
        """Mirrors `_resolve_table_id` — column comparisons are case-insensitive."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "Amount"}],
                },
            ]
        )
        db, ctx = make_review_ctx("SELECT amount FROM orders", profile, db_path)
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_fqn_resolves_to_named_source(self, make_review_package, make_review_ctx) -> None:
        """3-segment FQN must resolve the column against the explicit
        `catalog.db.table` source — not the first source whose `orders`
        happens to carry the column. Without this guard, two same-named
        tables in different sources silently shadow each other."""
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
        # proj_a.default.orders has `amount`; proj_b.default.orders doesn't.
        # The bare-name path would resolve `orders` to proj_a (first match)
        # and miss the typo against proj_b's narrower schema.
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "amount"}],
                },
                {"source_key": "proj_b__default", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx("SELECT amount FROM proj_b.default.orders", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "schema.column-not-found"
            assert "amount" in issues[0].message
        finally:
            db.close()

    def test_fqn_dedup_checks_both_same_bare_name_refs(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Two ``orders`` from different sources, joined by alias — the
        column-not-found check must run on *both* refs. Bare-name dedup
        on ``(table, col)`` would skip ``b.amount`` once ``a.amount`` is
        seen, hiding a real typo against the second source's schema."""
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
                    "columns": [{"name": "id"}, {"name": "amount"}],
                },
                {"source_key": "proj_b__default", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT a.amount, b.amount FROM proj_a.default.orders a "
            "JOIN proj_b.default.orders b ON a.id = b.id",
            profile,
            db_path,
        )
        try:
            issues = check_column_not_found(ctx)
            # Exactly one issue: proj_b.orders lacks `amount`.
            assert len(issues) == 1
            assert issues[0].rule == "schema.column-not-found"
            assert "amount" in issues[0].message
        finally:
            db.close()

    def test_cte_output_alias_not_checked_against_base_table(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: outer column refs that resolve to a CTE output
        alias must not be checked against base tables nested inside the
        CTE body.

        Pre-fix, the unqualified-column branch did
        ``stmt.find_all(exp.Table)`` over the *whole* statement, so the
        outer ``SELECT user_id FROM ev`` would pick up ``events`` as the
        sole real table in the statement (the CTE body's FROM) and flag
        ``user_id`` as column-not-found against ``events`` — even though
        the outer SELECT never touched ``events`` and ``user_id`` is a
        legitimate CTE output column. The fix scopes the lookup to the
        column's enclosing Select's *direct* FROM/JOIN tables.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "WITH ev AS (SELECT id AS user_id FROM events) SELECT user_id FROM ev",
            profile,
            db_path,
        )
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_nested_subquery_alias_shadowing_does_not_mis_resolve(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: a subquery reusing an outer alias for a different
        table must not pull the outer column ref's resolution into the
        inner scope.

        Pre-fix the alias map was statement-wide (``alias_to_table(stmt)``
        walked every ``exp.Table`` regardless of nesting), so the inner
        ``other o`` overwrote the outer ``orders o`` entry and the outer
        ``o.id`` was checked against ``other`` — which only has ``x``,
        flagging a valid SQL with a hard ``schema.column-not-found``
        error and misleading the agent into rewriting working SQL. The
        fix scopes the alias map to the column's enclosing Select.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
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
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_unqualified_column_in_subquery_scoped_to_subquery_from(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: an unqualified column in a subquery must resolve
        against that subquery's FROM table, not bail because the outer
        statement has multiple real tables.

        Pre-fix the unqualified branch fell back to a statement-wide
        ``find_all(exp.Table)`` and bailed when ``len != 1``; with an
        outer FROM and an inner subquery FROM the count was 2 and the
        check silently dropped — missing a legitimate
        ``schema.column-not-found`` against the subquery's table.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT id FROM orders WHERE EXISTS (SELECT bogus FROM users)",
            profile,
            db_path,
        )
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert "bogus" in issues[0].message
            assert "users" in issues[0].message
        finally:
            db.close()

    def test_available_truncates_when_over_cap(self, make_review_package, make_review_ctx) -> None:
        """Wide tables (many columns) get a `(+N more)` overflow marker
        instead of a multi-kilobyte Available list."""
        # 25 columns; cap is 20.
        cols = [{"name": f"c{i:02d}"} for i in range(25)]
        profile, db_path = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "wide", "columns": cols},
            ]
        )
        db, ctx = make_review_ctx("SELECT bogus FROM wide", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            msg = issues[0].message
            assert "(+5 more)" in msg
            assert "c19" in msg  # 20th name is included
            assert "c20" not in msg  # 21st name is truncated
        finally:
            db.close()

    def test_order_by_alias_does_not_false_positive(
        self, make_review_package, make_review_ctx
    ) -> None:
        """``SELECT COUNT(*) AS cnt FROM orders ORDER BY cnt`` must not
        flag ``cnt``. MaxCompute (like standard SQL) lets ORDER BY
        reference SELECT-list aliases. Pre-fix the rule resolved the
        unqualified ``cnt`` against the single real table (``orders``)
        and emitted ``schema.column-not-found`` for valid SQL — Round 6
        Codex flagged this as P1 because the agent's "fix every error"
        loop then blocked legitimate aggregation queries."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                }
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT COUNT(*) AS cnt FROM orders ORDER BY cnt",
            profile,
            db_path,
        )
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_group_by_alias_does_not_false_positive(
        self, make_review_package, make_review_ctx
    ) -> None:
        """GROUP BY can reference SELECT-list aliases in MaxCompute."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "region_id"}],
                }
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT region_id AS region, COUNT(*) FROM orders GROUP BY region",
            profile,
            db_path,
        )
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_having_alias_does_not_false_positive(
        self, make_review_package, make_review_ctx
    ) -> None:
        """HAVING can reference projection aliases too."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "BIGINT"}],
                }
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(amount) AS total FROM orders HAVING total > 100",
            profile,
            db_path,
        )
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_where_alias_still_flags(self, make_review_package, make_review_ctx) -> None:
        """WHERE evaluates *before* the SELECT projection, so a reference
        to a projection alias from WHERE is a genuine error and must
        still fire — the suppression is scoped to ORDER/GROUP/HAVING/
        QUALIFY only."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "BIGINT"}],
                }
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT SUM(amount) AS total FROM orders WHERE total > 100",
            profile,
            db_path,
        )
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert "total" in issues[0].message
        finally:
            db.close()

    def test_alias_lookup_is_case_insensitive(self, make_review_package, make_review_ctx) -> None:
        """``SELECT o.bogus FROM orders O`` must still flag ``bogus``.
        Pre-fix the alias map stored ``O`` verbatim and the lookup used
        ``o``, so the unknown-alias branch swallowed every reference
        and silently suppressed every column-not-found in mixed-case
        alias SQL — Round 6 Codex P2."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                }
            ]
        )
        db, ctx = make_review_ctx("SELECT o.bogus FROM orders O", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert "bogus" in issues[0].message
        finally:
            db.close()

    def test_two_segment_schema_table_resolves_to_named_schema(
        self, make_review_package, make_review_ctx
    ) -> None:
        """``SELECT amount FROM schema_b.orders`` must check ``amount``
        against ``schema_b.orders`` specifically — not silently fall
        through to ``default.orders``. Round 6 Codex P1: pre-fix the
        column-check resolved through bare-name ``to_source_key`` and
        let a missing-column on the named schema slip past detection."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="multi_schema",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="rev_proj", schema="default", tables="*"),
                DataSource(project="rev_proj", schema="schema_b", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "BIGINT"}],
                },
                {
                    "source_key": "rev_proj__schema_b",
                    "name": "orders",
                    "columns": [{"name": "id"}],  # no `amount` here
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT amount FROM schema_b.orders", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert "amount" in issues[0].message
        finally:
            db.close()

    def test_two_segment_schema_table_passes_when_column_exists(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Positive control for the 2-segment FQN fix: a real column on
        the named schema must not be flagged."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="multi_schema",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="rev_proj", schema="default", tables="*"),
                DataSource(project="rev_proj", schema="schema_b", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "BIGINT"}],
                },
                {
                    "source_key": "rev_proj__schema_b",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "BIGINT"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT amount FROM schema_b.orders", profile, db_path)
        try:
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_two_segment_table_not_found_emits_issue(
        self, make_review_package, make_review_ctx
    ) -> None:
        """``check_table_not_found`` must flag a 2-segment reference whose
        named schema does not contain the table — pre-fix it walked the
        bare name across all sources and silently passed."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="multi_schema",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="rev_proj", schema="default", tables="*"),
                DataSource(project="rev_proj", schema="schema_b", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                # `orders` exists only on `default`, not on `schema_b`.
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM schema_b.orders", profile, db_path)
        try:
            issues = check_table_not_found(ctx)
            assert len(issues) == 1
            assert "schema_b.orders" in issues[0].message
        finally:
            db.close()

    def test_two_segment_prefers_target_project(self, make_review_package, make_review_ctx) -> None:
        """Regression (round 7 P1 #2): with --project proj_b and two
        sources sharing schema_x, resolve must prefer proj_b's source."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="proj_b",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="schema_x", tables="*"),
                DataSource(project="proj_b", schema="schema_x", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__schema_x",
                    "name": "orders",
                    "columns": [{"name": "id", "type": "BIGINT"}],
                },
                {
                    "source_key": "proj_b__schema_x",
                    "name": "orders",
                    "columns": [
                        {"name": "id", "type": "BIGINT"},
                        {"name": "amount", "type": "DECIMAL"},
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT amount FROM schema_x.orders", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert issues == []
        finally:
            db.close()

    def test_two_segment_target_project_missing_table_does_not_fallback(
        self, make_review_package, make_review_ctx
    ) -> None:
        """If schema_x exists in the target project but the table does
        not, review must not validate against another project's
        schema_x.orders."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="proj_b",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="schema_x", tables="*"),
                DataSource(project="proj_b", schema="schema_x", tables="*"),
            ),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__schema_x",
                    "name": "orders",
                    "columns": [{"name": "amount", "type": "DECIMAL"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT amount FROM schema_x.orders", profile, db_path)
        try:
            table_issues = check_table_not_found(ctx)
            assert len(table_issues) == 1
            assert "schema_x.orders" in table_issues[0].message
            assert check_column_not_found(ctx) == []
        finally:
            db.close()

    def test_project_table_does_not_resolve_non_default_schema(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Two-segment project.table is the 2-level/default-schema form;
        it must not match proj_a.schema_x.orders."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="proj_a",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(DataSource(project="proj_a", schema="schema_x", tables="*"),),
        )
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__schema_x",
                    "name": "orders",
                    "columns": [{"name": "id", "type": "BIGINT"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM proj_a.orders", profile, db_path)
        try:
            issues = check_table_not_found(ctx)
            assert len(issues) == 1
            assert "proj_a.orders" in issues[0].message
        finally:
            db.close()


class TestCaseInsensitiveTableLookup:
    def test_uppercase_table_name_resolves(self, make_review_package, make_review_ctx) -> None:
        """Regression: ``SELECT bogus FROM ORDERS`` must still detect
        the missing column. Pre-fix: get_table(sk, 'ORDERS') missed
        the row named 'orders' because the lookup was case-sensitive."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id", "type": "BIGINT"}],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT bogus FROM ORDERS", profile, db_path)
        try:
            issues = check_column_not_found(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "schema.column-not-found"
            assert "bogus" in issues[0].message
        finally:
            db.close()

    def test_mixed_case_table_coverage_resolves(self, make_review_package, make_review_ctx) -> None:
        """Coverage calculator must find the table row regardless of
        case mismatch between SQL and stored name."""
        from maxcompute_semantic.commands.sql_review.coverage import (
            compute_model_coverage,
        )

        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "ai_context": "Order facts.",
                    "columns": [
                        {"name": "id", "semantic_role": "identifier", "id_type": "primary_key"},
                    ],
                },
            ],
        )
        db, ctx = make_review_ctx("SELECT ORDERS.id FROM ORDERS", profile, db_path)
        try:
            cov = compute_model_coverage(ctx)
            assert cov["tables_referenced"] == 1
            assert cov["tables_with_ai_context"] == 1
            assert cov["columns_with_semantic_role"] == 1
        finally:
            db.close()
