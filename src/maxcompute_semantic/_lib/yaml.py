"""Minimal YAML emitter + reader for our profile-frontmatter shapes.

We control everything we emit, so a tiny hand-rolled emitter is enough
(no PyYAML dependency). Supports the shapes used in our profile .md
files: ``dict[str, str|int|float|bool|None|list|dict]``. Lists of
scalars go on one line; lists of dicts are NOT supported (we don't emit
any).

Reader is just-enough to parse our own emitter's output (verified
queries / column hints round-tripping).
"""

from __future__ import annotations

import re
from typing import Any

_YAML_QUOTE_NEEDED = re.compile(r"[:\-#&*!|>'\"%@`,\[\]{}\\]|^\s|\s$|^$")


def _yaml_str(s: str) -> str:
    if _YAML_QUOTE_NEEDED.search(s) or s.lower() in ("yes", "no", "true", "false", "null", "~"):
        # Use single quotes; escape internal singles by doubling.
        return "'" + s.replace("'", "''") + "'"
    return s


def _yaml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _yaml_str(v)
    raise TypeError(f"yaml: unsupported scalar type {type(v).__name__}")


def _yaml_inline_list(xs: list) -> str:
    return "[" + ", ".join(_yaml_scalar(x) for x in xs) + "]"


def emit_yaml(d: dict, indent: int = 0) -> str:
    """Emit ``d`` as a YAML mapping. Recursive for nested dicts.
    Lists of scalars go inline; dict values get nested mappings."""
    lines: list[str] = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            if not v:
                lines.append(f"{pad}{k}: {{}}")
            else:
                lines.append(f"{pad}{k}:")
                lines.append(emit_yaml(v, indent + 1))
        elif isinstance(v, list):
            if not v:
                lines.append(f"{pad}{k}: []")
            elif all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                lines.append(f"{pad}{k}: {_yaml_inline_list(v)}")
            else:
                lines.append(f"{pad}{k}:")
                for item in v:
                    if isinstance(item, dict):
                        first = True
                        for ik, iv in item.items():
                            if first:
                                if isinstance(iv, (dict, list)):
                                    lines.append(f"{pad}  -")
                                    lines.append(emit_yaml({ik: iv}, indent + 2))
                                else:
                                    lines.append(f"{pad}  - {ik}: {_yaml_scalar(iv)}")
                                first = False
                            else:
                                if isinstance(iv, (dict, list)):
                                    lines.append(emit_yaml({ik: iv}, indent + 2))
                                else:
                                    lines.append(f"{pad}    {ik}: {_yaml_scalar(iv)}")
                    else:
                        raise TypeError("yaml: list items must be scalar or dict")
        else:
            lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
    return "\n".join(lines)


# Reader — tolerant of our own emitter output only. We split a profile
# .md into (frontmatter_text, body_text); the frontmatter parser handles
# top-level keys with scalar/inline-list/nested-mapping values.

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body_text)``. Empty frontmatter if no
    ``---``-delimited header is present."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def _parse_yaml_scalar(s: str) -> Any:
    s = s.strip()
    if s == "null" or s == "~" or s == "":
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    # Quoted string
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1].replace("''", "'") if s.startswith("'") else s[1:-1]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_frontmatter(fm_text: str) -> dict:
    """Parse frontmatter text into a dict. Tolerant only of shapes our
    emitter produces; do NOT use against arbitrary YAML."""
    if not fm_text.strip():
        return {}
    out: dict = {}
    lines = fm_text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if not line.startswith(" "):
            # Top-level key
            if ":" not in line:
                i += 1
                continue
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                # Nested mapping or list — collect indented continuation
                child_lines: list[str] = []
                i += 1
                while i < n and (lines[i].startswith("  ") or lines[i].strip() == ""):
                    child_lines.append(lines[i][2:] if lines[i].startswith("  ") else "")
                    i += 1
                child_text = "\n".join(child_lines)
                if child_text.strip().startswith("- "):
                    out[key] = _parse_yaml_block_list(child_text)
                else:
                    out[key] = parse_frontmatter(child_text)
                continue
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                if not inner.strip():
                    out[key] = []
                else:
                    out[key] = [_parse_yaml_scalar(x) for x in _split_inline_list(inner)]
            elif rest == "{}":
                out[key] = {}
            elif rest == "[]":
                out[key] = []
            else:
                out[key] = _parse_yaml_scalar(rest)
        i += 1
    return out


def _split_inline_list(s: str) -> list[str]:
    """Split a comma-separated inline list, honoring quoted commas."""
    parts: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _parse_yaml_block_list(text: str) -> list:
    """Parse a block-style list (``- item`` per line). Items can be inline
    scalars or nested mappings (one item == multiple ``key: val`` lines
    after the leading ``- ``)."""
    items: list = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if not line.lstrip().startswith("- "):
            i += 1
            continue
        rest = line.lstrip()[2:]
        if ":" in rest and not rest.startswith(("'", '"', "[", "{")):
            # Mapping list-item — collect siblings (indented at "  " in
            # the already-stripped child text; the original line had a
            # leading "    " which the caller stripped to "  ").
            mapping: dict = {}
            key, _, val = rest.partition(":")
            mapping[key.strip()] = _parse_yaml_scalar(val)
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("  ") and not nxt.lstrip().startswith("- ") and ":" in nxt:
                    k, _, v = nxt.strip().partition(":")
                    mapping[k.strip()] = _parse_yaml_scalar(v)
                    i += 1
                else:
                    break
            items.append(mapping)
        else:
            items.append(_parse_yaml_scalar(rest))
            i += 1
    return items
