"""MaxCompute-side error classes plus :func:`map_pyodps_exception`.

Holds the bulk of the historical ``mc_client/errors.py`` content. Auth
classes (``AuthFailedError``, ``IdentityNotAuthorizedError``) moved into
:mod:`maxcompute_semantic.errors.auth` because they're auth-axis, even
though :func:`map_pyodps_exception` raises them — split by domain, not
by who-raises-them.
"""

from __future__ import annotations

from maxcompute_semantic.errors.auth import (
    AuthFailedError,
    IdentityNotAuthorizedError,
)
from maxcompute_semantic.errors.base import ErrorCode, McsError


class ProjectNotFoundError(McsError):
    code = ErrorCode.PROJECT_NOT_FOUND
    exit_code = 5


class EndpointUnreachableError(McsError):
    code = ErrorCode.ENDPOINT_UNREACHABLE
    exit_code = 1


class TableNotFoundError(McsError):
    code = ErrorCode.TABLE_NOT_FOUND
    exit_code = 5


class PackageNotBuiltError(McsError):
    """``mcs show`` / sibling read verbs ran against a profile that has
    no semantic package on disk yet (no ``_overview.md`` / no
    ``package.db``). Distinct from :class:`TableNotFoundError` (package
    exists but the named table is absent): here the build step itself
    has not run for this profile.
    """

    code = ErrorCode.PACKAGE_NOT_BUILT
    exit_code = 5


class AmbiguousTableError(McsError):
    """``mcs show --table T`` received a bare name that resolves to more
    than one source in a multi-source profile. Exit code 2 (usage) so
    callers / smoke tests can distinguish "you gave me an ambiguous
    name, retry with FQN" from a real resource miss.
    """

    code = ErrorCode.AMBIGUOUS_TABLE
    exit_code = 2


class SchemaNotFoundError(McsError):
    code = ErrorCode.SCHEMA_NOT_FOUND
    exit_code = 5


class SchemaRequiredError(McsError):
    """Caller targeted a 3-level project without naming a schema.

    Distinct from :class:`SchemaNotFoundError` (a real schema name was
    supplied but doesn't exist in the project): here no schema name was
    supplied at all and the active profile didn't carry one (either it
    has multiple sources so the auto-pick would be ambiguous, or the
    caller is on the env-var anonymous fallback with no profile at all).

    Exit code 2 — usage error, not a server-side miss; the caller can
    retry by passing ``--schema NAME`` or by binding a single-source
    profile in the cwd with ``mcs link bind <NAME>``.
    """

    code = ErrorCode.SCHEMA_REQUIRED
    exit_code = 2


class InstanceNotFoundError(McsError):
    code = ErrorCode.INSTANCE_NOT_FOUND
    exit_code = 5


class PermissionDeniedError(McsError):
    """Any MaxCompute permission denial — table SELECT ACL, column-level
    LabelSecurity, meta Describe/List, function-namespace grant,
    information_schema tenant/project access.

    Earlier revisions split these into six subclasses
    (``PermissionDeniedTableError`` / ``Column`` / ``Meta`` / ``Function``
    / ``InfoSchemaTenant`` / ``InfoSchemaProject``) and ran ODPS
    error-message keyword classification to pick the right one. That
    classifier carried two costs the agent didn't get value from: it
    silently mis-bucketed messages we hadn't seen before, and every new
    ODPS message format (a new privilege name, a re-worded ``Deny as
    default`` variant) needed a code change to land in the right bucket.
    Collapsing to a single ``PermissionDenied`` lets the raw pyodps
    message pass through verbatim — the message itself names the
    privilege and object, which is what the agent and the user actually
    need to remediate.
    """

    code = ErrorCode.PERMISSION_DENIED
    exit_code = 5


class SyntaxErrorMcs(McsError):
    code = ErrorCode.SYNTAX_ERROR
    exit_code = 1


class TimeoutError(McsError):
    code = ErrorCode.TIMEOUT
    exit_code = 1


class RateLimitError(McsError):
    code = ErrorCode.RATE_LIMIT
    exit_code = 1


class UnknownError(McsError):
    code = ErrorCode.UNKNOWN
    exit_code = 1


