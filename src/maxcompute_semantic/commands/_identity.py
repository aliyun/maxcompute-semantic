# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared identity-display helpers.

Four small functions, each pure-data:

- ``mask_ak_id(value)`` — fingerprints a literal AK id into the
  ``FIRST4***LAST4`` shape used by ``mcs profile show`` and the
  editor's section list. Matches what ``maxc auth whoami`` emits
  as ``principal_masked``. Env-refs (``${env:VAR}``) pass through
  unchanged: they're lookup pointers, not secrets.

- ``env_ref_name(value)`` / ``env_ref_status(value)`` — recognize
  the ``${env:NAME}`` reference form and report whether the named
  variable is currently exported in the process environment. Used
  by ``mcs profile show`` to annotate each env-ref row with the
  shell-environment status without echoing the resolved literal.

- ``resolve_principal(client)`` — the live ODPS whoami probe used
  by ``mcs profile whoami``. Issues
  ``odps.execute_security_query("whoami")`` and pulls the first
  non-empty principal-display field from the response. Never
  raises — failures collapse to ``None`` so the caller can render
  a graceful "no identity available" message.

The full-redaction marker form (``***REDACTED***`` for the
machine-readable JSON / YAML round-trip output) lives in
``commands/profile.py::_maybe_redact_secret``. It's a *protocol*
marker the loader detects and substitutes against the existing
profile's stored value during ``mcs profile update --from-spec``,
not a display fingerprint, so the two facilities are intentionally
named differently.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.mc_client.client import MaxComputeClient


# ── Env-var reference helpers ────────────────────────────────────────────
#
# Auth fields are either literals (the actual credential lives in the
# profile yaml — the masking layer redacts these on display) or
# environment references of the form ``${env:VAR_NAME}`` (the literal
# lives in the shell environment, and the profile yaml carries only
# the pointer name). The two cases render differently in ``mcs profile
# show`` so the user knows where the secret actually lives.

_ENV_REF_PREFIX = "${env:"
_ENV_REF_SUFFIX = "}"


def env_ref_name(value: str) -> str | None:
    """Return the env-var name inside a ``${env:NAME}`` reference,
    or ``None`` when ``value`` is a literal credential.

    Empty inner name (``"${env:}"``) is treated as malformed and
    also returns ``None`` so the caller doesn't render a confusing
    "(env var '' set)" annotation.
    """
    if not (value.startswith(_ENV_REF_PREFIX) and value.endswith(_ENV_REF_SUFFIX)):
        return None
    inner = value[len(_ENV_REF_PREFIX) : -len(_ENV_REF_SUFFIX)]
    return inner or None


def env_ref_status(value: str, *, env: Mapping[str, str] | None = None) -> tuple[str, bool] | None:
    """Inspect an env-ref auth field's status in the current shell.

    Returns a ``(human-readable-label, is_set)`` pair on env-refs and
    ``None`` on literal credentials. The label is a short tag
    suitable for appending to the auth-row line in ``mcs profile
    show`` ("(env var FOO set)" / "(env var FOO NOT set in current
    shell)"). The boolean lets the caller pick a color (green when
    set, yellow when not) without having to re-parse the label.

    Looks the name up in ``env`` (defaults to ``os.environ``). The
    indirection is for tests, which exercise the both-paths matrix
    without monkey-patching the global ``os.environ``.
    """
    name = env_ref_name(value)
    if name is None:
        return None
    e = env if env is not None else os.environ
    val = e.get(name) or ""
    if val.strip():
        return f"(env var {name} set in current shell)", True
    return f"(env var {name} NOT set in current shell)", False


def mask_ak_id(value: str) -> str:
    """Mask a literal AK id as ``FIRST4***LAST4``.

    Env-refs (``${env:VAR}``) pass through unchanged. IDs shorter
    than 9 chars collapse to ``***`` to avoid the degenerate case
    where the mask would leave no characters in the middle.
    """
    if value.startswith("${env:"):
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


