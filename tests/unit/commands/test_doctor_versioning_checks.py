# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the five versioning-related ``mcs doctor`` checks
introduced in T19.

The five checks:

- ``_check_git_available`` — probes the system ``git`` binary
- ``_check_profile_versioned`` — whether the profile's data-dir is a
  git repo (warn on legacy, fail on double-orphan fork)
- ``_check_working_tree_clean`` — whether the profile's working tree
  has uncommitted changes (warn on dirty, skipped when versioning is
  not in place)
- ``_check_forks_healthy`` — system-level audit of every fork in
  profiles.yaml (warn on orphan/ghost, fail on double-orphan)
- ``_check_package_sql_parses`` — sqlite3 in-memory parse of
  ``package.sql`` with rollback-target hint on parse failure

This file also pins the renderer integration: the new ``warn`` status
is treated like ``skip`` for exit-code purposes (warn does *not* trip
exit 1) and the JSON envelope's ``summary`` field reflects the new
status in the precedence order ``fail > warn > skip > pass``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build._logic_version import INFERENCE_LOGIC_VERSION
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.commands.doctor import (
    _check_forks_healthy,
    _check_git_available,
    _check_inference_logic_current,
    _check_update_channel_reachable,
    _check_update_version_current,
    _check_package_sql_parses,
    _check_profile_versioned,
    _check_working_tree_clean,
    _run_update_check_fetch,
)
from maxcompute_semantic._internal.update_check import LatestMetadata
from maxcompute_semantic.versioning.errors import GitNotAvailable
from maxcompute_semantic.versioning.git_repo import GitRepo

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile versioning checks require the ``git`` binary",
)


def _seed(repo: GitRepo, msg: str) -> str:
    marker = repo.root / f"_marker_{msg.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(msg + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(msg)
    assert sha is not None
    return sha


def _make_fork(runner: CliRunner, parent: Profile, label: str) -> tuple[str, str]:
    repo = GitRepo(profile_data_dir(parent))
    anchor = _seed(repo, f"build: {label}")
    fork_name = f"{parent.name}@{label}"
    res = runner.invoke(
        mcs_cli,
        ["profile", "fork", fork_name, "--from", anchor, "--profile", parent.name],
    )
    assert res.exit_code == 0, res.output + res.stderr
    return fork_name, anchor


# ── _check_git_available ────────────────────────────────────────────


def test_git_available_pass_when_binary_present() -> None:
    """Happy path: ``git --version`` exits 0 and returns the version
    string. The test environment is gated by the module-level
    ``skipif`` so this should always pass when run."""
    name, status, detail = _check_git_available()
    assert name == "git_available"
    assert status == "pass"
    assert detail.startswith("git version ")


def test_git_available_warn_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``subprocess.run`` raises ``FileNotFoundError`` the check
    emits ``warn`` (not ``fail``) — since 0.10.19 the auto-commit hook
    treats missing git as a soft opt-out, so the install is degraded
    but functional. The detail still names ``MCS_NO_VERSIONING=1`` so
    the user can silence the warning."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(subprocess, "run", fake_run)
    name, status, detail = _check_git_available()
    assert status == "warn"
    assert "MCS_NO_VERSIONING" in detail


def test_git_available_fails_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "--version"], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)

    name, status, detail = _check_git_available()

    assert name == "git_available"
    assert status == "fail"
    assert "timed out" in detail


def test_git_available_fails_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = subprocess.CompletedProcess(["git", "--version"], returncode=42, stdout="", stderr="bad")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: proc)

    name, status, detail = _check_git_available()

    assert name == "git_available"
    assert status == "fail"
    assert "exited 42" in detail


# ── _check_profile_versioned ────────────────────────────────────────


def test_profile_versioned_pass_on_main_with_commits(
    versioned_profile: Profile,
) -> None:
    """A fresh ``mcs profile create`` leaves the data-dir as a git
    repo with the inaugural commit; the check should pass with the
    HEAD short-sha and subject in the detail."""
    name, status, detail = _check_profile_versioned(versioned_profile)
    assert name == "profile_versioned"
    assert status == "pass"
    assert versioned_profile.name in detail


