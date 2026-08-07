# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile reset --to <ref>`` — the rollback verb (T13).

Pins the verb's user-visible contract: ref / keyword resolution,
fork rejection, MCS_NO_VERSIONING error, no-op when target equals
HEAD, [y/N] confirmation behavior, banner shape (10-cap +
non-ancestor warn), uncommitted-state recovery commit, and
rebuild-failure bounce-back.

The primary test seeds history via ``GitRepo`` directly so the
verb's HEAD-move + tree-checkout invariants are exercised without
needing the live MaxCompute fixture. The rebuild-from-package.sql
path is covered by a separate test that writes a minimal valid
``package.sql`` dump into the working tree before committing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_package_sql_path,
)
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _seed(repo: GitRepo, message: str) -> str:
    """Drop a unique marker file in the working tree and commit
    with ``message``. Returns the full SHA."""
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def _seed_with_sql(repo: GitRepo, message: str, sql_body: str) -> str:
    """Commit a tracked-glob change *and* a ``package.sql`` dump.
    Used by the rebuild-path tests that need a valid SQL dump at
    the target commit so the post-reset ``restore_sql_to_db`` call
    has something to materialize."""
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    (repo.root / "package.sql").write_text(sql_body, encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def _minimal_valid_sql(schema_version: int = 8) -> str:
    """Tiny ``package.sql`` body that ``restore_sql_to_db`` accepts:
    the magic comment header + a single CREATE TABLE so the dump
    isn't degenerate."""
    return (
        f"-- mcs-versioning: schema_version={schema_version}\n"
        "BEGIN TRANSACTION;\n"
        "CREATE TABLE IF NOT EXISTS _marker_table (k TEXT, v TEXT);\n"
        "INSERT INTO _marker_table VALUES ('seeded', 'yes');\n"
        "COMMIT;\n"
    )


# ── ref / keyword resolution ───────────────────────────────────────────────


def test_reset_to_short_sha_moves_head(versioned_profile: Profile) -> None:
    """A short SHA on ``--to`` moves HEAD to that commit."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    a = _seed(repo, "build: A")
    _seed(repo, "build: B")  # this becomes HEAD; we'll roll back to A.

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            a[:7],
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == a


def test_reset_to_full_sha_moves_head(versioned_profile: Profile) -> None:
    """Full SHAs are accepted too (``rev_parse`` collapses both forms)."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    a = _seed(repo, "build: A-full")
    _seed(repo, "build: B-full")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "reset", "--to", a, "--profile", versioned_profile.name, "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == a


def test_reset_to_head_tilde_n(versioned_profile: Profile) -> None:
    """``HEAD~N`` walks the linear history N steps back."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    a = _seed(repo, "build: step 1")
    _seed(repo, "build: step 2")
    _seed(repo, "build: step 3")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "HEAD~2",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == a


def test_reset_keyword_last_build(versioned_profile: Profile) -> None:
    """``--to last-build`` resolves to the most-recent ``build*``
    commit, even when a ``memory:`` commit is on top of it."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: older")
    target = _seed(repo, "build: newest build")
    _seed(repo, "memory: trailing noise")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "last-build",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == target


def test_reset_keyword_last_refresh(versioned_profile: Profile) -> None:
    """``--to last-refresh`` picks the most-recent ``refresh*`` commit."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "refresh: cycle 1")
    target = _seed(repo, "refresh: cycle 2")
    _seed(repo, "memory: trailing noise")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "last-refresh",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == target


def test_reset_to_head_is_noop(versioned_profile: Profile) -> None:
    """``--to HEAD`` doesn't run the reset (target equals current
    HEAD); exits 0 with a 'nothing to do' stderr hint."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    head_before = repo.rev_parse("HEAD")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "HEAD",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.stderr
    assert repo.rev_parse("HEAD") == head_before


