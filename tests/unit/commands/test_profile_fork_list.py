"""``mcs profile fork-list`` — orphan / ghost / healthy state
detection and the self-heal pass (T15).

Pins:
- the empty-list "no forks" message,
- the ``--profile`` filter on parent name,
- the three states (healthy / ORPHAN / GHOST),
- the ghost self-heal that prunes the parent's worktree-admin entry
  and drops the yaml row (default-on; ``--no-self-heal`` opts out),
- the no-parent ORPHAN sub-case (the parent profile was removed),
- the orphan-after-reset case (anchor isn't an ancestor of HEAD
  after the parent moved backward),
- the JSON envelope shape.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
from maxcompute_semantic.auth.profile_store import remove as remove_profile
from maxcompute_semantic.auth.profile_store import upsert as upsert_profile
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


# ── empty / filter ─────────────────────────────────────────────────────────


def test_fork_list_with_no_forks_emits_empty_message(isolated_config: Path) -> None:
    """When no forks are registered, the verb exits 0 and tells the
    user there are no forks."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result.exit_code == 0, result.output
    combined = result.output + result.stderr
    assert "no forks" in combined.lower()


def test_fork_list_with_unknown_parent_filter_returns_empty(
    versioned_profile: Profile,
) -> None:
    """``--profile ghost-parent`` where ghost-parent isn't a fork's
    parent returns "no forks for that parent" (not an error)."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "fork-list", "--profile", "this-profile-doesnt-exist"]
    )
    assert result.exit_code == 0
    combined = result.output + result.stderr
    assert "no forks" in combined.lower()


# ── healthy ────────────────────────────────────────────────────────────────


def test_fork_list_reports_healthy_when_anchor_is_ancestor_of_head(
    versioned_profile: Profile,
) -> None:
    """A fork whose anchor is the parent's current HEAD shows
    ``healthy`` in the STATE column."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")
    _seed(repo, "memory: trailing")  # parent moves past the anchor

    runner = CliRunner()
    res_fork = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@anchor",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert res_fork.exit_code == 0, res_fork.output + res_fork.stderr

    result = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result.exit_code == 0, result.output + result.stderr
    assert "healthy" in result.output
    assert f"{versioned_profile.name}@anchor" in result.output


# ── orphan (no-parent sub-case) ────────────────────────────────────────────


def test_fork_list_reports_orphan_when_parent_yaml_is_gone(
    versioned_profile: Profile, tmp_path: Path
) -> None:
    """If the parent profile's yaml entry is gone, the fork is
    flagged ORPHAN with a "parent profile is gone" detail."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = repo.rev_parse("HEAD")

    # Manually register a fork whose parent_profile points at a
    # name that doesn't exist in profiles.yaml.
    fork_dir = tmp_path / "orphan-fork"
    fork_dir.mkdir()
    fork = Profile(
        name="t15_no_parent",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
        kind="fork",
        parent_profile="ghost-parent-never-existed",
        git_sha=anchor,
        package_path=str(fork_dir),
    )
    upsert_profile(fork)

    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result.exit_code == 0
    assert "ORPHAN" in result.output
    assert "t15_no_parent" in result.output
    assert "parent profile is gone" in result.output


# ── orphan (anchor-not-an-ancestor sub-case) ───────────────────────────────


def test_fork_list_reports_orphan_after_parent_reset_past_anchor(
    versioned_profile: Profile,
) -> None:
    """After the parent is reset *backward* past the fork's anchor,
    the anchor SHA is no longer an ancestor of the parent's HEAD;
    fork-list flags the fork as ORPHAN."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    base = _seed(repo, "build: base")
    anchor = _seed(repo, "build: later")  # the fork will anchor here

    runner = CliRunner()
    res_fork = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@later",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert res_fork.exit_code == 0, res_fork.output + res_fork.stderr

    # Reset the parent backward to ``base``; the fork's anchor
    # (``later``) is now ahead of HEAD and not an ancestor.
    repo.reset_hard(base)
    # Sanity: the anchor is unreachable from HEAD's walk-back.
    assert not repo.merge_base_is_ancestor(anchor, "HEAD")

    result = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result.exit_code == 0, result.output + result.stderr
    assert "ORPHAN" in result.output
    assert f"{versioned_profile.name}@later" in result.output
    assert "HEAD-walk-back" in result.output


# ── ghost + self-heal ──────────────────────────────────────────────────────


