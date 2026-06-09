# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""link.json read/write: cwd -> profile name binding (a1-style single file)."""

from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maxcompute_semantic._internal.paths import link_json_path
from maxcompute_semantic.auth.errors import ConfigPermissionError, ConfigWriteError
from maxcompute_semantic.versioning.lock import WriteLock

logger = logging.getLogger("maxcompute_semantic")

_VERSION = 1


def _read() -> dict[str, Any]:
    """Return parsed link.json contents; empty doc on missing or corrupt file."""
    path = link_json_path()
    if not path.exists():
        return {"version": _VERSION, "links": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("link.json at %s unreadable: %s; treating as empty", path, e)
        return {"version": _VERSION, "links": {}}
    if "links" not in data:
        data["links"] = {}
    return dict(data)


def _write(data: dict[str, Any]) -> None:
    path = link_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM):
            tmp_path.unlink(missing_ok=True)
            raise ConfigPermissionError(
                f"permission denied writing link.json: {e}",
                remediation="chmod the config directory or use an alternative location",
            ) from e
        # For general IO failures, preserve temp file for forensic.
        raise ConfigWriteError(
            f"atomic write failed for link.json: {e}",
            remediation="check disk space and filesystem health; temp file preserved at "
            f"{tmp_name} for forensic inspection",
        ) from e
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def get_link(cwd: str | None = None) -> str | None:
    """Profile bound to cwd (None for current dir). Returns profile name or None."""
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:
            return None
    data = _read()
    entry = data["links"].get(cwd)
    return entry["profile"] if entry else None


def set_link(cwd: str, profile_name: str) -> None:
    path = link_json_path()
    with WriteLock(path.parent / ".link.lock"):
        data = _read()
        data["links"][cwd] = {
            "profile": profile_name,
            "linked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        _write(data)


def unlink(cwd: str) -> None:
    path = link_json_path()
    with WriteLock(path.parent / ".link.lock"):
        data = _read()
        data["links"].pop(cwd, None)
        _write(data)


def list_all() -> dict[str, str]:
    """All cwd -> profile bindings."""
    data = _read()
    return {cwd: entry["profile"] for cwd, entry in data["links"].items()}
