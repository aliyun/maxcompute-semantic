# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""WriteLock — fcntl-based per-profile write serialization.

The lock file lives at ``<profile_data_dir>/.mcs-lock``. Its body is the
holder's PID. A process re-entering the lock from the same Python
interpreter is a no-op (nesting counter on a thread-local). A second
process attempting to take the lock with ``LOCK_NB`` fails immediately
and the error message names the holder PID parsed out of the file's
body. A stale lock file whose PID no longer corresponds to a live
process is detected (via ``os.kill(pid, 0)`` raising
``ProcessLookupError``) and silently cleared on entry, so an interrupted
mcs invocation doesn't permanently lock its profile.
"""

from __future__ import annotations

import multiprocessing
import os
import time
import warnings
from pathlib import Path

import pytest
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning.errors import (
    LockedByOtherProcessError,
    StaleLockClearedWarning,
)
from maxcompute_semantic.versioning.lock import WriteLock

# Use ``fork`` start method on every platform so the child process
# inherits the parent's importable modules without having to re-import
# the test module by name — which the default ``spawn`` start method
# on macOS / Windows can't do for sibling test modules under
# ``tests.unit.*`` because those paths aren't on ``sys.path`` in the
# bare child interpreter.
_mp_ctx = multiprocessing.get_context("fork")


def test_acquire_writes_pid_and_releases_on_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / ".mcs-lock"
    assert not lock_path.exists()
    with WriteLock(lock_path):
        body = lock_path.read_text(encoding="ascii").strip()
        assert body == str(os.getpid())
    # Exit drops the body but the file itself may remain (it's a lock
    # anchor; the next acquirer truncates and rewrites). We only require
    # the lock to not be *held* — checkable by a second acquire from the
    # same process succeeding immediately.
    with WriteLock(lock_path):
        pass


def test_reentrant_within_same_process_is_noop(tmp_path: Path) -> None:
    lock_path = tmp_path / ".mcs-lock"
    with WriteLock(lock_path):
        # Nested acquire from the same process should succeed without
        # blocking and without producing a second flock syscall on the
        # OS — verified indirectly by the absence of a timeout. The
        # outer-scope context manager still holds the lock during the
        # inner block.
        with WriteLock(lock_path):
            assert lock_path.read_text().strip() == str(os.getpid())
        # After the inner exits the outer is still holding (the nesting
        # counter is now 1, not 0); the body's PID is unchanged.
        assert lock_path.read_text().strip() == str(os.getpid())


def _hold_lock_in_child(lock_path_str: str, ready_path_str: str, release_after_secs: float) -> None:
    """Subprocess entrypoint: acquire the lock, signal readiness by
    creating ``ready_path``, then sleep before releasing. Used to drive
    the cross-process contention test below."""
    ready_path = Path(ready_path_str)
    with WriteLock(Path(lock_path_str)):
        ready_path.touch()
        time.sleep(release_after_secs)


def test_cross_process_contention_raises_locked_by_other(tmp_path: Path) -> None:
    lock_path = tmp_path / ".mcs-lock"
    ready_path = tmp_path / ".ready"
    proc = _mp_ctx.Process(
        target=_hold_lock_in_child,
        args=(str(lock_path), str(ready_path), 2.0),
    )
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if time.monotonic() > deadline:
                pytest.fail("child process never signalled lock acquisition")
            time.sleep(0.05)
        # Child holds the lock for ~2s; parent's attempt must fail-fast.
        with (
            pytest.raises(LockedByOtherProcessError) as exc_info,
            WriteLock(lock_path, timeout=0.0),
        ):
            pytest.fail("expected LockedByOtherProcessError, got the lock")
        # The error message names the holder PID so the user knows who's
        # holding it (the canonical "another mcs is writing to this
        # profile (PID NNN)" string from the spec's error-handling
        # table).
        assert str(proc.pid) in str(exc_info.value)
        assert "PID" in str(exc_info.value) or "pid" in str(exc_info.value)
    finally:
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def test_stale_lockfile_with_dead_pid_is_cleared(tmp_path: Path) -> None:
    """A lockfile whose body is a non-existent PID is leftover state
    from an interrupted mcs run. The next acquire must clear it
    silently and proceed — otherwise the profile becomes permanently
    locked after any SIGKILL of mcs."""
    lock_path = tmp_path / ".mcs-lock"
    # PID 2**31 - 1 is the maximum signed-32-bit PID. Linux's default
    # PID-max is 32768 (kernel.pid_max); on macOS the cap is 99998.
    # Either way the value below is unallocatable, so os.kill(pid, 0)
    # raises ProcessLookupError. We don't pick 0 or 1 (both are
    # special: 0 is "current process group", 1 is init).
    fake_dead_pid = 2**31 - 2
    lock_path.write_text(f"{fake_dead_pid}\n", encoding="ascii")
    # No flock is actually held by the OS — the bytes in the file are
    # the only "is-it-locked" signal. The WriteLock acquisition path
    # tries fcntl.flock first (succeeds because nothing else has the
    # OS lock), then reads the body and finds a PID that doesn't exist
    # (os.kill(pid, 0) → ProcessLookupError), so it overwrites the body
    # with its own PID instead of failing.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StaleLockClearedWarning)
        with WriteLock(lock_path):
            body_now = lock_path.read_text(encoding="ascii").strip()
            assert body_now == str(os.getpid())


def test_lockfile_holder_pid_zero_or_negative_is_treated_as_corrupt(
    tmp_path: Path,
) -> None:
    """A lockfile body of ``"0"`` or a negative integer is treated as
    corrupted state, not a live holder. The acquire path must
    overwrite it with the new PID without calling ``os.kill(pid, 0)``
    — calling ``os.kill(0, 0)`` or ``os.kill(-N, 0)`` signals the
    current process group / a foreign group, a syscall side effect
    we must never trigger from a liveness probe."""
    lock_path = tmp_path / ".mcs-lock"
    for bad_body in ("0", "0\n", "-1", "-42\n"):
        lock_path.write_text(bad_body, encoding="ascii")
        # No StaleLockClearedWarning expected — a non-positive body
        # is dropped at parse time (returns None from
        # _read_holder_pid), so the staleness check is skipped.
        with warnings.catch_warnings():
            warnings.simplefilter("error", StaleLockClearedWarning)
            with WriteLock(lock_path):
                assert lock_path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_lockfile_holder_pid_parse_tolerates_trailing_newline(tmp_path: Path) -> None:
    """Defensive: the PID body has a trailing newline (we write with
    ``\\n``); the parser must ``int(body.strip())``, not ``int(body)``,
    or a hand-written lockfile with a single newline at the end of the
    digits will raise ``ValueError`` and crash the contention path."""
    lock_path = tmp_path / ".mcs-lock"
    # Simulate the child-process body shape: PID followed by a newline.
    # The flock attempt below has nobody else holding the OS lock so
    # the WriteLock should still acquire — the stale-PID branch sees a
    # body the int() of which has to work despite the newline.
    lock_path.write_text("99999999\n", encoding="ascii")  # also a dead PID
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StaleLockClearedWarning)
        with WriteLock(lock_path):
            assert lock_path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_directory_does_not_exist_creates_parent(tmp_path: Path) -> None:
    """``WriteLock`` ``mkdir(parents=True, exist_ok=True)`` the parent
    of the lock anchor before opening, so a fresh-tempdir test fixture
    can point the lock at a sub-path without preparing the directory."""
    subdir = tmp_path / "subdir" / "deeper"
    assert not subdir.exists()
    lock_path = subdir / ".mcs-lock"
    with WriteLock(lock_path):
        assert subdir.is_dir()
        assert lock_path.is_file()


def test_lockfile_left_with_zero_body_after_clean_release(tmp_path: Path) -> None:
    """After a clean exit, the file may still exist on disk (forensic
    tombstone). The post-exit file, if present, is either empty or the
    next acquirer's PID. What matters for correctness: a fresh acquire
    must not block."""
    lock_path = tmp_path / ".mcs-lock"
    with WriteLock(lock_path):
        pass
    # File may or may not exist; but if it does, it should not be a
    # "holder is alive" sentinel that blocks the next acquire.
    if lock_path.exists():
        body = lock_path.read_text(encoding="ascii").strip()
        # Either empty (we never zero out post-exit explicitly but in
        # principle some other path could) or our own dead-after-exit
        # PID — that exact PID is os.getpid(), which is the test
        # runner's still-live PID. The check below just verifies a
        # subsequent acquire works (which is the actual contract).
        assert body == str(os.getpid()) or body == ""
    # Acquire again — must succeed without blocking.
    with WriteLock(lock_path, timeout=0.0):
        pass


def test_corrupted_lockfile_body_treated_as_stale(tmp_path: Path) -> None:
    """A lockfile whose body is non-integer garbage (e.g. the user
    ``echo hi > .mcs-lock``) is treated as a stale lock — the acquire
    proceeds and overwrites the body with the new holder's PID. The
    acquire does NOT raise on parse failure (it'd brick the profile
    permanently)."""
    lock_path = tmp_path / ".mcs-lock"
    lock_path.write_text("not-a-pid-just-garbage\n", encoding="ascii")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StaleLockClearedWarning)
        with WriteLock(lock_path):
            assert lock_path.read_text(encoding="ascii").strip() == str(os.getpid())


def test_timeout_parameter_zero_is_immediate_fail(tmp_path: Path) -> None:
    """``WriteLock(path, timeout=0.0)`` against a held lock raises
    ``LockedByOtherProcessError`` without sleeping."""
    lock_path = tmp_path / ".mcs-lock"
    ready_path = tmp_path / ".ready"
    proc = _mp_ctx.Process(
        target=_hold_lock_in_child,
        args=(str(lock_path), str(ready_path), 2.0),
    )
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if time.monotonic() > deadline:
                pytest.fail("child process never signalled lock acquisition")
            time.sleep(0.05)
        t0 = time.monotonic()
        with pytest.raises(LockedByOtherProcessError), WriteLock(lock_path, timeout=0.0):
            pytest.fail("expected LockedByOtherProcessError, got the lock")
        elapsed = time.monotonic() - t0
        # Should fail-fast — well under 100ms.
        assert elapsed < 0.5, f"timeout=0.0 should be immediate, slept {elapsed:.3f}s"
    finally:
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def _hold_lock_briefly(lock_path_str: str, ready_path_str: str, hold_secs: float) -> None:
    """Acquire the lock, signal ready, hold briefly, then release."""
    ready_path = Path(ready_path_str)
    with WriteLock(Path(lock_path_str)):
        ready_path.touch()
        time.sleep(hold_secs)


def test_timeout_parameter_positive_polls_until_timeout(tmp_path: Path) -> None:
    """``WriteLock(path, timeout=2.0)`` against a lock held for ~0.5 s
    acquires successfully after the holder releases."""
    lock_path = tmp_path / ".mcs-lock"
    ready_path = tmp_path / ".ready"
    proc = _mp_ctx.Process(
        target=_hold_lock_briefly,
        args=(str(lock_path), str(ready_path), 0.5),
    )
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if time.monotonic() > deadline:
                pytest.fail("child process never signalled lock acquisition")
            time.sleep(0.05)
        # Holder will release in ~0.5s; with timeout=2.0 we should
        # acquire successfully after polling.
        t0 = time.monotonic()
        with WriteLock(lock_path, timeout=2.0):
            elapsed = time.monotonic() - t0
            # We had to wait at least some time for the holder to release.
            # But less than the timeout.
            assert elapsed < 2.0, f"acquire took {elapsed:.3f}s, timeout was 2.0s"
    finally:
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def test_holder_pid_in_error_message_is_parsed_not_hardcoded(
    tmp_path: Path,
) -> None:
    """The error text includes the actual PID parsed from the lockfile
    body, not a placeholder. Hold the lock from a child process so
    contention occurs and assert that both the body-read PID and the
    known child PID appear verbatim in the error string."""
    lock_path = tmp_path / ".mcs-lock"
    ready_path = tmp_path / ".ready"
    proc = _mp_ctx.Process(
        target=_hold_lock_in_child,
        args=(str(lock_path), str(ready_path), 2.0),
    )
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if time.monotonic() > deadline:
                pytest.fail("child process never signalled lock acquisition")
            time.sleep(0.05)
        with (
            pytest.raises(LockedByOtherProcessError) as exc_info,
            WriteLock(lock_path, timeout=0.0),
        ):
            pytest.fail("expected LockedByOtherProcessError, got the lock")
        # The parsed PID from the lockfile body — which the holder
        # child wrote — appears in the error message.
        body_pid_str = lock_path.read_text(encoding="ascii").strip()
        assert body_pid_str in str(exc_info.value)
        assert str(proc.pid) in str(exc_info.value)
    finally:
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def test_error_class_inherits_mcserror_and_has_remediation(tmp_path: Path) -> None:
    """``LockedByOtherProcessError`` is a subclass of
    ``mc_client.errors.McsError`` and the raised instance has a
    non-empty ``remediation`` attribute."""
    lock_path = tmp_path / ".mcs-lock"
    ready_path = tmp_path / ".ready"
    proc = _mp_ctx.Process(
        target=_hold_lock_in_child,
        args=(str(lock_path), str(ready_path), 2.0),
    )
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if time.monotonic() > deadline:
                pytest.fail("child process never signalled lock acquisition")
            time.sleep(0.05)
        with (
            pytest.raises(LockedByOtherProcessError) as exc_info,
            WriteLock(lock_path, timeout=0.0),
        ):
            pass
        assert isinstance(exc_info.value, McsError)
        assert exc_info.value.remediation
        assert isinstance(exc_info.value.remediation, str)
    finally:
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2.0)


def test_stale_lock_clear_emits_warning(tmp_path: Path) -> None:
    """The stale-PID branch emits a ``StaleLockClearedWarning`` so the
    recovery is visible in mcs's stderr."""
    lock_path = tmp_path / ".mcs-lock"
    fake_dead_pid = 2**31 - 2
    lock_path.write_text(f"{fake_dead_pid}\n", encoding="ascii")
    with pytest.warns(StaleLockClearedWarning), WriteLock(lock_path):
        pass