def test_profile_versioned_warn_when_data_dir_has_no_git(
    isolated_config: Path,
) -> None:
    """A profile whose data-dir has no ``.git/`` (legacy / pre-T6)
    warns with the ``enable-versioning`` remediation."""
    p = Profile(
        name="legacy",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
    )
    upsert(p)
    name, status, detail = _check_profile_versioned(p)
    assert status == "warn"
    assert "enable-versioning" in detail


def test_profile_versioned_fail_on_double_orphan_fork(
    isolated_config: Path,
) -> None:
    """A fork-kind profile whose ``parent_profile`` is missing from
    profiles.yaml fails with a 'double-orphan' detail."""
    fork = Profile(
        name="ghost@x",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
        kind="fork",
        parent_profile="ghost",
        git_sha="0" * 40,
        package_path=isolated_config / "ghost_wt",
    )
    upsert(fork)
    name, status, detail = _check_profile_versioned(fork)
    assert status == "fail"
    assert "double-orphan" in detail


def test_profile_versioned_skip_when_no_resolved_profile() -> None:
    """When the upstream profile_resolution check failed, the
    versioned check skips."""
    name, status, detail = _check_profile_versioned(None)
    assert status == "skip"


# ── _check_working_tree_clean ───────────────────────────────────────


def test_working_tree_clean_pass_when_no_pending_changes(
    versioned_profile: Profile,
) -> None:
    """A fresh profile-create leaves the working tree clean."""
    name, status, detail = _check_working_tree_clean(versioned_profile, "pass")
    assert name == "working_tree_clean"
    assert status == "pass"


def test_working_tree_clean_warn_when_dirty(
    versioned_profile: Profile,
) -> None:
    """An uncommitted file in the data-dir surfaces as a warn."""
    pdir = profile_data_dir(versioned_profile)
    (pdir / "_dirty.md").write_text("scratch\n", encoding="utf-8")
    name, status, detail = _check_working_tree_clean(versioned_profile, "pass")
    assert status == "warn"
    assert "uncommitted" in detail


def test_working_tree_clean_skip_when_prereq_did_not_pass(
    versioned_profile: Profile,
) -> None:
    """When the upstream profile_versioned check warned or failed
    the working_tree check is skipped."""
    _, status, _ = _check_working_tree_clean(versioned_profile, "warn")
    assert status == "skip"


def test_working_tree_clean_skip_when_profile_missing() -> None:
    assert _check_working_tree_clean(None, "pass") == (
        "working_tree_clean",
        "skip",
        "skipped: prerequisite failed",
    )


def test_working_tree_clean_skip_when_fork_parent_missing(isolated_config: Path) -> None:
    fork = Profile(
        name="ghost@x",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
        kind="fork",
        parent_profile="ghost",
        git_sha="0" * 40,
        package_path=isolated_config / "ghost_wt",
    )

    assert _check_working_tree_clean(fork, "pass") == (
        "working_tree_clean",
        "skip",
        "skipped: fork parent missing from profiles.yaml",
    )


