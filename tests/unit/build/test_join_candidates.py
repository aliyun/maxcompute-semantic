# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/join_candidates.py — ranked JOIN candidate logic."""

from __future__ import annotations

from maxcompute_semantic.build.join_candidates import (
    build_overlap_validation_sql,
    rank_join_candidates,
)


class TestRankJoinCandidates:
    def test_history_join_beats_same_name_heuristic(self) -> None:
        tables = {
            ("p__s", "cards"): {
                "uuid": {"uniqueness_ratio": 0.99, "approx_ndv": 1000},
                "id": {"uniqueness_ratio": 1.0, "approx_ndv": 1000},
            },
            ("p__s", "legalities"): {
                "uuid": {"uniqueness_ratio": 0.25, "approx_ndv": 250},
                "id": {"uniqueness_ratio": 1.0, "approx_ndv": 1000},
            },
        }
        workload = {"join_counts": {"cards.uuid=legalities.uuid": 5}}
        name_edges = [
            {
                "left_source_key": "p__s",
                "left_table": "cards",
                "left_col": "id",
                "right_source_key": "p__s",
                "right_table": "legalities",
                "right_col": "id",
                "kind": "same_name",
                "confidence": 0.5,
            },
        ]

        ranked = rank_join_candidates(
            tables=tables,
            workload_summary=workload,
            name_edges=name_edges,
        )

        uuid_candidate = [c for c in ranked if c.left_col == "uuid"]
        assert len(uuid_candidate) >= 1
        assert uuid_candidate[0].confidence > 0.25

        # cards.id↔legalities.id is PK↔PK (both uniqueness ~1.0) so the
        # ranker penalizes it: 0.40 * 0.6 = 0.24, well below the workload
        # edge for cards.uuid=legalities.uuid at 0.275.
        id_candidate = [c for c in ranked if c.left_col == "id" and c.left_table == "cards"]
        assert id_candidate, "PK↔PK candidate should still be emitted, just penalized"
        assert id_candidate[0].confidence < uuid_candidate[0].confidence
        assert id_candidate[0].cardinality == "1:1"

    def test_pk_pk_same_name_is_penalized(self) -> None:
        """Both sides ~unique → PK↔PK shape; confidence drops below same_name cap."""
        ranked = rank_join_candidates(
            tables={
                ("p__s", "cards"): {"id": {"uniqueness_ratio": 1.0, "approx_ndv": 60000}},
                ("p__s", "legalities"): {"id": {"uniqueness_ratio": 1.0, "approx_ndv": 430000}},
            },
            workload_summary={"join_counts": {}},
            name_edges=[
                {
                    "left_source_key": "p__s",
                    "left_table": "cards",
                    "left_col": "id",
                    "right_source_key": "p__s",
                    "right_table": "legalities",
                    "right_col": "id",
                    "kind": "same_name",
                    "confidence": 0.5,
                },
            ],
        )
        assert len(ranked) == 1
        assert ranked[0].confidence < 0.40
        assert ranked[0].cardinality == "1:1"
        shape_evidence = next(e for e in ranked[0].evidence if e.get("source") == "profile_stats")
        assert shape_evidence["join_shape"] == "pk-pk"

    def test_fk_pk_same_name_is_boosted_over_pk_pk(self) -> None:
        """When both cards.id↔legalities.id (PK↔PK) and cards.uuid↔legalities.uuid
        (FK→PK on the legalities side) exist, the FK→PK edge must win."""
        tables = {
            ("p__s", "cards"): {
                "id": {"uniqueness_ratio": 1.0, "approx_ndv": 60000},
                "uuid": {"uniqueness_ratio": 1.0, "approx_ndv": 60000},
            },
            ("p__s", "legalities"): {
                "id": {"uniqueness_ratio": 1.0, "approx_ndv": 430000},
                "uuid": {"uniqueness_ratio": 0.13, "approx_ndv": 57000},
            },
        }
        name_edges = [
            {
                "left_source_key": "p__s",
                "left_table": "cards",
                "left_col": "id",
                "right_source_key": "p__s",
                "right_table": "legalities",
                "right_col": "id",
                "kind": "same_name",
                "confidence": 0.5,
            },
            {
                "left_source_key": "p__s",
                "left_table": "cards",
                "left_col": "uuid",
                "right_source_key": "p__s",
                "right_table": "legalities",
                "right_col": "uuid",
                "kind": "same_name",
                "confidence": 0.5,
            },
        ]
        ranked = rank_join_candidates(
            tables=tables,
            workload_summary={"join_counts": {}},
            name_edges=name_edges,
        )

        # Both candidates connect cards→legalities (same conflict group).
        # The FK→PK (uuid) candidate must have higher confidence than the
        # PK↔PK (id) candidate, so the id candidate is marked conflicting.
        uuid = next(c for c in ranked if c.left_col == "uuid")
        id_ = next(c for c in ranked if c.left_col == "id")
        assert uuid.confidence > id_.confidence
        assert id_.status == "conflicting"
        # The pk-fk edge (left=cards.uuid unique, right=legalities.uuid
        # non-unique) reads as 1:n from the cards perspective.
        assert uuid.cardinality == "1:n"

    def test_shared_fk_columns_both_non_unique_kept(self) -> None:
        """team.team_api_id↔team_attributes.team_api_id (shared FK to absent
        team_api): both sides non-unique → fk-fk shape, base confidence kept."""
        ranked = rank_join_candidates(
            tables={
                ("p__s", "team"): {
                    "team_api_id": {"uniqueness_ratio": 0.95, "approx_ndv": 200},
                },
                ("p__s", "team_attributes"): {
                    "team_api_id": {"uniqueness_ratio": 0.05, "approx_ndv": 200},
                },
            },
            workload_summary={"join_counts": {}},
            name_edges=[
                {
                    "left_source_key": "p__s",
                    "left_table": "team",
                    "left_col": "team_api_id",
                    "right_source_key": "p__s",
                    "right_table": "team_attributes",
                    "right_col": "team_api_id",
                    "kind": "same_name",
                    "confidence": 0.5,
                },
            ],
        )
        assert len(ranked) == 1
        # left ~unique, right non-unique → pk-fk → boosted by left_uniqueness.
        assert ranked[0].confidence > 0.40
        assert ranked[0].cardinality == "1:n"

    def test_name_only_same_name_candidate_has_low_confidence(self) -> None:
        ranked = rank_join_candidates(
            tables={
                ("p__s", "cards"): {"name": {"uniqueness_ratio": 0.02}},
                ("p__s", "sets"): {"name": {"uniqueness_ratio": 0.5}},
            },
            workload_summary={"join_counts": {}},
            name_edges=[
                {
                    "left_source_key": "p__s",
                    "left_table": "cards",
                    "left_col": "name",
                    "right_source_key": "p__s",
                    "right_table": "sets",
                    "right_col": "name",
                    "kind": "same_name",
                    "confidence": 0.5,
                },
            ],
        )

        assert ranked[0].confidence <= 0.40
        assert ranked[0].evidence[0]["source"] == "name_heuristic"

    def test_cross_source_loose_id_with_same_table_name_kept(self) -> None:
        """Cross-source loose_id survives ranking even when left/right table names match."""
        ranked = rank_join_candidates(
            tables={("warehouse", "account"): {"id": {"uniqueness_ratio": 1.0}}},
            workload_summary={"join_counts": {}},
            name_edges=[
                {
                    "left_source_key": "ledger",
                    "left_table": "account",
                    "left_col": "account_id",
                    "right_source_key": "warehouse",
                    "right_table": "account",
                    "right_col": "id",
                    "kind": "loose_id",
                    "confidence": 0.5,
                },
            ],
        )
        assert len(ranked) == 1

    def test_workload_candidate_picks_up_source_key_and_cardinality(self) -> None:
        """Workload edges are stored with bare table names (no source_key
        on either side). When exactly one source carries the bare name,
        the ranker must:
        - set the source_key on the candidate (so per-table markdown
          rendering, which filters ``join_candidates`` by
          ``left_source_key``, surfaces the candidate to the agent),
        - look up uniqueness on both sides,
        - emit join_shape evidence and infer cardinality.

        Confidence stays at the workload-frequency-derived value — the
        SQL itself is authoritative, no PK-PK penalty or uniqueness
        boost applies to workload edges.
        """
        tables = {
            ("warehouse", "orders"): {
                "customer_id": {"uniqueness_ratio": 0.03, "approx_ndv": 200},
            },
            ("warehouse", "customers"): {
                "id": {"uniqueness_ratio": 1.0, "approx_ndv": 200},
            },
        }
        ranked = rank_join_candidates(
            tables=tables,
            workload_summary={"join_counts": {"orders.customer_id=customers.id": 6}},
            name_edges=[],
        )
        assert len(ranked) == 1
        c = ranked[0]
        assert c.left_source_key == "warehouse"
        assert c.right_source_key == "warehouse"
        # 6/10 * 0.55 = 0.33, unchanged by shape adjustment.
        assert abs(c.confidence - 0.33) < 1e-6
        assert c.cardinality == "n:1"
        assert c.right_uniqueness_ratio == 1.0
        shape_evidence = next(e for e in c.evidence if e.get("source") == "profile_stats")
        assert shape_evidence["join_shape"] == "fk-pk"

    def test_workload_candidate_ambiguous_table_name_keeps_empty_source_key(self) -> None:
        """When two sources both carry a bare table name, the ranker
        cannot safely choose one — leave source_key empty so the
        downstream agent view shows the workload signal without
        misattributing it to a single source.
        """
        tables = {
            ("warehouse", "orders"): {"id": {"uniqueness_ratio": 1.0}},
            ("ledger", "orders"): {"id": {"uniqueness_ratio": 1.0}},
            ("warehouse", "customers"): {"id": {"uniqueness_ratio": 1.0}},
        }
        ranked = rank_join_candidates(
            tables=tables,
            workload_summary={"join_counts": {"orders.id=customers.id": 4}},
            name_edges=[],
        )
        assert len(ranked) == 1
        c = ranked[0]
        assert c.left_source_key == ""  # ambiguous: orders in two sources
        assert c.right_source_key == "warehouse"  # customers unambiguous
        # Left side uniqueness can't be looked up without source_key,
        # so join_shape sees only the right side.
        assert c.right_uniqueness_ratio == 1.0


class TestBuildOverlapValidationSql:
    def test_includes_count_and_join(self) -> None:
        sql = build_overlap_validation_sql(
            left_fq_name="p.s.orders",
            left_col="customer_id",
            right_fq_name="p.s.customers",
            right_col="id",
            left_where_clause="WHERE ds = '20260521'",
            right_where_clause="",
        )

        assert "COUNT(1) AS left_non_null" in sql
        assert "COUNT(r.id) AS matched_rows" in sql
        assert "FROM (SELECT customer_id FROM p.s.orders WHERE ds = '20260521')" in sql
        assert "LEFT JOIN (SELECT DISTINCT id FROM p.s.customers)" in sql
