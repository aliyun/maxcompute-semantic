# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Thin ncs subprocess helpers used by `mcs profile create` wizard.

These are best-effort: any failure returns None / [] rather than raising,
because the wizard falls back to manual entry when ncs isn't usable.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("maxcompute_semantic")


@dataclass(frozen=True)
class NcsWhoami:
    identity_name: str
    employee_id: str


@dataclass(frozen=True)
class NcsAuth:
    buc_user_id: str
    buc_user_type: str
    buc_account_name: str


def is_available() -> bool:
    return shutil.which("ncs") is not None


def whoami() -> NcsWhoami | None:
    """Return current ncs identity or None if not logged in / unavailable."""
    if not is_available():
        return None
    try:
        proc = subprocess.run(
            ["ncs", "auth", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("ncs auth whoami failed: %s", e)
        return None
    if proc.returncode != 0:
        return None
    return _parse_whoami(proc.stdout)


def _parse_whoami(output: str) -> NcsWhoami | None:
    """Extract identity_name + employee_id from `ncs auth whoami` output."""
    name = _extract_field(output, "identity_name")
    key = _extract_field(output, "identity_key")
    if not name or not key:
        return None
    match = re.search(r"(\d+)$", key)
    if not match:
        return None
    return NcsWhoami(identity_name=name, employee_id=match.group(1))


def _extract_field(output: str, key: str) -> str:
    """Find a line like 'identity_name    Alice (alias)' and return the value."""
    pattern = re.compile(rf"^{re.escape(key)}\s+(.+?)\s*$", re.MULTILINE)
    m = pattern.search(output)
    return m.group(1).strip() if m else ""


def list_odps_authorizations() -> list[NcsAuth]:
    """Return list of authorized ODPS user identities; empty on any error."""
    if not is_available():
        return []
    try:
        proc = subprocess.run(
            [
                "ncs",
                "list",
                "authorizations",
                "odpsuser",
                "-o",
                "custom-columns=BUC_USER_ID:.extension.bucUserId,"
                "BUC_USER_TYPE:.extension.bucUserType,"
                "BUC_ACCOUNT_NAME:.extension.bucDomainAccount",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("ncs list authorizations failed: %s", e)
        return []
    if proc.returncode != 0:
        logger.warning("ncs list authorizations exit=%d: %s", proc.returncode, proc.stderr.strip())
        return []
    return _parse_authorizations(proc.stdout)


def _parse_authorizations(output: str) -> list[NcsAuth]:
    """Parse the table output, skip header + separator rows."""
    auths: list[NcsAuth] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("BUC_USER_ID") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        auths.append(
            NcsAuth(buc_user_id=parts[0], buc_user_type=parts[1], buc_account_name=parts[2])
        )
    return auths


NCS_INSTALL_DOC_URL = "https://authx.io.alibaba-inc.com"


def install_hint() -> str:
    """Multi-line hint pointing the user at the Akless CLI install docs.

    Used by the profile-create wizard preflight, the `mcs doctor`
    `ncs_available` check, and `AuthBinaryMissingError`'s remediation
    when the missing binary is `ncs`. Single source of truth for the
    wording and the URL.
    """
    return f"Akless CLI `ncs` not found on PATH.\nInstall ncs: {NCS_INSTALL_DOC_URL}"
