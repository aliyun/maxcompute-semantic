# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile fork-remove`` — the dual of ``mcs profile fork`` (T16).

Pins:
- happy path: yaml entry + worktree dir + admin-side entry all gone,
- not-a-fork rejection (main-kind profile),
- unknown name errors with a remediation,
- abort on the [y/N] confirmation prompt,
- ghost-fork: worktree dir hand-deleted; remove invokes ``git
  worktree prune`` and drops the yaml row,
- double-orphan: parent yaml gone too; yaml-only cleanup runs and
  the worktree dir is left in place with a warning,
- success message includes the parent name and the short anchor SHA,
- parent's HEAD is unchanged by the operation,
- ``unregister_fork`` is called with ``delete_data_dir=False``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir, profiles_yaml_path
from maxcompute_semantic.auth.errors import ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _seed(repo: GitRepo, message: str) -> str:
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def _make_fork(runner: CliRunner, versioned_profile: Profile, label: str) -> tuple[str, str, Path]:
    """Create a fork at HEAD and return (fork_name, anchor_sha, worktree_path)."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, f"build: {label}")
    fork_name = f"{versioned_profile.name}@{label}"
    res = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            fork_name,
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert res.exit_code == 0, res.output + res.stderr
    fork = get_profile(fork_name)
    assert fork.package_path is not None
    return fork_name, anchor, Path(fork.package_path)


# ── happy path ────────────────────────────────────────────────────────────


def test_fork_remove_drops_yaml_and_worktree(versioned_profile: Profile) -> None:
    """Happy path: the fork's yaml entry is gone, the worktree
    directory is gone, and the parent's ``.git/worktrees/<short>/``
    admin entry is swept (``git worktree remove`` handles both)."""
    runner = CliRunner()
    fork_name, _, worktree_path = _make_fork(runner, versioned_profile, "removal")

    parent_dir = profile_data_dir(versioned_profile)
    parent_repo_obj = GitRepo(parent_dir)
    pre_worktrees = parent_repo_obj.worktree_list()
    assert any(wt.path.resolve() == worktree_path.resolve() for wt in pre_worktrees)

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    combined = result.output + result.stderr
    assert "removed" in combined.lower()

    with pytest.raises(ProfileNotFoundError):
        get_profile(fork_name)
    assert fork_name not in load_all_profiles()
    assert not worktree_path.exists()

    post_worktrees = parent_repo_obj.worktree_list()
    assert all(wt.path.resolve() != worktree_path.resolve() for wt in post_worktrees)


def test_fork_remove_success_message_names_parent_and_anchor(
    versioned_profile: Profile,
) -> None:
    """The user-facing success line includes the parent name and the
    short anchor SHA so the operator can locate the matching commit
    in ``mcs profile log`` immediately."""
    runner = CliRunner()
    fork_name, anchor, _ = _make_fork(runner, versioned_profile, "evidence")

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    assert result.exit_code == 0
    combined = result.output + result.stderr
    assert versioned_profile.name in combined
    # Either the success-line names the short SHA or the upfront
    # context banner does. Both flow through the same invocation.
    assert anchor[:12] in combined


def test_fork_remove_leaves_parent_history_unchanged(
    versioned_profile: Profile,
) -> None:
    """The parent's HEAD is invariant across the fork-remove
    invocation (the fork was an auxiliary view, never a commit on
    the parent's graph)."""
    runner = CliRunner()
    fork_name, _, _ = _make_fork(runner, versioned_profile, "leaves")

    parent_repo_obj = GitRepo(profile_data_dir(versioned_profile))
    pre_head = parent_repo_obj.rev_parse("HEAD")
    runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    post_head = parent_repo_obj.rev_parse("HEAD")
    assert pre_head == post_head


# ── rejections ────────────────────────────────────────────────────────────


def test_fork_remove_on_unknown_name_errors_with_remediation(
    isolated_config: Path,
) -> None:
    """Removing a profile name that doesn't exist exits non-zero and
    the error message points at ``mcs profile list``."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "fork-remove", "no-such-fork", "--yes"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "no-such-fork" in combined
    assert "mcs profile list" in combined


def test_fork_remove_against_main_kind_profile_errors(
    versioned_profile: Profile,
) -> None:
    """Running fork-remove against a main-kind profile is rejected
    with the "use ``mcs profile remove`` for main-kind" remediation."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "fork-remove", versioned_profile.name, "--yes"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "not a fork" in combined.lower() or "kind=main" in combined.lower()
    assert "mcs profile remove" in combined


# ── confirmation prompt ───────────────────────────────────────────────────


def test_fork_remove_aborts_on_default_no_confirmation(
    versioned_profile: Profile,
) -> None:
    """Without ``--yes``, the verb prompts ``[y/N]`` and a no-input
    answer aborts with exit 0 and leaves state unchanged."""
    runner = CliRunner()
    fork_name, _, worktree_path = _make_fork(runner, versioned_profile, "abort")

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name], input="\n")
    assert result.exit_code == 0
    combined = result.output + result.stderr
    assert "aborted" in combined.lower()
    # State unchanged.
    assert fork_name in load_all_profiles()
    assert worktree_path.exists()


