"""Tests for mc_client/envelope.py."""

from __future__ import annotations

from maxcompute_semantic.mc_client.envelope import Envelope, ErrorDetail
from maxcompute_semantic.mc_client.errors import AuthFailedError


def test_success_envelope_to_dict() -> None:
    env = Envelope.success({"rows": [[1]], "row_count": 1})
    assert env.to_dict() == {
        "status": "success",
        "data": {"rows": [[1]], "row_count": 1},
    }


def test_error_envelope_from_mcs_error() -> None:
    err = AuthFailedError("auth failed", remediation="run ncs auth login")
    env = Envelope.from_error(err)
    d = env.to_dict()
    assert d["status"] == "error"
    assert d["error"]["code"] == "AuthFailed"
    assert d["error"]["message"] == "auth failed"
    assert d["error"]["remediation"] == "run ncs auth login"


def test_error_envelope_preserves_context() -> None:
    err = AuthFailedError("x", remediation="y", sql="SELECT 1")
    env = Envelope.from_error(err)
    assert env.to_dict()["error"]["context"] == {"sql": "SELECT 1"}


def test_error_detail_default_context_is_empty() -> None:
    detail = ErrorDetail(code="X", message="m", remediation="r")
    assert detail.context == {}