def test_working_tree_clean_fails_when_repo_status_raises(
    versioned_profile: Profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenRepo:
        def __init__(self, root):
            self.root = root

        def has_uncommitted_changes(self):
            raise RuntimeError("status broke")

    monkeypatch.setattr("maxcompute_semantic.versioning.git_repo.GitRepo", BrokenRepo)

    name, status, detail = _check_working_tree_clean(versioned_profile, "pass")

    assert name == "working_tree_clean"
    assert status == "fail"
    assert "status broke" in detail


# ── _check_forks_healthy ─────────────────────────────────────────────


def test_forks_healthy_pass_when_no_forks_registered(
    versioned_profile: Profile,
) -> None:
    """profiles.yaml with no fork rows → pass with 'no forks'."""
    name, status, detail = _check_forks_healthy()
    assert name == "forks_healthy"
    assert status == "pass"
    assert "no forks" in detail


def test_forks_healthy_pass_with_one_healthy_fork(
    versioned_profile: Profile,
) -> None:
    """A live fork whose worktree dir exists → all-healthy pass."""
    runner = CliRunner()
    _make_fork(runner, versioned_profile, "alive")
    name, status, detail = _check_forks_healthy()
    assert status == "pass"
    assert "1 fork" in detail


def test_forks_healthy_warn_on_ghost_fork(
    versioned_profile: Profile,
) -> None:
    """Hand-deleting the fork's worktree dir produces a ghost; the
    check warns and points at ``mcs profile fork-list``."""
    runner = CliRunner()
    fork_name, _ = _make_fork(runner, versioned_profile, "ghost")
    fork = get_profile(fork_name)
    assert fork.package_path is not None
    shutil.rmtree(fork.package_path)
    _, status, detail = _check_forks_healthy()
    assert status == "warn"
    assert "ghost" in detail
    assert "fork-list" in detail


def test_forks_healthy_fail_on_double_orphan(
    isolated_config: Path,
) -> None:
    """A fork row whose parent name is missing from profiles.yaml
    yields a fail with 'double-orphan' in the detail."""
    fork = Profile(
        name="ghost@x",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
        kind="fork",
        parent_profile="ghost",
        git_sha="0" * 40,
        package_path=isolated_config / "ghost_wt",
    )
    upsert(fork)
    _, status, detail = _check_forks_healthy()
    assert status == "fail"
    assert "double-orphan" in detail


def test_forks_healthy_skips_when_profiles_yaml_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maxcompute_semantic.auth.profile_store.load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("yaml broke")),
    )

    name, status, detail = _check_forks_healthy()

    assert name == "forks_healthy"
    assert status == "skip"
    assert "yaml broke" in detail


def test_forks_healthy_warns_when_parent_repo_missing(isolated_config: Path) -> None:
    parent = Profile(
        name="parent",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
    )
    fork = Profile(
        name="parent@old",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=parent.sources,
        kind="fork",
        parent_profile=parent.name,
        git_sha="0" * 40,
        package_path=isolated_config / "fork-wt",
    )
    upsert(parent)
    upsert(fork)
    fork.package_path.mkdir(parents=True)

    name, status, detail = _check_forks_healthy()

    assert name == "forks_healthy"
    assert status == "warn"
    assert "orphan" in detail


def test_forks_healthy_skips_when_git_unavailable_on_merge_base(
    versioned_profile: Profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fork = Profile(
        name="parent@old",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:AK_ID}", "${env:AK_SECRET}"),
        sources=versioned_profile.sources,
        kind="fork",
        parent_profile=versioned_profile.name,
        git_sha="0" * 40,
        package_path=profile_data_dir(versioned_profile) / "fork-wt",
    )
    fork.package_path.mkdir()
    upsert(fork)

    def fake_merge_base(self, *args, **kwargs):
        raise GitNotAvailable("git missing")

    monkeypatch.setattr(GitRepo, "merge_base_is_ancestor", fake_merge_base)

    name, status, detail = _check_forks_healthy()

    assert name == "forks_healthy"
    assert status == "skip"
    assert "git binary not available" in detail


# ── _check_package_sql_parses ───────────────────────────────────────


def test_package_sql_parses_skip_when_absent(
    versioned_profile: Profile,
) -> None:
    """No package.sql on disk → skipped (the profile may pre-date
    versioning or never have been built)."""
    pdir = profile_data_dir(versioned_profile)
    sql_path = pdir / "package.sql"
    if sql_path.exists():
        sql_path.unlink()
    name, status, detail = _check_package_sql_parses(versioned_profile)
    assert name == "package_sql_parses"
    assert status == "skip"


def test_package_sql_parses_pass_on_valid_sql(
    versioned_profile: Profile,
) -> None:
    """A package.sql that ``sqlite3.executescript`` digests cleanly
    passes; the detail line names the line count."""
    pdir = profile_data_dir(versioned_profile)
    (pdir / "package.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);\nINSERT INTO t VALUES (1);\n",
        encoding="utf-8",
    )
    _, status, detail = _check_package_sql_parses(versioned_profile)
    assert status == "pass"
    assert "package.sql parses cleanly" in detail


