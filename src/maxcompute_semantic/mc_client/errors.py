"""DEPRECATED: import from :mod:`maxcompute_semantic.errors` instead.

This module is a thin re-export shim kept for one release cycle so
existing callers don't break during the errors-consolidation
migration. PR2 of the consolidation will delete it.
"""

from __future__ import annotations

# Re-export the McsError base + the most-used auth-side classes that
# historically lived in this module so legacy imports keep resolving.
from maxcompute_semantic.errors.auth import (  # noqa: F401
    AuthFailedError,
    IdentityNotAuthorizedError,
    NoBoundProfileError,
    WhoAmIFailedError,
)
from maxcompute_semantic.errors.base import (  # noqa: F401
    ErrorCode,
    McsError,
)
from maxcompute_semantic.errors.mc import (  # noqa: F401
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
