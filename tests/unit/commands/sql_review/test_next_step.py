# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from maxcompute_semantic.commands.sql_review.next_step import next_step_for_sql


def _suggested_tables(msg: str) -> list[str]:
    """Extract the comma-separated list after ``--tables`` from *msg*."""
    marker = "--tables "
    idx = msg.index(marker) + len(marker)
    end = msg.index(" ", idx)
    return msg[idx:end].split(",")


class TestNextStepForSql:
    def test_select_with_tables_suggests_memory_verify(self) -> None:
        msg = next_step_for_sql("SELECT id FROM orders")
        assert "mcs memory verify" in msg
        assert "orders" in msg

    def test_select_no_tables_is_empty(self) -> None:
        # `SELECT 1` has no tables; no memory.verify suggestion makes sense
        assert next_step_for_sql("SELECT 1") == ""

    def test_write_sql_returns_empty(self) -> None:
        assert next_step_for_sql("INSERT INTO orders VALUES (1)") == ""

    def test_cte_name_excluded_from_suggested_tables(self) -> None:
        msg = next_step_for_sql("WITH cte AS (SELECT id FROM real_table) SELECT id FROM cte")
        assert set(_suggested_tables(msg)) == {"real_table"}

    def test_fqn_qualifiers_preserved(self) -> None:
        msg = next_step_for_sql("SELECT id FROM proj.sch.foo")
        assert "proj.sch.foo" in msg
        assert "--tables proj.sch.foo" in msg

    def test_two_segment_db_qualifier_preserved(self) -> None:
        msg = next_step_for_sql("SELECT id FROM sch.foo")
        assert "sch.foo" in msg


def test_envelope_contains_next_step_for_select_with_tables() -> None:
    from unittest.mock import MagicMock

    from maxcompute_semantic.mc_client.client import MaxComputeClient

    instance = MagicMock()
    instance.open_reader.return_value.__enter__.return_value.schema.columns = []
    instance.open_reader.return_value.__enter__.return_value.__iter__.return_value = iter([])
    instance.get_logview_address.return_value = "http://logview"
    client = MaxComputeClient.__new__(MaxComputeClient)
    envelope = client._build_success_envelope(instance, started=0.0, sql="SELECT id FROM orders")
    assert envelope.to_dict()["data"]["next_step"].startswith("If the result")


def test_envelope_omits_next_step_for_writes() -> None:
    """Empty-suggestion path must not emit a next_step key at all."""
    from unittest.mock import MagicMock

    from maxcompute_semantic.mc_client.client import MaxComputeClient

    instance = MagicMock()
    instance.open_reader.return_value.__enter__.return_value.schema.columns = []
    instance.open_reader.return_value.__enter__.return_value.__iter__.return_value = iter([])
    instance.get_logview_address.return_value = "http://logview"
    client = MaxComputeClient.__new__(MaxComputeClient)
    envelope = client._build_success_envelope(
        instance, started=0.0, sql="INSERT INTO orders VALUES (1)"
    )
    assert "next_step" not in envelope.to_dict()["data"]
