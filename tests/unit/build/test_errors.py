"""Tests for build/errors.py — build-specific error classes."""

from maxcompute_semantic.build.errors import (
    BuildPhaseError,
    HistoryMiningError,
    SamplingFailedError,
    UdfDiscoveryError,
)


def test_build_phase_error_code() -> None:
    assert BuildPhaseError.code == "BuildPhase"
    assert BuildPhaseError.exit_code == 1


def test_sampling_failed_error_inherits_build_phase() -> None:
    e = SamplingFailedError("sampling failed for card_games", remediation="check table permissions")
    assert isinstance(e, BuildPhaseError)
    assert e.code == "SamplingFailed"


def test_history_mining_error() -> None:
    e = HistoryMiningError("TASKS_HISTORY query failed", remediation="try --no-history")
    assert e.code == "HistoryMining"


def test_udf_discovery_error() -> None:
    e = UdfDiscoveryError("list_functions failed", remediation="skip UDFs with --no-udf")
    assert e.code == "UdfDiscovery"
