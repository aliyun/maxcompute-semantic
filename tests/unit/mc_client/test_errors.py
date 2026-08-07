# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client/errors.py -- base class + classified subclasses."""

from __future__ import annotations

import pytest

from maxcompute_semantic.mc_client.errors import (
    AuthFailedError,
    EndpointUnreachableError,
    IdentityNotAuthorizedError,
    McsError,
    PermissionDeniedError,
    ProjectNotFoundError,
    RateLimitError,
    SchemaNotFoundError,
    SyntaxErrorMcs,
    TableNotFoundError,
    UnknownError,
    WriteOpRejectedError,
)
from maxcompute_semantic.mc_client.errors import (
    TimeoutError as McsTimeoutError,
)


@pytest.mark.parametrize(
    "cls,code,exit_code",
    [
        (AuthFailedError, "AuthFailed", 4),
        (IdentityNotAuthorizedError, "IdentityNotAuthorized", 4),
        (ProjectNotFoundError, "ProjectNotFound", 5),
        (EndpointUnreachableError, "EndpointUnreachable", 1),
        (TableNotFoundError, "TableNotFound", 5),
        (SchemaNotFoundError, "SchemaNotFound", 5),
        (PermissionDeniedError, "PermissionDenied", 5),
        (SyntaxErrorMcs, "SyntaxError", 1),
        (McsTimeoutError, "Timeout", 1),
        (RateLimitError, "RateLimit", 1),
        (UnknownError, "Unknown", 1),
    ],
)
def test_subclass_has_correct_code_and_exit_code(cls, code, exit_code) -> None:
    assert cls.code == code
    assert cls.exit_code == exit_code
    assert issubclass(cls, McsError)


def test_cost_blocked_error_carries_code_and_exit_code() -> None:
    from maxcompute_semantic.mc_client.errors import CostBlockedError

    e = CostBlockedError(
        "exceeded",
        estimated_cost_cny=200.0,
        blocked_cny=100.0,
    )
    assert e.code == "CostBlocked"
    assert e.exit_code == 2
    assert issubclass(CostBlockedError, McsError)
    assert e.context["estimated_cost_cny"] == 200.0
    assert e.context["blocked_cny"] == 100.0


def test_cost_confirm_required_error_carries_code_and_exit_code() -> None:
    from maxcompute_semantic.mc_client.errors import CostConfirmRequiredError

    e = CostConfirmRequiredError(
        "needs confirm",
        estimated_cost_cny=15.0,
        confirm_cny=10.0,
    )
    assert e.code == "CostConfirmRequired"
    assert e.exit_code == 2
    assert issubclass(CostConfirmRequiredError, McsError)
    assert e.context["estimated_cost_cny"] == 15.0
    assert e.context["confirm_cny"] == 10.0


def test_constructor_accepts_message_and_remediation() -> None:
    err = AuthFailedError("auth fail", remediation="run ncs auth login")
    assert "auth fail" in str(err)
    assert err.remediation == "run ncs auth login"


def test_constructor_default_remediation_is_empty_string() -> None:
    err = UnknownError("something")
    assert err.remediation == ""


def test_constructor_accepts_context_kwargs() -> None:
    err = TableNotFoundError("nope", sql="SELECT * FROM x")
    assert err.context == {"sql": "SELECT * FROM x"}


def test_write_op_rejected_is_mcs_error() -> None:
    assert issubclass(WriteOpRejectedError, McsError)


def test_write_op_rejected_code_and_exit_code() -> None:
    # Mirrors CostBlockedError: exit code 2 (usage / refused-by-policy)
    # so CI / smoke can distinguish a guard refusal from a runtime
    # failure (exit 1).
    assert WriteOpRejectedError.code == "WriteOpRejected"
    assert WriteOpRejectedError.exit_code == 2


def test_write_op_rejected_carries_remediation_and_sql_context() -> None:
    exc = WriteOpRejectedError(
        "test message",
        remediation="pass --allow-write",
        sql="DROP TABLE t",
    )
    assert exc.message == "test message"
    assert exc.remediation == "pass --allow-write"
    assert exc.context == {"sql": "DROP TABLE t"}
