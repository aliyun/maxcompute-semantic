# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Annotate-specific error classes.

Two McsError subclasses:
- AnnotateValidationError (exit_code=2): cross-field consistency rule violations
- AnnotateNotFoundError (exit_code=4): named table/column not found
"""

from __future__ import annotations

from typing import Any

from maxcompute_semantic.errors.base import ErrorCode, McsError


class AnnotateValidationError(McsError):
    """Raised when an annotate write violates §1 rules 1-9.

    code_subkey carries "rule-N" for the specific rule that fired.
    """

    code = ErrorCode.ANNOTATE_VALIDATION
    exit_code = 2

    def __init__(
        self,
        message: str,
        remediation: str = "",
        *,
        code_subkey: str | None = None,
    ) -> None:
        super().__init__(message=message, remediation=remediation, code_subkey=code_subkey)
        self.code_subkey = code_subkey


class AnnotateNotFoundError(McsError):
    """Raised when a named table or column is not found.

    scope carries "table" or "column" identifying which level missed.
    """

    code = ErrorCode.ANNOTATE_NOT_FOUND
    exit_code = 4

    def __init__(
        self,
        message: str,
        remediation: str = "",
        *,
        scope: str | None = None,
    ) -> None:
        ctx: dict[str, Any] = {}
        if scope:
            ctx["scope"] = scope
        super().__init__(message=message, remediation=remediation, **ctx)
        self.scope = scope
