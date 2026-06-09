# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs memory's commit_after_command wiring (T8).

Six write verbs share the ``memory:`` action prefix:

  * ``memory verify``  — ``memory: verify <id> ('<question…>')``
  * ``memory fail``    — ``memory: fail <id> ('<question…>')``
  * ``memory note``    — ``memory: note <id>``
  * ``memory remove``  — ``memory: remove <id>``
  * ``memory clear``   — ``memory: clear (<n> entries)``
  * ``memory reindex`` — ``memory: reindex (<fts>, <vec>)``

These tests pin the prefix and a stable substring of the summary.
The numeric id field is the autoincrement primary key of the
``memory_entries`` row — we extract it from the JSON envelope the
verb prints so the assertion isn't brittle across test ordering.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _parse_json_envelope(output: str) -> dict:
    """Pull the last JSON-shaped line out of the CLI output."""
    for line in reversed(output.strip().split("\n")):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"no JSON envelope found in:\n{output[:400]}")


def _head_subject(profile: Profile) -> str:
    repo = GitRepo(profile_data_dir(profile))
    rows = repo.log(limit=None)
    assert rows, "expected at least one commit"
    return rows[0].message


def test_memory_verify_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "verify",
            "--question",
            "How many orders?",
            "--sql",
            "SELECT count(*) FROM orders",
            "--tables",
            "orders",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = _parse_json_envelope(result.output)
    entry_id = envelope["data"]["id"]

    subject = _head_subject(versioned_profile)
    # The summary shape is ``verify <id> ('<question…>')`` with
    # the question quoted via ``repr`` so the apostrophe-style
    # quote is the outer wrapper. The middle is the id and a
    # truncated form of the question — assert just on the prefix
    # and id presence so the exact truncation isn't pinned.
    assert subject.startswith(f"memory: verify {entry_id}"), subject
    assert "How many orders" in subject


def test_memory_fail_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "fail",
            "--question",
            "Why is X broken?",
            "--sql",
            "SELECT bogus FROM t",
            "--error-msg",
            "Column not found",
            "--remediation",
            "use the correct column name",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = _parse_json_envelope(result.output)
    entry_id = envelope["data"]["id"]

    subject = _head_subject(versioned_profile)
    assert subject.startswith(f"memory: fail {entry_id}"), subject
    assert "Why is X broken" in subject


def test_memory_note_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "note",
            "The fact table is partitioned by ds.",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = _parse_json_envelope(result.output)
    entry_id = envelope["data"]["id"]

    subject = _head_subject(versioned_profile)
    assert subject == f"memory: note {entry_id}", subject


def test_memory_remove_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    """Seed a note, then remove it. The remove verb's commit subject
    is ``memory: remove <id>``."""
    runner = CliRunner()
    seed = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "note",
            "ephemeral",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert seed.exit_code == 0, seed.output
    seed_id = _parse_json_envelope(seed.output)["data"]["id"]

    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "remove",
            str(seed_id),
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output

    subject = _head_subject(versioned_profile)
    assert subject == f"memory: remove {seed_id}", subject


def test_memory_clear_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    """Seed two notes, then clear. The clear verb's commit subject
    is ``memory: clear (<n> entries)``."""
    runner = CliRunner()
    for text in ("note A", "note B"):
        seed = runner.invoke(
            mcs_cli,
            [
                "-f",
                "json",
                "memory",
                "note",
                text,
                "--profile",
                versioned_profile.name,
            ],
        )
        assert seed.exit_code == 0, seed.output

    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "clear",
            "--yes",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output

    subject = _head_subject(versioned_profile)
    assert subject.startswith("memory: clear ("), subject
    assert "entries)" in subject


def test_memory_reindex_commits_with_memory_prefix(
    versioned_profile: Profile,
) -> None:
    """``memory reindex`` emits ``memory: reindex (<fts>, <vec>)`` —
    the two integers are the FTS and vector index counts."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "-f",
            "json",
            "memory",
            "reindex",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output

    subject = _head_subject(versioned_profile)
    # reindex on a fresh, empty memory store emits ``memory: reindex
    # (0, …)``. Pin the prefix and the parenthesised-count shape.
    assert subject.startswith("memory: reindex ("), subject
    assert subject.endswith(")"), subject


def test_memory_verify_no_versioning_env_suppresses_commit(
    versioned_profile: Profile, monkeypatch
) -> None:
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "memory",
            "verify",
            "--question",
            "q",
            "--sql",
            "SELECT 1",
            "--tables",
            "t",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output
    repo = GitRepo(profile_data_dir(versioned_profile))
    rows = repo.log(limit=None)
    assert len(rows) == 1
    assert rows[0].message == "init: import existing data"
