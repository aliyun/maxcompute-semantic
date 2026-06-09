# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""ruamel.yaml singleton + atomic write."""

from __future__ import annotations

import errno
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from maxcompute_semantic.auth.errors import ConfigPermissionError, ConfigWriteError

logger = logging.getLogger("maxcompute_semantic")

_yaml = YAML(typ="rt")
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True
_yaml.default_flow_style = False
_yaml.allow_unicode = True


def load_yaml(path: Path) -> Any:
    """Read YAML file, return ruamel CommentedMap / list / scalar.

    Raises FileNotFoundError if missing. Raises ruamel.yaml exceptions on
    parse error -- caller must catch and re-classify if needed.
    """
    with path.open(encoding="utf-8") as f:
        return _yaml.load(f)


def dump_yaml(data: Any, path: Path) -> None:
    """Write YAML atomically: tmp file in same dir + os.replace.

    Auto-creates parent directory. Raises ConfigPermissionError on
    permission-related OSError (EACCES/EPERM); raises ConfigWriteError
    on other OSError. The temp file is NOT cleaned up on
    ConfigWriteError (preserved for forensic), but IS cleaned up on
    ConfigPermissionError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.fchmod(fd, 0o600)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml.dump(data, f)
        os.replace(tmp_name, path)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM):
            tmp_path.unlink(missing_ok=True)
            raise ConfigPermissionError(
                f"permission denied writing {path}: {e}",
                remediation="chmod the config directory or use an alternative location",
            ) from e
        # For general IO failures, preserve temp file for forensic.
        raise ConfigWriteError(
            f"atomic write failed for {path}: {e}",
            remediation="check disk space and filesystem health; temp file preserved at "
            f"{tmp_name} for forensic inspection",
        ) from e
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
