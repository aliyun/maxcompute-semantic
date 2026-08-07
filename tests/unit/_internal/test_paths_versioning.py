# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Versioning-related path helpers in ``_internal/paths``.

These join the per-profile data dir with the well-known subpaths the
versioning module reads and writes. The helpers are pure (no I/O) so
they're testable without setting up a real data dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_git_dir,
    profile_gitignore_path,
    profile_lock_path,
    profile_package_sql_path,
)


def test_profile_git_dir_is_dotgit_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert profile_git_dir("acme") == tmp_path / "acme" / ".git"


def test_profile_gitignore_is_dotignore_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert profile_gitignore_path("acme") == tmp_path / "acme" / ".gitignore"


def test_profile_package_sql_path_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert profile_package_sql_path("acme") == tmp_path / "acme" / "package.sql"


def test_profile_lock_path_under_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    assert profile_lock_path("acme") == tmp_path / "acme" / ".mcs-lock"


def test_path_helpers_honor_profile_package_path_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the caller passes a ``Profile`` (not a name string) and the
    Profile has a non-None ``package_path``, the helpers route through
    the override the same way ``profile_data_dir`` already does. This
    is the fork case — a fork's ``package_path`` is its detached
    worktree directory, not the default per-name slot."""
    from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile

    custom = tmp_path / "custom-worktree-location"
    custom.mkdir()
    profile = Profile(
        name="acme-baseline",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
        package_path=custom,
    )
    # The data-dir helper already does the right thing — these new
    # helpers must follow the same dispatch.
    assert profile_data_dir(profile) == custom
    assert profile_git_dir(profile) == custom / ".git"
    assert profile_gitignore_path(profile) == custom / ".gitignore"
    assert profile_package_sql_path(profile) == custom / "package.sql"
    assert profile_lock_path(profile) == custom / ".mcs-lock"


def test_paths_accept_bare_string_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Passing the profile name as a ``str`` (the "no Profile object
    available yet" call path used at the very top of ``mcs profile
    create``) returns the same paths as a ``Profile`` with no
    ``package_path`` override."""
    from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile

    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    profile = Profile(
        name="acme",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
        package_path=None,
    )
    assert profile_git_dir("acme") == profile_git_dir(profile)
    assert profile_gitignore_path("acme") == profile_gitignore_path(profile)
    assert profile_package_sql_path("acme") == profile_package_sql_path(profile)
    assert profile_lock_path("acme") == profile_lock_path(profile)


def test_helpers_dont_create_anything_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Calling all four helpers against a name whose data dir doesn't
    exist yet returns the expected ``Path`` and does not create any of
    the parent directories. The mkdir is the caller's responsibility."""
    monkeypatch.setenv("MCS_PROFILES_DIR", str(tmp_path))
    # Sanity: the data root exists (it's tmp_path) but the profile slot does not.
    profile_slot = tmp_path / "fresh-profile-never-created"
    assert not profile_slot.exists()

    g = profile_git_dir("fresh-profile-never-created")
    gi = profile_gitignore_path("fresh-profile-never-created")
    ps = profile_package_sql_path("fresh-profile-never-created")
    lp = profile_lock_path("fresh-profile-never-created")

    assert g == profile_slot / ".git"
    assert gi == profile_slot / ".gitignore"
    assert ps == profile_slot / "package.sql"
    assert lp == profile_slot / ".mcs-lock"
    # None of the parents were created.
    assert not profile_slot.exists()
