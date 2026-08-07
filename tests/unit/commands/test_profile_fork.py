# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile fork --from <ref>`` — the fork-creation verb (T14).

Pins the CLI surface of the verb: name validation, ref resolution
(short SHA / full SHA / HEAD~N / keywords), gating (fork-of-fork,
MCS_NO_VERSIONING, missing parent repo, existing worktree path),
the yaml-entry-plus-on-disk-worktree pairing, the
``package.sql`` → ``package.db`` materialization, and the warn
banner when the anchor predates the first ``mcs build``.

The yaml-side ``register_fork`` / ``unregister_fork`` /
``parent_repo`` helpers have their own coverage in
``tests/unit/versioning/test_forks.py``; this file is the CLI
verb's contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic._internal.paths import data_root, profile_data_dir
from maxcompute_semantic.auth.profile_store import get as get_profile
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


def _minimal_valid_sql(schema_version: int = 8) -> str:
    """``package.sql`` body that ``restore_sql_to_db`` accepts —
    matching the helper in ``test_profile_reset_versioning.py``."""
    return (
        f"-- mcs-versioning: schema_version={schema_version}\n"
        "BEGIN TRANSACTION;\n"
        "CREATE TABLE IF NOT EXISTS _marker_table (k TEXT, v TEXT);\n"
        "INSERT INTO _marker_table VALUES ('seeded', 'yes');\n"
        "COMMIT;\n"
    )


def _seed_with_sql(repo: GitRepo, message: str, sql_body: str) -> str:
    marker = repo.root / f"_marker_{message.replace(' ', '_').replace(':', '_')[:60]}.md"
    marker.write_text(message + "\n", encoding="utf-8")
    (repo.root / "package.sql").write_text(sql_body, encoding="utf-8")
    repo.add_all()
    sha = repo.commit(message)
    assert sha is not None
    return sha


# ── happy path ─────────────────────────────────────────────────────────────


def test_fork_creates_yaml_entry_and_worktree(versioned_profile: Profile) -> None:
    """The verb pairs a ``kind=fork`` yaml entry with a real
    ``git worktree`` on disk; the worktree's HEAD is the anchor sha."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed_with_sql(repo, "build: with sql", _minimal_valid_sql())
    _seed(repo, "memory: trailing")  # HEAD moves past the anchor

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@base",
            "--from",
            anchor[:7],
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr

    fork = get_profile(f"{versioned_profile.name}@base")
    assert fork.kind == "fork"
    assert fork.parent_profile == versioned_profile.name
    assert fork.git_sha == anchor  # short-SHA normalized to full

    # The worktree is registered with git at the default location and
    # checked out at the anchor commit.
    worktree_path = data_root() / f"{versioned_profile.name}@base"
    assert worktree_path.exists()
    fork_repo = GitRepo(worktree_path)
    assert fork_repo.rev_parse("HEAD") == anchor


def test_fork_materializes_package_db_when_anchor_has_sql(
    versioned_profile: Profile,
) -> None:
    """When ``package.sql`` is in the anchor's tree, the verb
    rebuilds ``package.db`` so ``mcs sql execute --profile <fork>``
    works immediately. The seeded row from the dump is queryable."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed_with_sql(repo, "build: with sql", _minimal_valid_sql())

    runner = CliRunner()
    result = runner.invoke(
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
    assert result.exit_code == 0, result.output + result.stderr

    worktree_path = data_root() / f"{versioned_profile.name}@anchor"
    db_path = worktree_path / "package.db"
    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT v FROM _marker_table WHERE k = ?", ("seeded",)).fetchone()
    assert row is not None and row[0] == "yes"


def test_fork_at_anchor_without_package_sql_warns(versioned_profile: Profile) -> None:
    """A pre-build anchor (no ``package.sql`` in tree) lands a warn
    banner but the verb still succeeds — the worktree is created and
    the yaml entry exists; only ``package.db`` is absent."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    inaugural = repo.rev_parse("HEAD")  # the bare init commit, no package.sql
    _seed_with_sql(repo, "build: later", _minimal_valid_sql())

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@pre",
            "--from",
            inaugural,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    combined = result.output + result.stderr
    assert "no ``package.sql``" in combined

    worktree_path = data_root() / f"{versioned_profile.name}@pre"
    assert worktree_path.exists()
    assert not (worktree_path / "package.db").exists()


def test_fork_with_keyword_anchor(versioned_profile: Profile) -> None:
    """``--from last-build`` resolves to the most-recent ``build*``
    commit, matching the same resolver used by ``mcs profile reset``."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    _seed_with_sql(repo, "build: older", _minimal_valid_sql())
    expected = _seed_with_sql(repo, "build: newest", _minimal_valid_sql())
    _seed(repo, "memory: trailing noise")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@latest",
            "--from",
            "last-build",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    fork = get_profile(f"{versioned_profile.name}@latest")
    assert fork.git_sha == expected


def test_fork_with_explicit_worktree_path(versioned_profile: Profile, tmp_path: Path) -> None:
    """``--worktree-path`` overrides the default ``data_root()`` slot."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed_with_sql(repo, "build: A", _minimal_valid_sql())

    explicit = tmp_path / "custom" / "fork-location"
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@custom",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
            "--worktree-path",
            str(explicit),
        ],
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert explicit.exists()
    fork = get_profile(f"{versioned_profile.name}@custom")
    assert Path(fork.package_path) == explicit


# ── name validation ────────────────────────────────────────────────────────


def test_fork_rejects_invalid_name(versioned_profile: Profile) -> None:
    """Fork names must match the schema's ``_NAME_RE`` (same as
    main-kind profile names, extended with ``@:`` delimiters).
    A name starting with a non-alphanumeric character is rejected."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            "@bad-leading-at",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code == 2
    combined = result.output + result.stderr
    assert "doesn't match" in combined or "name regex" in combined


