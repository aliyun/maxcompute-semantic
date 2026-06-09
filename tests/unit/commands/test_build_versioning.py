# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs build's commit_after_command wiring (T8).

The build verb is the canonical case: at the success-path tail, the
hook runs with ``action=ACTION_BUILD`` (or ``ACTION_REFRESH`` when
``--refresh`` is passed) and a summary of the form
``<profile-name> @ <UTC ISO-8601 timestamp>``. The resulting commit
in the per-profile git repo has a subject like::

    build: t8test @ 2026-05-23T12:34:56+00:00

These tests pin both the prefix (``build:`` / ``refresh:``) and
the subject format. The build pipeline is driven against the
faked MaxComputeClient via the ``fake_maxcompute`` fixture in
``conftest.py`` so no network is touched.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _build_args(profile: Profile, *extra: str) -> list[str]:
    """Args for a fast schema-only build — skip sampling / history /
    joins / udf phases so the test runs in tenths of a second and
    the faked-client surface stays narrow."""
    return [
        "build",
        "--profile",
        profile.name,
        "--no-sampling",
        "--no-history",
        "--no-joins",
        "--no-udf",
        *extra,
    ]


def test_mcs_build_commits_with_build_prefix(
    versioned_profile: Profile, fake_maxcompute: MagicMock
) -> None:
    """A successful ``mcs build`` lands one new commit on top of
    the fixture's inaugural ``init: import existing data`` whose
    subject is ``build: <profile-name> @ <UTC ISO-8601 timestamp>``."""
    runner = CliRunner()
    result = runner.invoke(mcs_cli, _build_args(versioned_profile))
    assert result.exit_code == 0, f"mcs build exited non-zero; output:\n{result.output!r}"

    repo = GitRepo(profile_data_dir(versioned_profile))
    rows = repo.log(limit=None)
    assert len(rows) == 2, (
        f"expected one new commit on top of the inaugural one; "
        f"got log {[c.message for c in rows]!r}"
    )
    build_commit = rows[0]  # newest-first
    pattern = (
        r"^build: "
        + re.escape(versioned_profile.name)
        + r" @ \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$"
    )
    assert re.fullmatch(pattern, build_commit.message), (
        f"build commit's subject {build_commit.message!r} does not "
        f"match the spec's ``build: <name> @ <UTC ISO-8601>`` "
        f"format. Expected regex: {pattern!r}."
    )

    # The commit's diff should mention at least one of the canonical
    # build outputs — package.sql is the dump that always appears,
    # plus _overview.md / _joins.md / _state.json for any real build.
    diff_text = repo.show(build_commit.full_sha)
    assert any(
        marker in diff_text
        for marker in ("_overview.md", "_joins.md", "_state.json", "package.sql")
    ), (
        f"build commit's diff doesn't mention any of the standard "
        f"build-output filenames. Diff text:\n{diff_text[:1000]}"
    )


def test_mcs_build_refresh_uses_refresh_action_prefix(
    versioned_profile: Profile, fake_maxcompute: MagicMock
) -> None:
    """``mcs build --refresh`` produces a ``refresh: ...`` commit
    so it's distinguishable from the plain ``build:`` prefix in the
    log. This matters for the T13 ``mcs profile reset --to
    last-build`` keyword: ``last-build`` matches ``^build`` (not
    ``refresh:``), so the two prefix families must stay separate."""
    runner = CliRunner()

    # First, a plain build to seed the log past the fixture's
    # inaugural commit.
    first = runner.invoke(mcs_cli, _build_args(versioned_profile))
    assert first.exit_code == 0, first.output

    # Then a refresh.
    second = runner.invoke(mcs_cli, _build_args(versioned_profile, "--refresh"))
    assert second.exit_code == 0, second.output

    repo = GitRepo(profile_data_dir(versioned_profile))
    rows = repo.log(limit=None)
    subjects = [c.message for c in rows]  # newest-first

    # Newest is the refresh.
    assert subjects[0].startswith(f"refresh: {versioned_profile.name} @ "), (
        f"newest subject should be the refresh commit; got {subjects[0]!r}"
    )
    # Middle is the plain build (when present).
    if len(subjects) >= 2:
        assert subjects[1].startswith(f"build: {versioned_profile.name} @ "), (
            f"second-newest subject should be the plain build commit; got {subjects[1]!r}"
        )
    # Oldest is always the inaugural commit.
    assert subjects[-1] == "init: import existing data"


def test_mcs_build_no_versioning_env_suppresses_commit(
    versioned_profile: Profile,
    fake_maxcompute: MagicMock,
    monkeypatch,
) -> None:
    """With ``MCS_NO_VERSIONING=1``, the build still runs and writes
    package data to disk, but the hook's env short-circuit (T5 step
    1) prevents a new commit. The pre-existing inaugural commit
    from the fixture stays the lone log entry."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(mcs_cli, _build_args(versioned_profile))
    assert result.exit_code == 0, result.output

    repo = GitRepo(profile_data_dir(versioned_profile))
    rows = repo.log(limit=None)
    assert len(rows) == 1, (
        f"MCS_NO_VERSIONING should have suppressed the build commit; "
        f"got log {[c.message for c in rows]!r}"
    )
    assert rows[0].message == "init: import existing data"
