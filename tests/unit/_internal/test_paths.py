"""Tests for _internal/paths.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from maxcompute_semantic._internal.paths import (
    config_dir,
    data_dir,
    data_root,
    link_json_path,
    profile_data_dir,
    profiles_yaml_path,
    tier_cache_path,
)


def test_config_dir_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MCS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".config" / "maxcompute-semantic"


def test_config_dir_xdg_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MCS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_dir() == tmp_path / "xdg" / "maxcompute-semantic"


def test_config_dir_mcs_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_CONFIG_DIR", str(tmp_path / "explicit"))
    assert config_dir() == tmp_path / "explicit"


# --- data_dir tests ---


def test_data_dir_default_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """On non-macOS Unix, data_dir defaults to ~/.local/share/maxcompute-semantic."""
    monkeypatch.delenv("MCS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force platform to Linux for deterministic assertion
    monkeypatch.setattr(sys, "platform", "linux")
    assert data_dir() == tmp_path / ".local" / "share" / "maxcompute-semantic"


def test_data_dir_default_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """On macOS, data_dir defaults to ~/Library/Application Support/maxcompute-semantic."""
    monkeypatch.delenv("MCS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert data_dir() == tmp_path / "Library" / "Application Support" / "maxcompute-semantic"


def test_data_dir_mcs_data_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """MCS_DATA_DIR takes precedence over everything."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path / "data"))
    assert data_dir() == tmp_path / "data"


def test_data_dir_xdg_data_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """XDG_DATA_HOME env var overrides default when MCS_DATA_DIR unset."""
    monkeypatch.delenv("MCS_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    assert data_dir() == tmp_path / "xdg-data" / "maxcompute-semantic"


def test_data_dir_mcs_data_overrides_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """MCS_DATA_DIR wins even when XDG_DATA_HOME is also set."""
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path / "mcs-data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    assert data_dir() == tmp_path / "mcs-data"


def test_data_dir_no_longer_under_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """data_dir() is NOT config_dir()/data anymore — it uses XDG_DATA_HOME."""
    monkeypatch.delenv("MCS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("MCS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    # config_dir and data_dir are now separate trees
    assert data_dir() != config_dir() / "data"
    # data_dir uses ~/.local/share, config_dir uses ~/.config
    assert data_dir() == tmp_path / ".local" / "share" / "maxcompute-semantic"
    assert config_dir() == tmp_path / ".config" / "maxcompute-semantic"


# --- data_root tests ---


def test_data_root_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MCS_PROFILES_DIR", raising=False)
    monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
    assert data_root() == tmp_path / "data"


def test_data_root_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path / "custom"))
    assert data_root() == tmp_path / "custom"


def test_data_root_under_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With default data_dir, data_root is data_dir()/data."""
    monkeypatch.delenv("MCS_DATA_DIR", raising=False)
    monkeypatch.delenv("MCS_PROFILES_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    assert data_root() == tmp_path / ".local" / "share" / "maxcompute-semantic" / "data"


# --- profile_data_dir tests ---


def test_profile_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert profile_data_dir("acme-corp") == tmp_path / "acme-corp"


# --- config-only path tests ---


def test_profiles_yaml_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_CONFIG_DIR", str(tmp_path))
    assert profiles_yaml_path() == tmp_path / "profiles.yaml"


def test_link_json_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_CONFIG_DIR", str(tmp_path))
    assert link_json_path() == tmp_path / "link.json"


# --- tier_cache_path tests (per-(profile, project) keying) ---


def test_tier_cache_path_basic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert tier_cache_path("acme-corp", "acme_warehouse") == (
        tmp_path / "acme-corp" / "tier_cache" / "acme_warehouse"
    )


def test_tier_cache_path_per_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Different MaxCompute projects under the same profile get distinct cache files."""
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    p1 = tier_cache_path("acme-corp", "acme_warehouse")
    p2 = tier_cache_path("acme-corp", "acme_prod")
    assert p1 != p2
    assert p1.parent == p2.parent  # both under <profile>/tier_cache/


def test_tier_cache_path_rejects_empty_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="non-empty MaxCompute project"):
        tier_cache_path("acme-corp", "")
    with pytest.raises(ValueError, match="non-empty MaxCompute project"):
        tier_cache_path("acme-corp", "   ")


def test_cache_dir_xdg_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``XDG_CACHE_HOME`` env var is honored when set, suffixed with the
    package name."""
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-cache")
    monkeypatch.delenv("MCS_CACHE_DIR", raising=False)
    from maxcompute_semantic._internal.paths import cache_dir

    assert cache_dir() == Path("/tmp/xdg-cache/maxcompute-semantic")


def test_cache_dir_mcs_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MCS_CACHE_DIR`` env var is an absolute override that does NOT
    get the package suffix appended — symmetric with ``MCS_DATA_DIR``."""
    monkeypatch.setenv("MCS_CACHE_DIR", "/tmp/explicit-cache")
    from maxcompute_semantic._internal.paths import cache_dir

    assert cache_dir() == Path("/tmp/explicit-cache")


def test_cache_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env override, the path is platform-appropriate XDG."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("MCS_CACHE_DIR", raising=False)
    from maxcompute_semantic._internal.paths import cache_dir

    result = cache_dir()
    # Platform-defaulted: ends with the package basename.
    assert result.name == "maxcompute-semantic"
    # Sits under the user's home (~/.cache/... on Linux, ~/Library/Caches/...
    # on macOS — both are descendants of $HOME).
    assert str(Path.home()) in str(result)
