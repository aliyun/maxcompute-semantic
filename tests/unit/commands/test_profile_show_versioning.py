# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile show`` + ``remove`` extensions for the per-profile
git-versioning layer (T17).

Pins:
- main-kind versioned profile with no forks → ``version:`` line, no ``forks:`` line,
- two forks of one parent → comma-separated alphabetical ``forks:`` line,
- fork-kind profile → ``parent: <name> @ <anchor>`` line, no ``version:`` line,
- legacy (no .git/) profile → ``enable-versioning`` hint, no ``version:`` line,
- JSON envelope grows ``version`` / ``forks`` on main and ``parent`` / ``anchor`` on fork,
- ``mcs profile remove <main>`` blocks when fork rows reference it,
- ``mcs profile remove <main>`` succeeds normally with no forks,
- ``mcs profile remove <fork>`` delegates through the fork-remove path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _seed(repo: GitRepo, message: str) -> str:
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def _make_fork(runner: CliRunner, parent: Profile, label: str) -> tuple[str, str]:
    """Create a fork at HEAD; return (fork_name, anchor_sha)."""
    repo = GitRepo(profile_data_dir(parent))
    anchor = _seed(repo, f"build: {label}")
    fork_name = f"{parent.name}@{label}"
    res = runner.invoke(
        mcs_cli,
        ["profile", "fork", fork_name, "--from", anchor, "--profile", parent.name],
    )
    assert res.exit_code == 0, res.output + res.stderr
    return fork_name, anchor


# ── text-mode trailer ─────────────────────────────────────────────────────


def test_show_main_kind_with_no_forks_omits_forks_line(
    versioned_profile: Profile,
) -> None:
    """A versioned main-kind profile with zero registered forks
    emits the ``Version`` trailer and no ``Forks`` trailer."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "show", versioned_profile.name])
    assert result.exit_code == 0, result.output + result.stderr
    assert "Version" in result.output
    assert "Forks" not in result.output


def test_show_main_kind_with_two_forks_lists_them_alphabetically(
    versioned_profile: Profile,
) -> None:
    """Two forks of one parent appear comma-separated and sorted."""
    runner = CliRunner()
    # Create forks in non-alphabetical insertion order to prove the
    # sort happens at render time.
    _make_fork(runner, versioned_profile, "zebra")
    _make_fork(runner, versioned_profile, "alpha")

    result = runner.invoke(mcs_cli, ["profile", "show", versioned_profile.name])
    assert result.exit_code == 0, result.output + result.stderr
    assert "Forks" in result.output
    # Alphabetical: alpha first, zebra second.
    forks_line = next(ln for ln in result.output.splitlines() if "Forks" in ln)
    alpha_idx = forks_line.index(f"{versioned_profile.name}@alpha")
    zebra_idx = forks_line.index(f"{versioned_profile.name}@zebra")
    assert alpha_idx < zebra_idx


def test_show_fork_kind_emits_parent_line_instead_of_forks_line(
    versioned_profile: Profile,
) -> None:
    """A ``kind=fork`` profile's show output names the parent + the
    anchor short-sha + the anchor commit's subject."""
    runner = CliRunner()
    fork_name, anchor = _make_fork(runner, versioned_profile, "parent-line")

    result = runner.invoke(mcs_cli, ["profile", "show", fork_name])
    assert result.exit_code == 0, result.output + result.stderr
    assert "Parent" in result.output
    assert versioned_profile.name in result.output
    assert anchor[:12] in result.output
    # No ``Version`` trailer — the fork's identity *is* the anchor.
    # (The ``Version`` token also appears inside literals like
    # `MAXCOMPUTE_VERSION` — anchor on the leading emoji that the
    # show-cmd actually renders for the version row.)
    assert "📜 Version" not in result.output


def test_show_unversioned_profile_omits_version_line_with_hint(
    isolated_config: Path,
) -> None:
    """A legacy (no ``.git/``) profile's show output appends the
    ``enable-versioning`` hint where the version row would have
    been."""
    p = Profile(
        name="legacy",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
    )
    upsert(p)
    # Don't create the data-dir — no .git/ means unversioned.

    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "show", "legacy"])
    assert result.exit_code == 0, result.output + result.stderr
    assert "enable-versioning" in result.output
    assert "not versioned" in result.output


# ── JSON envelope shape ────────────────────────────────────────────────────