# Order matters: try the most identity-specific fields first, fall
# back to less specific ones. Mirrors the order ``maxc auth whoami``
# uses when assembling its ``principal_display``. The exact set of
# keys returned by ODPS varies by region and account type (Aliyun
# account vs. RAM user vs. RAM role); the union of observed keys
# is the lookup list.
_WHOAMI_PRINCIPAL_KEYS: tuple[str, ...] = (
    "DisplayName",
    "EndUser",
    "Subaccount",
    "Name",
    "ID",
)


def resolve_principal(client: MaxComputeClient) -> str | None:
    """Issue ODPS whoami and return the principal display string.

    Returns the first non-empty value from ``_WHOAMI_PRINCIPAL_KEYS``
    in the response payload, or None on any failure. Classified
    ``McsError`` exceptions are **re-raised** so callers can present
    the specific code and remediation (auth failed, identity not
    authorized, endpoint unreachable, etc.) instead of a generic
    "no identity" message. Only unclassified / unexpected exceptions
    are folded into ``None``.
    """
    from maxcompute_semantic.mc_client.errors import McsError

    try:
        odps = client._ensure_odps()
        result = odps.execute_security_query("whoami")
    except McsError:
        raise
    except Exception:  # noqa: BLE001 — unclassified errors fold into None (see docstring)
        return None
    if isinstance(result, dict):
        for key in _WHOAMI_PRINCIPAL_KEYS:
            v = result.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    if isinstance(result, str) and result.strip():
        # Some endpoints return the whoami output as a multi-line
        # text block; the first non-empty line is the identity
        # banner. Be conservative — only the first line.
        first_line = result.strip().splitlines()[0].strip()
        return first_line or None
    return None


def live_identity(profile: Profile) -> str | None:
    """Probe the live identity for a profile, return display string.

    Dispatches on the auth variant. The wiring matches what the
    historical ``mcs auth whoami`` verb produced for each shape:

    - ``AkAuth`` constructs a fresh ``MaxComputeClient`` and calls
      :func:`resolve_principal` (the ODPS whoami security query). A
      classified ``McsError`` (auth failed, identity not authorized,
      endpoint unreachable, etc.) is **re-raised** so the CLI can
      present the specific code and remediation instead of a generic
      "no identity" message. Only unexpected exceptions are folded
      into the ``None`` failure path.
    - ``ProcessAuth`` calls the ncs credential helper's ``whoami()``
      function and formats the resulting record as
      ``"<identity_name> (employee.<id>)"``, the same string the
      retired ``mcs auth whoami`` verb used to emit. Classified
      ``McsError`` exceptions propagate; unexpected ones return
      ``None``.

    Returns ``None`` on any unclassified failure or for unrecognized
    auth variants. The CLI surface that calls this is
    ``mcs profile whoami NAME`` (in ``commands/profile.py``).
    """
    # Imports are function-scoped — this module sits below the
    # mc_client / auth packages in the dependency graph and a
    # module-level import of either would close the cycle through
    # the package ``__init__``.
    from maxcompute_semantic.auth.schema import AkAuth, ProcessAuth
    from maxcompute_semantic.mc_client.errors import McsError

    if isinstance(profile.auth, AkAuth):
        from maxcompute_semantic.mc_client.client import MaxComputeClient

        try:
            client = MaxComputeClient(profile)
        except McsError:
            raise
        except Exception:  # noqa: BLE001 — unclassified errors fold into None (see docstring)
            return None
        return resolve_principal(client)
    if isinstance(profile.auth, ProcessAuth):
        try:
            from maxcompute_semantic.auth import ncs as ncs_mod

            info = ncs_mod.whoami()
        except McsError:
            raise
        except Exception:  # noqa: BLE001 — unclassified errors fold into None (see docstring)
            return None
        if info is None:
            return None
        return f"{info.identity_name} (employee.{info.employee_id})"
    return None
