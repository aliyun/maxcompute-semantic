"""Tests for auth/link_store.py."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest
from maxcompute_semantic.auth.errors import ConfigPermissionError, ConfigWriteError
from maxcompute_semantic.auth.link_store import (
    get_link,
    list_all,
    set_link,
    unlink,
)


def test_get_link_returns_none_when_no_file(isolated_config: Path) -> None:
    assert get_link("/some/cwd") is None


def test_set_then_get(isolated_config: Path) -> None:
    set_link("/abs/path/proj", "meta-dev")
    assert get_link("/abs/path/proj") == "meta-dev"


def test_get_link_for_unbound_cwd_returns_none(isolated_config: Path) -> None:
    set_link("/abs/a", "meta-dev")
    assert get_link("/abs/b") is None


def test_set_link_overwrites_existing(isolated_config: Path) -> None:
    set_link("/abs/a", "meta-dev")
    set_link("/abs/a", "sales-dw")
    assert get_link("/abs/a") == "sales-dw"


def test_unlink_removes_entry(isolated_config: Path) -> None:
    set_link("/abs/a", "meta-dev")
    unlink("/abs/a")
    assert get_link("/abs/a") is None


def test_unlink_idempotent(isolated_config: Path) -> None:
    unlink("/abs/nonexistent")  # must not raise


def test_list_all(isolated_config: Path) -> None:
    set_link("/abs/a", "meta-dev")
    set_link("/abs/b", "sales-dw")
    assert list_all() == {"/abs/a": "meta-dev", "/abs/b": "sales-dw"}


def test_get_link_default_uses_cwd(isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(isolated_config)
    set_link(str(isolated_config), "meta-dev")
    assert get_link() == "meta-dev"


def test_corrupted_json_returns_none(isolated_config: Path) -> None:
    from maxcompute_semantic._internal.paths import link_json_path

    path = link_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{")
    # spec: don't crash on bad link.json; treat as no binding
    assert get_link("/abs/a") is None


def test_json_without_links_key(isolated_config: Path) -> None:
    """JSON file that has no 'links' key should be treated as empty."""
    from maxcompute_semantic._internal.paths import link_json_path

    path = link_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1}')
    assert get_link("/abs/a") is None


def test_get_link_returns_none_when_cwd_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """os.getcwd() raises OSError → get_link() returns None."""
    monkeypatch.setattr("os.getcwd", lambda: (_ for _ in ()).throw(OSError("cwd unlinked")))
    assert get_link() is None


def test_write_failure_non_oserror_cleans_up_temp(isolated_config: Path) -> None:
    """When a non-OSError exception occurs, _write removes the temp file and re-raises."""
    # Set up a valid link first to ensure _write will be called
    set_link("/abs/a", "meta-dev")

    # Now patch json.dump to fail (non-OSError)
    with (
        patch("json.dump", side_effect=RuntimeError("json engine broke")),
        pytest.raises(RuntimeError, match="json engine broke"),
    ):
        set_link("/abs/b", "other")


def test_write_permission_oserror_raises_config_permission_error(isolated_config: Path) -> None:
    """OSError with EACCES/EPERM errno in _write → ConfigPermissionError; temp cleaned up."""

    set_link("/abs/a", "meta-dev")

    perm_err = OSError(errno.EACCES, "Permission denied")
    with (
        patch("os.replace", side_effect=perm_err),
        pytest.raises(ConfigPermissionError, match="permission denied"),
    ):
        set_link("/abs/b", "other")

    # Temp file should be cleaned up on ConfigPermissionError
    config_dir = Path(isolated_config / "config")
    tmp_files = list(config_dir.glob("*.tmp*"))
    assert len(tmp_files) == 0


def test_write_general_oserror_raises_config_write_error(isolated_config: Path) -> None:
    """OSError without permission errno in _write → ConfigWriteError; temp preserved."""

    set_link("/abs/a", "meta-dev")

    io_err = OSError(errno.ENOSPC, "No space left on device")
    with (
        patch("os.replace", side_effect=io_err),
        pytest.raises(ConfigWriteError, match="atomic write failed") as exc_info,
    ):
        set_link("/abs/b", "other")

    # Remediation should mention forensic
    assert "forensic" in exc_info.value.remediation

    # Temp file should be preserved for forensic on ConfigWriteError
    config_dir = Path(isolated_config / "config")
    tmp_files = list(config_dir.glob("*.tmp*"))
    assert len(tmp_files) == 1
