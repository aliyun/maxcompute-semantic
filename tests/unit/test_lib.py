# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the _lib package — yaml, schema_hash, status.

The ``_lib`` package holds small stateless helpers shared across the
CLI; the ``paths`` and ``info_schema`` modules that used to live here
were the v0.x per-project-keyed layout and have been replaced by
``_internal/paths.py`` (per-(profile, project) tier cache) and
``build/info_schema.py`` (per-profile dir + cache_dir-scoped sentinel)
respectively. Their tests live alongside the live modules.
"""

from __future__ import annotations

import pytest
from maxcompute_semantic._lib.schema_hash import schema_hash
from maxcompute_semantic._lib.status import die, emit_status
from maxcompute_semantic._lib.yaml import (
    emit_yaml,
    parse_frontmatter,
    split_frontmatter,
)

# ---------------------------------------------------------------------------
# yaml
# ---------------------------------------------------------------------------


def test_emit_yaml_simple_dict() -> None:
    out = emit_yaml({"name": "x", "version": 1})
    assert out == "name: x\nversion: 1"


def test_emit_yaml_inline_list() -> None:
    out = emit_yaml({"keywords": ["a", "b", "c"]})
    assert out == "keywords: [a, b, c]"


def test_emit_yaml_empty_list_and_dict() -> None:
    assert emit_yaml({"x": []}) == "x: []"
    assert emit_yaml({"x": {}}) == "x: {}"


def test_emit_yaml_nested_dict() -> None:
    out = emit_yaml({"meta": {"version": 1, "author": "x"}})
    assert out == "meta:\n  version: 1\n  author: x"


def test_emit_yaml_quotes_specials() -> None:
    out = emit_yaml({"name": "value: with colon"})
    assert "'value: with colon'" in out or '"value: with colon"' in out


def test_emit_yaml_quotes_yaml_literals_and_escapes_single_quotes() -> None:
    out = emit_yaml({"enabled": "true", "owner": "Alice's"})
    assert "enabled: 'true'" in out
    assert "owner: 'Alice''s'" in out


def test_emit_yaml_null_and_bool() -> None:
    out = emit_yaml({"a": None, "b": True, "c": False})
    assert "a: null" in out
    assert "b: true" in out
    assert "c: false" in out


def test_emit_yaml_block_list_of_dicts() -> None:
    out = emit_yaml(
        {
            "sources": [
                {"project": "p1", "schema": "default"},
                {"project": "p2", "enabled": False},
            ]
        }
    )
    assert "- project: p1" in out
    assert "schema: default" in out
    assert "- project: p2" in out
    assert "enabled: false" in out


def test_emit_yaml_rejects_unsupported_scalar() -> None:
    with pytest.raises(TypeError, match="unsupported scalar type"):
        emit_yaml({"bad": object()})


def test_emit_yaml_rejects_unsupported_list_item() -> None:
    with pytest.raises(TypeError, match="list items must be scalar or dict"):
        emit_yaml({"bad": [object()]})


def test_split_frontmatter_basic() -> None:
    raw = "---\nname: x\n---\nbody text\n"
    fm, body = split_frontmatter(raw)
    assert "name: x" in fm
    assert body.strip() == "body text"


def test_split_frontmatter_no_header() -> None:
    raw = "no frontmatter here\nstill body\n"
    fm, body = split_frontmatter(raw)
    assert fm == ""
    assert "no frontmatter" in body


def test_parse_frontmatter_round_trip() -> None:
    raw = "---\nname: x\nversion: 1\n---\n"
    fm = parse_frontmatter(raw)
    assert fm == {"name": "x", "version": 1}


def test_parse_frontmatter_handles_block_list() -> None:
    raw = "---\nkeywords:\n  - a\n  - b\n---\n"
    fm = parse_frontmatter(raw)
    assert fm == {"keywords": ["a", "b"]}


def test_parse_frontmatter_handles_mapping_block_list_and_comments() -> None:
    raw = (
        "---\n"
        "# ignored\n"
        "sources:\n"
        "  - project: p1\n"
        "    schema: default\n"
        "  - project: p2\n"
        "broken-line-without-colon\n"
        "---\n"
    )
    fm = parse_frontmatter(raw)
    assert fm == {
        "sources": [
            {"project": "p1", "schema": "default"},
            {"project": "p2"},
        ]
    }


def test_parse_frontmatter_splits_quoted_commas_in_inline_list() -> None:
    fm = parse_frontmatter("items: 'a,b', plain")
    assert fm == {"items": "'a,b', plain"}

    fm = parse_frontmatter("items: ['a,b', plain]")
    assert fm == {"items": ["a,b", "plain"]}


# ---------------------------------------------------------------------------
# schema_hash
# ---------------------------------------------------------------------------


def test_schema_hash_deterministic() -> None:
    cols = [
        {"name": "id", "type": "BIGINT"},
        {"name": "name", "type": "STRING"},
    ]
    h1 = schema_hash(cols)
    h2 = schema_hash(cols)
    assert h1 == h2
    # SHA-256 hex digest = 64 chars
    assert len(h1) == 64


def test_schema_hash_changes_with_type() -> None:
    a = schema_hash([{"name": "id", "type": "BIGINT"}])
    b = schema_hash([{"name": "id", "type": "STRING"}])
    assert a != b


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_emit_status_success(capsys: pytest.CaptureFixture[str]) -> None:
    emit_status({"x": 1})
    out = capsys.readouterr().out
    assert '"status": "success"' in out
    assert '"x": 1' in out


def test_emit_status_failure(capsys: pytest.CaptureFixture[str]) -> None:
    emit_status({"x": 1}, success=False)
    out = capsys.readouterr().out
    assert '"status": "error"' in out


def test_die_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        die("x", code="BOOM", exit_status=3)
    assert exc.value.code == 3
    out = capsys.readouterr().out
    assert '"BOOM"' in out


# acl_filter tests live in tests/unit/_lib/test_acl_filter.py.
