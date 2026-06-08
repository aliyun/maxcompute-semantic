# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Status JSON helper — every script ends with one of these on stdout.

Lets the SKILL.md workflow parse without regex.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.errors import McsError


def emit_status(payload: dict, *, success: bool = True) -> None:
    out = {"status": "success" if success else "error", "data": payload}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


def die(message: str, code: str = "ERROR", exit_status: int = 1) -> None:
    print(json.dumps({"status": "error", "error": {"code": code, "message": message}}))
    sys.exit(exit_status)


def emit_mcs_error(exc: McsError) -> NoReturn:
    """Print the standard error envelope for *exc* and exit with its code.

    Mirrors the shape that ``meta``/``sql`` verbs emitted inline before
    extraction: ``{status: "error", error: {code, message, remediation,
    context}}``. The ``context`` field is the McsError's ``**context``
    kwargs (e.g. ``sql=…``), passed through verbatim.
    """
    payload = {
        "status": "error",
        "error": {
            "code": exc.code,
            "message": exc.message,
            "remediation": exc.remediation,
            "context": dict(exc.context),
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(exc.exit_code)
