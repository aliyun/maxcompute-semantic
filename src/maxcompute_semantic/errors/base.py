# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs error base class, wire-code enum, envelope serializer, and the
``@maps_pyodps_errors`` decorator.

This is the canonical home for ``McsError`` after the consolidation; the
old ``mc_client/errors.py`` location keeps a deprecation shim for one
release cycle. See
``docs/superpowers/specs/2026-05-25-mcs-errors-consolidation-design.md``
for the full plan.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from enum import Enum
from functools import wraps
from typing import Any, ClassVar, ParamSpec, TypeVar


class ErrorCode(str, Enum):
    """Stable wire codes for every :class:`McsError` subclass.

    The string ``value`` of each entry is the contract — it appears in
    the JSON envelope that the eval harness, CI summaries, and agent
    transcripts parse. Don't rename a value without coordinating a
    migration. Adding a new value is fine.
    """

    UNKNOWN = "Unknown"

    # Auth / identity (typically exit 4)
    AUTH_FAILED = "AuthFailed"
    AUTH_BINARY_MISSING = "AuthBinaryMissing"
    IDENTITY_NOT_AUTHORIZED = "IdentityNotAuthorized"
    WHO_AM_I_FAILED = "WhoAmIFailed"
    NO_BOUND_PROFILE = "NoBoundProfile"

    # Profile / config (typically exit 3)
    CONFIG_ENV_NOT_SET = "ConfigEnvNotSet"
    CONFIG_PERMISSION = "ConfigPermission"
    CONFIG_WRITE = "ConfigWrite"
    INCOMPATIBLE_PROFILE_VERSION = "IncompatibleProfileVersion"
    INVALID_PROFILE = "InvalidProfile"
    INVALID_PROFILE_FILE = "InvalidProfileFile"
    NO_PROFILES_CONFIGURED = "NoProfilesConfigured"
    PROFILE_NOT_FOUND = "ProfileNotFound"

    WORKING_DIRECTORY = "WorkingDirectory"

    # MaxCompute resource lookups (typically exit 5)
    PROJECT_NOT_FOUND = "ProjectNotFound"
    SCHEMA_NOT_FOUND = "SchemaNotFound"
    SCHEMA_REQUIRED = "SchemaRequired"
    TABLE_NOT_FOUND = "TableNotFound"
    AMBIGUOUS_TABLE = "AmbiguousTable"
    INSTANCE_NOT_FOUND = "InstanceNotFound"
    PACKAGE_NOT_BUILT = "PackageNotBuilt"
    PERMISSION_DENIED = "PermissionDenied"

    # Network / transport
    ENDPOINT_UNREACHABLE = "EndpointUnreachable"
    TIMEOUT = "Timeout"
    RATE_LIMIT = "RateLimit"

    # SQL / runtime
    SYNTAX_ERROR = "SyntaxError"

    # CLI gates (typically exit 2)
    COST_BLOCKED = "CostBlocked"
    COST_CONFIRM_REQUIRED = "CostConfirmRequired"
    WRITE_OP_REJECTED = "WriteOpRejected"

    # Annotate
    ANNOTATE_VALIDATION = "AnnotateValidation"
    ANNOTATE_NOT_FOUND = "AnnotateNotFound"

    # Memory — wire code normalized to PascalCase in this consolidation;
    # the old value was "MEMORY_NOT_FOUND".
    MEMORY_NOT_FOUND = "MemoryNotFound"

    # Metrics (top-level named expressions)
    METRIC_EXISTS = "MetricExists"
    METRIC_NOT_FOUND = "MetricNotFound"
    METRIC_VALIDATION = "MetricValidation"

    # SQL Review
    REVIEW_UNSUPPORTED = "MCS_REVIEW_UNSUPPORTED"

    # Proposals
    SEMANTIC_PROPOSAL = "SemanticProposalError"

    # Table resolution
    TABLE_RESOLUTION = "TableResolution"

    # Build
    BUILD_PHASE = "BuildPhase"
    SAMPLING_FAILED = "SamplingFailed"
    HISTORY_MINING = "HistoryMining"
    UDF_DISCOVERY = "UdfDiscovery"
    REBUILD_REQUIRED = "RebuildRequired"

    # Versioning
    LOCKED_BY_OTHER_PROCESS = "LockedByOtherProcess"
    GIT_NOT_AVAILABLE = "GitNotAvailable"
    PROFILE_READ_ONLY = "ProfileReadOnly"
    PACKAGE_SQL_CORRUPT = "PackageSqlCorrupt"

    def __str__(self) -> str:
        # Pre-consolidation, ``code`` was a plain string so f-strings and
        # ``str()`` rendered the wire value. Python 3.11 changed
        # ``(str, Enum)`` mixin's ``__str__`` to return the qualified
        # name (``ErrorCode.AUTH_FAILED``) instead of the value
        # (``AuthFailed``); this override restores the old contract that
        # callers like ``doctor.py`` and ``_auth_probe.py`` rely on.
        return self.value