def test_package_sql_parses_fail_on_corrupt_sql_with_rollback_hint(
    versioned_profile: Profile,
) -> None:
    """A package.sql that sqlite refuses with DatabaseError surfaces
    as a fail. When the profile is versioned and the prior commit
    touched package.sql, the remediation names that commit's
    short-sha as the rollback target."""
    pdir = profile_data_dir(versioned_profile)
    sql_path = pdir / "package.sql"
    repo = GitRepo(pdir)
    # First commit: a valid package.sql (the rollback target).
    sql_path.write_text("CREATE TABLE t (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    repo.add_all()
    rollback_sha = repo.commit("good package.sql")
    assert rollback_sha is not None
    # Second commit: corrupting package.sql.
    sql_path.write_text("NOT VALID SQL ;;;\n", encoding="utf-8")
    repo.add_all()
    repo.commit("bad package.sql")

    _, status, detail = _check_package_sql_parses(versioned_profile)
    assert status == "fail"
    assert "failed to parse" in detail
    assert "mcs profile reset --to" in detail
    assert rollback_sha[:7] in detail or rollback_sha[:12] in detail


def test_package_sql_parses_fails_when_file_unreadable(
    versioned_profile: Profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdir = profile_data_dir(versioned_profile)
    sql_path = pdir / "package.sql"
    sql_path.write_text("CREATE TABLE t (id INTEGER);\n", encoding="utf-8")

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args, **kwargs):
        if self == sql_path:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    name, status, detail = _check_package_sql_parses(versioned_profile)

    assert name == "package_sql_parses"
    assert status == "fail"
    assert "permission denied" in detail


# ── _check_inference_logic_current ──────────────────────────────────


def _stamp_logic_version(profile: Profile, version: int | None) -> None:
    """Open the profile's package.db and stamp the inference-logic
    version (creating package.db if it doesn't exist).

    ``version=None`` simulates a pre-feature profile: a package.db is
    created without ever writing the ``inference_logic_version`` key.
    """
    pdir = profile_data_dir(profile)
    db = PackageDB(pdir / "package.db")
    try:
        if version is not None:
            db.set_inference_logic_version(version)
    finally:
        db.close()


def test_inference_logic_check_pass_when_no_built_profile_exists(
    versioned_profile: Profile,
) -> None:
    """A registered profile with no ``package.db`` (the user created
    the profile but hasn't built yet) is not stale — it's simply
    unbuilt. The check should pass with a 'no built profiles' line."""
    pdir = profile_data_dir(versioned_profile)
    db_path = pdir / "package.db"
    if db_path.exists():
        db_path.unlink()

    name, status, detail = _check_inference_logic_current()
    assert name == "inference_logic_current"
    assert status == "pass"
    assert "no built profiles" in detail


def test_inference_logic_check_passes_when_current(
    versioned_profile: Profile,
) -> None:
    """A built profile whose stamp equals the CLI's
    :data:`INFERENCE_LOGIC_VERSION` is current — the check passes
    and the detail names the version on disk."""
    _stamp_logic_version(versioned_profile, INFERENCE_LOGIC_VERSION)

    _, status, detail = _check_inference_logic_current()
    assert status == "pass"
    assert f"v{INFERENCE_LOGIC_VERSION}" in detail


def test_inference_logic_check_warns_on_stale(
    versioned_profile: Profile,
) -> None:
    """A built profile whose stamp is behind the CLI's current logic
    version is stale; the check warns and the remediation names
    ``mcs build --refresh`` plus the profile's name."""
    _stamp_logic_version(versioned_profile, INFERENCE_LOGIC_VERSION - 1)

    _, status, detail = _check_inference_logic_current()
    assert status == "warn"
    assert "mcs build --refresh" in detail
    assert versioned_profile.name in detail
    assert "offline" in detail or "no MaxCompute" in detail


def test_inference_logic_check_warns_on_missing_stamp(
    versioned_profile: Profile,
) -> None:
    """A pre-feature profile (``package.db`` exists but no
    ``inference_logic_version`` row in package_settings) reads back
    as 0, which sorts below any current version and triggers the
    same warn path as an explicit stale stamp."""
    _stamp_logic_version(versioned_profile, None)

    _, status, detail = _check_inference_logic_current()
    assert status == "warn"
    assert versioned_profile.name in detail


def test_inference_logic_check_skips_when_profiles_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "maxcompute_semantic.auth.profile_store.load_all",
        lambda: (_ for _ in ()).throw(RuntimeError("yaml broke")),
    )

    name, status, detail = _check_inference_logic_current()

    assert name == "inference_logic_current"
    assert status == "skip"
    assert "yaml broke" in detail


