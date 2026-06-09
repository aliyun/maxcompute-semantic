# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile log-show <ref>`` — single-commit dump (T11).

Pins the verb's user-visible contract: short/full SHA acceptance,
``HEAD`` / ``HEAD~N`` acceptance, the three ``last-*`` keywords,
fork-redirect to parent, JSON envelope shape, and the
no-such-ref error path.

Mounted as ``log-show`` (not ``show``) because ``mcs profile show
<name>`` already owns ``show`` for the config-dump verb.
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
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


def test_log_show_accepts_full_sha(versioned_profile: Profile) -> None:
    """Passing the full 40-hex SHA dumps the commit's diff."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    sha = _seed(repo, "build: synthetic 1")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "log-show", sha, "--profile", versioned_profile.name]
    )
    assert result.exit_code == 0, result.output
    # ``git show`` header carries the commit SHA + author.
    assert sha in result.output
    assert "build: synthetic 1" in result.output


def test_log_show_accepts_short_sha(versioned_profile: Profile) -> None:
    """The short SHA (7+ chars) is resolved via ``rev_parse``."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    sha = _seed(repo, "build: synthetic 2")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "log-show", sha[:7], "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "build: synthetic 2" in result.output


def test_log_show_accepts_head_keyword(versioned_profile: Profile) -> None:
    """``HEAD`` resolves to the most recent commit."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: synthetic at-head")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli, ["profile", "log-show", "HEAD", "--profile", versioned_profile.name]
    )
    assert result.exit_code == 0, result.output
    assert "build: synthetic at-head" in result.output


def test_log_show_accepts_head_tilde_n(versioned_profile: Profile) -> None:
    """``HEAD~N`` walks the history N steps back."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: first")
    _seed(repo, "build: second")

    runner = CliRunner()
    # HEAD~1 = "build: first" (the inaugural is HEAD~2).
    result = runner.invoke(
        mcs_cli,
        ["profile", "log-show", "HEAD~1", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "build: first" in result.output
    assert "build: second" not in result.output


def test_log_show_last_build_keyword(versioned_profile: Profile) -> None:
    """``last-build`` resolves to the most recent ``build*`` commit."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "build: older build")
    _seed(repo, "memory: irrelevant noise")
    _seed(repo, "build: newest build")
    _seed(repo, "memory: more noise")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "log-show", "last-build", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "build: newest build" in result.output


def test_log_show_last_refresh_keyword(versioned_profile: Profile) -> None:
    """``last-refresh`` resolves to the most recent ``refresh*``
    commit."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    _seed(repo, "refresh: A")
    _seed(repo, "build: B")
    _seed(repo, "refresh: C")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "log-show", "last-refresh", "--profile", versioned_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "refresh: C" in result.output


def test_log_show_unknown_ref_errors(versioned_profile: Profile) -> None:
    """A SHA / keyword that doesn't resolve produces a non-zero
    exit and an error message naming the bad ref."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "log-show",
            "deadbeef0123",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code != 0
    assert "deadbeef0123" in result.output


def test_log_show_json_envelope_shape(versioned_profile: Profile) -> None:
    """JSON output wraps the diff in the standard success envelope."""
    repo = GitRepo(profile_data_dir(versioned_profile))
    sha = _seed(repo, "build: json-shape")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "profile",
            "log-show",
            sha,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    payload = envelope["data"]
    assert set(payload.keys()) == {"short_sha", "full_sha", "message", "diff_text"}
    assert payload["full_sha"] == sha
    assert payload["message"] == "build: json-shape"
    # The diff body should contain the marker file we added.
    assert "_marker_" in payload["diff_text"] or "diff --git" in payload["diff_text"]


def test_log_show_redirects_fork_to_parent(versioned_profile: Profile, tmp_path: Path) -> None:
    """A fork's ``log-show`` reads against the parent's repo and
    emits the parent-redirect banner on stderr."""
    parent_repo = GitRepo(profile_data_dir(versioned_profile))
    anchor = parent_repo.rev_parse("HEAD")
    parent_sha = _seed(parent_repo, "build: post-fork-1")

    fork_dir = tmp_path / "fork-show-pkg"
    fork_dir.mkdir()
    fork = Profile(
        name="t8test_fork_show",
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
        ["profile", "log-show", parent_sha, "--profile", fork.name],
    )
    assert result.exit_code == 0, result.output
    assert versioned_profile.name in result.stderr
    assert "build: post-fork-1" in result.output


def test_log_show_unversioned_profile_exits_zero(isolated_config: Path, monkeypatch) -> None:
    """``log-show`` against an unversioned profile exits 0 with the
    enable-versioning hint on stderr."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    spec = json.dumps(
        {
            "name": "unversioned_show",
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
        ["profile", "log-show", "HEAD", "--profile", "unversioned_show"],
    )
    assert result.exit_code == 0, result.output
    assert "not versioned" in result.stderr
