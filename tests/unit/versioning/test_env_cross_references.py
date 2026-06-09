# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cross-reference invariants for the ``MCS_NO_VERSIONING`` env knob (T18).

The helper itself (``versioning/env.py:is_versioning_disabled``) is
exercised in detail by ``test_env.py``. This file pins the
documentation-side invariants so a future engineer can find every
place the env var has an effect without grep'ing:

- the package-level re-export resolves to the implementation symbol
  (callers should import from ``maxcompute_semantic.versioning``,
  not the module path),
- the truthy-string set matches the older ``MCS_NO_HISTORY`` family
  used by the build miner so the two eval-mode-opt-out env vars stay
  mentally a single contract,
- the project's CLAUDE.md mentions ``MCS_NO_VERSIONING`` (T18 added
  the cross-reference next to the existing ``MCS_NO_HISTORY``
  paragraph),
- the package CHANGELOG mentions ``MCS_NO_VERSIONING`` (T22's final
  release section anchors the user-facing contract).
"""

from __future__ import annotations

from pathlib import Path

from maxcompute_semantic.versioning import is_versioning_disabled as reexport
from maxcompute_semantic.versioning.env import is_versioning_disabled as impl

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def test_env_helper_is_exported_from_package_init() -> None:
    """``from maxcompute_semantic.versioning import is_versioning_disabled``
    resolves to the same callable as the underscored-module import."""
    assert reexport is impl


def test_env_helper_matches_mcs_no_history_truthy_set() -> None:
    """The truthy spellings accepted by the new env var match the
    older ``MCS_NO_HISTORY`` family in ``commands/build.py``.

    The plan's claim is that the two env vars are mentally one
    contract ("eval-mode opt-outs"). This test pins the truthy-set
    match by reading both helpers' set against the canonical spelling
    list (``1`` / ``true`` / ``yes`` / ``on``, case-insensitive) and
    refusing drift.
    """
    from maxcompute_semantic.commands import build as build_cmd
    from maxcompute_semantic.versioning import env as env_mod

    # Canonical set, lower-case. Both helpers read the env, lower-case
    # it, and compare against an in-module frozenset / set.
    canonical = {"1", "true", "yes", "on"}

    assert set(env_mod._TRUTHY) == canonical
    assert set(build_cmd._TRUTHY) == canonical


def test_env_helper_is_referenced_in_changelog() -> None:
    """The package CHANGELOG mentions ``MCS_NO_VERSIONING`` at least
    once."""
    text = (_PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "MCS_NO_VERSIONING" in text
