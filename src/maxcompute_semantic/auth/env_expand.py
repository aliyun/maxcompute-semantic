# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""${env:VAR} expansion for AK auth credentials."""

from __future__ import annotations

import os
import re

from maxcompute_semantic.auth.errors import ConfigEnvNotSetError

_ENV_RE = re.compile(r"^\$\{env:([A-Z_][A-Z0-9_]*)\}$")


def expand_env(value: str) -> str:
    """Expand ${env:VAR} -> os.environ[VAR]. Literal otherwise.

    Raises ConfigEnvNotSetError if VAR is referenced but unset/empty.
    """
    m = _ENV_RE.match(value)
    if m is None:
        return value
    var = m.group(1)
    expanded = os.environ.get(var, "")
    if not expanded:
        raise ConfigEnvNotSetError(
            f"env var {var!r} referenced by profile but not set (or empty)",
            remediation=f"export {var}=... before running mcs, or use auth.type=process",
        )
    return expanded