def test_show_json_format_carries_the_extra_fields_for_main(
    versioned_profile: Profile,
) -> None:
    """The JSON envelope for a main-kind profile grows ``version``
    (a dict with short_sha / full_sha / subject) and ``forks`` (a
    list of fork names)."""
    runner = CliRunner()
    _make_fork(runner, versioned_profile, "json-a")
    _make_fork(runner, versioned_profile, "json-b")

    result = runner.invoke(mcs_cli, ["-f", "json", "profile", "show", versioned_profile.name])
    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.output)
    data = payload["data"]
    assert isinstance(data["version"], dict)
    assert set(data["version"]) == {"short_sha", "full_sha", "subject"}
    assert sorted(data["forks"]) == data["forks"]
    assert f"{versioned_profile.name}@json-a" in data["forks"]
    assert f"{versioned_profile.name}@json-b" in data["forks"]
    # Fork-kind keys are not present on a main-kind payload.
    assert "parent" not in data
    assert "anchor" not in data


def test_show_json_format_carries_parent_and_anchor_for_fork(
    versioned_profile: Profile,
) -> None:
    """A fork-kind profile's JSON envelope carries ``parent``
    (the parent name) and ``anchor`` (the commit dict)."""
    runner = CliRunner()
    fork_name, anchor = _make_fork(runner, versioned_profile, "json-fork")

    result = runner.invoke(mcs_cli, ["-f", "json", "profile", "show", fork_name])
    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.output)
    data = payload["data"]
    assert data["parent"] == versioned_profile.name
    assert data["anchor"]["full_sha"] == anchor
    assert data["anchor"]["short_sha"] == anchor[:12]
    assert "build: json-fork" in data["anchor"]["subject"]
    # Main-kind keys are not present on a fork-kind payload.
    assert "version" not in data
    assert "forks" not in data


# ── ``mcs profile remove`` guards ──────────────────────────────────────────


def test_remove_main_with_live_forks_errors_naming_the_forks(
    versioned_profile: Profile,
) -> None:
    """``mcs profile remove <main>`` is blocked when fork rows in
    yaml reference the main profile. The error names the forks and
    points at ``mcs profile fork-remove``."""
    runner = CliRunner()
    _make_fork(runner, versioned_profile, "blocker")

    result = runner.invoke(mcs_cli, ["profile", "remove", versioned_profile.name, "--yes"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "live fork" in combined.lower()
    assert f"{versioned_profile.name}@blocker" in combined
    assert "mcs profile fork-remove" in combined
    # Profile is still present.
    assert versioned_profile.name in load_all_profiles()


def test_remove_main_with_no_forks_proceeds_normally(
    versioned_profile: Profile,
) -> None:
    """No fork rows → the standard remove flow runs unchanged."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "remove", versioned_profile.name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    assert versioned_profile.name not in load_all_profiles()


def test_remove_fork_kind_delegates_to_unregister_fork_path(
    versioned_profile: Profile,
) -> None:
    """``mcs profile remove <fork>`` goes through the fork-remove
    code path: the parent's worktree admin entry is swept and the
    yaml row is dropped via ``unregister_fork``."""
    runner = CliRunner()
    fork_name, _ = _make_fork(runner, versioned_profile, "delegate")
    fork = get_profile(fork_name)
    assert fork.package_path is not None
    worktree_path = Path(fork.package_path)
    assert worktree_path.exists()

    parent_repo_obj = GitRepo(profile_data_dir(versioned_profile))
    pre_worktrees = parent_repo_obj.worktree_list()
    assert any(wt.path.resolve() == worktree_path.resolve() for wt in pre_worktrees)

    result = runner.invoke(mcs_cli, ["profile", "remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    assert fork_name not in load_all_profiles()
    # The worktree dir + the parent's admin-side entry are both gone.
    assert not worktree_path.exists()
    post_worktrees = parent_repo_obj.worktree_list()
    assert all(wt.path.resolve() != worktree_path.resolve() for wt in post_worktrees)


def test_remove_fork_kind_ghost_does_not_error(
    versioned_profile: Profile,
) -> None:
    """``mcs profile remove <fork>`` on a ghost-fork (worktree dir
    hand-deleted) still falls through to a yaml-only cleanup via
    ``worktree prune`` rather than erroring."""
    runner = CliRunner()
    fork_name, _ = _make_fork(runner, versioned_profile, "ghost-rm")
    fork = get_profile(fork_name)
    assert fork.package_path is not None
    worktree_path = Path(fork.package_path)
    shutil.rmtree(worktree_path)

    result = runner.invoke(mcs_cli, ["profile", "remove", fork_name, "--yes"])
    assert result.exit_code == 0, result.output + result.stderr
    assert fork_name not in load_all_profiles()
