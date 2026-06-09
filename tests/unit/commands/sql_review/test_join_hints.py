# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for join hint generators (`hints/join_hints.py`)."""

from __future__ import annotations

from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    Profile,
)
from maxcompute_semantic.commands.sql_review.hints.join_hints import (
    hint_join_bridge_suggested,
    hint_join_not_declared,
)


def _two_source_profile() -> Profile:
    return Profile(
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


class TestJoinNotDeclared:
    def test_join_with_no_declaration_emits_hint(
        self, make_review_package, make_review_ctx
    ) -> None:
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
            joins=[],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders o JOIN users u ON o.user_id = u.id",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "join.not-declared"
            assert "orders" in hints[0].message
            assert "users" in hints[0].message
            assert hints[0].if_misleading is not None
            # Points at runnable, real CLI guidance; the specific wording
            # is allowed to evolve as long as it surfaces the live
            # ``mcs build --refresh`` / ``mcs package propose`` paths.
            assert "mcs build --refresh" in hints[0].if_misleading
        finally:
            db.close()

    def test_declared_join_no_hint(self, make_review_package, make_review_ctx) -> None:
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
            assert hint_join_not_declared(ctx) == []
        finally:
            db.close()

    def test_cross_source_declared_join_does_not_suppress_other_source(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: a join declared between ``proj_a.default.orders``
        and ``proj_a.default.users`` must NOT suppress the
        ``join.not-declared`` hint for the same bare-name pair on
        ``proj_b``. Pre-fix the bare-name ``{left_table, right_table}``
        check in ``_declared_pair`` collapsed both source's
        ``{"orders", "users"}`` sets to the same target and silently
        skipped the cross-source hint."""
        profile = _two_source_profile()
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
                },
                {
                    "source_key": "proj_a__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[
                # Declared only on proj_a side.
                {
                    "left_source_key": "proj_a__default",
                    "left_table": "orders",
                    "left_col": "user_id",
                    "right_source_key": "proj_a__default",
                    "right_table": "users",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM proj_b.default.orders o JOIN proj_b.default.users u ON o.user_id = u.id",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "join.not-declared"
            assert hints[0].evidence["left_source_key"] == "proj_b__default"
            assert hints[0].evidence["right_source_key"] == "proj_b__default"
            # Source qualifier surfaces in the message so the agent can
            # tell which source's `orders`/`users` the hint flags.
            assert "proj_b__default.orders" in hints[0].message
            assert "proj_b__default.users" in hints[0].message
        finally:
            db.close()

    def test_subquery_join_not_paired_with_outer_from(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: a JOIN inside a subquery must not pair with the
        outer FROM table.

        Pre-fix ``_sql_join_pairs`` seeded ``left`` from the top-level
        FROM via ``stmt.find(exp.From)`` and then walked every Join in
        the statement via ``stmt.find_all(exp.Join)`` — so a JOIN
        nested inside ``WHERE EXISTS (...)`` was paired against the
        outer ``orders`` FROM table, fabricating ``orders <-> users``
        when the SQL actually only joins ``events <-> users``.

        The fix iterates each ``exp.Select`` independently using its
        own ``args["from_"]`` + ``args["joins"]``, so outer and inner
        scopes never cross-pair.
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
                    "name": "events",
                    "columns": [{"name": "user_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders WHERE EXISTS ("
            "SELECT 1 FROM events JOIN users ON events.user_id = users.id"
            ")",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            # Only the real inner JOIN ``events <-> users`` should fire;
            # the pre-fix phantom ``orders <-> users`` must be gone.
            assert len(hints) == 1
            assert hints[0].evidence["left_table"] == "events"
            assert hints[0].evidence["right_table"] == "users"
        finally:
            db.close()

    def test_outer_and_inner_joins_paired_independently(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression sibling: when both outer and inner Selects have
        their own JOINs, each pair is extracted independently per
        scope. Pre-fix the outer ``left`` would chain through the
        inner subquery's JOINs, producing nonsense like
        ``orders -> events -> users -> products``.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "customer_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "customers",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "events",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders o "
            "JOIN customers c ON o.customer_id = c.id "
            "WHERE EXISTS ("
            "SELECT 1 FROM events e JOIN users u ON e.user_id = u.id"
            ")",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            pairs = {(h.evidence["left_table"], h.evidence["right_table"]) for h in hints}
            # Exactly two pairs, one per scope. Pre-fix the outer
            # ``left`` would have chained through the inner JOIN,
            # producing ``customers -> users`` (or similar phantom).
            assert pairs == {("orders", "customers"), ("events", "users")}
        finally:
            db.close()

    def test_cte_join_does_not_fire(self, make_review_package, make_review_ctx) -> None:
        """CTE refs surface as exp.Table nodes; they must not pair as joins.

        Mirrors the CTE-skipping pattern in ``rules/schema.py`` and
        ``rules/tier.py`` — a CTE name is not a real table, so any
        JOIN pair that touches it is dropped before the declared-pair
        lookup.
        """
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
            joins=[],
        )
        db, ctx = make_review_ctx(
            "WITH cte AS (SELECT * FROM orders) "
            "SELECT * FROM cte JOIN users u ON cte.user_id = u.id",
            profile,
            db_path,
        )
        try:
            assert hint_join_not_declared(ctx) == []
        finally:
            db.close()

    def test_on_clause_drives_pairing_not_source_order(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression for the Round 6 Codex P2 #4 finding.

        ``orders JOIN users ON ... JOIN payments ON orders.id =
        payments.order_id`` actually joins ``orders <-> payments`` —
        the ``users`` table is along for the ride for the previous
        JOIN. The pre-fix source-order pairing logic chained the JOINs as
        ``[(orders, users), (users, payments)]`` and fired a wrong
        ``join.not-declared`` hint for ``users <-> payments`` even
        when ``orders <-> payments`` is declared in the package.

        With ``orders <-> payments`` declared (and ``orders <-> users``
        declared so its hint doesn't fire either), the only behaviour
        that proves the ON-clause pairing works is: no
        ``join.not-declared`` hints at all. A failure of the new
        pairing bug surfaces as a ``users <-> payments`` hint.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [
                        {"name": "id"},
                        {"name": "user_id"},
                    ],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "payments",
                    "columns": [{"name": "order_id"}],
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
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "orders",
                    "left_col": "id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "payments",
                    "right_col": "order_id",
                    "kind": "one_to_many",
                    "confidence": 0.9,
                    "cardinality": "one-to-many",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders "
            "JOIN users ON orders.user_id = users.id "
            "JOIN payments ON orders.id = payments.order_id",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            # Both pairs are declared. Pre-fix the source-order pairing logic
            # would produce (users, payments) — undeclared — and fire
            # a phantom hint. Post-fix we get exactly (orders, users)
            # and (orders, payments) — both declared — so no hint.
            assert hints == [], f"unexpected hints: {[h.evidence for h in hints]}"
        finally:
            db.close()

    def test_chain_fallback_when_on_clause_missing(
        self, make_review_package, make_review_ctx
    ) -> None:
        """When the ON clause is missing (CROSS JOIN here), fall back
        to source-order chain pairing so the v1 ``A JOIN B JOIN C =>
        [(A,B), (B,C)]`` shape is preserved.

        Declare ``a <-> b`` and ``b <-> c`` so the only way both
        pairs end up "declared" is if the fallback fires exactly
        once per consecutive pair. If the fallback regressed and
        emitted ``(a, c)`` instead, that pair is undeclared and
        we'd see a hint.
        """
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "a",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "b",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "c",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "a",
                    "left_col": "id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "b",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "b",
                    "left_col": "id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "c",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM a CROSS JOIN b CROSS JOIN c",
            profile,
            db_path,
        )
        try:
            hints = hint_join_not_declared(ctx)
            assert hints == [], f"unexpected hints: {[h.evidence for h in hints]}"
        finally:
            db.close()


class TestJoinBridgeSuggested:
    def test_bridge_path_emits_hint(self, make_review_package, make_review_ctx) -> None:
        # orders -> customers -> users; SQL JOINs orders <-> users directly.
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "customer_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "customers",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
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
                    "left_col": "customer_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "customers",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "customers",
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
            hints = hint_join_bridge_suggested(ctx)
            assert len(hints) == 1
            assert hints[0].kind == "join.bridge-suggested"
            assert "customers" in hints[0].evidence.get("bridge_path", "")
        finally:
            db.close()

    def test_no_bridge_path_no_hint(self, make_review_package, make_review_ctx) -> None:
        """Direct JOIN with no declared bridge in the graph emits nothing."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "user_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "isolated_table",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM orders o JOIN isolated_table t ON o.user_id = t.id",
            profile,
            db_path,
        )
        try:
            assert hint_join_bridge_suggested(ctx) == []
        finally:
            db.close()

    def test_bridge_graph_does_not_cross_sources_via_same_bare_name(
        self, make_review_package, make_review_ctx
    ) -> None:
        """Regression: the BFS bridge graph must not link
        ``proj_b.orders`` to ``proj_b.users`` by walking edges declared
        entirely within ``proj_a`` (which happen to use the same bare
        ``customers`` name). Pre-fix the graph keyed on bare table names
        and silently treated ``proj_a.customers`` and ``proj_b.customers``
        as the same node, manufacturing a phantom cross-source path."""
        profile = _two_source_profile()
        profile, db_path = make_review_package(
            profile=profile,
            tables=[
                {
                    "source_key": "proj_a__default",
                    "name": "orders",
                    "columns": [{"name": "customer_id"}],
                },
                {
                    "source_key": "proj_a__default",
                    "name": "customers",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
                },
                {
                    "source_key": "proj_a__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "orders",
                    "columns": [{"name": "id"}, {"name": "user_id"}],
                },
                {
                    "source_key": "proj_b__default",
                    "name": "users",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[
                # Bridge declared *only* on proj_a side.
                {
                    "left_source_key": "proj_a__default",
                    "left_table": "orders",
                    "left_col": "customer_id",
                    "right_source_key": "proj_a__default",
                    "right_table": "customers",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "proj_a__default",
                    "left_table": "customers",
                    "left_col": "user_id",
                    "right_source_key": "proj_a__default",
                    "right_table": "users",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM proj_b.default.orders o JOIN proj_b.default.users u ON o.user_id = u.id",
            profile,
            db_path,
        )
        try:
            # No phantom bridge — proj_b nodes are absent from the graph.
            assert hint_join_bridge_suggested(ctx) == []
        finally:
            db.close()

    def test_paths_beyond_max_hops_not_suggested(
        self, make_review_package, make_review_ctx
    ) -> None:
        """5-table linear chain a->b->c->d->e is 4 hops; max_hops=3 caps it."""
        profile, db_path = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "a",
                    "columns": [{"name": "id"}, {"name": "b_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "b",
                    "columns": [{"name": "id"}, {"name": "c_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "c",
                    "columns": [{"name": "id"}, {"name": "d_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "d",
                    "columns": [{"name": "id"}, {"name": "e_id"}],
                },
                {
                    "source_key": "rev_proj__default",
                    "name": "e",
                    "columns": [{"name": "id"}],
                },
            ],
            joins=[
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "a",
                    "left_col": "b_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "b",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "b",
                    "left_col": "c_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "c",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "c",
                    "left_col": "d_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "d",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
                {
                    "left_source_key": "rev_proj__default",
                    "left_table": "d",
                    "left_col": "e_id",
                    "right_source_key": "rev_proj__default",
                    "right_table": "e",
                    "right_col": "id",
                    "kind": "many_to_one",
                    "confidence": 0.9,
                    "cardinality": "many-to-one",
                },
            ],
        )
        db, ctx = make_review_ctx(
            "SELECT * FROM a JOIN e ON a.id = e.id",
            profile,
            db_path,
        )
        try:
            assert hint_join_bridge_suggested(ctx) == []
        finally:
            db.close()
