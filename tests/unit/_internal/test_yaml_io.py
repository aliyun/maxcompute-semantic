"""Tests for _internal/yaml_io.py."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest
from maxcompute_semantic._internal.yaml_io import dump_yaml, load_yaml
from maxcompute_semantic.auth.errors import ConfigPermissionError, ConfigWriteError
from ruamel.yaml import YAMLError


def test_dump_then_load_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "x.yaml"
    dump_yaml({"a": 1, "b": [2, 3]}, target)
    loaded = load_yaml(target)
    assert loaded["a"] == 1
    assert list(loaded["b"]) == [2, 3]


def test_load_preserves_comments_on_dump(tmp_path: Path) -> None:
    target = tmp_path / "x.yaml"
    target.write_text("# header comment\na: 1  # inline\n")
    data = load_yaml(target)
    data["a"] = 2
    dump_yaml(data, target)
    out = target.read_text()
    assert "# header comment" in out
    assert "# inline" in out


def test_dump_atomic_uses_tempfile(tmp_path: Path) -> None:
    """Atomic write: target file must never appear half-written."""
    target = tmp_path / "x.yaml"
    target.write_text("old: value\n")
    dump_yaml({"new": "value"}, target)
    assert load_yaml(target) == {"new": "value"}
    # No leftover temp files
    assert list(tmp_path.glob("*.tmp*")) == []


def test_dump_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "x.yaml"
    dump_yaml({"a": 1}, target)
    assert target.exists()


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_yaml(tmp_path / "nope.yaml")


def test_load_invalid_yaml_raises(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_text("key: [unclosed\n")
    with pytest.raises(YAMLError):
        load_yaml(target)


def test_dump_write_failure_cleans_up(tmp_path: Path) -> None:
    """On write failure (non-OSError), dump_yaml removes the temp file and re-raises."""
    target = tmp_path / "x.yaml"

    # Make the actual write fail with a non-OSError exception.
    def _failing_dump(data, stream):
        raise RuntimeError("yaml engine broke")

    with (
        patch.object(
            __import__("maxcompute_semantic._internal.yaml_io", fromlist=["_yaml"])._yaml,
            "dump",
            side_effect=_failing_dump,
        ),
        pytest.raises(RuntimeError, match="yaml engine broke"),
    ):
        dump_yaml({"a": 1}, target)

    # No leftover temp files
    assert list(tmp_path.glob("*.tmp*")) == []


def test_dump_permission_oserror_raises_config_permission_error(tmp_path: Path) -> None:
    """OSError with EACCES/EPERM errno → ConfigPermissionError; temp file cleaned up."""
    target = tmp_path / "x.yaml"
    perm_err = OSError(errno.EACCES, "Permission denied")
    with (
        patch("os.replace", side_effect=perm_err),
        pytest.raises(ConfigPermissionError, match="permission denied"),
    ):
        dump_yaml({"a": 1}, target)

    # Temp file should be cleaned up on ConfigPermissionError
    assert list(tmp_path.glob("*.tmp*")) == []


def test_dump_general_oserror_raises_config_write_error(tmp_path: Path) -> None:
    """OSError without permission errno → ConfigWriteError; temp file preserved."""
    target = tmp_path / "x.yaml"
    io_err = OSError(errno.ENOSPC, "No space left on device")
    with (
        patch("os.replace", side_effect=io_err),
        pytest.raises(ConfigWriteError, match="atomic write failed") as exc_info,
    ):
        dump_yaml({"a": 1}, target)

    # Remediation should mention forensic
    assert "forensic" in exc_info.value.remediation

    # Temp file should be preserved for forensic on ConfigWriteError
    tmp_files = list(tmp_path.glob("*.tmp*"))
    assert len(tmp_files) == 1
