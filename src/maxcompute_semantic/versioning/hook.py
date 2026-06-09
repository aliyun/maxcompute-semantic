# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Auto-commit hook for mcs write commands.

The two public functions — ``commit_after_command`` and
``commit_if_uncommitted_on_entry`` — wrap the per-profile git
repository at ``<profile_data_dir>/.git/`` and turn every mcs
write command into a versioned commit. The full algorithm follows
the spec's "Architecture / Auto-commit hook" section step-for-
step:

1. Env short-circuit on ``MCS_NO_VERSIONING``.
2. Fork-write rejection via ``_reject_if_fork``.
3. Acquire the ``.mcs-lock`` file lock (per-process flock).
4. Mutually-exclusive choice — either auto-init OR crash-recovery,
   never both. On a pre-versioning profile (``.git/`` absent), do
   ``git init -b main`` + write ``.gitignore`` + dump
   ``package.db`` → ``package.sql`` + ``add -A`` + inaugural
   ``init: import existing data`` commit so the imported data
   actually lands in history. On an existing repo, instead invoke
   ``commit_if_uncommitted_on_entry`` to snapshot any leftover
   uncommitted state from a prior interrupted mcs invocation as a
   ``recover: pre-existing changes`` commit before the current
   command's own work.
5. Best-effort dump of ``package.db`` → ``package.sql``
   (warn-and-continue on OSError so disk-full doesn't lose
   markdown-side work). On the just-auto-inited path this is a
   no-op second dump against the byte-identical bytes already on
   disk.
6. ``git add -A`` to stage everything not matched by
   ``.gitignore``.
7. ``GitRepo.commit("<action>: <summary>", allow_empty=...)``.
   The wrapper's empty-staged-tree short-circuit returns ``None``
   when the staged tree is byte-identical to ``HEAD``'s AND the
   action prefix matches HEAD's; the differs-from-HEAD-action
   case sets ``allow_empty=True`` so a write command's logical
   end-marker shows up in the log even when the underlying byte-
   deterministic dump has nothing new to say (the canonical
   example: a ``mcs build`` call right after the crash-recovery
   branch fired a ``recover:`` commit — the recover snapshot is
   the "interrupted prior work" and the action commit is the
   "current intent" marker that ``mcs profile reset --to
   last-build`` reaches for).
8. Release the lock (implicit via ``with``-block exit).

The ``ACTION_*`` module-level constants are the literal prefix
strings from the spec's "Commit message conventions" table —
every write command's wiring imports a constant rather than
hard-coding the string so the spec's table and the source agree
by construction.
"""

from __future__ import annotations

import logging
import sqlite3

from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_gitignore_path,
    profile_lock_path,
    profile_package_sql_path,
)
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.versioning.env import (
    is_git_available,
    is_versioning_disabled,
    warn_git_missing_once,
)
from maxcompute_semantic.versioning.errors import (
    ProfileReadOnly,
)
from maxcompute_semantic.versioning.git_repo import GitRepo
from maxcompute_semantic.versioning.gitignore_default import PROFILE_GITIGNORE
from maxcompute_semantic.versioning.lock import WriteLock
from maxcompute_semantic.versioning.sql_dump import dump_db_to_sql

log = logging.getLogger("maxcompute_semantic.versioning")


# --- Action prefix constants (spec "Commit message conventions" table) -----

ACTION_INIT = "init"
"""``init: <profile>`` on mcs profile create. Also
``init: import existing data`` on the inaugural commit (whether the
profile was born-versioned or upgraded from a pre-versioning data
directory) — the constant supplies the prefix and the caller (or
the auto-init branch) supplies the matching summary."""

ACTION_BUILD = "build"
"""``build: <profile> @ <ISO-timestamp>``."""

ACTION_REFRESH = "refresh"
"""``refresh: <profile> @ <ISO-timestamp>`` (mcs build --refresh)."""

ACTION_MEMORY_PREFIX = "memory"
"""``memory: verify <id> (...)`` / fail / note / remove / clear."""

ACTION_UDF_PREFIX = "udf"
"""``udf: create <name>`` / ``udf: remove <name>``."""

ACTION_METRIC_PREFIX = "metric"
"""``metric: add <name>`` / ``metric: edit <name>`` / ``metric: remove <name>``."""

ACTION_PACKAGE_PREFIX = "package"
"""``package: propose/apply/reject ...`` semantic proposal review workflow."""

ACTION_RECOVER = "recover"
"""``recover: pre-existing changes`` — emitted by the crash-
recovery helper, never by a write command."""


_INAUGURAL_COMMIT_SUMMARY = "import existing data"
"""Subject-line summary for the ``ACTION_INIT`` commit produced by
the auto-init branch. Used both for the first-contact-with-a-
pre-versioning-profile path and (collapsing through the byte-
deterministic short-circuit) for the brand-new-profile path that
``mcs profile create`` triggers via ``ACTION_INIT`` + the profile
name."""


# --- Internal helpers --------------------------------------------------------


def _schema_version_of(profile: Profile) -> int:
    """The integer ``PRAGMA user_version`` of the live ``package.db``,
    or the current source-tree's ``_SCHEMA_VERSION`` constant if the
    DB file doesn't exist yet (the brand-new-profile case where
    the user has run ``mcs profile create`` but no ``mcs build``
    yet — the inaugural commit still wants the magic comment's
    schema version field populated with *something* sensible, and
    the constant from build/storage is the right "what schema
    would the next build produce" answer)."""
    from maxcompute_semantic.build.storage import _SCHEMA_VERSION

    db_path = profile_data_dir(profile) / "package.db"
    if not db_path.exists():
        return _SCHEMA_VERSION
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("PRAGMA user_version").fetchone()
    except sqlite3.DatabaseError:
        # The file exists but isn't a valid SQLite database (truncated,
        # garbage bytes from a corrupted import, etc.). Fall back to the
        # source-tree constant so the magic-comment writer doesn't crash;
        # the matching markdown-only-degrade path in the dump call site
        # is the one that prevents the bad bytes from blocking the commit
        # entirely.
        return _SCHEMA_VERSION
    else:
        if row is None or row[0] == 0:
            # ``PRAGMA user_version`` of 0 is the default for a fresh
            # sqlite DB that nobody's stamped — should not happen for
            # an mcs-managed package.db because PackageDB.open stamps
            # the constant during ``_ensure_schema``. If it does
            # happen (a hand-created DB file, or a corruption), fall
            # back to the source-tree constant so the magic comment
            # in the dump isn't a confusing zero. The mismatch will
            # surface to the user as a "package was built by mcs
            # X.Y.Z" message on the next ``PackageDB.open`` migration
            # check.
            return _SCHEMA_VERSION
        return int(row[0])