class McsError(Exception):
    """Base class for all mcs classified errors.

    ``code`` / ``exit_code`` are ClassVars supplying defaults at the
    subclass level. The constructor also accepts ``code=`` / ``exit_code=``
    kwargs that, when non-None, shadow the ClassVar on the instance —
    useful only for codes too niche for a dedicated subclass. Prefer
    declaring a subclass when in doubt.
    """

    code: ClassVar[ErrorCode] = ErrorCode.UNKNOWN
    exit_code: ClassVar[int] = 1

    def __init__(
        self,
        message: str,
        *,
        remediation: str = "",
        code: ErrorCode | str | None = None,
        exit_code: int | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        if code is not None:
            if isinstance(code, ErrorCode):
                self.code = code  # type: ignore[misc]
            else:
                # Legacy string: try the enum, otherwise keep the literal
                # so callers passing ad-hoc codes (rare) still work.
                try:
                    self.code = ErrorCode(code)  # type: ignore[misc]
                except ValueError:
                    self.code = code  # type: ignore[assignment, misc]
        if exit_code is not None:
            self.exit_code = exit_code  # type: ignore[misc]
        self.context = context

    def to_envelope(self) -> dict[str, Any]:
        """Return the canonical JSON envelope for this error.

        Shape: ``{"status": "error", "error": {"code", "message",
        "remediation"?}}``. The cli-boundary handler is the dominant
        caller; tests that pin envelope shape should compare against
        the output of this method.
        """
        code_value = self.code.value if isinstance(self.code, ErrorCode) else str(self.code)
        envelope: dict[str, Any] = {
            "status": "error",
            "error": {"code": code_value, "message": self.message},
        }
        if self.remediation:
            envelope["error"]["remediation"] = self.remediation
        return envelope


P = ParamSpec("P")
R = TypeVar("R")


def maps_pyodps_errors(
    *,
    sql_arg: str | None = None,
    catch_all: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: any pyodps ``ODPSError`` raised inside the wrapped
    function is reclassified into an :class:`McsError` via
    :func:`map_pyodps_exception` and re-raised with the original as
    ``__cause__``.

    Replaces the copy-pasted six-line ``except odps_errors.ODPSError as
    e: raise map_pyodps_exception(e, sql=sql) from e`` blocks in
    ``mc_client/client.py``. Use this for the simple cases — call sites
    that pass ``source_key=self._source_key(project, schema)`` need
    locally-scoped values the decorator can't capture; those keep their
    explicit ``try/except``.

    Args:
        sql_arg: name of the parameter carrying SQL text. The value is
            forwarded to ``map_pyodps_exception(sql=...)`` so the
            envelope's context preserves it. Resolved by inspecting the
            wrapped function's signature, so positional or keyword
            forms both work.
        catch_all: if True, catch ``Exception`` rather than just
            ``ODPSError``. Use only for paths where pyodps wraps
            non-ODPS exceptions before raising (e.g. odps init that
            normalizes some auth/network failures into stdlib errors).
    """

    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters)
        except (TypeError, ValueError):
            params = []

        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                if not _should_catch(exc, catch_all=catch_all):
                    raise
                from maxcompute_semantic.errors.mc import map_pyodps_exception

                sql_value: str | None = None
                if sql_arg is not None:
                    raw = kwargs.get(sql_arg)
                    if raw is None and params and sql_arg in params:
                        idx = params.index(sql_arg)
                        if idx < len(args):
                            raw = args[idx]
                    if isinstance(raw, str):
                        sql_value = raw

                # _should_catch confirmed exc is an ordinary Exception
                # (not BaseException-only like KeyboardInterrupt).
                raise map_pyodps_exception(
                    exc,  # type: ignore[arg-type]
                    sql=sql_value,
                ) from exc

        return wrapper

    return deco


def _should_catch(exc: BaseException, *, catch_all: bool) -> bool:
    if isinstance(exc, McsError):
        # Already classified — don't double-wrap.
        return False
    if catch_all:
        return not isinstance(exc, (KeyboardInterrupt, SystemExit))
    try:
        from odps import errors as odps_errors  # type: ignore[import-untyped]
    except ImportError:
        return False
    return isinstance(exc, odps_errors.ODPSError)
