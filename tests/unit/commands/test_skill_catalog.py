# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from maxcompute_semantic.commands.skill_catalog import (
    collect_supplementary_files,
    discover_runtime_skills,
    parse_frontmatter,
)


def test_parse_frontmatter_reads_name_description_and_hidden() -> None:
    content = """---
name: query
description: Use when querying MaxCompute data.
hidden: true
---

# Query
"""
    meta = parse_frontmatter(content)
    assert meta is not None
    assert meta.name == "query"
    assert meta.description == "Use when querying MaxCompute data."
    assert meta.hidden is True


def test_parse_frontmatter_reads_multiline_description() -> None:
    content = """---
name: query
description: Use when answering data questions,
  writing SQL, and inspecting schemas.
---

# Query
"""
    meta = parse_frontmatter(content)
    assert meta is not None
    assert meta.description == (
        "Use when answering data questions, writing SQL, and inspecting schemas."
    )


def test_discover_runtime_skills_ignores_dirs_without_skill_md(tmp_path: Path) -> None:
    query = tmp_path / "query"
    query.mkdir()
    (query / "SKILL.md").write_text(
        "---\nname: query\ndescription: Query flow.\n---\n\n# Query\n",
        encoding="utf-8",
    )
    (tmp_path / "not-a-skill").mkdir()

    skills = discover_runtime_skills([tmp_path])
    assert [s.name for s in skills] == ["query"]
    assert skills[0].dir == query


def test_discover_runtime_skills_includes_enrich() -> None:
    skills = discover_runtime_skills()
    assert "enrich" in {skill.name for skill in skills}


def test_collect_supplementary_files_reads_references_and_templates(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "cold-start.md").write_text("# Cold\n", encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "example.sql").write_text("SELECT 1;\n", encoding="utf-8")

    files = collect_supplementary_files(tmp_path)
    assert files == [
        ("references/cold-start.md", "# Cold\n"),
        ("templates/example.sql", "SELECT 1;\n"),
    ]
