"""``mcs profile log`` — per-profile history listing (T10).

Pins the verb's user-visible contract: default ``memory:`` noise
filter, ``-n`` cap, ``--all`` opt-in to noise, ``--grep`` regex
filter, JSON output shape, fork-redirect to parent, unversioned-
profile graceful exit, and the ``MCS_NO_VERSIONING`` read-asymmetry
warning.

The fixture seeds an inaugural ``init: import existing data`` commit
via the standard ``versioned_profile`` recipe; tests that need
additional commits run ``GitRepo.commit`` directly rather than
going through a write verb — keeps the test surface narrow.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _seed(repo: GitRepo, message: str) -> str:
    """Write an empty marker file and commit with ``message``.

    Uses an ``--allow-empty``-friendly path: write a unique file so
    the commit always has content, no short-circuit. Returns the
    full SHA of the new commit.
    """
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def test_log_defaults_filter_memory_noise(versioned_profile: Profile) -> None:
    """Default ``mcs profile log`` hides ``memory:`` prefix commits
    and caps at 20 rows. The inaugural commit (not a memory: prefix)
    must appear; the seeded ``memory: ...`` commit must not."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "memory: store verified sql foo")

    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "log", "--profile", versioned_profile.name])
    assert result.exit_code == 0, result.output

    # Inaugural commit visible.
    assert "init: import existing data" in result.output
    # memory: noise hidden by default.
    assert "memory: store verified sql foo" not in result.output


def test_log_all_flag_includes_memory_noise(versioned_profile: Profile) -> None:
    """``--all`` drops the implicit ``^memory:`` filter."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "memory: store verified sql foo")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "log", "--profile", versioned_profile.name, "--all"]
    )
    assert result.exit_code == 0, result.output
    assert "memory: store verified sql foo" in result.output
    assert "init: import existing data" in result.output


def test_log_limit_caps_rows(versioned_profile: Profile) -> None:
    """``-n N`` caps output to N rows (counting only the rows that
    survive the default ``memory:`` filter)."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    for i in range(5):
        _seed(repo, f"build: synthetic {i}")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "log", "--profile", versioned_profile.name, "-n", "2"]
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected 2 rows, got {lines!r}"


def test_log_limit_zero_means_unlimited(versioned_profile: Profile) -> None:
    """``-n 0`` removes the cap entirely."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    for i in range(3):
        _seed(repo, f"build: synthetic {i}")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "log", "--profile", versioned_profile.name, "-n", "0"]
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    # 3 build commits + 1 inaugural = 4 rows.
    assert len(lines) == 4, f"expected 4 rows, got {lines!r}"


def test_log_grep_supersedes_default_filter(versioned_profile: Profile) -> None:
    """Explicit ``--grep`` bypasses the implicit ``^memory:`` filter
    (git log can't AND two --grep regexes) and emits a stderr note."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "memory: keep me visible")
    _seed(repo, "build: keep me too")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "log", "--profile", versioned_profile.name, "--grep", "keep me"],
    )
    assert result.exit_code == 0, result.output
    # Both keep-me commits are matched (memory filter bypassed).
    assert "memory: keep me visible" in result.output
    assert "build: keep me too" in result.output
    # Stderr note about the bypass.
    assert "default ^memory: noise filter is bypassed" in result.stderr


def test_log_json_output_shape(versioned_profile: Profile) -> None:
    """JSON output is the standard envelope with ``data.commits``."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["-f", "json", "profile", "log", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    payload = envelope["data"]["commits"]
    assert len(payload) >= 1
    row = payload[0]
    assert set(row.keys()) == {"short_sha", "full_sha", "message"}
    assert len(row["full_sha"]) == 40
    assert row["message"] == "init: import existing data"


def test_log_empty_filtered_window_warns(versioned_profile: Profile) -> None:
    """When the filter window is empty (only ``memory:`` commits in
    the repo and the default filter is on), the verb exits 0 with a
    stderr hint pointing at ``--all``."""
    # The hint path triggers when the filter result is empty; pass
    # a regex with zero matches (the inaugural commit is still in
    # the underlying history, but the filter drops it).
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "log",
            "--profile",
            versioned_profile.name,
            "--grep",
            "this-string-will-never-match-any-subject",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no non-memory commits in the filtered window" in result.stderr


def test_log_redirects_fork_to_parent(versioned_profile: Profile, tmp_path: Path) -> None:
    """A ``kind="fork"`` profile's history is the parent's history;
    the verb emits a stderr banner naming the parent + anchor SHA."""
    parent_repo = GitRepo(profile_data_dir(versioned_profile))
    parent_anchor = parent_repo.rev_parse("HEAD")
    _seed(parent_repo, "build: post-fork parent commit")

    # Forge a fork profile pointing at the parent. The fork's
    # ``package_path`` is required by Profile.validate; the
    # read-verb's history-of-record lookup walks back to the
    # parent's data-dir, so the path itself doesn't need to be a
    # real worktree.
    fork_dir = tmp_path / "fork-pkg"
    fork_dir.mkdir()
    fork = Profile(
        name="t8test_fork",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
        kind="fork",
        parent_profile=versioned_profile.name,
        git_sha=parent_anchor,
        package_path=str(fork_dir),
    )
    upsert(fork)

    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "log", "--profile", fork.name])
    assert result.exit_code == 0, result.output
    # Fork-redirect banner names the parent and shows the anchor SHA.
    assert versioned_profile.name in result.stderr
    assert parent_anchor[:12] in result.stderr
    # The parent's post-fork commit appears in the fork's output
    # because we're reading the parent's full history.
    assert "build: post-fork parent commit" in result.output


def test_log_unversioned_profile_exits_zero_with_hint(isolated_config: Path, monkeypatch) -> None:
    """``mcs profile log`` against a profile whose data-dir has no
    ``.git/`` exits 0 with a stderr remediation hint pointing at
    ``mcs profile enable-versioning``."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    # Create a profile *without* versioning (MCS_NO_VERSIONING=1
    # suppresses the inaugural commit / git init from the create
    # path).
    spec = json.dumps(
        {
            "name": "unversioned",
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
    create_result = runner.invoke(
        mcs_cli,
        ["profile", "create", "--from-spec", spec, "--no-test"],
    )
    assert create_result.exit_code == 0, create_result.output

    # MCS_NO_VERSIONING off now — but the profile still has no .git/.
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    result = runner.invoke(mcs_cli, ["profile", "log", "--profile", "unversioned"])
    assert result.exit_code == 0, result.output
    assert "not versioned" in result.stderr
    assert "enable-versioning" in result.stderr


def test_log_warns_when_no_versioning_env_set(versioned_profile: Profile, monkeypatch) -> None:
    """With ``MCS_NO_VERSIONING=1`` set, the read still works
    against the existing git history but the renderer warns the
    user about the asymmetry."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "log", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "MCS_NO_VERSIONING is set" in result.stderr
    # The actual log content still rendered.
    assert "init: import existing data" in result.output
