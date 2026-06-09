"""Tests for mc_client/errors.py — map_pyodps_exception.

After the 0.5.0a45 substring-stripping refactor the classifier routes
exclusively on the structured ``exc.code`` attribute pyodps attaches to
its ODPSError instances. The earlier two-layer scheme — Layer 1
structured-code, Layer 2 message-keyword fallback — was prone to
flipping classification when the same MC condition emitted different
wording across cache states (the live-matrix arms flapped between
PermissionDeniedError and IdentityNotAuthorizedError for identical
ACL state). Layer 2 was removed entirely; any exception that arrives
without a recognized ``exc.code`` now falls through to UnknownError
carrying the raw message verbatim.

Permission errors collapse into a single :class:`PermissionDeniedError`;
the earlier ``IdentityNotAuthorizedError`` substring disambiguation
inside the permission-denied path is gone for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from maxcompute_semantic.mc_client.errors import (
    AuthFailedError,
    EndpointUnreachableError,
    IdentityNotAuthorizedError,
    InstanceNotFoundError,
    PermissionDeniedError,
    ProjectNotFoundError,
    RateLimitError,
    SchemaNotFoundError,
    SyntaxErrorMcs,
    TableNotFoundError,
    UnknownError,
    map_pyodps_exception,
)

FIXTURES_DIR: Path = Path(__file__).parent.parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / "pyodps_errors" / f"{name}.json").read_text())


def _build_exc(fixture: dict) -> Exception:
    code = fixture["code"]
    message = fixture["message"]

    class _FakePyodpsExc(Exception):
        pass

    exc = _FakePyodpsExc(message)
    exc.code = code
    return exc


# ─── Fixture-based classification ───


@pytest.mark.parametrize(
    "fixture_name,expected_cls",
    [
        ("auth_failed_invalid_ak", AuthFailedError),
        ("auth_failed_expired_sts", AuthFailedError),
        ("auth_failed_access_key_id_not_found", AuthFailedError),
        ("identity_not_authorized", IdentityNotAuthorizedError),
        ("project_not_found", ProjectNotFoundError),
        ("project_not_found_nosuchobject", ProjectNotFoundError),
        ("endpoint_unreachable", EndpointUnreachableError),
        ("schema_not_found", SchemaNotFoundError),
        ("table_not_found", TableNotFoundError),
        ("no_permission_select_table", PermissionDeniedError),
        ("syntax_error", SyntaxErrorMcs),
        ("rate_limit", RateLimitError),
    ],
)
def test_fixture_maps_to_expected_class(fixture_name, expected_cls) -> None:
    fixture = _load_fixture(fixture_name)
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc)
    assert isinstance(result, expected_cls)


def test_fixture_maps_preserve_message() -> None:
    fixture = _load_fixture("table_not_found")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc)
    assert fixture["message"] in result.message


def test_fixture_maps_with_sql_context() -> None:
    fixture = _load_fixture("no_permission_select_table")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc, sql="SELECT * FROM t")
    assert result.context.get("sql") == "SELECT * FROM t"


# ─── Code-less exceptions fall through to UnknownError ───
#
# After 0.5.0a45 the substring-matching Layer 2 is gone. Any exception
# arriving with no recognized ``exc.code`` — including messages that
# previously *looked* like an auth / table-not-found / connection
# problem on substring inspection — folds into UnknownError carrying
# the raw message. Callers read the message; nothing types on it.


def test_codeless_exception_falls_through_to_unknown() -> None:
    """A bare Exception with no recognized code becomes UnknownError,
    even if the message wording resembles a typed error category."""
    for message in (
        "InvalidAccessKeyId - something went wrong",
        "table not found - 'x.y'",
        "Project not found - 'nonexistent_project'",
        "schema not found - 'nonexistent_schema'",
        "connection refused: Failed to connect to endpoint",
        "something weird happened",
    ):
        exc = Exception(message)
        exc.code = ""
        result = map_pyodps_exception(exc)
        assert isinstance(result, UnknownError), f"expected UnknownError for {message!r}"


def test_fallback_non_odps_uses_local_cli_remediation() -> None:
    """Non-ODPS exceptions (raised by local CLI paths: YAML parsers,
    annotate batch validators, ad-hoc Python errors that leaked past a
    command's own handlers) must not surface the MC-specific
    ``see logview URL`` remediation — that hint is actively misleading
    for problems the server never produced a logview for."""
    exc = AttributeError("'list' object has no attribute 'items'")
    result = map_pyodps_exception(exc)
    assert isinstance(result, UnknownError)
    assert "logview" not in (result.remediation or "").lower()
    assert "local CLI error" in (result.remediation or "")


def test_fallback_odps_unknown_keeps_logview_remediation() -> None:
    """Actual pyodps ODPSError that doesn't match any classification
    arm still gets the logview-URL remediation, because the server
    very likely did produce a logview for it."""
    try:
        from odps.errors import ODPSError  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("pyodps not installed")
    exc = ODPSError("some unrecognized server failure")
    result = map_pyodps_exception(exc)
    assert isinstance(result, UnknownError)
    assert "logview" in (result.remediation or "").lower()


# ─── PermissionDenied: collapsed-class assertions ───


def test_no_permission_collapses_to_permission_denied() -> None:
    """Any 'no permission' / 'access denied' message → PermissionDeniedError.
    The raw pyodps message passes through verbatim; the user / agent
    reads the privilege and object directly from it."""
    fixture = _load_fixture("no_permission_select_table")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)
    assert result.code == "PermissionDenied"
    assert result.exit_code == 5
    assert fixture["message"] in result.message


def test_access_denied_routes_via_structured_code() -> None:
    """An ``AccessDenied`` structured code maps to PermissionDeniedError
    regardless of message wording. The earlier substring-driven path
    (``"access denied"`` keyword in the message) is gone — without a
    recognized ``exc.code`` the result is UnknownError now."""
    exc = Exception("Access Denied - SELECT on Table 'my_proj.restricted_table'")
    exc.code = "AccessDenied"
    result = map_pyodps_exception(exc, sql="SELECT * FROM restricted_table")
    assert isinstance(result, PermissionDeniedError)


def test_permission_denied_carries_raw_message_no_invented_remediation() -> None:
    """The raw pyodps message names the privilege + object; the
    classifier doesn't invent a remediation hint on top — the message
    itself is the remediation guide."""
    exc = Exception("ACL check failed: no SELECT privilege on table 'my_proj.users'")
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)
    assert result.remediation == ""


def test_info_schema_message_collapses_to_permission_denied() -> None:
    """information_schema deny is no longer a distinct class — the raw
    message already names ``information_schema.*``, so the collapsed
    PermissionDeniedError carries it verbatim."""
    exc = Exception("no permission to query information_schema.tables")
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)
    assert "information_schema" in result.message


def test_label_security_message_collapses_to_permission_denied() -> None:
    """LabelSecurity column deny is no longer a distinct class — the
    raw message already names the column, label, and table."""
    exc = Exception(
        "CheckLabelSecurity failed: Your LABEL 0 cannot access column "
        "'sensitive_c' of table 'test_label_demo' with LABEL 3"
    )
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)
    assert "sensitive_c" in result.message


def test_create_function_message_collapses_to_permission_denied() -> None:
    """CreateFunction deny is no longer a distinct class — the raw
    message already names the privilege and target function."""
    exc = Exception("ACL check failed: no permission to create function 'my_udf'")
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)


# ─── ODPS-0130013 multi-purpose error code ───


def test_odps_0130013_project_not_found() -> None:
    exc = Exception("Project not found - 'nonexistent'")
    exc.code = "ODPS-0130013"
    result = map_pyodps_exception(exc)
    assert isinstance(result, ProjectNotFoundError)


def test_odps_0130013_table_not_found() -> None:
    exc = Exception("Table not found - 'x.y'")
    exc.code = "ODPS-0130013"
    result = map_pyodps_exception(exc)
    assert isinstance(result, TableNotFoundError)


def test_odps_0130013_no_permission() -> None:
    exc = Exception("No permission to select on table 'my_proj.users'")
    exc.code = "ODPS-0130013"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)


# ─── InstanceNotFound coverage ───


def test_code_instance_not_found() -> None:
    exc = Exception("Instance 'abc123' not found")
    exc.code = "InstanceNotFound"
    result = map_pyodps_exception(exc)
    assert isinstance(result, InstanceNotFoundError)


def test_codeless_instance_not_found_falls_through_to_unknown() -> None:
    """Without a structured ``InstanceNotFound`` code the result is
    UnknownError, even when the message clearly names the condition."""
    exc = Exception("instance not found - 'abc123'")
    exc.code = ""
    result = map_pyodps_exception(exc)
    assert isinstance(result, UnknownError)


# ─── AccessKeyIdNotFound code classification ───


def test_code_access_key_id_not_found() -> None:
    exc = Exception("Specified access key is not found.")
    exc.code = "AccessKeyIdNotFound"
    result = map_pyodps_exception(exc)
    assert isinstance(result, AuthFailedError)


# ─── NoSuchObject code message-disambiguation ───


def test_code_nosuchobject_project_not_found() -> None:
    exc = Exception("Project not found - 'nonexistent_project_xyz'")
    exc.code = "NoSuchObject"
    result = map_pyodps_exception(exc)
    assert isinstance(result, ProjectNotFoundError)


def test_code_nosuchobject_table_not_found() -> None:
    exc = Exception("Object not found - 'some_table'")
    exc.code = "NoSuchObject"
    result = map_pyodps_exception(exc)
    assert isinstance(result, TableNotFoundError)


# ─── NoSuchTable / ODPS-0130131 (parser-side table-resolution failure) ───
#
# pyodps's ``parse_instance_error`` constructs ``NoSuchTable`` instances
# with ``exc.code = "ODPS-0130131"`` (the wire-level ODPS error code,
# not the class name) for the parser's "table cannot be resolved" path.
# This is a distinct code from the multi-purpose ``ODPS-0130013`` that
# pyodps uses for the meta-REST table-not-found / no-permission path.
# Both shapes need to land in TableNotFoundError so the build-phase
# soft-failure path and the live-matrix parser arm see the typed result.


def test_code_nosuchtable_via_class_name() -> None:
    """Some pyodps surfaces stamp ``.code = "NoSuchTable"`` (the class
    name)."""
    exc = Exception("ODPS-0130131:[1,15] Table not found - table p.s.`t` cannot be resolved")
    exc.code = "NoSuchTable"
    result = map_pyodps_exception(exc)
    assert isinstance(result, TableNotFoundError)


def test_code_odps_0130131_routes_to_table_not_found() -> None:
    """The parser-side resolution failure uses ``.code = "ODPS-0130131"``
    (the wire-level ODPS code, what ``parse_instance_error`` writes onto
    NoSuchTable). Must route to TableNotFoundError — the previous
    classifier missed it and folded the live-matrix parser arm into
    UnknownError."""
    exc = Exception("ODPS-0130131:[1,15] Table not found - table p.s.`t` cannot be resolved")
    exc.code = "ODPS-0130131"
    result = map_pyodps_exception(exc)
    assert isinstance(result, TableNotFoundError)


# ─── "Doesn't exist in the project" no longer disambiguated ───
#
# Earlier this wording (which MC sometimes returns when the principal
# isn't a member of the project, depending on cache state) was routed
# to IdentityNotAuthorizedError via substring. That branch is gone in
# 0.5.0a45 — MC emits this wording inconsistently for the same
# underlying ACL state, so the classification flapped on identical
# inputs. PermissionDeniedError is the stable answer; the raw message
# still names the principal for the user / agent to read.
#
# IdentityNotAuthorizedError is still reachable via the
# ``exc.code == "IdentityNotAuthorized"`` structured branch.


def test_doesnt_exist_in_project_now_permission_denied() -> None:
    exc = Exception(
        "ODPS-0130013: Authorization Failed - "
        "User doesn't exist in the project: RAM$ais-netpila:netpila-dev"
    )
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)


def test_dont_exist_in_project_now_permission_denied() -> None:
    exc = Exception("You don't exist in project 'test_project'")
    exc.code = "NoPermission"
    result = map_pyodps_exception(exc)
    assert isinstance(result, PermissionDeniedError)


def test_identity_not_authorized_still_reachable_via_structured_code() -> None:
    """The IdentityNotAuthorizedError class isn't dead — pyodps emits
    ``exc.code == "IdentityNotAuthorized"`` directly for the unmistakable
    case where the principal has zero authorization in the project."""
    exc = Exception("RAM$x:y is not authorized to do action 'odps:Read' on resource 'p'")
    exc.code = "IdentityNotAuthorized"
    result = map_pyodps_exception(exc)
    assert isinstance(result, IdentityNotAuthorizedError)


# ─── source_key attribution tests ───


def test_source_key_prepended_for_table_not_found() -> None:
    fixture = _load_fixture("table_not_found")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc, source_key="proj__s")
    assert isinstance(result, TableNotFoundError)
    assert result.message.startswith("[source=proj__s] ")


def test_source_key_prepended_for_permission_denied() -> None:
    fixture = _load_fixture("no_permission_select_table")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc, source_key="p__s")
    assert isinstance(result, PermissionDeniedError)
    assert result.message.startswith("[source=p__s] ")


def test_source_key_not_prepended_when_none() -> None:
    fixture = _load_fixture("table_not_found")
    exc = _build_exc(fixture)
    result = map_pyodps_exception(exc, source_key=None)
    assert isinstance(result, TableNotFoundError)
    assert not result.message.startswith("[source=")


def test_source_key_not_prepended_for_unrelated_error() -> None:
    exc = Exception("something weird happened")
    exc.code = ""
    result = map_pyodps_exception(exc, source_key="p__s")
    assert isinstance(result, UnknownError)
    assert not result.message.startswith("[source=")
