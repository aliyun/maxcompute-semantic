# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``maxcompute_semantic.build.cross_env``.

The detector identifies source pairs whose table-name sets overlap
heavily enough to be confident they're dev/prod copies of the same
schema, so the build pipeline can suppress JOIN inference between
them.
"""

from __future__ import annotations

from maxcompute_semantic.build.cross_env import (
    CrossEnvDuplicatePair,
    detect_cross_env_duplicate_sources,
)


class TestDetectCrossEnvDuplicateSources:
    def test_empty_input_returns_empty_list(self) -> None:
        assert detect_cross_env_duplicate_sources({}) == []

    def test_single_source_returns_empty(self) -> None:
        result = detect_cross_env_duplicate_sources({"acme__prod": {"users", "orders", "products"}})
        assert result == []

    def test_two_disjoint_sources_no_pair(self) -> None:
        result = detect_cross_env_duplicate_sources(
            {
                "acme__warehouse": {"users", "orders", "products"},
                "billing__main": {"invoices", "payments", "refunds"},
            }
        )
        assert result == []

    def test_two_identical_sources_flagged_at_100pct(self) -> None:
        tables = {"users", "orders", "products", "events"}
        result = detect_cross_env_duplicate_sources({"acme__prod": tables, "acme__staging": tables})
        assert len(result) == 1
        p = result[0]
        assert p.source_a == "acme__prod"
        assert p.source_b == "acme__staging"
        assert p.shared_count == 4
        assert p.smaller_size == 4
        assert p.overlap_ratio == 1.0
        assert p.shared_tables == ("events", "orders", "products", "users")

    def test_alphabetical_order_independent_of_input_order(self) -> None:
        """``source_a`` is always alphabetically first regardless of dict ordering."""
        tables = {"a", "b", "c", "d"}
        result_one = detect_cross_env_duplicate_sources({"z_source": tables, "a_source": tables})
        result_two = detect_cross_env_duplicate_sources({"a_source": tables, "z_source": tables})
        assert result_one == result_two
        assert result_one[0].source_a == "a_source"
        assert result_one[0].source_b == "z_source"

    def test_partial_overlap_above_default_threshold_flagged(self) -> None:
        # Prod: 5 tables; staging: 4 tables (subset). 4/4 = 100% overlap of smaller.
        result = detect_cross_env_duplicate_sources(
            {
                "acme__prod": {"users", "orders", "products", "events", "carts"},
                "acme__staging": {"users", "orders", "products", "events"},
            }
        )
        assert len(result) == 1
        assert result[0].smaller_size == 4
        assert result[0].shared_count == 4
        assert result[0].overlap_ratio == 1.0

    def test_overlap_below_threshold_not_flagged(self) -> None:
        # Two 10-table sources, share 5 → 50% of smaller. Below 70% default.
        result = detect_cross_env_duplicate_sources(
            {
                "src_a": {f"t{i}" for i in range(10)},
                "src_b": {f"t{i}" for i in range(5, 15)},
            }
        )
        assert result == []

    def test_shared_count_below_floor_not_flagged(self) -> None:
        # Two 2-table sources sharing 2 → 100% ratio but only 2 shared tables.
        # Default min_shared_tables=3 blocks the flag.
        result = detect_cross_env_duplicate_sources(
            {"src_a": {"users", "events"}, "src_b": {"users", "events"}}
        )
        assert result == []

    def test_smaller_set_governs_ratio(self) -> None:
        # Tiny dev source (3 tables, all in prod) vs large prod source (50 tables).
        # Ratio = 3/3 = 100%. Flagged.
        dev_tables = {"users", "orders", "products"}
        prod_tables = dev_tables | {f"prod_only_{i}" for i in range(47)}
        result = detect_cross_env_duplicate_sources(
            {"acme__dev": dev_tables, "acme__prod": prod_tables}
        )
        assert len(result) == 1
        assert result[0].smaller_size == 3
        assert result[0].overlap_ratio == 1.0

    def test_three_sources_all_share_emits_three_pairs(self) -> None:
        tables = {"users", "orders", "products", "events"}
        result = detect_cross_env_duplicate_sources(
            {"acme__dev": tables, "acme__prod": tables, "acme__staging": tables}
        )
        assert len(result) == 3
        pairs = {(p.source_a, p.source_b) for p in result}
        assert pairs == {
            ("acme__dev", "acme__prod"),
            ("acme__dev", "acme__staging"),
            ("acme__prod", "acme__staging"),
        }

    def test_empty_source_excluded_from_pair_count(self) -> None:
        # A source with zero tables can't be a duplicate of anything.
        result = detect_cross_env_duplicate_sources(
            {
                "empty__src": set(),
                "real__src": {"users", "orders", "products", "events"},
            }
        )
        assert result == []

    def test_custom_thresholds_relax_detection(self) -> None:
        # Two 2-table sources sharing both → 100% ratio, 2 shared.
        # Default rejects (min_shared_tables=3); custom min_shared_tables=2 flags.
        result = detect_cross_env_duplicate_sources(
            {"src_a": {"users", "events"}, "src_b": {"users", "events"}},
            min_shared_tables=2,
        )
        assert len(result) == 1
        assert result[0].shared_count == 2

    def test_custom_thresholds_tighten_detection(self) -> None:
        # 4/5 shared = 80% of smaller. Default flags; tightened 0.95 rejects.
        tables_a = {"users", "orders", "products", "events", "carts"}
        tables_b = {"users", "orders", "products", "events", "totally_different"}
        baseline = detect_cross_env_duplicate_sources({"src_a": tables_a, "src_b": tables_b})
        assert len(baseline) == 1
        tightened = detect_cross_env_duplicate_sources(
            {"src_a": tables_a, "src_b": tables_b}, min_overlap_ratio=0.95
        )
        assert tightened == []

    def test_pair_is_frozen_dataclass(self) -> None:
        p = CrossEnvDuplicatePair(
            source_a="a",
            source_b="b",
            shared_tables=("x",),
            shared_count=1,
            smaller_size=1,
            overlap_ratio=1.0,
        )
        # Frozen dataclass — mutation raises.
        import dataclasses

        try:
            p.source_a = "c"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError("CrossEnvDuplicatePair should be frozen")
