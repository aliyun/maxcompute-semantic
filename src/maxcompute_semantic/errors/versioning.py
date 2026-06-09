"""Versioning-subsystem error classes.

Subclasses of :class:`McsError` so the CLI boundary's ``except McsError``
block surfaces them with the same envelope and remediation rendering as
every other classified error in the codebase.
"""

from __future__ import annotations

from maxcompute_semantic.errors.base import ErrorCode, McsError


class LockedByOtherProcessError(McsError):
    """The per-profile ``.mcs-lock`` is held by another live mcs
    process. The error message names the holder's PID parsed out of
    the lockfile's body. The canonical remediation in the spec's
    error-handling table is "wait for the other process to finish, or
    kill it if it's a zombie"."""

    code = ErrorCode.LOCKED_BY_OTHER_PROCESS
    exit_code = 1


class GitNotAvailable(McsError):
    """The ``git`` executable isn't on PATH, or invoking
    ``git --version`` failed with a ``FileNotFoundError`` from the
    ``subprocess`` layer. Raised by the lazy ``_git_available()`` probe
    inside ``GitRepo._run`` so commands degrade gracefully on a machine
    without git installed. ``mcs doctor`` exposes the check as the
    ``git_available`` line item."""

    code = ErrorCode.GIT_NOT_AVAILABLE
    exit_code = 1


class ProfileReadOnly(McsError):
    """A write command was invoked against a ``kind="fork"`` profile.
    Forks are pinned to a parent commit and are read-only at the mcs
    layer — the remediation is to either ``mcs profile reset --to
    <sha>`` on the parent profile to adopt the fork's anchor commit as
    the new head of the main history, or ``mcs profile fork <new-name>
    --from <sha>`` to branch off into a fresh writable fork."""

    code = ErrorCode.PROFILE_READ_ONLY
    exit_code = 2


class PackageSqlCorrupt(McsError):
    """Raised by :func:`restore_sql_to_db` when the ``package.sql`` text
    dump can't be parsed or applied to a fresh sqlite database.

    Three failure modes funnel into this class:

    * The magic-comment header ``-- mcs-versioning: schema_version=N``
      is missing from the file. Either the file was written by a
      pre-versioning-era mcs (no longer supported) or the file was
      corrupted on disk.
    * The body's SQL is syntactically invalid and
      ``sqlite3.Connection.executescript`` raises
      ``sqlite3.DatabaseError`` while applying it.
    * The schema-version stamp the dump declares is outside the range
      the current mcs's ``build/storage.py:_SCHEMA_VERSION`` migration
      chain can rebuild from.

    The ``remediation`` field carries the ``git log -- package.sql``
    invocation the user runs to find the last good commit, then
    ``mcs profile reset --to <previous-sha>`` to roll the profile
    back to that state.
    """

    code = ErrorCode.PACKAGE_SQL_CORRUPT
    exit_code = 1


class StaleLockClearedWarning(UserWarning):
    """Emitted (via ``warnings.warn``, not raised) when ``WriteLock``
    detects and clears a stale lockfile on entry. This is a normal
    recovery from an interrupted prior mcs invocation, not an error
    condition; the warning makes the event visible in mcs's stderr so
    a user investigating a missing commit knows the prior process
    didn't exit cleanly."""
