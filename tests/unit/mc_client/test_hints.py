"""Tests for mc_client/hints.py — build_hints helper."""

from __future__ import annotations

from maxcompute_semantic.mc_client.hints import build_hints


def test_no_tier_no_user_returns_empty() -> None:
    assert build_hints(tier=None, schema=None) == {}


def test_user_hints_passed_through() -> None:
    result = build_hints(tier=None, schema=None, user_hints={"odps.sql.timezone": "Asia/Shanghai"})
    assert result == {"odps.sql.timezone": "Asia/Shanghai"}


def test_3_level_adds_namespace_hints() -> None:
    result = build_hints(tier="3", schema="sales_west")
    assert result["odps.namespace.schema"] == "true"
    assert result["odps.default.schema"] == "sales_west"


def test_2_level_no_namespace_hints() -> None:
    result = build_hints(tier="2", schema="default")
    assert "odps.namespace.schema" not in result


def test_user_hints_not_overridden_by_tier() -> None:
    user = {"odps.namespace.schema": "false", "odps.default.schema": "my_schema"}
    result = build_hints(tier="3", schema="sales_west", user_hints=user)
    # setdefault does NOT override existing keys
    assert result["odps.namespace.schema"] == "false"
    assert result["odps.default.schema"] == "my_schema"


def test_user_hints_combine_with_tier() -> None:
    user = {"odps.sql.timezone": "UTC"}
    result = build_hints(tier="3", schema="sales_west", user_hints=user)
    assert result["odps.sql.timezone"] == "UTC"
    assert result["odps.namespace.schema"] == "true"
    assert result["odps.default.schema"] == "sales_west"


def test_tier3_no_schema_still_injects_namespace_hint() -> None:
    """On tier=3, ``odps.namespace.schema=true`` is always injected so
    3-segment ``project.schema.table`` references parse correctly,
    even when the caller didn't pass an explicit session schema.
    ``odps.default.schema`` stays absent so bare names don't silently
    resolve against an unintended default.
    """
    result = build_hints(tier="3", schema=None, source_key="p__s")
    assert result == {"odps.namespace.schema": "true"}


def test_source_key_parameter_accepted_but_noop_on_happy_path() -> None:
    """source_key is accepted but doesn't affect output when hints succeed."""
    result = build_hints(tier="3", schema="s", source_key="x")
    assert result["odps.namespace.schema"] == "true"
    assert result["odps.default.schema"] == "s"
