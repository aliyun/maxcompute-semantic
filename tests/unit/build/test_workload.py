# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/workload.py — SQL workload evidence extraction."""

from __future__ import annotations

from maxcompute_semantic.build.workload import (
    aggregate_workload_evidence,
    extract_sql_evidence,
)


class TestExtractSqlEvidence:
    def test_join_where_group_and_aggregate(self) -> None:
        evidence = extract_sql_evidence(
            """
            SELECT c.rarity, SUM(c.convertedmanacost)
            FROM cards c
            JOIN legalities l ON c.uuid = l.uuid
            WHERE l.format = 'commander'
            GROUP BY c.rarity
            """
        )

        assert "cards" in evidence.tables
        assert "legalities" in evidence.tables
        assert len(evidence.join_edges) >= 1
        assert ("cards", "rarity") in evidence.group_by_columns
        assert ("SUM", "cards", "convertedmanacost") in evidence.aggregates

    def test_keeps_unknown_alias_as_seen(self) -> None:
        evidence = extract_sql_evidence("SELECT * FROM orders o WHERE o.status = 'PAID'")

        assert "orders" in evidence.tables
        assert ("orders", "status") in evidence.where_columns
        assert evidence.parse_error is None

    def test_parse_error_returns_partial_evidence(self) -> None:
        evidence = extract_sql_evidence("NOT SQL AT ALL !!!")

        assert evidence.parse_error is not None


class TestAggregateWorkloadEvidence:
    def test_counts_accumulate(self) -> None:
        sqls = [
            "SELECT c.rarity FROM cards c WHERE c.rarity = 'rare'",
            "SELECT c.name FROM cards c WHERE c.rarity = 'common'",
        ]
        summary = aggregate_workload_evidence(sqls)

        assert summary.table_counts["cards"] == 2
        assert summary.where_counts["cards.rarity"] == 2
        assert summary.parse_errors == 0

    def test_to_jsonable(self) -> None:
        summary = aggregate_workload_evidence(["SELECT * FROM orders"])
        result = summary.to_jsonable()

        assert "join_counts" in result
        assert "table_counts" in result
        assert isinstance(result["table_counts"], dict)

    def test_min_shape_frequency_drops_singletons(self) -> None:
        # Three SQLs: two share a shape, one is unique.
        sqls = [
            "SELECT c.rarity FROM cards c GROUP BY c.rarity",
            "SELECT c.rarity FROM cards c GROUP BY c.rarity",
            "SELECT s.name FROM sets s GROUP BY s.name",
        ]
        # Default (min_shape_frequency=1): every SQL contributes.
        unfiltered = aggregate_workload_evidence(sqls)
        assert unfiltered.group_by_counts["cards.rarity"] == 2
        assert unfiltered.group_by_counts["sets.name"] == 1
        # min_shape_frequency=2: singleton shape dropped, repeating shape kept.
        filtered = aggregate_workload_evidence(sqls, min_shape_frequency=2)
        assert filtered.group_by_counts["cards.rarity"] == 2
        assert "sets.name" not in filtered.group_by_counts

    def test_min_shape_frequency_counts_parse_errors_separately(self) -> None:
        summary = aggregate_workload_evidence(
            ["NOT SQL AT ALL !!!", "SELECT * FROM orders"],
            min_shape_frequency=2,
        )
        # Parse error still tallied even though it doesn't contribute keys.
        assert summary.parse_errors == 1
        # Singleton valid SQL dropped under threshold; nothing accumulates.
        assert "orders" not in summary.table_counts

    def test_allowed_tables_drops_cross_source_keys(self) -> None:
        """A mined SQL that JOINs an in-source table (``legalities``) with
        an out-of-source one (``cards``) must not contribute cards-side
        joins / where-cols / aggregates to the per-source workload
        summary — those keys would mis-rank join candidates and column
        classification for the source that doesn't own ``cards``.

        This is the exact regression observed against profile ``test3``
        on real TASKS_HISTORY: 31 occurrences of ``cards.uuid =
        legalities.uuid`` were attributed to the legalities source even
        though ``cards`` was not in its selection.
        """
        sqls = [
            ("SELECT l.format FROM legalities l "
            "JOIN cards c ON c.uuid = l.uuid "
            "WHERE c.power = 'X' AND l.format = 'commander'"),
        ]
        summary = aggregate_workload_evidence(sqls, allowed_tables={"legalities"})

        assert "cards" not in summary.table_counts
        assert "legalities" in summary.table_counts
        assert "cards.power" not in summary.where_counts
        assert "legalities.format" in summary.where_counts
        # cross-source JOIN edge has ``cards`` on one side -> dropped.
        assert summary.join_counts == {}

    def test_allowed_tables_keeps_multi_clause_in_source_join(self) -> None:
        """Multi-clause ON expressions (``t1.a=t2.b AND t1.c=?``) must
        survive ``allowed_tables`` filtering as long as every
        table-qualified column ref resolves into the source. An
        earlier ``split('=')`` implementation incorrectly dropped these
        because the multi-equality string had 3 ``=`` parts.
        """
        sqls = [
            ("SELECT s.name FROM sets s "
            "JOIN set_translations st ON s.code = st.setcode "
            "AND st.language = 'en'"),
        ]
        summary = aggregate_workload_evidence(sqls, allowed_tables={"sets", "set_translations"})

        # The single multi-clause edge survived.
        assert len(summary.join_counts) == 1

    def test_allowed_tables_keeps_unqualified_column_refs(self) -> None:
        """Unqualified column refs (no ``table.`` prefix) typically come
        from single-FROM queries where the column unambiguously belongs
        to the in-source table. Filtering them out would shrink the
        ``workload_columns_this_source`` seed set that drives sampling
        and profiling decisions.
        """
        sqls = [
            "SELECT name FROM cards WHERE rarity = 'rare' GROUP BY name",
        ]
        summary = aggregate_workload_evidence(sqls, allowed_tables={"cards"})

        # ``name`` and ``rarity`` arrive as ``("", col)`` from
        # extract_sql_evidence (no alias resolution on bare columns
        # without an alias-qualified reference); they must pass the
        # filter rather than being dropped.
        assert "rarity" in summary.where_counts or "cards.rarity" in summary.where_counts
        assert "name" in summary.group_by_counts or "cards.name" in summary.group_by_counts

    def test_allowed_tables_none_preserves_legacy_behavior(self) -> None:
        """``allowed_tables=None`` (the default) must be a strict
        no-op vs the pre-filter code path. Verified by exercising the
        same SQL with and without the parameter and asserting equal
        counts on every accumulator.
        """
        sqls = [
            "SELECT l.format FROM legalities l JOIN cards c ON c.uuid = l.uuid",
        ]
        with_filter = aggregate_workload_evidence(sqls)
        without_param = aggregate_workload_evidence(sqls, allowed_tables=None)

        assert with_filter.to_jsonable() == without_param.to_jsonable()
