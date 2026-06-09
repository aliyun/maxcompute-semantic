"""Runtime skill content served by ``mcs skill get``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeSkill:
    name: str
    description: str
    dir: Path
    hidden: bool = False


def skill_data_root() -> Path:
    """Return the package directory containing runtime skill content."""
    override = os.environ.get("MCS_SKILL_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "_skill_data"


def parse_frontmatter(content: str) -> RuntimeSkill | None:
    """Parse minimal SKILL.md frontmatter.

    Only the fields needed by ``mcs skill catalog/get`` are supported:
    ``name``, ``description``, and optional ``hidden``.
    """
    text = content.lstrip()
    if not text.startswith("---"):
        return None
    rest = text[3:]
    end = rest.find("\n---")
    if end < 0:
        return None

    name: str | None = None
    description = ""
    hidden = False
    lines = rest[:end].splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("name:"):
            name = _clean_yaml_scalar(line.split(":", 1)[1])
        elif line.startswith("description:"):
            description = _clean_yaml_scalar(line.split(":", 1)[1])
            while idx + 1 < len(lines) and lines[idx + 1].startswith((" ", "\t")):
                idx += 1
                description = f"{description} {_clean_yaml_scalar(lines[idx])}".strip()
        elif line.startswith("hidden:"):
            hidden = _clean_yaml_scalar(line.split(":", 1)[1]).lower() in {"true", "yes"}
        idx += 1

    if not name:
        return None
    return RuntimeSkill(name=name, description=description, dir=Path(), hidden=hidden)


def discover_runtime_skills(roots: list[Path] | None = None) -> list[RuntimeSkill]:
    """Discover runtime skills under one or more roots."""
    skills: list[RuntimeSkill] = []
    for root in roots or [skill_data_root()]:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            skill_md = entry / "SKILL.md"
            if not entry.is_dir() or not skill_md.is_file():
                continue
            parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            if parsed is None:
                continue
            skills.append(
                RuntimeSkill(
                    name=parsed.name,
                    description=parsed.description,
                    dir=entry,
                    hidden=parsed.hidden,
                )
            )
    return sorted(skills, key=lambda skill: skill.name)


def collect_supplementary_files(skill_dir: Path) -> list[tuple[str, str]]:
    """Read supplementary reference/template files in deterministic order."""
    files: list[tuple[str, str]] = []
    for subdir_name in ("references", "templates"):
        subdir = skill_dir / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(p for p in subdir.iterdir() if p.is_file()):
            files.append((f"{subdir_name}/{path.name}", path.read_text(encoding="utf-8")))
    return files


def _clean_yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"")
