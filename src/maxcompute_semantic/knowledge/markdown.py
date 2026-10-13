# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic Markdown metadata/link acquisition, with no model calls.

Only ``mcs.nodes`` and ``mcs.relations`` in safe YAML frontmatter have structured
meaning. Markdown links are mentions, never inferred equivalence or dependencies.
Ordinary name occurrences may become candidates, with their exact source line.
"""

from __future__ import annotations

import re
from typing import Any

from ruamel.yaml import YAML

from maxcompute_semantic.knowledge.model import KnowledgeError

_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^\s)]+)(?:\s+[^)]*)?\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def parse_markdown(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    metadata: dict[str, Any] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise KnowledgeError("markdown frontmatter is not terminated")
        try:
            raw = YAML(typ="safe").load("\n".join(lines[1:end]))
        except Exception as exc:
            raise KnowledgeError("invalid markdown frontmatter") from exc
        if raw is not None and not isinstance(raw, dict):
            raise KnowledgeError("markdown frontmatter must be an object")
        metadata = (raw or {}).get("mcs", {})
        if not isinstance(metadata, dict):
            raise KnowledgeError("frontmatter mcs must be an object")
        body_start = end + 1
    headings: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for index, line in enumerate(lines[body_start:], body_start + 1):
        heading = _HEADING.match(line)
        if heading:
            headings.append({"title": heading.group(1), "line": index})
        for match in _LINK.finditer(line):
            links.append(
                {"label": match.group(1), "target": match.group(2), "line": index}
            )
    for key in ("nodes", "relations"):
        if not isinstance(metadata.get(key, []), list):
            raise KnowledgeError(f"frontmatter mcs.{key} must be a list")
    return {
        "metadata": metadata,
        "headings": headings,
        "links": links,
        "body_start": body_start,
    }
