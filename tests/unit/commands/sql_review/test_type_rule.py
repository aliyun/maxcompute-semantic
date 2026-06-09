import pytest
from maxcompute_semantic.commands.sql_review.rules.type_check import (
    check_string_date_compare,
)


class TestStringDateCompare:
    def test_string_col_compared_to_date_literal_emits_warning(
        self, make_review_package, make_review_ctx
    ) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE created_at > '2026-01-01'",
            profile,
            db_path,
        )
        try:
            issues = check_string_date_compare(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "type.string-date-compare"
            assert issues[0].severity == "warning"
            assert "created_at" in issues[0].message
        finally:
            db.close()

    def test_date_col_compared_to_date_literal_no_issue(
        self, make_review_package, make_review_ctx
    ) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "DATETIME"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE created_at > '2026-01-01'",
            profile,
            db_path,
        )
        try:
            assert check_string_date_compare(ctx) == []
        finally:
            db.close()

    def test_string_col_compared_to_string_no_issue(
        self, make_review_package, make_review_ctx
    ) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "status", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE status = 'open'",
            profile,
            db_path,
        )
        try:
            assert check_string_date_compare(ctx) == []
        finally:
            db.close()

    def test_cte_query_still_fires(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "WITH ev AS (SELECT * FROM events WHERE created_at > '2026-01-01') SELECT * FROM ev",
            profile,
            db_path,
        )
        try:
            issues = check_string_date_compare(ctx)
            assert len(issues) == 1
            assert "created_at" in issues[0].message
        finally:
            db.close()

    def test_literal_on_left_still_fires(self, make_review_package, make_review_ctx) -> None:
        """The rule's left/right swap path needs a pin: when the literal
        sits on the left and the column on the right, the issue must
        still fire and still name the column."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE '2026-01-01' < created_at",
            profile,
            db_path,
        )
        try:
            issues = check_string_date_compare(ctx)
            assert len(issues) == 1
            assert "created_at" in issues[0].message
        finally:
            db.close()

    @pytest.mark.parametrize("col_type", ["STRING", "VARCHAR(64)", "CHAR(10)"])
    def test_varchar_and_char_also_fire(
        self, make_review_package, make_review_ctx, col_type
    ) -> None:
        """The implementation treats VARCHAR/CHAR alongside STRING; pin
        all three prefixes so a future tightening doesn't silently drop
        VARCHAR or CHAR coverage."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": col_type}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE created_at > '2026-01-01'",
            profile,
            db_path,
        )
        try:
            assert len(check_string_date_compare(ctx)) == 1
        finally:
            db.close()

    def test_fqn_resolves_to_named_source(self, make_review_package, make_review_ctx) -> None:
        """3-segment FQN must resolve the column type against the
        explicit ``catalog.db.table`` source — not the first source
        whose ``orders`` happens to carry a STRING ``created_at``.
        Regression: bare ``ctx.to_source_key(table_name)`` would pick
        proj_a (listed first), report a false-positive type warning
        even though proj_b's ``created_at`` is genuinely DATETIME.
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
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "orders",
                    "columns": [{"name": "created_at", "type": "DATETIME"}],
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM proj_b.default.orders WHERE created_at > '2026-01-01'",
            profile,
            db_path,
        )
        try:
            # proj_b's created_at is DATETIME — no warning.
            assert check_string_date_compare(ctx) == []
        finally:
            db.close()

    def test_nested_subquery_alias_shadowing_does_not_mis_route(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: an inner subquery reusing an outer alias for a
        different table must not pull the outer comparison's column
        resolution into the inner scope.

        Here ``e.created_at`` in the outer SELECT is a DATE column —
        the comparison should NOT fire. Pre-fix the statement-wide
        ``alias_to_table`` had the inner ``events e`` (where
        ``created_at`` is STRING) overwrite the outer entry, so the
        rule would see the outer date literal compared to a STRING
        column and fire a false ``type.string-date-compare`` warning.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events_dt",
                    "columns": [{"name": "created_at", "type": "DATE"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT e.created_at FROM events_dt e "
            "WHERE e.created_at > '2026-01-01' "
            "AND EXISTS (SELECT 1 FROM events e WHERE e.created_at = '2026-01-02')",
            profile,
            db_path,
        )
        try:
            issues = check_string_date_compare(ctx)
            # Only the inner subquery's STRING column should fire; the
            # outer DATE column must NOT mis-route to the inner STRING
            # column's schema.
            assert len(issues) == 1
            assert "events.created_at" in issues[0].message
        finally:
            db.close()

    def test_uppercase_alias_resolves_case_insensitively(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression for the Round 6 Codex P2 #3 finding.

        ``SELECT * FROM events E WHERE E.created_at > '2026-01-01'``
        must resolve ``E.created_at`` against the lower-cased alias
        key ``e``. Pre-fix the alias map stored the raw alias ``E``
        but the lookup lower-cased to ``e``, so the lookup missed
        and the type check silently dropped the column — the
        ``type.string-date-compare`` issue did not fire on a STRING
        column.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events E WHERE E.created_at > '2026-01-01'",
            profile,
            db_path,
        )
        try:
            issues = check_string_date_compare(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "type.string-date-compare"
            assert "created_at" in issues[0].message
        finally:
            db.close()

    def test_date_literal_does_not_fire(self, make_review_package, make_review_ctx) -> None:
        """Real ``DATE '2026-01-01'`` literals are typed-date nodes, not
        string literals — the ``is_string`` guard in ``_looks_like_date``
        must keep the rule from firing on them."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "created_at", "type": "STRING"}],
                },
            ]
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM events WHERE created_at > DATE '2026-01-01'",
            profile,
            db_path,
        )
        try:
            assert check_string_date_compare(ctx) == []
        finally:
            db.close()