# ── ref resolution / not-found ─────────────────────────────────────────────


def test_fork_unknown_ref_errors(versioned_profile: Profile) -> None:
    """An unresolvable ``--from`` ref produces a non-zero exit naming
    the ref."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@nope",
            "--from",
            "deadbeef0123",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "deadbeef0123" in combined


# ── gating ─────────────────────────────────────────────────────────────────


def test_fork_with_no_versioning_env_errors(
    versioned_profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MCS_NO_VERSIONING=1`` hard-errors the fork verb."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@base",
            "--from",
            "HEAD",
            "--profile",
            versioned_profile.name,
        ],
    )
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "MCS_NO_VERSIONING" in combined


def test_fork_rejects_fork_as_parent(versioned_profile: Profile, tmp_path: Path) -> None:
    """Forking from a fork-kind profile is rejected; the error
    points the user at the underlying parent."""
    parent_dir = profile_data_dir(versioned_profile)
    parent_repo = GitRepo(parent_dir)
    anchor = parent_repo.rev_parse("HEAD")

    # Register a fork manually so we can try to fork-of-fork.
    fork_dir = tmp_path / "first-fork"
    fork_dir.mkdir()
    fork = Profile(
        name="t14_first_fork",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
        kind="fork",
        parent_profile=versioned_profile.name,
        git_sha=anchor,
        package_path=str(fork_dir),
    )
    upsert_profile(fork)

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            "t14_second_fork",
            "--from",
            "HEAD",
            "--profile",
            fork.name,
        ],
    )
    assert result.exit_code == 2
    combined = result.output + result.stderr
    assert versioned_profile.name in combined
    assert "main-kind" in combined


def test_fork_unversioned_parent_errors(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the parent profile's data-dir isn't a git repo, the verb
    surfaces the ``enable-versioning`` remediation."""
    import json

    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    spec = json.dumps(
        {
            "name": "unversioned_parent",
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
            "fork",
            "unversioned_parent@x",
            "--from",
            "HEAD",
            "--profile",
            "unversioned_parent",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "enable-versioning" in combined


def test_fork_refuses_existing_worktree_path(versioned_profile: Profile, tmp_path: Path) -> None:
    """If ``--worktree-path`` (or the default slot) already exists,
    the verb refuses rather than overwriting it."""
    parent_dir = profile_data_dir(versioned_profile)
    repo = GitRepo(parent_dir)
    anchor = _seed(repo, "build: A")

    existing = tmp_path / "already-here"
    existing.mkdir()
    (existing / "stale.txt").write_text("preexisting\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "fork",
            f"{versioned_profile.name}@taken",
            "--from",
            anchor,
            "--profile",
            versioned_profile.name,
            "--worktree-path",
            str(existing),
        ],
    )
    assert result.exit_code == 2
    combined = result.output + result.stderr
    assert "already exists" in combined