def test_fork_list_self_heals_ghost_by_default(versioned_profile: Profile) -> None:
    """A fork whose worktree directory was hand-deleted is flagged
    GHOST; the default-on self-heal sweeps the yaml entry on the
    same invocation, and a follow-up listing no longer shows it."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    runner = CliRunner()
    res_fork = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@ghost",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert res_fork.exit_code == 0, res_fork.output + res_fork.stderr

    fork = get_profile(f"{versioned_profile.name}@ghost")
    assert fork.package_path is not None
    shutil.rmtree(fork.package_path)

    # First invocation: ghost row is reported + self-healed.
    result = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result.exit_code == 0, result.output + result.stderr
    assert "GHOST" in result.output
    assert f"{versioned_profile.name}@ghost" in result.output
    assert "self-healed" in result.output.lower()
    # The yaml entry is gone after the self-heal.
    assert f"{versioned_profile.name}@ghost" not in load_all_profiles()

    # Second invocation: no row for the swept fork.
    result2 = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert result2.exit_code == 0
    assert f"{versioned_profile.name}@ghost" not in result2.output


def test_fork_list_no_self_heal_preserves_ghost_yaml_entry(
    versioned_profile: Profile,
) -> None:
    """``--no-self-heal`` reports the GHOST row but the yaml entry
    survives — the operator can come back later (or run the verb
    again without the flag) for the actual cleanup."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    runner = CliRunner()
    res_fork = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@audit",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert res_fork.exit_code == 0, res_fork.output + res_fork.stderr

    fork = get_profile(f"{versioned_profile.name}@audit")
    assert fork.package_path is not None
    shutil.rmtree(fork.package_path)

    # Audit-mode: report without side effect.
    res_audit = runner.invoke(mcs_cli, ["profile", "fork-list", "--no-self-heal"])
    assert res_audit.exit_code == 0
    assert "GHOST" in res_audit.output
    assert f"{versioned_profile.name}@audit" in res_audit.output
    # Yaml entry preserved.
    assert f"{versioned_profile.name}@audit" in load_all_profiles()

    # Follow-up without the flag does the cleanup.
    res_heal = runner.invoke(mcs_cli, ["profile", "fork-list"])
    assert res_heal.exit_code == 0
    assert "self-healed" in res_heal.output.lower()
    assert f"{versioned_profile.name}@audit" not in load_all_profiles()


# ── filter by parent ───────────────────────────────────────────────────────


def test_fork_list_filters_by_parent_name(versioned_profile: Profile) -> None:
    """``--profile <name>`` restricts the listing to that parent's
    forks. Forks of other parents don't appear."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    runner = CliRunner()
    runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@mine",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )

    # An unrelated parent + fork that won't be in the filtered list.
    other = Profile(
        name="t15_other_parent",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
    )
    upsert_profile(other)
    other_fork = Profile(
        name="t15_other_fork",
        compute_project=other.compute_project,
        endpoint=other.endpoint,
        auth=other.auth,
        sources=other.sources,
        kind="fork",
        parent_profile=other.name,
        git_sha="b" * 40,
        package_path=str(parent_dir.parent / "other-fork-dir"),
    )
    upsert_profile(other_fork)

    result = runner.invoke(mcs_cli, ["profile", "fork-list", "--profile", versioned_profile.name])
    assert result.exit_code == 0, result.output + result.stderr
    assert f"{versioned_profile.name}@mine" in result.output
    assert "t15_other_fork" not in result.output

    # Cleanup: drop the unrelated parent (its data dir is empty).
    remove_profile(other.name, delete_data_dir=False)
    remove_profile(other_fork.name, delete_data_dir=False)


# ── JSON envelope shape ────────────────────────────────────────────────────


def test_fork_list_json_envelope_carries_forks_and_totals(
    versioned_profile: Profile,
) -> None:
    """``-f json profile fork-list`` emits a success envelope whose data
    carries ``forks: [...]`` and ``totals: {...}``."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    runner = CliRunner()
    runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@json",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )

    result = runner.invoke(mcs_cli, ["-f", "json", "profile", "fork-list"])
    assert result.exit_code == 0, result.output + result.stderr
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert "forks" in payload and "totals" in payload
    assert isinstance(payload["forks"], list)
    for entry in payload["forks"]:
        assert set(entry.keys()) == {"name", "parent", "anchor", "state", "detail"}
    assert set(payload["totals"].keys()) == {
        "total",
        "healthy",
        "orphan",
        "ghost",
        "self_healed",
    }
