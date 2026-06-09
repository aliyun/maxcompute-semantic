# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Envelope + ErrorDetail: structured CLI output format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from maxcompute_semantic.mc_client.errors import McsError


@dataclass
class ErrorDetail:
    code: str
    message: str
    remediation: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
            "context": self.context,
        }


@dataclass
class Envelope:
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    error: ErrorDetail | None = None

    @classmethod
    def success(cls, data: dict[str, Any]) -> Envelope:
        return cls(status="success", data=data)

    @classmethod
    def from_error(cls, err: McsError) -> Envelope:
        return cls(
            status="error",
            error=ErrorDetail(
                code=err.code,
                message=err.message,
                remediation=err.remediation,
                context=dict(err.context),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error.to_dict()
        return result