def test_inference_logic_check_ignores_unopenable_package_db(
    versioned_profile: Profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdir = profile_data_dir(versioned_profile)
    (pdir / "package.db").write_bytes(b"not sqlite")

    class BrokenPackageDB:
        def __init__(self, path):
            raise RuntimeError("cannot open db")

    monkeypatch.setattr("maxcompute_semantic.build.storage.PackageDB", BrokenPackageDB)

    name, status, detail = _check_inference_logic_current()

    assert name == "inference_logic_current"
    assert status == "pass"
    assert "no built profiles" in detail


def test_update_channel_and_version_render_failure_and_skip() -> None:
    fetch_result = (None, "HTTP 500 from https://example.test/latest.json")

    channel = _check_update_channel_reachable(fetch_result)
    version = _check_update_version_current(fetch_result)

    assert channel[0] == "update_channel"
    assert channel[1] == "fail"
    assert "HTTP 500" in (channel[2] or "")
    assert version == (
        "update_version",
        "skip",
        "skipped: update_channel check failed (see line above)",
    )


def test_update_version_pass_and_upgrade_available(monkeypatch: pytest.MonkeyPatch) -> None:
    md_current = LatestMetadata(
        schema_version=1,
        latest_version="0.1.0",
        min_supported="0.0.1",
        disabled=[],
        wheel_url="https://example.test/mcs.whl",
        sha256="a" * 64,
        released_at="2026-01-01T00:00:00Z",
        notice="",
    )
    md_newer = LatestMetadata(
        schema_version=1,
        latest_version="999.0.0",
        min_supported="0.0.1",
        disabled=[],
        wheel_url="https://example.test/mcs.whl",
        sha256="a" * 64,
        released_at="2026-01-01T00:00:00Z",
        notice="upgrade now",
    )

    assert _check_update_version_current((md_current, ""))[1] == "pass"
    result = _check_update_version_current((md_newer, ""))
    assert result[1] == "skip"
    assert "upgrade available" in (result[2] or "")
    assert "upgrade now" in (result[2] or "")


def test_run_update_check_fetch_classifies_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    monkeypatch.setattr(
        "maxcompute_semantic.commands.doctor.fetch_latest_metadata",
        lambda: None,
    )

    def fake_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://example.test/latest.json",
            code=503,
            msg="unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    md, err = _run_update_check_fetch()

    assert md is None
    assert "HTTP 503" in err


def test_run_update_check_fetch_classifies_malformed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit):
            return b"{not json"

    monkeypatch.setattr(
        "maxcompute_semantic.commands.doctor.fetch_latest_metadata",
        lambda: None,
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())

    md, err = _run_update_check_fetch()

    assert md is None
    assert "unparseable response" in err


# ── doctor_cmd integration: warn status renders & exits 0 ───────────


def test_doctor_json_envelope_carries_new_check_names(
    versioned_profile: Profile,
) -> None:
    """Running ``mcs doctor --offline`` includes every new check
    name in the JSON envelope's ``checks`` array."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "doctor", "--offline", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.output)
    data = payload["data"]
    names = {c["name"] for c in data["checks"]}
    assert {
        "git_available",
        "profile_versioned",
        "working_tree_clean",
        "forks_healthy",
        "package_sql_parses",
        "inference_logic_current",
    }.issubset(names)


def test_doctor_warn_status_does_not_trip_exit_one(
    versioned_profile: Profile,
) -> None:
    """A dirty working tree (warn) must not flip the exit code to 1
    — warn is informational, parallel to skip."""
    pdir = profile_data_dir(versioned_profile)
    (pdir / "_dirty.md").write_text("scratch\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["doctor", "--offline", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output + result.stderr


def test_doctor_summary_reports_warn_count(
    versioned_profile: Profile,
) -> None:
    """The text-mode summary tally surfaces ``N warned`` when any
    check returned warn."""
    pdir = profile_data_dir(versioned_profile)
    (pdir / "_dirty.md").write_text("scratch\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["doctor", "--offline", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert "warned" in result.output
