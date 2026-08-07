# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Per-profile write serialization via ``filelock`` + a PID-body hint.

Two files, one purpose. The user-supplied ``lock_path`` (e.g.
``<profile_data_dir>/.mcs-lock``) is the **PID body file** — we own
its contents end-to-end and write the holder's PID into it. A
sibling at ``<lock_path>.flock`` is the **OS lock anchor** owned by
the ``filelock`` library (POSIX ``flock`` on Linux/macOS,
``msvcrt.locking`` on Windows). The split is necessary because
``filelock``'s Unix backend opens its anchor with ``O_TRUNC``, which
fires *before* the flock attempt and would wipe the body bytes on
every contention attempt — leaving us with "PID unknown" in the
"another mcs is running" error message. Keeping the body in a
separate file we never reopen with ``O_TRUNC`` preserves the
forensic record across failed acquires.

The locking convention:

- Non-blocking acquire is the default (``timeout=0.0``). A positive
  timeout polls every ``poll_interval_secs`` until the deadline. Both
  cases use ``filelock.FileLock.acquire`` and translate
  ``filelock.Timeout`` into ``LockedByOtherProcessError`` carrying the
  holder's PID read out of the body file (which the prior holder
  wrote and our failed acquire didn't touch).

- Same-process reentrancy is a no-op. ``filelock.FileLock`` keeps an
  internal per-thread counter, but the counter is per-instance — so two
  ``WriteLock`` instances pointing at the same anchor would not share
  the counter and the inner acquire would deadlock against the outer's
  OS-level lock. We work around this with a module-level cache that
  returns the same ``FileLock`` for the same resolved anchor path.

- Stale-PID detection on entry. After taking the OS lock successfully
  (which by definition means no live process holds it), the wrapper
  reads the body file's PID and probes whether that PID is still
  alive. POSIX uses ``os.kill(pid, 0)``; Windows uses ``OpenProcess``
  via ``ctypes`` because ``os.kill(pid, 0)`` on Windows is not a
  liveness probe — it calls ``TerminateProcess`` with exit code 0
  (any non-CTRL signal does), which would actually kill the prior
  holder. If the PID is unallocated, the wrapper emits
  ``warnings.warn(StaleLockClearedWarning(...))`` and proceeds. A
  permission-denied result (the PID is alive but owned by a different
  user — e.g. a system-wide installation where two users share a
  profile path on a network mount) is treated as "alive, not stale".

- The body is written with ``\\n``-terminated decimal PID at the end
  of ``__enter__`` and is *not* erased on ``__exit__`` (the file
  remains as a "who-held-this-last" tombstone for forensic purposes;
  the next acquirer overwrites it). Both the body file and the
  ``.flock`` anchor are in ``.gitignore`` (see
  ``versioning/gitignore_default.py``) and excluded from
  ``mcs profile export`` archives.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import warnings
from pathlib import Path
from types import TracebackType

from filelock import BaseFileLock, FileLock
from filelock import Timeout as FileLockTimeout
from typing_extensions import Self

from maxcompute_semantic.versioning.errors import (
    LockedByOtherProcessError,
    StaleLockClearedWarning,
)

# Suffix for the hidden filelock OS-anchor sibling. ``.mcs-lock``
# stays as the user-facing PID body file; ``.mcs-lock.flock`` is the
# anchor filelock truncates / opens / closes / locks. Both are
# gitignored in ``gitignore_default.py`` and excluded from export
# archives in ``commands/profile_export.py``.
_FLOCK_ANCHOR_SUFFIX = ".flock"


def _anchor_path_for(body_path: Path) -> Path:
    """Hidden sibling that ``filelock`` opens for the OS lock. Keeping
    the body file untouched by filelock's O_TRUNC means the PID body
    survives a contender's failed acquire attempt."""
    return body_path.with_name(body_path.name + _FLOCK_ANCHOR_SUFFIX)


