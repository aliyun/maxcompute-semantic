# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from maxcompute_semantic.commands.sql_review.rules.tier import (
    check_bare_table_in_3level,
)

from .conftest import _mock_multi_source_profile


class TestBareTableIn3Level:
    def test_bare_table_in_2level_no_issue(self, make_review_package, make_review_ctx) -> None:
        profile, db_path = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "orders", "columns": [{"name": "id"}]},
            ]
        )
        db, ctx = make_review_ctx("SELECT * FROM orders", profile, db_path, tier="2")
        try:
            assert check_bare_table_in_3level(ctx) == []
        finally:
            db.close()

    def test_single_source_3level_no_issue(self, make_review_package, make_review_ctx) -> None:
        """Single-source profile in 3-level: execute auto-injects
        odps.default.schema, so bare table names resolve correctly.
        The rule must not emit — doing so wastes agent turns on
        unnecessary FQN qualification.
        """
        profile, db_path = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "orders", "columns": [{"name": "id"}]},
            ]
        )
        db, ctx = make_review_ctx("SELECT * FROM orders", profile, db_path, tier="3")
        try:
            assert check_bare_table_in_3level(ctx) == []
        finally:
            db.close()

    def test_multi_source_3level_emits_issue(self, make_review_package, make_review_ctx) -> None:
        """Multi-source profile in 3-level: bare table names are
        ambiguous across sources — the rule must warn.
        """
        profile = _mock_multi_source_profile()
        _, db_path = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "rev_proj__schema_a", "name": "orders", "columns": [{"name": "id"}]},
                {"source_key": "rev_proj__schema_b", "name": "users", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx("SELECT * FROM orders", profile, db_path, tier="3")
        try:
            issues = check_bare_table_in_3level(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "tier.bare-table-in-3level"
            assert "orders" in issues[0].message
            assert "schema_a" in issues[0].message
            assert "schema_b" in issues[0].message
            assert "3-segment FQN" in issues[0].fix_hint
        finally:
            db.close()

    def test_two_segment_schema_table_multi_source_emits(
        self, make_review_package, make_review_ctx
    ) -> None:
        """`default.orders` (without `project.`) in multi-source must still fire."""
        profile = _mock_multi_source_profile()
        _, db_path = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "rev_proj__schema_a", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM default.orders",
            profile,
            db_path,
            tier="3",
        )
        try:
            issues = check_bare_table_in_3level(ctx)
            assert len(issues) == 1
            assert issues[0].rule == "tier.bare-table-in-3level"
        finally:
            db.close()

    def test_fqn_table_in_3level_no_issue(self, make_review_package, make_review_ctx) -> None:
        profile = _mock_multi_source_profile()
        _, db_path = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "rev_proj__schema_a", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM rev_proj.default.orders", profile, db_path, tier="3"
        )
        try:
            assert check_bare_table_in_3level(ctx) == []
        finally:
            db.close()

    def test_cte_reference_does_not_emit(self, make_review_package, make_review_ctx) -> None:
        """CTE reference in multi-source 3-level must not be flagged.
        Only the real bare table inside the CTE body should fire.
        """
        profile = _mock_multi_source_profile()
        _, db_path = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "rev_proj__schema_a", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )
        db, ctx = make_review_ctx(
            "WITH recent AS (SELECT id FROM orders) SELECT * FROM recent",
            profile,
            db_path,
            tier="3",
        )
        try:
            issues = check_bare_table_in_3level(ctx)
            assert len(issues) == 1
            assert "orders" in issues[0].message
        finally:
            db.close()