def _ensure_gitignore_present(profile: Profile) -> None:
    """Write the canonical ``.gitignore`` into the profile data dir
    if it's not already there. Idempotent — a pre-existing file is
    left untouched.

    The write is plain — no atomic rename — because the worst-case
    failure mode is a partially-written gitignore on the next mcs
    invocation's ``git add -A``, which is a recoverable hand-fix.
    The atomic-rename overhead is therefore not warranted.
    """
    gi_path = profile_gitignore_path(profile)
    if gi_path.exists():
        return
    gi_path.parent.mkdir(parents=True, exist_ok=True)
    gi_path.write_text(PROFILE_GITIGNORE, encoding="utf-8")


def _reject_if_fork(profile: Profile) -> None:
    """Raise ``ProfileReadOnly`` if ``profile.kind == "fork"``.

    A fork is an alias backed by a detached ``git worktree`` in
    the parent profile's repo; the mcs layer treats it as read-
    only — every write command's entry calls this helper before
    touching ``package.db`` or the per-table markdown. The
    remediation message names both the parent profile and the
    fork's anchor SHA so the user can either ``mcs profile reset
    --to <sha>`` on the parent to adopt the anchor as the new
    head of the main history, or ``mcs profile fork <new-name>
    --from <sha>`` to branch off into a fresh writable fork.
    """
    if profile.kind != "fork":
        return
    parent = profile.parent_profile or "<unknown>"
    sha = profile.git_sha or "<unknown>"
    raise ProfileReadOnly(
        f"profile {profile.name!r} is a fork of {parent!r} "
        f"at {sha} and cannot be modified directly.",
        remediation=(
            f"forks are read-only at the mcs layer. To adopt the "
            f"fork's anchor commit {sha} as the new head of "
            f"{parent!r}'s history, run ``mcs profile reset --to "
            f"{sha} --profile {parent}``. To branch off into a "
            f"fresh writable fork at a different SHA, run "
            f"``mcs profile fork <new-name> --from <sha> "
            f"--profile {parent}``. Direct writes against the "
            f"fork alias are rejected so the fork's anchor "
            f"contract isn't silently broken."
        ),
    )


def _head_action_prefix(repo: GitRepo) -> str | None:
    """The ``action`` portion (the substring before the first
    ``":"``) of HEAD's commit-subject line, or ``None`` if the
    repo has no HEAD yet (unborn-branch state right after
    ``git init -b main`` and before the inaugural commit).

    Used by the step-7 commit call site to decide whether an
    empty-diff action commit should short-circuit (same action as
    HEAD — the byte-deterministic dump's "no change" case is
    genuinely a no-op) or land via ``allow_empty=True`` (different
    action — the new action's commit is the logical end-marker of
    a different write command that the user-facing ``mcs profile
    log`` will show).
    """
    commits = repo.log(limit=1)
    if not commits:
        return None
    subject = commits[0].message
    prefix, _, _ = subject.partition(":")
    return prefix.strip() or None


