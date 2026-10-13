# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MCP connection configuration, separate from MaxCompute AK profiles."""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from maxcompute_semantic._internal.paths import config_dir
from maxcompute_semantic.errors import ErrorCode, McsError

_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PRIVATE_MODE = 0o600
_DIRECTORY_MODE = 0o700


class RemoteError(McsError):
    """Classified remote failures whose messages never include credentials."""

    code = ErrorCode.REMOTE

    def __init__(
        self,
        message: str,
        *,
        code: str = "RemoteError",
        remediation: str = "check the remote configuration and authentication",
        **context: Any,
    ) -> None:
        super().__init__(
            message, code=code, exit_code=3, remediation=remediation, **context
        )


@dataclass(frozen=True)
class RemoteConnection:
    alias: str
    url: str
    region: str
    token_env: str | None = None

    def __post_init__(self) -> None:
        validate_alias(self.alias)
        if not isinstance(self.url, str):
            raise RemoteError("MCP URL must be a string", code="RemoteInvalidConfig")
        try:
            parsed = urlsplit(self.url)
            # Force port validation before the SDK sees a malformed endpoint.
            _ = parsed.port
        except ValueError:
            raise RemoteError("invalid MCP URL", code="RemoteInvalidConfig") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ch.isspace() for ch in self.url)
        ):
            raise RemoteError(
                "MCP URL must be an HTTP(S) endpoint without credentials, query or fragment",
                code="RemoteInvalidConfig",
            )
        if not isinstance(self.region, str) or not self.region.strip():
            raise RemoteError("remote region is required", code="RemoteInvalidConfig")
        if self.token_env is not None and (
            not isinstance(self.token_env, str)
            or not _ENV_NAME.fullmatch(self.token_env)
        ):
            raise RemoteError(
                "invalid bearer environment variable name", code="RemoteInvalidConfig"
            )

    def to_dict(self) -> dict[str, Any]:
        """Export connection metadata only; never resolve the bearer variable."""
        return asdict(self)


def validate_alias(alias: str) -> None:
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
        raise RemoteError(
            "remote alias must contain 1-64 letters, digits, underscores or hyphens",
            code="RemoteInvalidConfig",
        )


class RemoteStore:
    """Store named connections without accessing MaxCompute profile credentials."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (
            directory if directory is not None else config_dir() / "remotes"
        )

    def _path(self, alias: str) -> Path:
        validate_alias(alias)
        return self.directory / f"{alias}.remote.json"

    def add(self, remote: RemoteConnection) -> None:
        path = self._path(remote.alias)
        fd: int | None = None
        temporary: str | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
            fd, temporary = tempfile.mkstemp(prefix=".remote-", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                os.fchmod(handle.fileno(), _PRIVATE_MODE)
                json.dump(remote.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Atomic no-clobber publication: a failed/partial write cannot leave
            # an unusable alias, and simultaneous adds cannot overwrite a user.
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise RemoteError(
                    "remote alias already exists", code="RemoteAlreadyExists"
                ) from None
        except OSError:
            if fd is not None:
                os.close(fd)
            raise RemoteError(
                "cannot save remote configuration", code="RemoteConfigWrite"
            ) from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    Path(temporary).unlink(missing_ok=True)

    def get(self, alias: str) -> RemoteConnection:
        path = self._path(alias)
        try:
            if path.is_symlink():
                raise RemoteError(
                    "remote configuration must not be a symlink",
                    code="RemoteInvalidConfig",
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RemoteError("remote alias not found", code="RemoteNotFound") from None
        except (OSError, ValueError):
            raise RemoteError(
                "cannot read remote configuration", code="RemoteInvalidConfig"
            ) from None
        if not isinstance(payload, dict) or set(payload) - {
            "alias",
            "url",
            "region",
            "token_env",
        }:
            raise RemoteError(
                "invalid remote configuration fields", code="RemoteInvalidConfig"
            )
        try:
            remote = RemoteConnection(**payload)
        except (TypeError, AttributeError):
            raise RemoteError(
                "invalid remote configuration fields", code="RemoteInvalidConfig"
            ) from None
        if remote.alias != alias:
            raise RemoteError(
                "remote alias does not match its file", code="RemoteInvalidConfig"
            )
        return remote

    def list(self) -> list[RemoteConnection]:
        return [
            self.get(path.name.removesuffix(".remote.json"))
            for path in sorted(self.directory.glob("*.remote.json"))
        ]

    def remove(self, alias: str) -> None:
        self.get(alias)
        try:
            for suffix in (".tokens.json", ".client.json", ".remote.json"):
                (self.directory / f"{alias}{suffix}").unlink(missing_ok=True)
        except OSError:
            raise RemoteError(
                "cannot remove remote configuration", code="RemoteConfigWrite"
            ) from None