# ── ghost-fork (worktree dir already deleted) ─────────────────────────────


def test_fork_remove_ghost_runs_prune_and_drops_yaml(
    versioned_profile: Profile,
) -> None:
    """The ghost-fork removal path: the worktree was hand-deleted,
    the verb runs ``git worktree prune`` to sweep the parent's
    admin-side entry, and then drops the yaml row."""
    runner = CliRunner()
    fork_name, _, worktree_path = _make_fork(runner, versioned_profile, "ghost")

    shutil.rmtree(worktree_path)

    admin_root = profile_data_dir(versioned_profile) / ".git" / "worktrees"
    pre_admin_children = (
        [c for c in admin_root.iterdir() if c.is_dir()] if admin_root.exists() else []
    )
    assert pre_admin_children, (
        "the parent's admin-side worktree dir should still hold the "
        "fork's entry before fork-remove runs."
    )

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    combined = (result.output + result.stderr).lower()
    assert "ghost" in combined or "prune" in combined
    assert fork_name not in load_all_profiles()

    # The admin-side entry whose ``gitdir`` pointed at the removed
    # worktree is gone.
    if admin_root.exists():
        for child in admin_root.iterdir():
            gd = child / "gitdir"
            if gd.exists():
                target = gd.read_text(encoding="utf-8").strip()
                assert Path(target).parent.resolve() != worktree_path.resolve()


# ── double-orphan (parent yaml gone too) ──────────────────────────────────


def test_fork_remove_double_orphan_runs_yaml_only_cleanup(
    versioned_profile: Profile,
) -> None:
    """The parent yaml row is hand-removed but the fork persists.
    The verb falls back to a yaml-only unregister and leaves the
    on-disk worktree in place with a warning."""
    from ruamel.yaml import YAML

    runner = CliRunner()
    fork_name, _, worktree_path = _make_fork(runner, versioned_profile, "double-orphan")

    yaml_path = profiles_yaml_path()
    yaml = YAML(typ="rt")
    with yaml_path.open("r", encoding="utf-8") as f:
        doc = yaml.load(f)
    profiles_map = doc.get("profiles", {})
    del profiles_map[versioned_profile.name]
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(doc, f)

    with pytest.raises(ProfileNotFoundError):
        get_profile(versioned_profile.name)

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    combined = (result.output + result.stderr).lower()
    assert "parent" in combined and "gone" in combined
    assert fork_name not in load_all_profiles()
    # The worktree dir is left in place because there's no parent
    # repo to ``worktree remove`` against.
    assert worktree_path.exists()

    # Cleanup so the temp-HOME doesn't carry the orphan dir forward.
    shutil.rmtree(worktree_path)


# ── unregister-fork wiring ────────────────────────────────────────────────


def test_fork_remove_calls_unregister_with_delete_data_dir_false(
    versioned_profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The yaml-side cleanup is delegated to
    ``versioning.forks.unregister_fork`` which in turn calls
    ``profile_store.remove(name, delete_data_dir=False)`` — the
    data-dir cleanup is git's responsibility, not the yaml-store's
    rmtree."""
    captured: list[tuple[str, bool]] = []

    runner = CliRunner()
    fork_name, _, _ = _make_fork(runner, versioned_profile, "wiring")

    import maxcompute_semantic.versioning.forks as forks_mod

    original_remove = forks_mod.remove_profile_yaml_entry

    def spy(name: str, delete_data_dir: bool = True) -> None:
        captured.append((name, delete_data_dir))
        original_remove(name, delete_data_dir=delete_data_dir)

    monkeypatch.setattr(forks_mod, "remove_profile_yaml_entry", spy)

    result = runner.invoke(mcs_cli, ["profile", "fork-remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    assert (fork_name, False) in captured, (
        f"unregister_fork should call remove(..., delete_data_dir=False); captured: {captured!r}"
    )