# --- Public functions --------------------------------------------------------


def commit_if_uncommitted_on_entry(profile: Profile) -> str | None:
    """Snapshot any leftover uncommitted state from a prior
    interrupted mcs invocation as a single ``recover: pre-existing
    changes`` commit. Returns the new SHA, or ``None`` if no
    snapshot was needed.

    This is the standalone helper that the write-command entry
    invokes (and that ``commit_after_command`` re-enters from
    step 5 — the reentrant ``WriteLock`` makes the inner acquire
    a no-op). Broken out so ``mcs profile reset`` can also call
    it directly before moving ``HEAD`` — the spec wants the pre-
    reset working tree captured in history before the destructive
    reset moves the pointer.
    """
    # Step 1: env-disabled short-circuit.
    if is_versioning_disabled():
        return None
    # Step 1b: git-missing soft opt-out. Without git, every git op
    # below would raise GitNotAvailable — but the user's write to
    # disk has already happened; the only loss from skipping is the
    # snapshot. Treat it like MCS_NO_VERSIONING=1 (silent return)
    # plus a one-shot warning so the user knows versioning is off.
    if not is_git_available():
        warn_git_missing_once()
        return None
    # Step 2: fork-kind profiles aren't mcs's responsibility for
    # uncommitted state — the fork's working tree may carry the
    # user's hand-edits which get the standard git semantics, not
    # an mcs auto-commit.
    if profile.kind == "fork":
        return None
    # Step 3: pre-versioning profiles haven't been auto-inited
    # yet; ``commit_after_command``'s step 4 will roll the dirty
    # state into the inaugural ``init: import existing data``
    # commit, so a separate ``recover:`` commit is unwarranted.
    pdir = profile_data_dir(profile)
    repo = GitRepo(pdir)
    if not repo.exists():
        return None
    # Step 4: acquire the lock (reentrant on the calling thread).
    lock_path = profile_lock_path(profile)
    with WriteLock(lock_path):
        # Step 5: re-dump package.db so the recovery commit's textual
        # diff reflects the post-crash DB state. The atomic-tmpfile-
        # rename inside ``dump_db_to_sql`` means an interrupted dump
        # doesn't corrupt the prior ``package.sql``. On OSError
        # (disk-full / RO mount) we degrade to a markdown-only
        # recovery commit so the user's hand-edits aren't lost; the
        # equivalent step in ``commit_after_command`` follows the
        # same contract.
        db_path = pdir / "package.db"
        if db_path.exists():
            try:
                dump_db_to_sql(
                    db_path,
                    profile_package_sql_path(profile),
                    schema_version=_schema_version_of(profile),
                )
            except (OSError, sqlite3.DatabaseError) as e:
                log.warning(
                    "recovery dump of package.db failed (%s); proceeding "
                    "with markdown-only recovery commit",
                    e,
                )
        # Step 6: ensure the gitignore is in place so .mcs-lock /
        # package.db / tier_cache aren't swept up by ``git add -A``.
        _ensure_gitignore_present(profile)
        # Step 7: nothing to recover if the working tree is clean.
        if not repo.has_uncommitted_changes():
            return None
        # Step 8: snapshot.
        repo.add_all()
        sha = repo.commit(f"{ACTION_RECOVER}: pre-existing changes")
        if sha is not None:
            log.info(
                "recovered pre-existing uncommitted changes in profile %r as commit %s",
                profile.name,
                sha[:8],
            )
        return sha


