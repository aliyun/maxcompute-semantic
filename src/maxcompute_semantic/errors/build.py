# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Build-phase error classes — all inherit :class:`McsError`."""

from __future__ import annotations

from maxcompute_semantic.errors.base import ErrorCode, McsError


class BuildPhaseError(McsError):
    """Base for build-phase failures."""

    code = ErrorCode.BUILD_PHASE
    exit_code = 1


class SamplingFailedError(BuildPhaseError):
    """Column sampling query failed for a table."""

    code = ErrorCode.SAMPLING_FAILED
    exit_code = 1


class HistoryMiningError(BuildPhaseError):
    """TASKS_HISTORY query failed."""

    code = ErrorCode.HISTORY_MINING
    exit_code = 1


class UdfDiscoveryError(BuildPhaseError):
    """list_functions() failed."""

    code = ErrorCode.UDF_DISCOVERY
    exit_code = 1


class RebuildRequiredError(McsError):
    """Opened a PackageDB whose ``PRAGMA user_version`` doesn't match
    the version this code knows how to read. The on-disk format
    changed in 0.4.0a4 (per-source-keyed ``tables`` / ``joins``); old
    packages can't be migrated in place. Remediation: ``mcs build``
    rebuilds from scratch.
    """

    code = ErrorCode.REBUILD_REQUIRED
    exit_code = 1


class MetricExistsError(McsError):
    """``mcs metric add`` tried to insert a metric whose name is already
    taken in this profile. The metric namespace is profile-global per
    ADR-0002.
    """

    code = ErrorCode.METRIC_EXISTS
    exit_code = 4

    def __init__(self, name: str) -> None:
        super().__init__(
            f"metric '{name}' already exists in this profile",
            remediation=(
                f"choose a different name, or run `mcs metric edit {name} "
                f"--expression ...` to update the existing metric"
            ),
        )


class MetricNotFoundError(McsError):
    """``mcs metric show / edit / remove`` referenced a name that
    doesn't exist in the profile.
    """

    code = ErrorCode.METRIC_NOT_FOUND
    exit_code = 5

    def __init__(self, name: str) -> None:
        super().__init__(
            f"metric '{name}' not found in this profile",
            remediation="run `mcs metric list` to see the available metrics",
        )


class MetricValidationError(McsError):
    """``mcs metric add / edit`` was given a SQL fragment that
    sqlglot could not parse. Exit code 2 mirrors ``AnnotateValidationError``
    so the agent's retry-on-validation-failure path treats both surfaces
    identically. Distinct ``code`` (``METRIC_VALIDATION``) keeps the wire
    contract clean — annotate failures don't leak into the metric verb's
    error stream.
    """

    code = ErrorCode.METRIC_VALIDATION
    exit_code = 2