def test_reset_unknown_ref_errors(versioned_profile: Profile) -> None:
    """An unresolvable ref produces a non-zero exit naming the ref."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "deadbeef0123",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert "deadbeef0123" in result.output


# ── confirmation prompt ─────────────────────────────────────────────────────


def test_reset_without_yes_aborts_on_n(versioned_profile: Profile) -> None:
    """Without ``--yes``, answering 'n' to the prompt aborts the
    reset; HEAD stays put."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: A")
    _seed(repo, "build: B")
    head_before = repo.rev_parse("HEAD")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "reset", "--to", "HEAD~1", "--profile", versioned_profile.name],
        input="n\n",
    )
    assert result.exit_code == 0, result.output
    assert "aborted" in result.stderr
    assert repo.rev_parse("HEAD") == head_before


def test_reset_without_yes_proceeds_on_y(versioned_profile: Profile) -> None:
    """Without ``--yes``, answering 'y' to the prompt runs the reset."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    target = _seed(repo, "build: A")
    _seed(repo, "build: B")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "reset", "--to", target, "--profile", versioned_profile.name],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert repo.rev_parse("HEAD") == target


# ── banner shape ───────────────────────────────────────────────────────────


def test_reset_discarded_list_caps_at_ten(versioned_profile: Profile) -> None:
    """When more than 10 commits would be discarded, the banner
    lists the first 10 and appends ``... and N more.``"""
    repo = GitRepo(profile_data_dir(versioned_profile))
    target = _seed(repo, "build: base")
    for i in range(15):
        _seed(repo, f"build: step {i:02d}")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            target,
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "discard 15 commit(s)" in result.stderr
    assert "and 5 more" in result.stderr


# ── fork rejection ─────────────────────────────────────────────────────────


def test_reset_against_fork_errors(versioned_profile: Profile, tmp_path: Path) -> None:
    """``profile reset`` against a fork-kind profile errors with the
    two-option remediation naming the parent."""
    parent_repo = GitRepo(profile_data_dir(versioned_profile))
    anchor = parent_repo.rev_parse("HEAD")

    fork_dir = tmp_path / "fork-reset"
    fork_dir.mkdir()
    fork = Profile(
        name="t8test_fork_reset",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
        kind="fork",
        parent_profile=versioned_profile.name,
        git_sha=anchor,
        package_path=str(fork_dir),
    )
    upsert(fork)

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "reset", "--to", "HEAD", "--profile", fork.name, "--yes"],
    )
    assert result.exit_code != 0
    # The error mentions the parent and both remediation options.
    combined = result.output + result.stderr
    assert versioned_profile.name in combined
    assert "fork-remove" in combined


# ── env / unversioned-profile gating ───────────────────────────────────────


def test_reset_with_no_versioning_env_errors(
    versioned_profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MCS_NO_VERSIONING=1`` hard-errors the reset verb (it can't
    rebuild from a history the env opts out of)."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "HEAD",
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "MCS_NO_VERSIONING" in combined


def test_reset_unversioned_profile_errors(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile without a ``.git/`` directory errors with the
    enable-versioning remediation."""
    import json

    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    spec = json.dumps(
        {
            "name": "unversioned_reset",
            "compute_project": "acme_proj",
            "endpoint": "http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:K}",
                "access_key_secret": "${env:S}",
            },
            "sources": [{"project": "acme_proj", "schema": "default", "tables": "*"}],
        }
    )
    runner = CliRunner()
    create_result = runner.invoke(mcs_cli, ["profile", "create", "--from-spec", spec, "--no-test"])
    assert create_result.exit_code == 0, create_result.output
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)

    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            "HEAD",
            "--profile",
            "unversioned_reset",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "enable-versioning" in combined


# ── rebuild from package.sql + reindex ─────────────────────────────────────