def commit_after_command(profile: Profile, *, action: str, summary: str) -> str | None:
    """End-of-write hook. Commits the current on-disk state of
    ``<profile_data_dir>`` as ``<action>: <summary>``. Returns
    the new commit's full 40-hex SHA, or ``None`` if the call
    was a no-op (env-disabled, or the staged tree was byte-
    identical to ``HEAD``'s and the action prefix matches HEAD's).

    The hook is called at the very end of every mcs write command
    (build / annotate / memory verify / udf create / …). The
    write itself happens *before* the call — by the time the hook
    runs, ``package.db`` and the per-table markdown files are
    already on disk in their post-command state, and the hook's
    job is purely to commit. Failures inside the hook propagate
    as ``McsError`` to the CLI boundary; the env-disabled case
    is the only silent return-None.
    """
    # Step 1: env-disabled short-circuit. Note that the user's
    # write to package.db / markdown is already on disk; only the
    # commit is skipped, not the write.
    if is_versioning_disabled():
        log.debug("skipping commit for profile %r, env-disabled", profile.name)
        return None
    # Step 1b: git-missing soft opt-out — same reasoning as in
    # ``commit_if_uncommitted_on_entry``. The auto-commit hook
    # silently degrades; explicit versioning verbs (mcs profile
    # log/diff/reset/fork) still surface GitNotAvailable.
    if not is_git_available():
        warn_git_missing_once()
        return None
    # Step 2: fork-write guard. Today this is the only entry-side
    # guard — T9 will add the per-write-command entry-side check
    # and this call becomes the backstop for any code path that
    # bypasses it.
    _reject_if_fork(profile)
    # Step 3: open the wrapper and acquire the lock.
    pdir = profile_data_dir(profile)
    repo = GitRepo(pdir)
    lock_path = profile_lock_path(profile)
    with WriteLock(lock_path):
        # Step 4: auto-init path runs only on a pre-versioning
        # profile (no ``.git/`` yet). On an existing repo we go
        # straight to dump+add+commit — the dirty state at tail
        # time IS the current command's writes, not a prior
        # interrupted command's leftover state. Crash recovery is
        # the responsibility of ``commit_if_uncommitted_on_entry``
        # called at write-command ENTRY (T9 wires it onto every
        # verb's entry-guard). Calling it again here at TAIL would
        # split every write into a ``recover:`` + empty ``action:``
        # pair, which is both ugly in the log and wrong: the dirty
        # state belongs to the action, not to a prior crash.
        if not repo.exists():
            # Auto-init path. Write gitignore + dump package.db
            # so the inaugural ``init: import existing data``
            # commit *captures* the imported data (rather than a
            # placeholder pointing at a yet-to-be-committed
            # package.sql). On the brand-new-profile path
            # (``mcs profile create``) package.db doesn't exist
            # yet and the dump is skipped — the inaugural commit
            # carries only the gitignore + any markdown / json
            # files the caller staged before this hook fired.
            log.info(
                "auto-initializing per-profile git repo for %r at %s",
                profile.name,
                pdir,
            )
            repo.init()
            _ensure_gitignore_present(profile)
            db_path = pdir / "package.db"
            if db_path.exists():
                try:
                    dump_db_to_sql(
                        db_path,
                        profile_package_sql_path(profile),
                        schema_version=_schema_version_of(profile),
                    )
                except (OSError, sqlite3.DatabaseError) as e:
                    # Disk-full / RO mount or corrupted DB bytes on
                    # auto-init — see the equivalent block in
                    # ``commit_if_uncommitted_on_entry`` for the
                    # canonical why. The inaugural commit degrades to
                    # a markdown-only snapshot so the user doesn't
                    # lose any hand-edits. The corrupted-DB path can
                    # legitimately fire on ``mcs profile import`` of
                    # an archive whose ``package.db`` is from an
                    # incompatible mcs major version.
                    log.warning(
                        "auto-init: package.db dump for %r failed (%s); "
                        "committing markdown-only inaugural snapshot",
                        profile.name,
                        e,
                    )
            repo.add_all()
            repo.commit(f"{ACTION_INIT}: {_INAUGURAL_COMMIT_SUMMARY}")
        # Step 5: best-effort dump capturing the post-command DB
        # state. On the auto-init path above this is a no-op second
        # dump against the byte-identical bytes already on disk; on
        # the recovery path it captures the post-command state of
        # ``package.db``. See the canonical WHY in
        # ``commit_if_uncommitted_on_entry``'s step 5 for the
        # OSError-degrade-to-markdown-only contract.
        db_path = pdir / "package.db"
        if db_path.exists():
            try:
                dump_db_to_sql(
                    db_path,
                    profile_package_sql_path(profile),
                    schema_version=_schema_version_of(profile),
                )
            except (OSError, sqlite3.DatabaseError) as e:
                log.warning(
                    "dump of package.db for %r failed (%s); committing markdown-only delta",
                    profile.name,
                    e,
                )
        # Step 6: stage everything not in .gitignore.
        repo.add_all()
        # Step 7: commit. ``GitRepo.commit`` short-circuits to
        # None when the staged tree is byte-identical to HEAD;
        # the ``allow_empty=True`` opt-in for different action
        # prefixes ensures the action commit appears in the log
        # as the write command's logical end-marker even when no
        # on-disk delta exists.
        head_action = _head_action_prefix(repo)
        allow_empty = head_action is not None and head_action != action
        sha = repo.commit(f"{action}: {summary}", allow_empty=allow_empty)
        return sha
