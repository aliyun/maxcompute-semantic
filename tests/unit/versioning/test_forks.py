# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``versioning.forks`` — the yaml-side helpers
that pair with the git-side worktree machinery.

The helpers (``register_fork``, ``unregister_fork``, ``parent_repo``)
are exercised here in isolation from the CLI surface. The CLI
verb (``mcs profile fork``) has its own coverage in
``tests/unit/commands/test_profile_fork.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.errors import ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import upsert as upsert_profile
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning.forks import (
    parent_repo,
    register_fork,
    unregister_fork,
)
from maxcompute_semantic.versioning.git_repo import GitRepo

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile git versioning requires the ``git`` binary on PATH",
)


def _make_main_profile_with_history(name: str = "p_main") -> tuple[Profile, str]:
    """Spin up a main-kind profile whose data-dir is a git repo
    with one commit on top of the inaugural state. Returns the
    profile and the HEAD SHA."""
    profile = Profile(
        name=name,
        compute_project="acme_proj",
        endpoint="http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
        auth=AkAuth(access_key_id="${env:K}", access_key_secret="${env:S}"),
        sources=(DataSource(project="acme_proj", schema="default", tables="*"),),
    )
    upsert_profile(profile)
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    repo = GitRepo(pdir)
    repo.init()
    (pdir / "_marker.md").write_text("seed\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit("init: seed")
    assert sha is not None
    return profile, sha


def test_register_fork_persists_yaml_entry_with_canonical_full_sha(
    isolated_config: Path,
) -> None:
    """``register_fork`` writes the fork into ``profiles.yaml`` with
    kind=fork, the parent name, and a 40-char full SHA (a short SHA
    passed in is normalized via ``rev_parse``)."""
    parent, head = _make_main_profile_with_history()
    worktree = isolated_config / "fork_wt"
    short = head[:7]
    fork = register_fork(parent, "p_main@base", short, worktree)
    assert fork.kind == "fork"
    assert fork.parent_profile == parent.name
    assert fork.git_sha == head
    assert len(fork.git_sha) == 40

    # Round-trips through profile_store with the same shape.
    reloaded = get_profile("p_main@base")
    assert reloaded.kind == "fork"
    assert reloaded.parent_profile == parent.name
    assert reloaded.git_sha == head


def test_register_fork_idempotent_on_same_anchor(isolated_config: Path) -> None:
    """A second ``register_fork`` call with the same fork name,
    parent, and anchor SHA is a no-op (returns the existing entry)."""
    parent, head = _make_main_profile_with_history()
    worktree = isolated_config / "fork_wt"
    first = register_fork(parent, "p_main@base", head, worktree)
    second = register_fork(parent, "p_main@base", head, worktree)
    assert first.name == second.name
    assert first.git_sha == second.git_sha


def test_register_fork_name_collision_with_different_anchor_errors(
    isolated_config: Path,
) -> None:
    """A second ``register_fork`` with the same name but a different
    anchor SHA raises ``McsError`` rather than silently overwriting."""
    parent, head = _make_main_profile_with_history()
    worktree = isolated_config / "fork_wt"
    register_fork(parent, "p_main@v1", head, worktree)

    # Make a second commit so we have a different anchor SHA.
    pdir = profile_data_dir(parent)
    (pdir / "_marker2.md").write_text("more\n", encoding="utf-8")
    repo = GitRepo(pdir)
    repo.add_all()
    head2 = repo.commit("more: extra")
    assert head2 is not None and head2 != head

    with pytest.raises(McsError) as exc:
        register_fork(parent, "p_main@v1", head2, worktree)
    assert "already exists" in str(exc.value)


def test_register_fork_name_collision_with_main_profile_errors(
    isolated_config: Path,
) -> None:
    """A fork name that collides with an existing main-kind profile
    is rejected (the McsError mentions the existing kind)."""
    parent, head = _make_main_profile_with_history()
    other_main = Profile(
        name="other_main",
        compute_project="acme_proj",
        endpoint="http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
        auth=AkAuth(access_key_id="${env:K}", access_key_secret="${env:S}"),
        sources=(DataSource(project="acme_proj", schema="default", tables="*"),),
    )
    upsert_profile(other_main)

    worktree = isolated_config / "fork_wt"
    with pytest.raises(McsError) as exc:
        register_fork(parent, "other_main", head, worktree)
    assert "kind='main'" in str(exc.value) or 'kind="main"' in str(exc.value)


def test_unregister_fork_drops_yaml_entry(isolated_config: Path) -> None:
    """``unregister_fork`` removes the yaml entry; the on-disk
    worktree directory is left untouched (caller's responsibility)."""
    parent, head = _make_main_profile_with_history()
    worktree = isolated_config / "fork_wt"
    worktree.mkdir()
    register_fork(parent, "p_main@base", head, worktree)
    assert get_profile("p_main@base") is not None

    unregister_fork("p_main@base")
    with pytest.raises(ProfileNotFoundError):
        get_profile("p_main@base")
    # The worktree dir is still on disk — caller of
    # unregister_fork owns the git-side cleanup.
    assert worktree.exists()


def test_unregister_fork_is_idempotent_on_missing_name(isolated_config: Path) -> None:
    """``unregister_fork`` on a never-registered name is a silent
    no-op."""
    unregister_fork("never_existed_fork")  # must not raise


def test_unregister_fork_refuses_main_kind_profile(isolated_config: Path) -> None:
    """``unregister_fork`` against a kind=main profile raises an
    ``McsError`` naming the standard ``profile remove`` flow."""
    parent, _ = _make_main_profile_with_history()
    with pytest.raises(McsError) as exc:
        unregister_fork(parent.name)
    assert "not a fork" in str(exc.value)


def test_parent_repo_returns_git_repo_rooted_at_parent_data_dir(
    isolated_config: Path,
) -> None:
    """``parent_repo(fork)`` returns a ``GitRepo`` rooted at the
    parent's data-dir, not at the fork's worktree."""
    parent, head = _make_main_profile_with_history()
    worktree = isolated_config / "fork_wt"
    fork = register_fork(parent, "p_main@base", head, worktree)

    repo = parent_repo(fork)
    assert isinstance(repo, GitRepo)
    assert repo.root.resolve() == profile_data_dir(parent).resolve()


def test_parent_repo_rejects_non_fork_input(isolated_config: Path) -> None:
    """``parent_repo`` raises ``ValueError`` on a main-kind input."""
    parent, _ = _make_main_profile_with_history()
    with pytest.raises(ValueError, match="kind=fork"):
        parent_repo(parent)
