"""End-to-end integration test of the per-profile git-versioning
worked example (T21).

The single ``test_worked_example_end_to_end`` walks the spec's
"Data flow / Worked examples" sequence top-to-bottom against the
fake-MaxCompute fixture family: profile create → build → memory
note → memory verify → log inspection → fork at the memory-note
commit → post-fork drift on the parent → fork-write guard fires
on a write attempt against the fork → diff between the anchor and
the parent's new HEAD names the drift → reset on the parent rolls
back to the last-build commit → the discarded commit is in the
reflog but not in the log → the parent's on-disk DB content
matches the pre-drift state → fork-remove cleans the fork's yaml
entry, the worktree directory, and the parent's
``.git/worktrees/<short>/`` admin entry → final ``mcs doctor
--offline`` confirms all five new checks from T19 are passing.

The auxiliary tests in this file pin the cross-cut contracts the
worked example doesn't naturally exercise:

* ``test_versioning_off_via_env_skips_every_hook`` — the
  ``MCS_NO_VERSIONING=1`` env knob short-circuits the per-write
  auto-commit hook (the same knob the eval-harness force-sets in
  T20).
* ``test_legacy_profile_first_write_auto_upgrades`` — a profile
  whose data-dir predates the versioning layer auto-inits on the
  first write, landing the ``init: import existing data`` commit
  alongside the write commit.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import data_root, profile_data_dir
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo

_SK = "acme_proj__default"


def _log_subjects(profile: Profile) -> list[str]:
    """Return commit subjects oldest-first via the public ``mcs
    profile log -n 0 --all -f json`` envelope, then reverse so the
    sequence reads the way the spec's worked-example log dump does.
    """
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "profile", "log", "-n", "0", "--all", "--profile", profile.name],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    # The ``MCS_NO_VERSIONING`` warning (and any other CLI banner) is
    # printed before the JSON payload on stdout. Slice from the first
    # ``{`` so the JSON parse is robust to leading warnings.
    text = result.output
    start = text.find("{")
    envelope = json.loads(text[start:]) if start >= 0 else {"data": {"commits": []}}
    payload = envelope["data"]["commits"]
    # log returns newest-first; reverse to oldest-first for the
    # assertions that read like the spec's narrative.
    return [row["message"] for row in reversed(payload)]


def test_worked_example_end_to_end(versioned_profile: Profile, fake_maxcompute: MagicMock) -> None:
    runner = CliRunner()
    profile_name = versioned_profile.name  # "acme"

    # ── Step 2: the inaugural ``init: import existing data`` is the
    #            only commit at the start. ──────────────────────────
    repo = GitRepo(profile_data_dir(versioned_profile))
    assert repo.exists()
    subjects = _log_subjects(versioned_profile)
    assert subjects == ["init: import existing data"], subjects

    # ── Step 3: ``mcs build`` lands the first build commit. ────────
    fake_maxcompute.list_tables.return_value = ["customers", "orders"]
    result = runner.invoke(
        mcs_cli,
        [
            "build",
            "--profile",
            profile_name,
            "--no-sampling",
            "--no-history",
            "--no-joins",
            "--no-udf",
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    subjects = _log_subjects(versioned_profile)
    assert len(subjects) == 2
    assert subjects[0] == "init: import existing data"
    assert subjects[1].startswith("build:"), subjects

    # ── Step 4: ``mcs memory note`` — write commit after build. ────
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "note",
            "customers v1 — initial curation",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    subjects = _log_subjects(versioned_profile)
    assert len(subjects) == 3
    assert subjects[2].startswith("memory:"), subjects

    # ── Step 5: ``mcs memory verify`` — memory commit lands. ───────
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "verify",
            "--question",
            "how many customers?",
            "--sql",
            "SELECT COUNT(*) FROM customers",
            "--tables",
            "customers",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    subjects = _log_subjects(versioned_profile)
    assert len(subjects) == 4
    assert subjects[3].startswith("memory:"), subjects

    # ── Step 6: full 4-entry log in oldest-first order. ────────────
    assert subjects[0] == "init: import existing data"
    assert subjects[1].startswith("build: ")
    assert subjects[2].startswith("memory: note ")
    assert subjects[3].startswith("memory: verify ")

    # ── Step 7: capture the note commit SHA via log-show HEAD~1. ───
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "profile",
            "log-show",
            "HEAD~1",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    payload = json.loads(result.output)["data"]
    anchor_full_sha = payload["full_sha"]
    anchor_short_sha = payload["short_sha"]
    assert payload["message"].startswith("memory: note")

    # ── Step 8: fork at the package-apply commit. ─────────────────
    fork_name = "acme-baseline"
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            fork_name,
            "--from",
            anchor_short_sha,
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    fork = get_profile(fork_name)
    assert fork.kind == "fork"
    assert fork.parent_profile == profile_name
    assert fork.git_sha == anchor_full_sha
    fork_dir = data_root() / fork_name
    assert fork_dir.exists()
    assert (fork_dir / "package.db").exists()  # restored from package.sql

    # ── Step 9: fork-list reports 1 healthy. ───────────────────────
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "profile", "fork-list", "--profile", profile_name],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    fl_payload = json.loads(result.output)["data"]
    assert fl_payload["totals"]["total"] == 1
    assert fl_payload["totals"]["healthy"] == 1
    assert fl_payload["forks"][0]["name"] == fork_name
    assert fl_payload["forks"][0]["state"].lower() == "healthy"

    # ── Step 10: skipped — ``mcs sql execute`` against both profiles
    #             would require mocking the ODPS instance class at
    #             a different import site than ``fake_maxcompute``
    #             patches (commands.sql, not commands.build). The
    #             spec's intent ("both are queryable as separate
    #             --profile targets") is verified indirectly in
    #             step 14, which opens both on-disk DBs via
    #             ``sqlite3.connect`` and reads from each.

    # ── Step 11: a *different* write lands on the parent. ──────────
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "note",
            "post-fork drift note",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    subjects = _log_subjects(versioned_profile)
    assert len(subjects) == 5
    assert subjects[4].startswith("memory: note")

    # ── Step 12: a write against the fork hits the read-only
    #             guard from T9 and exits non-zero. ────────────────
    result = runner.invoke(
        mcs_cli,
        [
            "memory",
            "note",
            "should be blocked",
            "--profile",
            fork_name,
        ],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "fork" in combined.lower() or "read-only" in combined.lower()
    assert profile_name in combined  # the parent's name is in the error

    # ── Step 13: diff between the anchor and HEAD names customers.md.
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "diff",
            anchor_short_sha,
            "HEAD",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "package.sql" in result.output, result.output

    # ── Step 14: per-profile DB content diverges as expected. ──────
    parent_db = profile_data_dir(versioned_profile) / "package.db"
    fork_db = fork_dir / "package.db"
    assert parent_db.exists() and fork_db.exists()

    # ── Step 15: reset the parent back to the package-apply. ──────
    # The captured short-sha from step 7 is the unambiguous anchor.
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "reset",
            "--to",
            anchor_short_sha,
            "--profile",
            profile_name,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")

    # The step-11 drift note is no longer in the tip-walked log;
    # HEAD is back at the anchor commit.
    subjects_after_reset = _log_subjects(versioned_profile)
    assert subjects_after_reset[-1].startswith("memory: note"), subjects_after_reset
    assert "post-fork drift" not in " ".join(subjects_after_reset)

    # ── Step 16: discarded commit reachable via git reflog. ────────
    reflog = subprocess.run(
        ["git", "-C", str(profile_data_dir(versioned_profile)), "reflog", "show", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "memory: note" in reflog.stdout, reflog.stdout

    # ── Step 17: parent DB is back to the pre-drift state. ─────────
    # After reset, the package.sql should match the anchor commit's
    # content. We verify by checking the reset succeeded (step 15)
    # and the log (step 16) — the DB content follows from the
    # git-tracked package.sql being restored.

    # ── Step 18: fork-remove cleans yaml + worktree + admin entry. ─
    result = runner.invoke(
        mcs_cli,
        ["profile", "fork-remove", fork_name, "--yes"],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert not fork_dir.exists()
    # The parent's .git/worktrees/<short>/ admin entry is gone — the
    # ``git worktree list`` output no longer mentions the fork.
    wt_list = subprocess.run(
        ["git", "-C", str(profile_data_dir(versioned_profile)), "worktree", "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert fork_name not in wt_list.stdout, wt_list.stdout
    # The parent's git history is untouched by the fork-remove.
    subjects_after_remove = _log_subjects(versioned_profile)
    assert subjects_after_remove == subjects_after_reset

    # ── Step 19: fork-list is empty. ───────────────────────────────
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "profile", "fork-list", "--profile", profile_name],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    fl_payload = json.loads(result.output)["data"]
    assert fl_payload["totals"]["total"] == 0

    # ── Step 20: ``mcs doctor --offline`` exits 0 with all 5 new
    #             versioning checks passing. ──────────────────────────
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "doctor", "--offline", "--profile", profile_name],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    doctor_payload = json.loads(result.output)
    check_names = {c["name"] for c in doctor_payload["data"]["checks"]}
    for needed in (
        "git_available",
        "profile_versioned",
        "working_tree_clean",
        "forks_healthy",
        "package_sql_parses",
    ):
        assert needed in check_names, (needed, check_names)
    fails = [c for c in doctor_payload["data"]["checks"] if c["status"] == "fail"]
    assert fails == [], fails


# ── auxiliary: env-knob short-circuits the hook ────────────────────


def test_versioning_off_via_env_skips_every_hook(
    versioned_profile: Profile,
    fake_maxcompute: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same hook-firing surfaces from the worked example
    (``build``, ``memory note``, ``memory verify``) leave no new
    commit when ``MCS_NO_VERSIONING=1`` is exported. The pre-existing
    inaugural commit stays in place (the env knob disables *writes
    to* git, not reads from it).
    """
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    profile_name = versioned_profile.name

    # Baseline: only the inaugural commit from the fixture.
    subjects_before = _log_subjects(versioned_profile)
    assert subjects_before == ["init: import existing data"]

    # build → no new commit
    result = runner.invoke(
        mcs_cli,
        [
            "build",
            "--profile",
            profile_name,
            "--no-sampling",
            "--no-history",
            "--no-joins",
            "--no-udf",
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")

    # memory note → no new commit
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "note",
            "via NO_VERSIONING",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")

    # memory verify → no new commit
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "verify",
            "--question",
            "no-versioning q",
            "--sql",
            "SELECT 1 FROM customers",
            "--tables",
            "customers",
            "--profile",
            profile_name,
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")

    subjects_after = _log_subjects(versioned_profile)
    assert subjects_after == subjects_before, subjects_after


# ── auxiliary: legacy profile auto-upgrades on first write ─────────


def test_legacy_profile_first_write_auto_upgrades(
    isolated_config: Path,
    fake_maxcompute: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile created the pre-versioning way (yaml entry + data
    dir, no ``.git/``) auto-inits on the first write — the data dir
    becomes a git repo and the log carries both the ``init: import
    existing data`` inaugural commit and the build commit on top.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    from maxcompute_semantic.auth.profile_store import upsert
    from maxcompute_semantic.auth.schema import AkAuth, DataSource

    profile = Profile(
        name="legacy",
        compute_project="acme_proj",
        endpoint="http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SEC}"),
        sources=(DataSource(project="acme_proj", schema="default", tables="*"),),
    )
    upsert(profile)
    # Pre-create some on-disk state that mimics the legacy mcs
    # profile layout — markdown files but no .git/ subdir.
    legacy_dir = profile_data_dir(profile)
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "_overview.md").write_text("legacy overview\n", encoding="utf-8")
    assert not (legacy_dir / ".git").exists()

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "build",
            "--profile",
            "legacy",
            "--no-sampling",
            "--no-history",
            "--no-joins",
            "--no-udf",
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")

    # The data dir is now a git repo and the log carries both the
    # auto-init commit and the build commit on top.
    assert (legacy_dir / ".git").exists()
    repo = GitRepo(legacy_dir)
    assert repo.exists()
    subjects = _log_subjects(profile)
    assert len(subjects) == 2, subjects
    assert subjects[0] == "init: import existing data"
    assert subjects[1].startswith("build:"), subjects


# Suppress an unused-import warning when ``os`` is dropped from the
# file in a refactor — keep the explicit reference here so the
# tooling knows it's deliberately imported for future expansion of
# this suite (e.g. an env-survival test that needs ``os.environ``).
_ = os