def test_reset_rebuilds_package_db_from_target_sql(versioned_profile: Profile) -> None:
    """When the target commit carries a valid ``package.sql``, the
    post-reset on-disk ``package.db`` reflects the target's dump:
    ``restore_sql_to_db`` ran and the seeded row is queryable."""
    pdir = profile_data_dir(versioned_profile)
    repo = GitRepo(pdir)
    target = _seed_with_sql(repo, "build: with sql", _minimal_valid_sql())
    # Trailing commit so HEAD is past the SQL-bearing commit.
    _seed(repo, "memory: after-build noise")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            target,
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert repo.rev_parse("HEAD") == target

    # The rebuilt DB has the seeded row.
    db_path = pdir / "package.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT v FROM _marker_table WHERE k = ?", ("seeded",)).fetchone()
    assert row is not None and row[0] == "yes"

    # The on-disk ``package.sql`` matches the target's tree.
    assert profile_package_sql_path(versioned_profile).read_text() == _minimal_valid_sql()


def test_reset_target_without_package_sql_warns(versioned_profile: Profile) -> None:
    """When the target commit has no ``package.sql`` in its tree (the
    bare inaugural is the canonical case), the verb emits a warn
    banner and leaves the on-disk ``package.db`` untouched rather
    than failing."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    inaugural = repo.rev_parse("HEAD")
    _seed_with_sql(repo, "build: A", _minimal_valid_sql())

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            inaugural,
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert repo.rev_parse("HEAD") == inaugural
    combined = result.output + result.stderr
    assert "no ``package.sql``" in combined


# ── recovery commit for uncommitted state ──────────────────────────────────


def test_reset_captures_uncommitted_state_as_recover_commit(
    versioned_profile: Profile,
) -> None:
    """A dirty tracked file in the working tree at reset time is
    packaged as a ``recover:`` commit *before* the reset moves HEAD
    past it. The recovery commit lands in the log as the
    just-before-the-reset tombstone."""
    pdir = profile_data_dir(versioned_profile)
    repo = GitRepo(pdir)
    target = _seed(repo, "build: A")
    _seed(repo, "build: B")

    # Hand-modify a tracked .md so the reset's
    # ``commit_if_uncommitted_on_entry`` step has something to
    # snapshot.
    overview = pdir / "_overview.md"
    if not overview.exists():
        overview.write_text("seed\n", encoding="utf-8")
        repo.add_all()
        repo.commit("build: seed _overview")
        target = repo.rev_parse("HEAD~1")  # rebase target before the seed
    overview.write_text("dirty edit\n", encoding="utf-8")
    assert repo.has_uncommitted_changes()

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            target,
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert repo.rev_parse("HEAD") == target

    combined = result.output + result.stderr
    assert "captured pre-reset uncommitted state" in combined

    # The reflog has the recovery commit as the entry just before
    # the reset's own entry.
    reflog = repo._run("reflog", "show", "HEAD", check=True)
    assert "recover" in reflog.lower()


# ── rebuild failure bounce-back ─────────────────────────────────────────────


def test_reset_rebuild_failure_bounces_back(
    versioned_profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkeypatch ``restore_sql_to_db`` to raise ``PackageSqlCorrupt``
    on the first call; the verb bounces back to the pre-reset HEAD
    and surfaces the original error with exit 3."""
    from maxcompute_semantic.commands import profile_history
    from maxcompute_semantic.versioning.errors import PackageSqlCorrupt

    pdir = profile_data_dir(versioned_profile)
    repo = GitRepo(pdir)
    target = _seed_with_sql(repo, "build: target-with-sql", _minimal_valid_sql())
    pre_reset = _seed_with_sql(repo, "build: pre-reset", _minimal_valid_sql(8))

    call_count = {"n": 0}
    real_restore = profile_history.restore_sql_to_db

    def flaky(sql_path: Path, db_path: Path) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise PackageSqlCorrupt("synthetic test failure")
        return real_restore(sql_path, db_path)

    monkeypatch.setattr(profile_history, "restore_sql_to_db", flaky)

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            target,
            "--profile",
            versioned_profile.name,
            "--yes",
        ],
    )
    # Exit 3 surfaces the original-rebuild error after a successful bounce.
    assert result.exit_code == 3, result.output + result.stderr
    # HEAD is back at the pre-reset commit.
    assert repo.rev_parse("HEAD") == pre_reset
    combined = result.output + result.stderr
    assert "Bouncing back" in combined or "synthetic test failure" in combined
