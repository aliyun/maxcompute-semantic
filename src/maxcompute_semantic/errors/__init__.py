"""Canonical mcs error hierarchy.

All public error classes plus :class:`ErrorCode`, the
``map_pyodps_exception`` classifier, and the ``maps_pyodps_errors``
decorator are re-exported here. Import from
``maxcompute_semantic.errors`` rather than the per-subpackage modules
unless you have a reason to be specific.

Subpackage-local ``errors.py`` files (``auth/errors.py``,
``mc_client/errors.py``, ``build/errors.py``, ``memory/errors.py``,
``versioning/errors.py``) are deprecation shims that re-export from
here for one release cycle.
"""

from __future__ import annotations

from maxcompute_semantic.errors.annotate import (
    AnnotateNotFoundError,
    AnnotateValidationError,
)
from maxcompute_semantic.errors.auth import (
    AuthBinaryMissingError,
    AuthFailedError,
    ConfigEnvNotSetError,
    ConfigPermissionError,
    ConfigWriteError,
    IdentityNotAuthorizedError,
    IncompatibleProfileVersionError,
    InvalidProfileError,
    InvalidProfileFileError,
    NoBoundProfileError,
    NoProfilesConfiguredError,
    ProfileNotFoundError,
    WhoAmIFailedError,
    WorkingDirectoryError,
)
from maxcompute_semantic.errors.base import (
    ErrorCode,
    McsError,
    maps_pyodps_errors,
)
from maxcompute_semantic.errors.build import (
    BuildPhaseError,
    HistoryMiningError,
    RebuildRequiredError,
    SamplingFailedError,
    UdfDiscoveryError,
)
from maxcompute_semantic.errors.mc import (
    AmbiguousTableError,
    CostBlockedError,
    CostConfirmRequiredError,
    EndpointUnreachableError,
    InstanceNotFoundError,
    PackageNotBuiltError,
    PermissionDeniedError,
    ProjectNotFoundError,
    RateLimitError,
    SchemaNotFoundError,
    SchemaRequiredError,
    SyntaxErrorMcs,
    TableNotFoundError,
    TimeoutError,
    UnknownError,
    WriteOpRejectedError,
    map_pyodps_exception,
)
from maxcompute_semantic.errors.memory import MemoryNotFoundError
from maxcompute_semantic.errors.versioning import (
    GitNotAvailable,
    LockedByOtherProcessError,
    PackageSqlCorrupt,
    ProfileReadOnly,
    StaleLockClearedWarning,
)

__all__ = [
    # base
    "ErrorCode",
    "McsError",
    "maps_pyodps_errors",
    "map_pyodps_exception",
    # auth
    "AuthBinaryMissingError",
    "AuthFailedError",
    "ConfigEnvNotSetError",
    "ConfigPermissionError",
    "ConfigWriteError",
    "IdentityNotAuthorizedError",
    "IncompatibleProfileVersionError",
    "InvalidProfileError",
    "InvalidProfileFileError",
    "NoBoundProfileError",
    "NoProfilesConfiguredError",
    "ProfileNotFoundError",
    "WhoAmIFailedError",
    "WorkingDirectoryError",
    # mc
    "AmbiguousTableError",
    "CostBlockedError",
    "CostConfirmRequiredError",
    "EndpointUnreachableError",
    "InstanceNotFoundError",
    "PackageNotBuiltError",
    "PermissionDeniedError",
    "ProjectNotFoundError",
    "RateLimitError",
    "SchemaNotFoundError",
    "SchemaRequiredError",
    "SyntaxErrorMcs",
    "TableNotFoundError",
    "TimeoutError",
    "UnknownError",
    "WriteOpRejectedError",
    # build
    "BuildPhaseError",
    "HistoryMiningError",
    "RebuildRequiredError",
    "SamplingFailedError",
    "UdfDiscoveryError",
    # annotate
    "AnnotateNotFoundError",
    "AnnotateValidationError",
    # memory
    "MemoryNotFoundError",
    # versioning
    "GitNotAvailable",
    "LockedByOtherProcessError",
    "PackageSqlCorrupt",
    "ProfileReadOnly",
    "StaleLockClearedWarning",
]
