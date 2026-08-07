# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs profile import's commit_after_command wiring (T8).

``mcs profile import`` is the bootstrap moment for the new
profile's history — the destination data-dir is brand-new at the
time the hook runs, so the hook takes the auto-init branch and
lands the canonical inaugural ``init: import existing data``
commit. The summary the import_cmd passes
(``import from <archive>.tar.gz``) is intentionally subsumed by
the inaugural-commit constant: the spec's table calls
``ACTION_INIT`` the action prefix used both for ``mcs profile
create`` and ``mcs profile import``, and on the auto-init branch
the constant ``_INAUGURAL_COMMIT_SUMMARY`` wins so the timeline's
zero-th commit is reliably greppable.

What we still pin:

  * Exactly one commit lands in the imported profile's per-profile
    repo (the inaugural one).
  * Its subject is the canonical ``init: import existing data``.
  * The imported package data (``package.db`` / per-table markdown)
    is captured *inside* that inaugural commit — no later commit is
    needed to bring the imported files under versioning.
  * ``MCS_NO_VERSIONING=1`` suppresses the commit entirely; the
    imported data sits on disk uncommitted, no ``.git/`` directory
    is created in the destination data-dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.commands.profile_export import export_profile
from maxcompute_semantic.versioning.git_repo import GitRepo

_SK = "acme_proj__default"


def _seed_db_for_export(profile: Profile) -> None:
    """Seed a minimal package.db so the source profile has something
    to export. The import test's source profile is the ``versioned_profile``
    fixture, which only has the inaugural git commit — the package.db
    file itself is created here before ``export_profile`` runs."""
    db_path = profile_data_dir(profile) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(db_path)
    db.upsert_table(_SK, "orders", "hash1")
    db.close()
    # _state.json is referenced by _build_manifest; an empty stub is
    # fine — the manifest summary just records what it can.
    (profile_data_dir(profile) / "_state.json").write_text('{"sources": {}}', encoding="utf-8")


def test_mcs_profile_import_creates_inaugural_commit(
    versioned_profile: Profile, tmp_path: Path
) -> None:
    """``mcs profile import`` lands a single ``init: import existing
    data`` commit in the imported profile's data-dir. The imported
    package data is captured *inside* that inaugural commit."""
    _seed_db_for_export(versioned_profile)

    # Export the seed profile under a sanitized name so the archive's
    # manifest carries a name that doesn't collide with the source.
    archive_path = tmp_path / "exported.tar.gz"
    export_profile(versioned_profile.name, archive_path, name_override="imported_t8")

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "import",
            str(archive_path),
        ],
    )
    assert result.exit_code == 0, result.output

    # Resolve the imported profile and inspect its per-profile repo.
    from maxcompute_semantic.auth.profile_store import get as get_profile

    imported = get_profile("imported_t8")
    repo = GitRepo(profile_data_dir(imported))
    rows = repo.log(limit=None)
    assert len(rows) == 1, (
        f"import should land exactly the inaugural commit; got log {[c.message for c in rows]!r}"
    )
    assert rows[0].message == "init: import existing data"

    # The inaugural commit must carry the imported data — at minimum
    # the package.sql dump and (when present) the _state.json stub.
    diff_text = repo.show(rows[0].full_sha)
    assert any(marker in diff_text for marker in ("package.sql", "_state.json")), (
        f"inaugural import commit's diff should mention the imported "
        f"package files; got:\n{diff_text[:1000]}"
    )


def test_mcs_profile_import_no_versioning_env_suppresses_commit(
    versioned_profile: Profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``MCS_NO_VERSIONING=1``, the import still runs and writes
    package data to disk under the imported profile, but the per-profile
    repo is never initialized — no ``.git/`` directory is created."""
    _seed_db_for_export(versioned_profile)
    archive_path = tmp_path / "exported.tar.gz"
    export_profile(versioned_profile.name, archive_path, name_override="imported_noversion")

    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "import",
            str(archive_path),
        ],
    )
    assert result.exit_code == 0, result.output

    from maxcompute_semantic.auth.profile_store import get as get_profile

    imported = get_profile("imported_noversion")
    pdir = profile_data_dir(imported)
    assert (pdir / "package.db").exists(), (
        "imported package.db should still land on disk even with versioning disabled"
    )
    assert not (pdir / ".git").exists(), (
        f"MCS_NO_VERSIONING=1 should suppress per-profile repo creation; "
        f"found .git at {pdir / '.git'}"
    )
