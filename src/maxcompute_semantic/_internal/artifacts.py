# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Atomic JSON exports that cannot overwrite their live source stores."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from maxcompute_semantic.errors import McsError


def sqlite_source_paths(path: Path) -> tuple[Path, ...]:
    """Protect journals of both the requested path and SQLite's resolved path."""
    return tuple(dict.fromkeys(
        Path(str(database) + suffix)
        for database in (path, path.resolve())
        for suffix in ("", "-wal", "-shm", "-journal")
    ))


def write_json_artifact(path: Path, data: Any, *, protected: tuple[Path, ...] = ()) -> None:
    """Publish a complete artifact, leaving existing contents intact on failure."""
    temporary: str | None = None
    try:
        for source in protected:
            if path.resolve() == source.resolve() or (
                path.exists() and source.exists() and path.samefile(source)
            ):
                raise McsError("artifact output would overwrite an active source store",
                               code="ArtifactSourceOverwrite", exit_code=2)
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise McsError("cannot write the JSON artifact", code="ArtifactWriteFailed",
                       exit_code=2, path=str(path)) from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
