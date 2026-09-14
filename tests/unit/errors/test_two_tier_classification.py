# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for errors/mc.py:is_two_tier_error.

``is_two_tier_error`` is the single source of truth for "MaxCompute says
this project has no schema layer". The tier probe (``mc_client/tier.py``),
``MaxComputeClient.list_schemas``, and the profile wizard's schema picker
all route through it, so the predicate's two failure directions each cost
something real:

- too narrow → the probe aborts on a working 2-level project and every
  cold-path verb (sql / build / meta / doctor) fails before it can
  even try;
- too broad → the CLI emits table references in the wrong form for the
  project and every query fails at parse time.

The server wording varies with which metadata path pyodps took, hence the
multi-signature table below. Exceptions are built through pyodps's real
``parse_instance_error`` wherever possible, so these tests pin the actual
production shape (including which *class* pyodps picks) rather than a
hand-rolled approximation.
"""

from __future__ import annotations

import pytest

from maxcompute_semantic.errors import McsError, is_two_tier_error, map_pyodps_exception

# What pyodps's REST /schemas fallback produces when it runs
# `SHOW SCHEMAS IN <project>` against a flat-namespace project.
_SHOW_SCHEMAS_DDL_FAILURE = (
    "ODPS-0110061: InstanceId: 20260101000000000aaaaaa000001\n"
    "ODPS-0110061: Failed to run ddltask - "
    "odps_metadata/ddlengine/ddl/sql_ddl_action.cpp(4102): ExceptionBase: "
    "Invalid database operations on two-tier model\n"
)

# The historical wording, delivered on an InternalServerError.
_LEGACY_NOT_THREE_TIER = "List schemas failed: Project acme is not 3-tier model project."


def _parse(raw: str) -> Exception:
    from odps import errors as odps_errors

    return odps_errors.parse_instance_error(raw)


# ─── recognized two-tier wordings ───


@pytest.mark.parametrize(
    "raw",
    [
        _SHOW_SCHEMAS_DDL_FAILURE,
        _LEGACY_NOT_THREE_TIER,
        "Project acme is not 3 tier model project.",
        "Project acme is not a 3-tier model project.",
        "Database acme runs on the 2-tier model; schemas are not available.",
    ],
)
def test_recognizes_two_tier_wordings(raw: str) -> None:
    assert is_two_tier_error(raw) is True
    assert is_two_tier_error(_parse(raw)) is True


def test_ddl_fallback_arrives_as_base_odps_error() -> None:
    """The shape that made the probe abort: pyodps has no class for
    ODPS-0110061, so it raises the base ``ODPSError``, which
    ``except InternalServerError`` never matched."""
    from odps import errors as odps_errors

    exc = _parse(_SHOW_SCHEMAS_DDL_FAILURE)
    assert type(exc) is odps_errors.ODPSError
    assert not isinstance(exc, odps_errors.InternalServerError)
    assert exc.code == "ODPS-0110061"


def test_recognized_through_a_mapped_mcs_error() -> None:
    """``_source_picker`` sees the mapped envelope, not the pyodps error.

    ``map_pyodps_exception`` routes the unrecognized code to
    ``UnknownError`` but carries the raw message verbatim, so the
    classifier must still recognize it from ``McsError.message``.
    """
    mapped = map_pyodps_exception(_parse(_SHOW_SCHEMAS_DDL_FAILURE))
    assert isinstance(mapped, McsError)
    assert str(mapped.code) == "Unknown"
    assert is_two_tier_error(mapped) is True


# ─── must NOT classify as two-tier ───


@pytest.mark.parametrize(
    "raw",
    [
        # Same ODPS code, unrelated DDL failure — the reason the code
        # alone is not a two-tier signal.
        (
            "ODPS-0110061: InstanceId: 20260101000000000aaaaaa000002\n"
            "ODPS-0110061: Failed to run ddltask - column count mismatch\n"
        ),
        "ODPS-0130131: Table not found - acme.orders",
        "NoPermission: no Describe privilege on acme",
        "",
    ],
)
def test_does_not_over_classify(raw: str) -> None:
    assert is_two_tier_error(raw) is False


def test_empty_message_exception_is_not_two_tier() -> None:
    from odps import errors as odps_errors

    assert is_two_tier_error(odps_errors.ODPSError("")) is False