class CostBlockedError(McsError):
    """The profile's ``cost_thresholds.blocked_cny`` ceiling was reached.
    Raised by the execute-time gate before the SQL is submitted, so the
    job never costs anything. Exit code 2 (usage) so smoke tests and
    pipelines can distinguish a refused-by-policy run from a real
    runtime error.
    """

    code = ErrorCode.COST_BLOCKED
    exit_code = 2


class CostConfirmRequiredError(McsError):
    """The cost estimate exceeded ``confirm_cny`` but we are in a
    non-interactive context (no TTY) and the caller did not pass
    ``assume_yes=True`` / ``--yes``. Raised by the execute-time gate
    so the agent or CI surface sees a classified refusal rather than
    a hung prompt. Exit code 2 (usage) — pass ``--yes`` or raise the
    threshold to proceed.
    """

    code = ErrorCode.COST_CONFIRM_REQUIRED
    exit_code = 2


class WriteOpRejectedError(McsError):
    """``mcs sql execute`` refused to submit a DML/DDL write — or a
    statement sqlglot cannot classify as a known read shape — because
    write intent was not explicit. Raised by the shared client-layer
    guard by default; managed write paths pass ``allow_write=True`` and,
    when appropriate, use their own cost-gate policy. Exit code 2
    (usage / refused-by-policy), mirroring :class:`CostBlockedError`.
    """

    code = ErrorCode.WRITE_OP_REJECTED
    exit_code = 2


