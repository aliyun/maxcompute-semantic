# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client.cost_gate.enforce_cost_gate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from maxcompute_semantic.auth.schema import CostThresholds
from maxcompute_semantic.mc_client.cost_gate import enforce_cost_gate
from maxcompute_semantic.mc_client.errors import (
    CostBlockedError,
    CostConfirmRequiredError,
    PermissionDeniedError,
    TableNotFoundError,
)


def _client(cost: dict, *, confirm: float = 10.0, blocked: float = 100.0) -> MagicMock:
    """Build a MagicMock client with the given profile thresholds and the
    given cost-estimate return payload. Tests use this instead of a real
    MaxComputeClient because the gate's contract is just (read thresholds,
    call cost_estimate, branch on verdict)."""
    c = MagicMock()
    c.profile.cost_thresholds = CostThresholds(confirm_cny=confirm, blocked_cny=blocked)
    c.cost_estimate.return_value = cost
    return c


def test_gate_disabled_short_circuits_without_calling_cost_estimate() -> None:
    c = MagicMock()
    c.profile.cost_thresholds = CostThresholds(enabled=False)
    result = enforce_cost_gate(c, "SELECT 1", assume_yes=False, is_tty=False)
    assert result is None
    c.cost_estimate.assert_not_called()


def test_gate_ok_verdict_passes() -> None:
    c = _client(
        {
            "verdict": "ok",
            "estimated_cost_cny": 0.5,
            "estimated_input_bytes": 100,
            "thresholds": {},
        }
    )
    enforce_cost_gate(c, "SELECT 1", assume_yes=False, is_tty=False)
    c.cost_estimate.assert_called_once()


def test_gate_blocked_verdict_always_raises_even_with_assume_yes() -> None:
    c = _client(
        {
            "verdict": "blocked",
            "estimated_cost_cny": 200.0,
            "estimated_input_bytes": 1,
            "thresholds": {"blocked_cny": 100.0},
        }
    )
    with pytest.raises(CostBlockedError) as exc:
        enforce_cost_gate(c, "SELECT 1", assume_yes=True, is_tty=True)
    assert "200" in str(exc.value)


def test_gate_confirm_no_tty_no_yes_raises() -> None:
    c = _client(
        {
            "verdict": "confirm",
            "estimated_cost_cny": 15.0,
            "estimated_input_bytes": 1,
            "thresholds": {"confirm_cny": 10.0},
        }
    )
    with pytest.raises(CostConfirmRequiredError):
        enforce_cost_gate(c, "SELECT 1", assume_yes=False, is_tty=False)


def test_gate_confirm_with_assume_yes_passes() -> None:
    c = _client(
        {
            "verdict": "confirm",
            "estimated_cost_cny": 15.0,
            "estimated_input_bytes": 1,
            "thresholds": {"confirm_cny": 10.0},
        }
    )
    enforce_cost_gate(c, "SELECT 1", assume_yes=True, is_tty=False)


def test_gate_confirm_tty_prompt_yes_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(
        {
            "verdict": "confirm",
            "estimated_cost_cny": 15.0,
            "estimated_input_bytes": 1,
            "thresholds": {"confirm_cny": 10.0},
        }
    )
    monkeypatch.setattr(
        "maxcompute_semantic.mc_client.cost_gate.click.confirm",
        lambda *a, **kw: True,
    )
    enforce_cost_gate(c, "SELECT 1", assume_yes=False, is_tty=True)


def test_gate_confirm_tty_prompt_no_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(
        {
            "verdict": "confirm",
            "estimated_cost_cny": 15.0,
            "estimated_input_bytes": 1,
            "thresholds": {"confirm_cny": 10.0},
        }
    )
    monkeypatch.setattr(
        "maxcompute_semantic.mc_client.cost_gate.click.confirm",
        lambda *a, **kw: False,
    )
    with pytest.raises(CostConfirmRequiredError):
        enforce_cost_gate(c, "SELECT 1", assume_yes=False, is_tty=True)


