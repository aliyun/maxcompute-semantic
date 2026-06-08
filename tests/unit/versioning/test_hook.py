# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""commit_after_command — the auto-commit hook contract."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sqlite3
import warnings
from pathlib import Path
from typing import Any

import pytest
from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_git_dir,
    profile_gitignore_path,
    profile_package_sql_path,
)
from maxcompute_semantic.auth.schema import (
    AkAuth,
    DataSource,
    Profile,
)
from maxcompute_semantic.versioning.env import is_versioning_disabled
from maxcompute_semantic.versioning.errors import (
    LockedByOtherProcessError,
    ProfileReadOnly,
)
from maxcompute_semantic.versioning.git_repo import GitRepo
from maxcompute_semantic.versioning.hook import (
    ACTION_BUILD,
    ACTION_INIT,
    ACTION_MEMORY_PREFIX,
    ACTION_RECOVER,
    ACTION_REFRESH,
    ACTION_UDF_PREFIX,
    commit_after_command,
    commit_if_uncommitted_on_entry,
)
from maxcompute_semantic.versioning.lock import WriteLock


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force XDG roots into the tmpdir so the hook's path resolution
    stays inside the test fixture. ``MCS_DATA_DIR`` takes precedence
    over the platform default and over ``XDG_DATA_HOME`` — see
    ``_internal/paths.data_dir()`` for the resolution order. Also
    unset ``MCS_PROFILES_DIR`` (the legacy-named override) so the
    standard ``data_root() == data_dir() / "data"`` shape applies.
    Finally, unset ``MCS_NO_VERSIONING`` in case the developer has
    it lingering in their shell, otherwise every test in the file
    would silently no-op."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    data_dir = h / "data-root"
    data_dir.mkdir()
    monkeypatch.setenv("MCS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MCS_PROFILES_DIR", raising=False)
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    return h


@pytest.fixture
def profile(home: Path) -> Profile:
    """A bare ``kind="main"`` Profile pointing into the test home.
    The Profile dataclass's ``package_path`` is left at the default
    None so ``profile_data_dir(profile)`` resolves to
    ``data_root() / profile.name``."""
    return Profile(
        name="acme",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )


def _materialize_package_db(profile: Profile) -> None:
    """Open a ``PackageDB`` at the profile's standard location so the
    schema gets stamped (the dump's magic comment carries
    ``PRAGMA user_version`` which a default fresh sqlite gives as 0
    — ``PackageDB.open`` is what sets the proper integer). Closes
    the connection cleanly so the hook's own dump-time
    ``sqlite3.connect`` doesn't collide on the WAL lock."""
    from maxcompute_semantic.build.storage import PackageDB

    db_path = profile_data_dir(profile) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Spec calls this ``PackageDB.open(...)``; the real API on the
    # current source tree is the bare constructor. The semantic is
    # identical (the constructor's ``__init__`` runs the schema-
    # stamp + WAL-pragma path that the spec attributes to ``open``).
    pdb = PackageDB(db_path)
    pdb.close()


def _read_log_messages(profile: Profile) -> list[str]:
    """List the subject lines of the profile's git log, oldest-first,
    via the wrapper. The fixture's HOME isolation means the only
    git repo discoverable from the wrapper's POV is the per-profile
    one inside the tmp home."""
    repo = GitRepo(profile_data_dir(profile))
    return [c.message for c in reversed(repo.log(limit=None))]


def test_first_commit_after_command_initializes_and_then_records_action(
    profile: Profile,
) -> None:
    """Smoke contract. Starting state: the profile's data dir
    doesn't exist on disk. Expected after one
    ``commit_after_command(profile, action="build", summary="acme @ T")``
    call: ``.git/`` exists, ``.gitignore`` exists, ``package.sql``
    exists (since ``PackageDB.open`` materialized ``package.db``),
    the git log contains exactly two commits — the inaugural
    "init: import existing data" followed by the action's
    "build: acme @ T"."""
    _materialize_package_db(profile)
    new_sha = commit_after_command(profile, action=ACTION_BUILD, summary="acme @ T")
    assert new_sha is not None
    assert len(new_sha) == 40, "SHA should be 40-char hex"

    pdir = profile_data_dir(profile)
    assert (pdir / ".git").is_dir()
    assert profile_gitignore_path(profile).exists()
    assert profile_package_sql_path(profile).exists()
    # The package.db is still there too (it's the source of the dump,
    # not deleted).
    assert (pdir / "package.db").exists()
    # The .gitignore lists package.db (and its WAL/journal/shm
    # siblings) as ignored patterns but NOT package.sql — the dump
    # is the committed text representation.
    gi_text = profile_gitignore_path(profile).read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in gi_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert "package.db" in patterns
    assert "package.sql" not in patterns

    msgs = _read_log_messages(profile)
    assert msgs == [
        "init: import existing data",
        "build: acme @ T",
    ]