def map_pyodps_exception(
    exc: Exception, *, sql: str | None = None, source_key: str | None = None
) -> McsError:
    """Map a pyodps ODPSError into the appropriate McsError subclass.

    Single-layer classification: structured ``exc.code`` (from pyodps
    error attributes) routes into a typed subclass. Anything without a
    recognized code falls through to :class:`UnknownError` carrying the
    raw pyodps message verbatim — the message itself names the privilege
    / object / SQL fragment, which is what the user and agent actually
    need to remediate.

    The classifier previously had a second layer that ran
    ``msg.lower()`` substring tests (``"access denied"``,
    ``"table not found"``, ``"doesn't exist in the project"``, …) when
    ``exc.code`` was empty or ambiguous. That layer was removed in
    0.5.0a45: pyodps server-side wording is not stable enough to bucket
    on — the same condition surfaced with different wording across
    cache states, and the live-matrix test arms flipped between
    PermissionDeniedError and IdentityNotAuthorizedError for identical
    ACL state. Routing on the structured code alone is more honest;
    the message carries the rest.

    Practical consequence: when pyodps gives no ``code`` attribute
    (local CLI exceptions, the rare server failure with no error code,
    network exceptions raised before ODPS responds), the result is an
    :class:`UnknownError` rather than a typed subclass. Callers that
    relied on substring-driven typing — notably the ``build/phases.py``
    per-table soft-failure path — now only get soft failure when pyodps
    actually emitted the structured error code (`NoSuchObject` /
    `NoPermission` / `AccessDenied` / `ODPS-0130013`). In practice those
    codes are emitted reliably for the conditions the soft-failure path
    cares about.

    When *source_key* is non-None and the mapped exception is a
    :class:`TableNotFoundError` or :class:`PermissionDeniedError`,
    ``[source={source_key}] `` is prepended to the message.

    Non-ODPS exceptions (raised by local CLI code: YAML parsers, the
    annotate batch validators, etc.) fall through to a generic
    :class:`UnknownError` envelope WITHOUT the "see logview URL"
    remediation — that hint only makes sense for failures the server
    already produced a logview for. The top-level CLI handler calls
    this on every uncaught exception, so the local-CLI fallback keeps
    local errors from leaking the MC-only remediation.
    """
    msg = str(exc)
    code = getattr(exc, "code", "") or ""
    if code in {
        "InvalidAccessKeyId",
        "AccessKeyIdNotFound",
        "SignatureDoesNotMatch",
        "InvalidSecurityToken",
    }:
        return AuthFailedError(
            msg, remediation="re-run `ncs auth login` or verify AK env vars", sql=sql
        )
    if code in {"IdentityNotAuthorized"}:
        return IdentityNotAuthorizedError(
            msg, remediation="check ODPS authorization or use a different account", sql=sql
        )
    if code == "NoSuchProject":
        return ProjectNotFoundError(msg, remediation="verify project name and endpoint", sql=sql)
    if code == "NoSuchObject":
        low = msg.lower()
        if "project not found" in low:
            return ProjectNotFoundError(
                msg, remediation="verify project name and endpoint", sql=sql
            )
    if code in {"NoSuchSchema"}:
        return SchemaNotFoundError(
            msg,
            remediation="verify schema name; use `mcs meta list-tables` to discover schemas",
            sql=sql,
        )
    if code in {"InstanceNotFound"}:
        return InstanceNotFoundError(
            msg, remediation="verify instance ID or check job list", sql=sql
        )
    if code in {"NoSuchObject", "TableNotFound", "NoSuchTable", "ODPS-0130131"}:
        return _maybe_attr_source(
            TableNotFoundError(
                msg,
                remediation=(
                    "verify table name with `mcs meta list-tables`; "
                    "use `mcs meta describe-table <T>` to confirm schema"
                ),
                sql=sql,
            ),
            source_key,
        )
    if code in {"ConnectionError", "ConnectTimeout", "ConnectionRefused"}:
        return EndpointUnreachableError(
            msg, remediation="check MAXCOMPUTE_ENDPOINT, network, and firewall settings", sql=sql
        )
    if code in {"NoPermission", "AccessDenied", "ODPS-0130013"}:
        if code == "ODPS-0130013":
            low = msg.lower()
            if "project not found" in low:
                return ProjectNotFoundError(
                    msg, remediation="verify project name and endpoint", sql=sql
                )
            if "table not found" in low:
                return _maybe_attr_source(TableNotFoundError(msg, sql=sql), source_key)
        return _permission_denied(msg, sql=sql, source_key=source_key)
    if code in {"OdpsTaskError", "SyntaxError"} and "parse" in msg.lower():
        return SyntaxErrorMcs(
            msg, remediation="check SQL syntax; logview has detailed parse error", sql=sql
        )
    if code in {"ServiceUnavailable", "Throttling"}:
        return RateLimitError(msg, remediation="retry with exponential backoff", sql=sql)
    try:
        from odps import errors as _odps_errors  # type: ignore[import-untyped]

        _is_odps = isinstance(exc, _odps_errors.ODPSError)
    except Exception:
        _is_odps = False
    if _is_odps:
        return UnknownError(msg, remediation="see logview URL for raw MaxCompute error", sql=sql)
    return UnknownError(
        msg,
        remediation=(
            "local CLI error (not a MaxCompute server error); "
            "re-run with --debug for a Python traceback"
        ),
        sql=sql,
    )


_SOURCE_ATTRIBUTABLE = (
    TableNotFoundError,
    PermissionDeniedError,
)


def _maybe_attr_source(error: McsError, source_key: str | None) -> McsError:
    """Prepend ``[source={source_key}] `` to *error*'s message when applicable."""
    if source_key and isinstance(error, _SOURCE_ATTRIBUTABLE):
        error.message = f"[source={source_key}] {error.message}"
    return error


def _permission_denied(
    msg: str, *, sql: str | None = None, source_key: str | None = None
) -> McsError:
    """Wrap a permission-denied ODPS message in a :class:`PermissionDeniedError`.

    Earlier revisions disambiguated ``"User doesn't exist in the
    project"`` / ``"You don't exist in project"`` wording into a separate
    :class:`IdentityNotAuthorizedError` (exit 4, auth-axis) because that
    condition is structurally different from a missing object-level
    grant (exit 5). Removed in 0.5.0a45: MC emits both wordings for the
    same underlying ACL state depending on cache warmth, which made the
    classifier flake between the two exception types on identical
    inputs. Routing all permission errors through this one class keeps
    the dispatch stable; the raw message still names the principal and
    object for the user / agent to read.
    """
    return _maybe_attr_source(
        PermissionDeniedError(
            msg,
            sql=sql,
        ),
        source_key,
    )