def test_gate_forwards_hints_and_schema_to_cost_estimate() -> None:
    """The gate must call cost_estimate with the same hints/schema the
    caller intends to pass to execute_sql — otherwise the estimate is
    taken under a different tier/namespace context than the real query."""
    c = _client(
        {
            "verdict": "ok",
            "estimated_cost_cny": 0.5,
            "estimated_input_bytes": 100,
            "thresholds": {},
        }
    )
    enforce_cost_gate(
        c,
        "SELECT 1",
        hints={"odps.namespace.schema": "true"},
        schema="my_schema",
        assume_yes=False,
        is_tty=False,
    )
    c.cost_estimate.assert_called_once_with(
        "SELECT 1",
        hints={"odps.namespace.schema": "true"},
        schema="my_schema",
    )


def test_cost_estimate_failure_select_one_probe_proceeds() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("COST planner unavailable")

    result = enforce_cost_gate(c, "SELECT 1", assume_yes=True, is_tty=False)

    assert result is None
    c.cost_estimate.assert_called_once()


def test_cost_estimate_failure_limit_zero_probe_proceeds() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("COST planner unavailable")

    result = enforce_cost_gate(c, "SELECT * FROM orders LIMIT 0", assume_yes=True, is_tty=False)

    assert result is None
    c.cost_estimate.assert_called_once()


def test_cost_estimate_failure_information_schema_probe_proceeds() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("In subquery is not supported in COST SQL")

    result = enforce_cost_gate(
        c,
        "SELECT task_name FROM information_schema.tasks_history LIMIT 1",
        assume_yes=True,
        is_tty=False,
    )

    assert result is None
    c.cost_estimate.assert_called_once()


def test_cost_estimate_failure_regular_read_sql_blocked() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("COST planner unavailable")

    with pytest.raises(CostBlockedError, match="not an allowed low-risk probe") as exc:
        enforce_cost_gate(c, "SELECT * FROM huge_table", assume_yes=True, is_tty=False)

    assert "mcs sql cost" in exc.value.remediation


@pytest.mark.parametrize(
    "error",
    [
        PermissionDeniedError("missing select grant"),
        TableNotFoundError("table does not exist"),
    ],
)
def test_cost_estimate_resource_errors_propagate(error: Exception) -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = error

    with pytest.raises(type(error), match=str(error)):
        enforce_cost_gate(
            c,
            "SELECT * FROM table_that_will_fail LIMIT 1",
            assume_yes=True,
            is_tty=False,
        )


def test_cost_estimate_failure_multi_statement_limit_zero_blocked() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("COST planner unavailable")

    with pytest.raises(CostBlockedError, match="not an allowed low-risk probe"):
        enforce_cost_gate(
            c,
            "SELECT * FROM huge_table; SELECT * FROM orders LIMIT 0",
            assume_yes=True,
            is_tty=False,
        )


def test_cost_estimate_failure_mixed_information_schema_query_blocked() -> None:
    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("COST planner unavailable")

    with pytest.raises(CostBlockedError, match="not an allowed low-risk probe"):
        enforce_cost_gate(
            c,
            "SELECT * FROM huge_table WHERE EXISTS ("
            "SELECT 1 FROM information_schema.tasks_history LIMIT 1)",
            assume_yes=True,
            is_tty=False,
        )


def test_cost_estimate_failure_write_sql_blocked() -> None:
    """When cost_estimate fails on write SQL, the gate blocks even
    with assume_yes=True (fail-closed for non-read SQL)."""
    from maxcompute_semantic.mc_client.errors import CostBlockedError

    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("cost estimation failed")
    with pytest.raises(CostBlockedError, match="not an allowed low-risk probe") as exc:
        enforce_cost_gate(c, "INSERT INTO t VALUES (1)", assume_yes=True, is_tty=False)
    assert "--allow-write" not in exc.value.remediation
    assert "profile cost thresholds" in exc.value.remediation


def test_cost_estimate_failure_no_assume_yes_raises() -> None:
    """Cost estimate failure with assume_yes=False propagates as
    CostBlockedError for non-read SQL."""
    from maxcompute_semantic.mc_client.errors import CostBlockedError

    c = _client({"verdict": "ok", "estimated_cost_cny": 0, "estimated_input_bytes": 0})
    c.cost_estimate.side_effect = RuntimeError("In subquery is not supported in COST SQL")
    with pytest.raises(CostBlockedError, match="not an allowed low-risk probe"):
        enforce_cost_gate(c, "DROP TABLE t", assume_yes=False, is_tty=False)