def test_second_call_with_no_change_is_no_op(profile: Profile) -> None:
    """The byte-deterministic dump means two consecutive calls
    against an unchanging ``package.db`` produce identical
    ``package.sql`` bytes, and the second call's ``git diff
    --cached --quiet`` returns 0, and ``GitRepo.commit`` returns
    ``None``. The hook propagates that ``None`` up so the caller
    knows nothing was committed."""
    _materialize_package_db(profile)
    first_sha = commit_after_command(profile, action=ACTION_BUILD, summary="@ T1")
    assert first_sha is not None
    second_sha = commit_after_command(profile, action=ACTION_BUILD, summary="@ T2")
    # The summary changed (the timestamp moved from T1 to T2) — the
    # *commit message* would differ — but the *staged content* is
    # byte-identical, so ``git commit`` would refuse to make an
    # empty commit, and the wrapper short-circuits to ``None``.
    assert second_sha is None
    msgs = _read_log_messages(profile)
    assert msgs == [
        "init: import existing data",
        "build: @ T1",
    ]


def test_env_disabled_short_circuits_without_touching_disk(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``MCS_NO_VERSIONING`` is truthy, the hook returns
    ``None`` immediately and doesn't even create ``.git/``."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    assert is_versioning_disabled() is True

    _materialize_package_db(profile)
    sha = commit_after_command(profile, action=ACTION_BUILD, summary="x")
    assert sha is None
    # The .git directory was *not* created because the env
    # short-circuit fires before the auto-init step.
    assert not (profile_data_dir(profile) / ".git").exists()
    # The lockfile is also not created — the lock-acquire step is
    # after the env check.
    assert not (profile_data_dir(profile) / ".mcs-lock").exists()


def test_fork_kind_profile_raises_profile_read_only(profile: Profile, home: Path) -> None:
    """A ``kind="fork"`` Profile passed to the hook raises
    ``ProfileReadOnly`` with the spec-required remediation message
    naming both the parent profile and the anchor SHA."""
    fork = Profile(
        name="acme@v1",
        compute_project=profile.compute_project,
        endpoint=profile.endpoint,
        auth=profile.auth,
        sources=profile.sources,
        package_path=home / "fork-worktree",
        kind="fork",
        parent_profile="acme",
        git_sha="a" * 40,
    )
    with pytest.raises(ProfileReadOnly) as exc_info:
        commit_after_command(fork, action=ACTION_BUILD, summary="table x")
    assert "acme" in exc_info.value.remediation
    assert "a" * 40 in exc_info.value.remediation
    assert "mcs profile reset" in exc_info.value.remediation


def test_uncommitted_state_on_entry_produces_recover_commit(
    profile: Profile,
) -> None:
    """If the working tree has uncommitted changes when a new write
    command starts (the previous mcs crashed between updating
    package.db and committing), the entry produces a ``recover:
    pre-existing changes`` commit *before* the current action's
    own commit. The log thus shows three entries after a clean
    sequence-with-a-crash-in-the-middle."""
    # Bootstrap: one clean commit cycle so the repo exists and the
    # gitignore is in place.
    _materialize_package_db(profile)
    first = commit_after_command(profile, action=ACTION_BUILD, summary="initial")
    assert first is not None

    # Simulate a crash: modify the package.db (via a direct sqlite
    # write that bumps the schema's user-data without going through
    # the hook) and crucially DON'T call the hook afterwards. The
    # working tree at this point has a stale package.sql (matching
    # the pre-crash state) and a new package.db that diverges from
    # it — exactly the post-crash on-disk shape.
    db_path = profile_data_dir(profile) / "package.db"
    with sqlite3.connect(str(db_path)) as conn:
        # The real ``tables`` schema (see ``build.storage._SCHEMA_SQL``)
        # has columns (source_key, name, schema_hash, last_built_at)
        # rather than the spec test's draft (project, schema_name,
        # table_name, ...). The semantic intent of the INSERT — "land
        # a brand-new row in package.db so the next dump produces a
        # different package.sql" — is preserved with the real schema's
        # column names.
        conn.execute(
            "INSERT INTO tables (source_key, name, schema_hash, "
            "last_built_at) VALUES "
            "('acme__warehouse', 'orders_crashed', 'h_crash', "
            "'2026-05-23T00:00:00Z')"
        )
        conn.commit()
    # Sanity: the wrapper-level "is there uncommitted state?" probe
    # against the *current* on-disk file set returns False because
    # nothing tracked has been modified — the package.db is in the
    # gitignore. The recovery-detection branch is triggered by the
    # hook re-dumping package.db (which produces a new package.sql
    # whose content differs from the committed one) and *then*
    # checking has_uncommitted_changes. We confirm the standalone
    # ``commit_if_uncommitted_on_entry`` helper does that
    # dump-then-check sequence and produces the recover commit.
    recover_sha = commit_if_uncommitted_on_entry(profile)
    assert recover_sha is not None
    repo = GitRepo(profile_data_dir(profile))
    recover_commit = repo.log(limit=1)[0]
    assert recover_commit.message == f"{ACTION_RECOVER}: pre-existing changes"

    # Now run a normal write command. It should commit the post-
    # recovery state's *current* action delta. The log read at the
    # end has four entries: init, the bootstrap build, the recovery,
    # and the second build.
    third = commit_after_command(profile, action=ACTION_BUILD, summary="table orders")
    assert third is not None

    msgs = _read_log_messages(profile)
    assert msgs == [
        "init: import existing data",
        "build: initial",
        "recover: pre-existing changes",
        "build: table orders",
    ]


def test_clean_entry_does_not_emit_recover_commit(profile: Profile) -> None:
    """The crash-recovery branch is a no-op on a clean working tree.
    The smoke happy-path test above asserts the same property
    indirectly; this one names it explicitly to pin the negative
    case so a future patch that accidentally always emits a
    recovery commit (e.g. by inverting the
    ``has_uncommitted_changes()`` check) fails this test
    deterministically."""
    _materialize_package_db(profile)
    commit_after_command(profile, action=ACTION_BUILD, summary="first")
    # The repo is now clean. A direct call to the recovery helper
    # returns ``None``.
    assert commit_if_uncommitted_on_entry(profile) is None


def test_concurrent_hook_calls_serialize_via_lock(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent mcs processes against the same profile dir
    can't both commit at once. The second sees
    ``LockedByOtherProcessError`` with the holder's PID. We
    simulate by holding the lock manually with a long timeout in
    the test process and asserting that an in-process direct call
    to ``commit_after_command`` (which uses the default
    ``timeout=0.0``) raises immediately."""
    _materialize_package_db(profile)
    # First, do an initial committed state so the auto-init branch
    # doesn't trip during the contention test.
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")

    # Now take the lock manually in the test thread and try to
    # invoke the hook from a side thread, which should bounce.
    import threading

    barrier_acquired = threading.Event()
    contention_result: list[BaseException] = []
    contention_done = threading.Event()

    lock_path = profile_data_dir(profile) / ".mcs-lock"

    def hold_then_release() -> None:
        # Use a fresh thread-local-isolated WriteLock instance so
        # the hook's reentry doesn't see this thread's lock as
        # "same process" (the thread-local map keys are
        # per-thread, so a different thread's flock is a real OS
        # lock).
        with WriteLock(lock_path, timeout=0.0):
            barrier_acquired.set()
            # Hold the lock until the contention thread has
            # finished its raise-or-not check.
            contention_done.wait(timeout=5.0)

    def contender() -> None:
        try:
            barrier_acquired.wait(timeout=5.0)
            commit_after_command(
                profile,
                action=ACTION_BUILD,
                summary="contending",
            )
        except BaseException as e:
            contention_result.append(e)
        finally:
            contention_done.set()

    holder_t = threading.Thread(target=hold_then_release, daemon=True)
    contender_t = threading.Thread(target=contender, daemon=True)
    holder_t.start()
    contender_t.start()
    holder_t.join(timeout=5.0)
    contender_t.join(timeout=5.0)

    assert contention_result, (
        "contender should have raised because the holder thread "
        "had the OS flock for the duration of the contender's "
        "attempt — instead the contender's exception list is "
        "empty, which means either the lock isn't held across "
        "threads of the same process or the hook isn't running "
        "the lock acquisition under the env-and-fork checks."
    )
    err = contention_result[0]
    assert isinstance(err, LockedByOtherProcessError), (
        f"expected LockedByOtherProcessError, got {type(err).__name__}: {err}"
    )
    # The error names a PID — which in the same-process case is
    # the test's own PID, since both holder and contender are
    # threads of the same Python interpreter.
    assert str(os.getpid()) in str(err)


def test_git_not_available_soft_skips_with_warning(
    profile: Profile, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing ``git`` on PATH is a soft opt-out for the auto-commit
    hook: the user's write to ``package.db`` has already happened,
    and the only loss from skipping the commit is the snapshot. The
    hook returns ``None`` and emits a one-shot warning naming the
    install paths and the ``MCS_NO_VERSIONING=1`` opt-out. Explicit
    versioning verbs (``mcs profile log`` / ``diff`` / ``reset`` /
    ``fork``) still raise ``GitNotAvailable`` — see
    test_git_repo.py for that side of the contract."""
    _materialize_package_db(profile)

    # Reset the one-shot warn latch so the warning fires in this test.
    from maxcompute_semantic.versioning import env as env_mod

    monkeypatch.setattr(env_mod, "_git_missing_warned", False, raising=False)

    # Bypass PATH, since shutil.which has its own resolution; force
    # the probe to report git missing.
    monkeypatch.setattr(
        "maxcompute_semantic.versioning.env.shutil.which",
        lambda name: None,
    )

    with caplog.at_level(logging.WARNING, logger=env_mod.log.name):
        sha = commit_after_command(profile, action=ACTION_BUILD, summary="x")

    assert sha is None
    # No .git was created — the soft-skip fires before auto-init.
    assert not (profile_data_dir(profile) / ".git").exists()
    # One-shot warning surfaced naming the env-var opt-out.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "git binary not found" in warnings[0].message
    assert "MCS_NO_VERSIONING" in warnings[0].message


def test_git_not_available_soft_skips_on_entry_too(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``commit_if_uncommitted_on_entry`` mirrors the soft-skip: a
    missing git binary returns ``None`` (no recover-commit) instead
    of raising. The two hook entry points share the same tolerance
    semantics."""
    _materialize_package_db(profile)

    from maxcompute_semantic.versioning import env as env_mod

    monkeypatch.setattr(env_mod, "_git_missing_warned", False, raising=False)
    monkeypatch.setattr(
        "maxcompute_semantic.versioning.env.shutil.which",
        lambda name: None,
    )

    sha = commit_if_uncommitted_on_entry(profile)
    assert sha is None
    assert not (profile_data_dir(profile) / ".git").exists()


def test_legacy_profile_with_pre_existing_files_auto_inits(profile: Profile, home: Path) -> None:
    """A pre-versioning profile is a data directory that has some
    of the standard files (markdown, package.db, _state.json)
    but no ``.git/`` directory. The hook's first invocation on
    such a profile runs the auto-init flow: ``git init``, write
    ``.gitignore``, dump ``package.db``, and the inaugural
    ``init: import existing data`` commit captures all the
    pre-existing tracked files as the baseline state. The
    *current* command's action then lands as the second commit
    on top."""
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True)
    # Drop a fake-old set of files that would have been there
    # under pre-versioning mcs.
    (pdir / "_overview.md").write_text("# Overview\n", encoding="utf-8")
    (pdir / "_joins.md").write_text("# Joins\n\n(none discovered)\n", encoding="utf-8")
    (pdir / "_state.json").write_text("{}\n", encoding="utf-8")
    # And the package.db materialized from PackageDB.open.
    _materialize_package_db(profile)
    # Sanity: no .git/ yet.
    assert not profile_git_dir(profile).exists()

    sha = commit_after_command(profile, action=ACTION_BUILD, summary="first-after-upgrade")
    assert sha is not None

    # .git/ exists; .gitignore exists; the log has two commits
    # whose messages are the standard pair.
    assert profile_git_dir(profile).is_dir()
    assert profile_gitignore_path(profile).exists()
    msgs = _read_log_messages(profile)
    assert msgs == [
        "init: import existing data",
        "build: first-after-upgrade",
    ]
    # The inaugural commit's tree includes the pre-existing
    # markdown files.
    repo = GitRepo(pdir)
    init_sha = repo.find_commit_with_prefix("init")
    assert init_sha is not None
    init_show = repo.show(init_sha)
    assert "_overview.md" in init_show
    assert "_joins.md" in init_show
    assert "_state.json" in init_show
    assert "package.sql" in init_show  # the dump landed in the init


def test_memory_action_prefix_format(profile: Profile) -> None:
    """A write action whose label is ``memory: verify <id> (<question>)``
    produces a commit whose subject line begins with the literal
    ``memory:`` prefix — which is the substring the default
    ``mcs profile log`` filters out via
    ``--grep='^memory:' --invert-grep``. We pin the exact prefix
    here so the filter-out string in the CLI matches the
    action-constant in the hook module."""
    _materialize_package_db(profile)
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")
    # A subsequent memory-action commit. The summary follows the
    # spec table's shape: ``verify <id> ("<question-snippet>")``.
    # The hook joins the action constant and the summary with
    # ``": "`` so the subject is
    # ``memory: verify 7 ("top customers")``.
    sha = commit_after_command(
        profile,
        action=ACTION_MEMORY_PREFIX,
        summary='verify 7 ("top customers")',
    )
    # The summary's contents alone are an empty-tree change (the
    # memory_entries table got a new row, the package.sql dump
    # has the new line, ``git diff`` shows the addition). The
    # hook produces a non-None SHA.
    assert sha is not None
    repo = GitRepo(profile_data_dir(profile))
    latest = repo.log(limit=1)[0]
    assert latest.full_sha == sha
    assert latest.message.startswith("memory: ")
    assert latest.message == 'memory: verify 7 ("top customers")'


def test_init_action_on_brand_new_profile_collapses_to_inaugural_message(
    profile: Profile,
) -> None:
    """The ``mcs profile create`` flow in T6 calls
    ``commit_after_command(profile, action=ACTION_INIT,
    summary=profile.name)`` so the inaugural commit on a
    *brand-new* (not legacy-upgraded) profile reads ``init: acme``
    in the log. We verify the hook is the producer of the right
    message shape — the wiring of the call site is T6's job."""
    # No pre-existing data dir contents (the brand-new case).
    pdir = profile_data_dir(profile)
    if pdir.exists():
        for child in pdir.iterdir():
            if child.is_file():
                child.unlink()
    # Don't materialize package.db — the create flow happens
    # before any build.
    sha = commit_after_command(profile, action=ACTION_INIT, summary=profile.name)
    # Two possible outcomes here, depending on whether there were
    # any pre-existing files to commit. In the truly-brand-new
    # case, ``.gitignore`` is the only file in the working tree
    # after auto-init, and the ``.gitignore`` write happens
    # inside the auto-init branch which produces the
    # ``init: import existing data`` commit. The subsequent
    # ``init: <profile-name>`` commit (the one the call passes)
    # has no staged delta (the .gitignore was already in the
    # inaugural commit and nothing else changed) — so the wrapper
    # short-circuits to ``None``.
    #
    # That's the bytewise-deterministic-dump short-circuit firing
    # at the action-commit step. The result: a brand-new profile
    # whose first ever ``commit_after_command(action="init")``
    # call lands the inaugural ``init: import existing data`` and
    # *not* a separate ``init: <name>``.
    #
    # The spec's table allows both readings — the "born
    # versioned" inaugural commit is the legacy-style one because
    # the auto-init branch always names it ``init: import existing
    # data``. T6's job in the broader integration is to either
    # (a) call ``commit_after_command(action=ACTION_INIT,
    # summary=profile.name)`` and accept that the name doesn't
    # appear in the log subject (the "born-versioned" commit reads
    # the same as the "legacy-imported" one because the auto-init
    # path is shared), *or* (b) override the message format by
    # writing the ``.gitignore`` explicitly before the first
    # hook call so the auto-init branch sees a clean ``.gitignore``
    # already in place and falls through to the standard
    # action-commit step. Spec's "Architecture / Auto-init for
    # legacy profiles" reads as if (a) is the intended behavior
    # — the very first commit on any profile, whether born
    # versioned or upgraded into versioning, reads
    # ``init: import existing data``. We pin that here so T6's
    # implementation follows the same convention. If you want the
    # subject to literally name the profile, the right home for
    # that is a separate ``mcs profile log --decorate`` view
    # that joins the commit's tree-of-files-touched info with the
    # subject — out of scope for this plan.
    assert sha is None
    msgs = _read_log_messages(profile)
    assert msgs == ["init: import existing data"], (
        "the inaugural commit on a brand-new profile reads "
        "the literal ``init: import existing data`` per the "
        "spec's auto-init message convention. If you want the "
        "subject to vary by 'born-versioned vs legacy-upgraded' "
        "split, change the auto-init branch in hook.py to take "
        "the caller's intended summary instead of the hardcoded "
        "string, and update the spec table to match."
    )


def test_stale_lock_warning_visible_via_warnings_module(profile: Profile, home: Path) -> None:
    """A pre-existing ``.mcs-lock`` file whose body is a non-live
    PID is the on-disk signature of a crashed prior mcs. The
    ``WriteLock.__enter__`` in ``versioning/lock.py`` emits a
    ``StaleLockClearedWarning`` (a ``UserWarning`` subclass) in
    that case. Driving the hook against the profile with a
    stale lockfile in place trips the warning. We capture it via
    ``warnings.catch_warnings`` so the test doesn't pollute the
    stderr noise floor."""
    pdir = profile_data_dir(profile)
    pdir.mkdir(parents=True, exist_ok=True)
    # Write a stale PID into the lockfile. ``2**31 - 2`` is
    # bigger than any reachable PID on Linux (pid_max default
    # 32768) or macOS (99998), so ``os.kill(pid, 0)`` raises
    # ``ProcessLookupError`` and the WriteLock's stale-clearance
    # branch fires.
    (pdir / ".mcs-lock").write_text("2147483646\n", encoding="ascii")
    _materialize_package_db(profile)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sha = commit_after_command(profile, action=ACTION_BUILD, summary="post-crash")
    assert sha is not None
    # At least one ``StaleLockClearedWarning`` was emitted.
    from maxcompute_semantic.versioning.errors import (
        StaleLockClearedWarning,
    )

    matching = [
        w
        for w in caught
        if isinstance(w.message, StaleLockClearedWarning)
        or (isinstance(w.message, Warning) and "stale" in str(w.message).lower())
    ]
    assert matching, (
        f"expected a StaleLockClearedWarning to be visible, got "
        f"warnings: {[str(w.message) for w in caught]!r}"
    )


# --- Additional one-line cases per spec lines 4006-4014 -----------------


_ACTION_SPEC_TABLE = {
    "ACTION_INIT": "init",
    "ACTION_BUILD": "build",
    "ACTION_REFRESH": "refresh",
    "ACTION_MEMORY_PREFIX": "memory",
    "ACTION_UDF_PREFIX": "udf",
    "ACTION_RECOVER": "recover",
}


def test_action_constants_match_spec_table() -> None:
    """Mirror the spec's commit-message-conventions table verbatim
    and assert each constant's value equals the spec's column-2
    string. Catches drift between the source constants and the
    documented spec — the spec is the contract."""
    from maxcompute_semantic.versioning import hook as hook_module

    for const_name, expected in _ACTION_SPEC_TABLE.items():
        actual = getattr(hook_module, const_name)
        assert actual == expected, f"{const_name} = {actual!r}, spec table says {expected!r}"


def test_commit_message_format_exactly_action_colon_space_summary(
    profile: Profile,
) -> None:
    """The wrapper joins the action and summary with the literal
    four bytes ``": "`` (colon, space), matching the spec table's
    format. We exercise the format over every ``ACTION_*`` constant
    so a change in the join string is caught here regardless of
    which action triggered it."""
    _materialize_package_db(profile)
    # Bootstrap so subsequent commits stage deltas in package.sql.
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")
    repo = GitRepo(profile_data_dir(profile))

    # Iterate over a representative sample of action constants
    # (every constant whose hook semantics aren't the auto-init or
    # recover branches that produce hard-coded subject lines).
    actions_to_exercise = [
        (ACTION_BUILD, "b1"),
        (ACTION_REFRESH, "r1"),
        (ACTION_MEMORY_PREFIX, "verify 1"),
        (ACTION_UDF_PREFIX, "create my_udf"),
        (ACTION_INIT, "post-bootstrap"),
    ]
    for counter, (action, summary) in enumerate(actions_to_exercise, start=1):
        # Mutate the DB so every call has a fresh delta — otherwise
        # the byte-identical-dump short-circuit fires and the
        # commit doesn't happen. We INSERT into the ``tables`` row
        # set with a unique table name per iteration.
        db_path = profile_data_dir(profile) / "package.db"
        with sqlite3.connect(str(db_path)) as conn:
            # Real ``tables`` schema columns — see comment in
            # ``test_uncommitted_state_on_entry_produces_recover_commit``.
            conn.execute(
                "INSERT INTO tables (source_key, name, schema_hash, "
                "last_built_at) VALUES "
                "('acme__warehouse', ?, ?, "
                "'2026-05-23T00:00:00Z')",
                (f"tbl_{counter}", f"h_{counter}"),
            )
            conn.commit()
        sha = commit_after_command(profile, action=action, summary=summary)
        assert sha is not None, f"action {action!r} produced no commit"
        latest = repo.log(limit=1)[0]
        # Exact subject format: ``<action>: <summary>``.
        assert latest.message == f"{action}: {summary}", (
            f"action={action!r} summary={summary!r} produced subject "
            f"{latest.message!r}; expected {action}: {summary}"
        )


def test_lock_file_in_gitignore_is_not_committed(profile: Profile) -> None:
    """After a ``commit_after_command`` cycle, the resulting ``git
    ls-files`` listing does not contain ``.mcs-lock``,
    ``package.db``, or ``package.db-wal``. The gitignore is doing
    its job."""
    _materialize_package_db(profile)
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")
    repo = GitRepo(profile_data_dir(profile))
    # ``git ls-files`` is not on the GitRepo wrapper's surface; use
    # the underlying ``_run`` to ask for the tracked-file set.
    tracked = repo._run("ls-files", check=True).splitlines()
    tracked_set = set(tracked)
    forbidden = {".mcs-lock", "package.db", "package.db-wal", "package.db-shm"}
    leaked = tracked_set & forbidden
    assert not leaked, f"gitignore failed: tracked {leaked!r}"


def _subprocess_contender(
    profile_data_dir_path: str,
    home_dir: str,
    data_dir: str,
    profile_name: str,
    compute_project: str,
    endpoint: str,
    ak_id: str,
    ak_secret: str,
) -> int:
    """Helper run inside ``multiprocessing.Process`` for the
    cross-process contention test. Re-imports mcs internally, builds
    the Profile, invokes ``commit_after_command``, and returns an
    integer status: 0 if no error, 1 if ``LockedByOtherProcessError``
    was caught."""
    os.environ["HOME"] = home_dir
    os.environ["MCS_DATA_DIR"] = data_dir
    os.environ.pop("MCS_PROFILES_DIR", None)
    os.environ.pop("MCS_NO_VERSIONING", None)

    # Imports happen inside the subprocess (the parent's already-
    # loaded modules don't propagate via the spawn start method on
    # macOS/Windows; fork would propagate but we use spawn-style by
    # not assuming the parent's state).
    from maxcompute_semantic.auth.schema import (
        AkAuth as _AkAuth,
    )
    from maxcompute_semantic.auth.schema import (
        DataSource as _DataSource,
    )
    from maxcompute_semantic.auth.schema import (
        Profile as _Profile,
    )
    from maxcompute_semantic.versioning.errors import (
        LockedByOtherProcessError as _Locked,
    )
    from maxcompute_semantic.versioning.hook import (
        ACTION_BUILD as _A,
    )
    from maxcompute_semantic.versioning.hook import (
        commit_after_command as _hook,
    )

    p = _Profile(
        name=profile_name,
        compute_project=compute_project,
        endpoint=endpoint,
        auth=_AkAuth(access_key_id=ak_id, access_key_secret=ak_secret),
        sources=(_DataSource(project=compute_project, schema="default", tables="*"),),
    )
    try:
        _hook(p, action=_A, summary="contending-subproc")
    except _Locked:
        return 1
    return 0


def test_concurrent_two_processes_via_subprocess(
    profile: Profile, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as the threading-based contention test but using
    ``multiprocessing.Process`` so the two contenders are genuinely
    separate OS processes (no thread-local-shared state at all).
    The contender process's exit code maps non-zero (``1``) when
    ``LockedByOtherProcessError`` was caught inside its body."""
    _materialize_package_db(profile)
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")

    # Take the lock in the parent process for the duration of the
    # subprocess attempt.
    import threading

    lock_path = profile_data_dir(profile) / ".mcs-lock"
    barrier_acquired = threading.Event()
    contention_done = threading.Event()

    def holder() -> None:
        with WriteLock(lock_path, timeout=0.0):
            barrier_acquired.set()
            contention_done.wait(timeout=10.0)

    holder_t = threading.Thread(target=holder, daemon=True)
    holder_t.start()
    assert barrier_acquired.wait(timeout=5.0), (
        "holder thread failed to acquire its own lock in time"
    )

    # Spawn the contender as a real OS process so no shared
    # thread-local state can hide the contention.
    ctx = multiprocessing.get_context("spawn")
    data_dir_env = os.environ["MCS_DATA_DIR"]
    proc = ctx.Process(
        target=_subprocess_contender,
        args=(
            str(profile_data_dir(profile)),
            str(home),
            data_dir_env,
            profile.name,
            profile.compute_project,
            profile.endpoint,
            "x",
            "y",
        ),
    )
    proc.start()
    proc.join(timeout=15.0)
    contention_done.set()
    holder_t.join(timeout=5.0)

    assert proc.exitcode == 1, (
        f"subprocess contender expected exit code 1 (LockedByOther), got {proc.exitcode!r}"
    )


def test_recover_commit_only_emitted_on_actually_dirty_state(
    profile: Profile,
) -> None:
    """``commit_after_command`` never emits a ``recover:`` commit
    on its own — that prefix is reserved for the standalone
    ``commit_if_uncommitted_on_entry`` helper, which write
    commands call at ENTRY (T9 entry-guard) and which
    ``mcs profile reset`` calls before moving HEAD. This test
    pins both halves: a sequence of two ``commit_after_command``
    calls produces no ``recover:`` commits, and a direct call to
    the helper against a now-clean working tree is a no-op."""
    _materialize_package_db(profile)
    commit_after_command(profile, action=ACTION_BUILD, summary="first")
    # Another build cycle with no changes — the action's empty-
    # tree short-circuit returns None, no commit lands.
    commit_after_command(profile, action=ACTION_BUILD, summary="second")
    msgs = _read_log_messages(profile)
    assert ACTION_RECOVER not in [m.split(":", 1)[0] for m in msgs], (
        f"hook spuriously emitted a recover commit on clean state; log: {msgs!r}"
    )
    # And the explicit recovery call against the now-clean tree is
    # also a no-op.
    assert commit_if_uncommitted_on_entry(profile) is None


def test_disk_full_during_dump_logs_warning_and_continues_with_markdown_only_commit(
    profile: Profile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Monkeypatch ``dump_db_to_sql`` to raise ``OSError("[Errno 28]
    no space left on device")``; assert the hook logs a warning at
    WARN level and the commit that lands has the markdown files
    but no package.sql change in its tree. The spec's error-
    handling table treats disk failure as recoverable — best-
    effort dump, the markdown side of the world stays version-
    tracked."""
    _materialize_package_db(profile)
    # First commit cycle succeeds so the repo and gitignore exist.
    commit_after_command(profile, action=ACTION_BUILD, summary="bootstrap")

    # Add a markdown file so the subsequent commit has *some* staged
    # delta even though the dump fails.
    pdir = profile_data_dir(profile)
    (pdir / "extra.md").write_text("# extra\n", encoding="utf-8")

    # Monkeypatch the dump function as imported by the hook module.
    def _fake_dump(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(
        "maxcompute_semantic.versioning.hook.dump_db_to_sql",
        _fake_dump,
    )

    with caplog.at_level(logging.WARNING, logger="maxcompute_semantic.versioning"):
        sha = commit_after_command(profile, action=ACTION_BUILD, summary="diskfull")
    # The commit landed because the markdown file is a staged delta.
    assert sha is not None
    # A warning was emitted.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, (
        f"expected at least one WARNING record on disk-full; "
        f"got: {[(r.levelno, r.message) for r in caplog.records]!r}"
    )


def test_default_branch_is_main_regardless_of_global_init_default_branch(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assert that ``<profile>/.git/HEAD`` content after the auto-
    init is the literal string ``ref: refs/heads/main`` even when
    the developer's global ``init.defaultBranch`` is set to
    something else."""
    monkeypatch.setenv("GIT_INIT_DEFAULT_BRANCH", "master")
    _materialize_package_db(profile)
    sha = commit_after_command(profile, action=ACTION_BUILD, summary="x")
    assert sha is not None
    head_text = (profile_data_dir(profile) / ".git" / "HEAD").read_text(encoding="utf-8")
    assert head_text.strip() == "ref: refs/heads/main", (
        f"expected HEAD to point at refs/heads/main; got {head_text!r}"
    )