# Cache ``FileLock`` instances by resolved anchor path so two
# ``WriteLock(p)`` calls in the same process share the per-thread
# reentrancy counter that ``filelock`` maintains on each instance.
# Without the cache, the inner acquire from the same thread would
# attempt the OS-level lock again and deadlock against the outer's
# hold (POSIX flock and Windows msvcrt are both per-fd, not
# per-process).
_FILELOCK_CACHE: dict[str, BaseFileLock] = {}
_FILELOCK_CACHE_LOCK = threading.Lock()


def _get_or_create_filelock(anchor_key: str) -> BaseFileLock:
    with _FILELOCK_CACHE_LOCK:
        existing = _FILELOCK_CACHE.get(anchor_key)
        if existing is not None:
            return existing
        new_lock = FileLock(anchor_key, thread_local=True)
        _FILELOCK_CACHE[anchor_key] = new_lock
        return new_lock


def _read_holder_pid(body_path: Path) -> int | None:
    """Read the body file and return the parsed PID, or ``None`` if
    the body is missing / empty / non-integer / non-positive (the
    "corrupted body" case which the contention error message
    degrades to "unknown holder PID" on).

    Non-positive PIDs (``"0"``, ``"-1"``, …) are rejected because
    ``os.kill(pid, 0)`` on POSIX interprets ``0`` as "signal every
    process in my process group" and negative values as "signal the
    process group with that pgid" — both syscall side-effects we must
    never trigger from a liveness probe. Treating those bodies as
    corrupted is also semantically right: a real mcs writes its own
    ``os.getpid()``, which is always positive.
    """
    try:
        body = body_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    if not body:
        return None
    try:
        pid = int(body)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # OpenProcess access right that succeeds for any process the caller
    # can describe — even ones running as another user. We don't need
    # to read memory or terminate, only to learn "does this PID exist".
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _ERROR_ACCESS_DENIED = 5

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _pid_is_alive(pid: int) -> bool:
        """Windows liveness probe via ``OpenProcess``. We deliberately
        do NOT use ``os.kill(pid, 0)``: on Windows that path calls
        ``TerminateProcess(handle, 0)`` (Python's ``os.kill`` on Windows
        only special-cases ``CTRL_C_EVENT`` / ``CTRL_BREAK_EVENT``, and
        every other signal value goes through ``TerminateProcess``).
        Using it here would actually kill the prior holder.
        """
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            _kernel32.CloseHandle(h)
            return True
        # NULL handle — figure out why. Access denied means the process
        # exists but the caller's token can't open it; treat as alive.
        # Anything else (most commonly ERROR_INVALID_PARAMETER, which
        # Windows returns for unallocated PIDs) means dead.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED

