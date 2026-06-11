# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/lint_diff.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_lint_diff() -> ModuleType:
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "lint_diff.py"
    spec = importlib.util.spec_from_file_location("lint_diff", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_diff = _load_lint_diff()


def _diagnostic(line: int) -> dict[str, object]:
    return {
        "tool": "ty",
        "rule": "unresolved-attribute",
        "path": "src/example.py",
        "line": line,
        "message": "Object has no attribute `missing`",
    }


def test_diff_preserves_duplicate_unchanged_diagnostics() -> None:
    base = [_diagnostic(10), _diagnostic(20)]
    head = [_diagnostic(11), _diagnostic(21)]

    new, fixed, unchanged = lint_diff._diff(base, head)

    assert new == []
    assert fixed == []
    assert len(unchanged) == 2


def test_diff_counts_removed_duplicate_as_fixed() -> None:
    base = [_diagnostic(10), _diagnostic(20)]
    head = [_diagnostic(11)]

    new, fixed, unchanged = lint_diff._diff(base, head)

    assert new == []
    assert len(fixed) == 1
    assert len(unchanged) == 1


def test_diff_counts_added_duplicate_as_new() -> None:
    base = [_diagnostic(10)]
    head = [_diagnostic(11), _diagnostic(21)]

    new, fixed, unchanged = lint_diff._diff(base, head)

    assert len(new) == 1
    assert fixed == []
    assert len(unchanged) == 1
