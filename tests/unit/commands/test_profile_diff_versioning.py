# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile diff <a> <b>`` — two-commit unified diff (T12).

Pins the verb's user-visible contract: SHA / keyword acceptance on
both arguments, JSON envelope shape, the
identical-trees informational stderr, fork-redirect, and the
unknown-ref error path.
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


def _seed(repo: GitRepo, message: str, body: str = "") -> str:
    """Write a tracked-glob file (``.md``) and commit. The ``body``
    arg lets a test mutate the file contents across calls so the
    diff is non-empty between commits."""
    marker = repo.root / "shared_marker.md"
    marker.write_text(body if body else message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def test_diff_two_full_shas(versioned_profile: Profile) -> None:
    """``profile diff <a> <b>`` shows the diff between two commits'
    trees over the tracked-file globs."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    a = _seed(repo, "build: first state", body="line-one\n")
    b = _seed(repo, "build: second state", body="line-one\nline-two\n")

    runner = CliRunner()
    result = runner.invoke(mcs_cli, ["profile", "diff", a, b, "--profile", versioned_profile.name])
    assert result.exit_code == 0, result.output
    assert "diff --git" in result.output
    # The added line should appear with a leading ``+``.
    assert "+line-two" in result.output


def test_diff_supports_keywords(versioned_profile: Profile) -> None:
    """``last-build`` / ``HEAD~N`` resolve on either side."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: older", body="a\n")
    _seed(repo, "build: newer", body="a\nb\n")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "diff",
            "HEAD~1",
            "last-build",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "diff --git" in result.output
    assert "+b" in result.output


def test_diff_identical_trees_stderr_hint(versioned_profile: Profile) -> None:
    """Diffing a commit against itself produces an informational
    stderr message and exits 0 — the diff is empty by definition."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    sha = _seed(repo, "build: x", body="content\n")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "diff", sha, sha, "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "no tracked-file changes" in result.stderr


def test_diff_json_envelope_shape(versioned_profile: Profile) -> None:
    """JSON output is the standard envelope with normalized refs."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    a = _seed(repo, "build: A", body="x\n")
    b = _seed(repo, "build: B", body="x\ny\n")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "profile",
            "diff",
            a,
            b,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert set(payload.keys()) == {"ref_a", "ref_b", "diff_text"}
    assert payload["ref_a"] == a[:12]
    assert payload["ref_b"] == b[:12]
    assert "diff --git" in payload["diff_text"]


def test_diff_unknown_ref_a_errors(versioned_profile: Profile) -> None:
    """An unresolvable ``ref_a`` produces a non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "diff",
            "no-such-ref",
            "HEAD",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code != 0
    assert "no-such-ref" in result.output


def test_diff_unknown_ref_b_errors(versioned_profile: Profile) -> None:
    """An unresolvable ``ref_b`` produces a non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "diff",
            "HEAD",
            "deadbeef0000",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code != 0
    assert "deadbeef0000" in result.output


def test_diff_redirects_fork_to_parent(versioned_profile: Profile, tmp_path: Path) -> None:
    """A fork's ``diff`` reads against the parent's repo and emits
    the parent-redirect banner on stderr."""
    parent_repo = GitRepo(profile_data_dir(versioned_profile))
    anchor = parent_repo.rev_parse("HEAD")
    a = _seed(parent_repo, "build: parent-A", body="x\n")
    b = _seed(parent_repo, "build: parent-B", body="x\ny\n")

    fork_dir = tmp_path / "fork-diff-pkg"
    fork_dir.mkdir()
    fork = Profile(
        name="t8test_fork_diff",
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
        ["profile", "diff", a, b, "--profile", fork.name],
    )
    assert result.exit_code == 0, result.output
    assert versioned_profile.name in result.stderr
    assert "diff --git" in result.output


def test_diff_unversioned_profile_exits_zero(isolated_config: Path, monkeypatch) -> None:
    """``diff`` against an unversioned profile exits 0 with the
    enable-versioning hint on stderr."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    spec = json.dumps(
        {
            "name": "unversioned_diff",
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
        ["profile", "diff", "HEAD", "HEAD~1", "--profile", "unversioned_diff"],
    )
    assert result.exit_code == 0, result.output
    assert "not versioned" in result.stderr