else:

    def _pid_is_alive(pid: int) -> bool:
        """POSIX liveness probe via ``os.kill(pid, 0)``. ``True`` if
        signal 0 succeeds or raises ``PermissionError`` (the process
        exists but is owned by another user). ``False`` if
        ``ProcessLookupError`` (ESRCH — the PID is unallocated).

        The PID-recycling race is unmitigated and benign: in the tiny
        window where a crashed-mcs's PID has been recycled to an
        unrelated process, we treat the lockfile as "held by a live
        process" and the second mcs gets a
        ``LockedByOtherProcessError``. The user retries. Race
        resolution that would require a kernel-level "this PID is the
        same process the file claims" check is out of scope — the OS
        flock semantics already prevent two live mcs processes from
        both holding, which is the only correctness requirement here.
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


class WriteLock:
    """Context manager that takes an exclusive advisory file lock on
    the per-profile lock anchor. See module docstring for the full
    contract."""

    __slots__ = ("_anchor_key", "_anchor_path", "_body_path", "_poll_interval_secs", "_timeout")

    def __init__(
        self,
        lock_path: Path,
        *,
        timeout: float = 0.0,
        poll_interval_secs: float = 0.05,
    ) -> None:
        self._body_path = Path(lock_path)
        self._anchor_path = _anchor_path_for(self._body_path)
        self._timeout = float(timeout)
        self._poll_interval_secs = float(poll_interval_secs)
        # Key the cache on the *resolved* anchor path so two name
        # spellings of the same on-disk file (relative vs absolute,
        # symlink vs target) share the counter and the OS lock is
        # taken exactly once.
        try:
            self._anchor_key = str(self._anchor_path.resolve(strict=False))
        except OSError:
            # ``resolve()`` can fail on some filesystems for paths
            # whose parent doesn't exist yet; fall back to the
            # absolute non-resolved form. mkdir-in-__enter__ then
            # creates the parent and the next call resolves cleanly.
            self._anchor_key = str(self._anchor_path.absolute())

    def __enter__(self) -> Self:
        # Create parent directory if missing (so the test-fixture
        # pattern of pointing the lock at a sub-path of a fresh
        # tempdir works without the test having to mkdir first).
        self._body_path.parent.mkdir(parents=True, exist_ok=True)

        fl = _get_or_create_filelock(self._anchor_key)
        # Snapshot whether this is the outermost acquire BEFORE calling
        # acquire(). After acquire, ``lock_counter`` is at least 1; the
        # outermost vs nested distinction is whether it was 0 before.
        is_outermost = fl.lock_counter == 0

        try:
            if self._timeout <= 0:
                fl.acquire(blocking=False)
            else:
                fl.acquire(
                    timeout=self._timeout,
                    poll_interval=self._poll_interval_secs,
                )
        except FileLockTimeout:
            # Body file is untouched by filelock's O_TRUNC because
            # filelock only opens the .flock anchor — so the holder's
            # PID is still in the body file we read here.
            holder = _read_holder_pid(self._body_path)
            holder_str = str(holder) if holder is not None else "unknown"
            raise LockedByOtherProcessError(
                f"profile lock at {self._body_path} is held by "
                f"another mcs process (PID {holder_str}).",
                remediation=(
                    "wait for the other process to finish; "
                    "if it has crashed and the lockfile body's "
                    "PID is no longer alive, the next mcs "
                    "invocation will detect the stale state "
                    "and proceed. To force-clear by hand, "
                    f"``rm {self._body_path}`` after confirming "
                    "no live mcs is running against the profile."
                ),
            ) from None

        if not is_outermost:
            # Inner acquire from a nested ``with`` in the same thread;
            # ``filelock`` already incremented its counter without
            # touching the OS lock. Skip stale-PID detection and PID
            # body rewrite (the outer acquire's body is correct).
            return self

        # Outermost acquire. Stale-body detection: the OS lock was
        # released by the kernel when the prior holder exited (cleanly
        # or via SIGKILL), but the body file's text is still whatever
        # PID the prior holder wrote. If that PID is no longer alive
        # we emit a warning and proceed; the body gets overwritten
        # below regardless.
        prior_pid = _read_holder_pid(self._body_path)
        if prior_pid is not None and prior_pid != os.getpid() and not _pid_is_alive(prior_pid):
            warnings.warn(
                StaleLockClearedWarning(
                    f"cleared stale lockfile at {self._body_path} "
                    f"(prior holder PID {prior_pid} is no longer alive)"
                ),
                stacklevel=2,
            )
            logging.getLogger("maxcompute_semantic").debug(
                "cleared stale lockfile at %s (prior holder PID %s is no longer alive)",
                self._body_path,
                prior_pid,
            )

        # Write our PID into the body. No fsync — the body is a
        # forensic hint, not authoritative state; the OS lock on the
        # ``.flock`` anchor is the source of truth and survives
        # crashes.
        self._body_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        fl = _get_or_create_filelock(self._anchor_key)
        # ``filelock.release`` decrements the counter and closes the
        # underlying fd (releasing the OS lock) only on the outermost
        # release. The body file's bytes are left on disk as a
        # forensic record — the next acquirer overwrites.
        fl.release()
