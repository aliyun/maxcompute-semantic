# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Credential resolver: process auth + ak auth (literal or env)."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from maxcompute_semantic.auth.env_expand import expand_env
from maxcompute_semantic.auth.errors import AuthBinaryMissingError, AuthFailedError
from maxcompute_semantic.auth.schema import AkAuth, ProcessAuth
from maxcompute_semantic.mc_client.errors import IdentityNotAuthorizedError

logger = logging.getLogger("maxcompute_semantic")


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    access_key_secret: str
    security_token: str = ""
    expiration: datetime | None = None


def resolve_credentials(auth: ProcessAuth | AkAuth) -> Credentials:
    """Resolve credentials from either process or ak auth spec.

    For AkAuth: expand env vars, return literal values.
    For ProcessAuth: run the command, parse JSON payload.
    Raises AuthBinaryMissingError if process binary not on PATH.
    Raises AuthFailedError if command ran but failed (timeout, error, login required).
    Raises IdentityNotAuthorizedError if identity is not authorized.
    Raises ConfigEnvNotSetError (via expand_env) if env var unset.
    """
    if isinstance(auth, AkAuth):
        return _resolve_ak(auth)
    return _resolve_process(auth)


def _resolve_ak(auth: AkAuth) -> Credentials:
    ak_id = expand_env(auth.access_key_id)
    ak_secret = expand_env(auth.access_key_secret)
    return Credentials(access_key_id=ak_id, access_key_secret=ak_secret)


def _resolve_process(auth: ProcessAuth) -> Credentials:
    # Tokenize the command safely (respects quoting/shell escapes)
    cmd_parts = shlex.split(auth.command)
    if not cmd_parts:
        raise AuthBinaryMissingError(
            "auth.command is empty; cannot resolve credentials",
            remediation="set auth.command to a valid credential helper command",
        )
    binary = cmd_parts[0]
    if shutil.which(binary) is None:
        remediation = f"install {binary} or switch to auth.type=ak"
        raise AuthBinaryMissingError(
            f"auth.command binary '{binary}' not found on PATH",
            remediation=remediation,
        )
    try:
        proc = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=auth.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise AuthFailedError(
            f"auth.command timed out after {auth.timeout}s",
            remediation="increase auth.timeout or check command hangs",
        ) from e
    except OSError as e:
        raise AuthFailedError(
            f"auth.command failed to execute: {e}",
            remediation="check auth.command syntax",
        ) from e

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        combined = stderr or stdout
        if _looks_like_login_required(combined):
            raise AuthFailedError(
                f"authentication required: {combined}",
                remediation="run the appropriate login command for your credential helper",
            )
        if _looks_like_identity_not_authorized(combined):
            raise IdentityNotAuthorizedError(
                f"identity not authorized for ODPS: {combined}",
                remediation="check ODPS authorization or use a different account",
            )
        raise AuthFailedError(
            f"auth.command exited {proc.returncode}: {combined}",
            remediation="check command output for details",
        )

    payload = _parse_payload(proc.stdout)
    if payload is None:
        raise AuthFailedError(
            "auth.command output could not be parsed as credential JSON",
            remediation="check auth.command output format",
        )
    return payload


def _parse_payload(raw: str | dict[str, Any]) -> Credentials | None:
    """Parse credential payload from JSON string or dict.

    Expected shape is the Alibaba Cloud STS AssumeRole response format
    (``AccessKeyId``, ``AccessKeySecret``, ``SecurityToken``, optional
    ``Expiration``).  snake_case variants
    (``access_key_id``, ``access_key_secret``, ``security_token``)
    are also accepted.
    Returns None if input cannot be parsed (non-JSON, non-dict).
    Raises AuthFailedError if payload is a valid dict but missing
    any required STS field (AccessKeyId, AccessKeySecret, SecurityToken).
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    else:
        data = raw

    if not isinstance(data, dict):
        return None

    # Accept both CamelCase and snake_case keys
    ak_id = data.get("AccessKeyId") or data.get("access_key_id") or ""
    ak_secret = data.get("AccessKeySecret") or data.get("access_key_secret") or ""
    token = data.get("SecurityToken") or data.get("security_token") or ""
    exp_str = data.get("Expiration") or data.get("expiration") or ""

    # STS payload must include all three required fields
    required_missing = []
    if not ak_id:
        required_missing.append("AccessKeyId/access_key_id")
    if not ak_secret:
        required_missing.append("AccessKeySecret/access_key_secret")
    if not token:
        required_missing.append("SecurityToken/security_token")

    if required_missing:
        found_keys = list(data.keys())
        raise AuthFailedError(
            f"process auth payload missing required fields: {', '.join(required_missing)}; "
            f"found keys: {found_keys}",
            remediation="check process auth command output format",
        )

    expiration: datetime | None = None
    if exp_str:
        try:
            expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            logger.warning("Expiration parse failure: %s", e)
            expiration = None

    return Credentials(
        access_key_id=ak_id,
        access_key_secret=ak_secret,
        security_token=token,
        expiration=expiration,
    )


_LOGIN_REQUIRED_PATTERNS = re.compile(
    r"authentication\s+required|please\s+login|not\s+logged\s+in", re.IGNORECASE
)


def _looks_like_login_required(text: str) -> bool:
    return bool(_LOGIN_REQUIRED_PATTERNS.search(text))


_NOT_AUTHORIZED_PATTERNS = re.compile(
    r"identity\s+not\s+authorized|not\s+authorized|ODPS\s+user\s+not\s+found", re.IGNORECASE
)


def _looks_like_identity_not_authorized(text: str) -> bool:
    return bool(_NOT_AUTHORIZED_PATTERNS.search(text))
