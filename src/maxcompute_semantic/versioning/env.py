# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Read the ``MCS_NO_VERSIONING`` opt-out env knob.

The same truthy-string set as the existing ``MCS_NO_HISTORY`` knob
used by the build miner — ``1`` / ``true`` / ``yes`` / ``on``,
case-insensitive. Any other value (including unset, empty, and the
explicit-falsy spellings ``0`` / ``false`` / ``no`` / ``off``)
means "not disabled", i.e. versioning is on by default.

The MCS_NO_HISTORY precedent and the matching truthy-set are
documented in this project's CLAUDE.md under "Eval mode:
MCS_NO_HISTORY". The new MCS_NO_VERSIONING knob mirrors that
contract exactly so the two env vars are mentally a single family
("eval-mode opt-outs"). The eval harness sets both via the
``build_minimal_env`` extras kwargs in
``eval/_skill_setup.py`` (the work of T20).
"""

from __future__ import annotations

import logging
import os
import shutil

_TRUTHY = frozenset({"1", "true", "yes", "on"})

log = logging.getLogger(__name__)

# Module-level latch so the "git not on PATH" warning fires once
# per process even though the auto-commit hook runs after every
# write command. Subsequent checks debug-log only.
_git_missing_warned = False


def is_versioning_disabled() -> bool:
    """``True`` iff ``MCS_NO_VERSIONING`` is set to a truthy spelling.

    Reads the env var fresh on every call (no caching) so a test
    that monkeypatches the env after import sees the change. The
    cost is a single ``os.environ.get`` per call which is sub-
    microsecond and negligible compared to the surrounding lock
    acquisition and git subprocess.
    """
    return os.environ.get("MCS_NO_VERSIONING", "").strip().lower() in _TRUTHY


def is_git_available() -> bool:
    """Return whether the ``git`` binary is on PATH.

    Used by the auto-commit hook to decide whether to silently skip
    versioning when git isn't installed. The probe is a cheap
    ``shutil.which`` (one PATH walk, no subprocess), so it's safe
    to call on every write command. Caching would add complexity
    for negligible savings — the hook only runs at the tail of
    user-driven CLI invocations.
    """
    return shutil.which("git") is not None


def warn_git_missing_once() -> None:
    """Emit a one-shot warning that git is missing → versioning is off.

    The auto-commit hook treats missing git as a soft opt-out (write
    succeeds, snapshot is skipped). To avoid silently surprising the
    user, we log a single warning the first time the gate fires;
    subsequent calls in the same process emit at debug only.

    Explicit versioning verbs (``mcs profile log`` / ``diff`` /
    ``reset`` / ``fork``) still raise ``GitNotAvailable`` with the
    full remediation text — the soft-skip only applies to the hook.
    """
    global _git_missing_warned
    if not _git_missing_warned:
        log.warning(
            "git binary not found on PATH; per-profile versioning is "
            "disabled for this session. Install git to enable history "
            "(macOS: `brew install git`; Debian/Ubuntu: `apt-get install "
            "git`; Windows: `winget install --id Git.Git`), or set "
            "MCS_NO_VERSIONING=1 to silence this warning."
        )
        _git_missing_warned = True
    else:
        log.debug("git still missing; versioning auto-skipped")
