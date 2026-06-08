# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Auth / profile / config error subclasses of :class:`McsError`."""

from __future__ import annotations

from maxcompute_semantic.errors.base import ErrorCode, McsError


class AuthBinaryMissingError(McsError):
    """process auth binary not found on PATH."""

    code = ErrorCode.AUTH_BINARY_MISSING
    exit_code = 4


class AuthFailedError(McsError):
    """Auth command ran but failed: timeout, error exit, login required, or
    output unparseable.

    Also covers ODPS auth failures (``InvalidAccessKeyId`` /
    ``SignatureDoesNotMatch`` / ``AccessKeyIdNotFound`` /
    ``InvalidSecurityToken``) raised through ``map_pyodps_exception``.
    Before this consolidation there were two distinct
    ``AuthFailedError`` classes — one in ``auth/errors.py`` for the
    process-auth helper layer and one in ``mc_client/errors.py`` for
    ODPS — sharing the same wire code but different class identities.
    Collapsed into this single class because no caller distinguished
    between them.
    """

    code = ErrorCode.AUTH_FAILED
    exit_code = 4


class IdentityNotAuthorizedError(McsError):
    """ODPS reported the credential is valid but not authorized for the
    target project / endpoint."""

    code = ErrorCode.IDENTITY_NOT_AUTHORIZED
    exit_code = 4


class WhoAmIFailedError(McsError):
    """``mcs profile whoami`` couldn't resolve a live identity.

    Distinct from :class:`AuthFailedError` (credential rejected) and
    :class:`EndpointUnreachableError` (network unreachable): this one
    means the credential resolved and the connection opened, but the
    ODPS ``execute_security_query("whoami")`` security query returned a
    payload we couldn't extract a principal-display string from.
    Empirically this happens against endpoints that don't implement the
    security-query verb, against credentials that resolve but have no
    associated RAM identity, and against the env-vars-anonymous
    fallback when the env vars aren't set. Exit code 4 — categorically
    an auth-axis failure (the credential resolved but no usable
    principal came back), so it belongs in the same exit-code bucket
    as :class:`AuthFailedError` / :class:`IdentityNotAuthorizedError`
    rather than the Unknown bucket.
    """

    code = ErrorCode.WHO_AM_I_FAILED
    exit_code = 4


class NoBoundProfileError(McsError):
    """``mcs profile update`` / ``whoami`` ran bare and the standard
    active-profile chain landed on the env-vars-anonymous Profile
    (no saved name, no on-disk yaml entry, the AK and endpoint
    come from the shell env). That Profile shape is fine for
    read-side verbs like ``mcs sql execute``, but ``update`` needs
    a saved alias to write back to, and ``whoami`` likes to have
    a name for the banner. Surfaces a clear "no saved profile is
    bound to this directory; here's how to fix it" remediation
    rather than the cascade of downstream confusion that would
    otherwise follow.
    """

    code = ErrorCode.NO_BOUND_PROFILE
    exit_code = 1


class ConfigEnvNotSetError(McsError):
    """``${env:VAR}`` reference in profiles.yaml but VAR not set."""

    code = ErrorCode.CONFIG_ENV_NOT_SET
    exit_code = 3


class ConfigPermissionError(McsError):
    """Cannot read/write config file due to FS permissions."""

    code = ErrorCode.CONFIG_PERMISSION
    exit_code = 3


class ConfigWriteError(McsError):
    """Atomic rename failed during config write."""

    code = ErrorCode.CONFIG_WRITE
    exit_code = 3


class IncompatibleProfileVersionError(McsError):
    """profiles.yaml version unsupported by this mcs build."""

    code = ErrorCode.INCOMPATIBLE_PROFILE_VERSION
    exit_code = 3


class InvalidProfileError(McsError):
    """A profile entry has invalid/missing fields."""

    code = ErrorCode.INVALID_PROFILE
    exit_code = 3


class InvalidProfileFileError(McsError):
    """profiles.yaml is not valid YAML."""

    code = ErrorCode.INVALID_PROFILE_FILE
    exit_code = 3


class NoProfilesConfiguredError(McsError):
    """profiles.yaml is empty/missing and a profile is required."""

    code = ErrorCode.NO_PROFILES_CONFIGURED
    exit_code = 3


class ProfileNotFoundError(McsError):
    """Named profile doesn't exist in profiles.yaml."""

    code = ErrorCode.PROFILE_NOT_FOUND
    exit_code = 3


class WorkingDirectoryError(McsError):
    """os.getcwd() failed (cwd unlinked / inaccessible)."""

    code = ErrorCode.WORKING_DIRECTORY
    exit_code = 1
