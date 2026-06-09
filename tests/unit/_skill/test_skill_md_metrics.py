# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Guard tests for the SKILL.md bundle's metric teaching.

These tests pin the v10 vocabulary split into the agent-facing
contract: the installed bundle must teach both layers (``## Using
metrics`` and ``## Sedimenting metrics``) and neither SKILL.md nor
the reference docs may regress to the old ``role: metric`` column-
level shape.
"""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[3] / "src" / "maxcompute_semantic" / "_skill"
SKILL_DATA_DIR = Path(__file__).resolve().parents[3] / "src" / "maxcompute_semantic" / "_skill_data"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_skill_md_no_v9_role_metric_examples() -> None:
    body = _read(SKILL_DIR / "SKILL.md")
    assert "role: metric" not in body
    assert "--role metric" not in body


def test_installed_skill_has_no_references_dir() -> None:
    """After the stub+dynamic-load migration, _skill/ should contain only
    SKILL.md — all reference content lives in _skill_data/ phases."""
    refs = SKILL_DIR / "references"
    assert not refs.exists(), (
        f"{refs} should not exist — reference content lives in _skill_data/"
    )


def test_metrics_reference_exists_in_query_phase() -> None:
    p = SKILL_DATA_DIR / "query" / "references" / "metrics.md"
    assert p.exists(), f"missing {p}"
    body = _read(p)
    assert "mcs metric add" in body
    assert "mcs metric list" in body
    assert "mcs metric show" in body
