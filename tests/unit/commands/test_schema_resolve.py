# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``commands/_schema_resolve.resolve_schema_for_tier``.

The shared policy used by every CLI verb that takes ``--project`` /
``--schema`` (``mcs sql {execute,cost,explain}``, the six ``mcs meta``
verbs, and ``mcs build``). The per-command tests live in
``test_sql_cmd.py`` / ``test_build_cmd.py`` and only verify that the
verb correctly routes through this helper; the policy itself lives
here.
"""

from __future__ import annotations

import pytest
from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile
from maxcompute_semantic.commands._schema_resolve import (
    resolve_project_for_profile,
    resolve_schema_for_tier,
)
from maxcompute_semantic.mc_client.errors import SchemaRequiredError


def _make_profile(sources: tuple[DataSource, ...]) -> Profile:
    return Profile(
        name="p",
        compute_project="p",
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=sources,
    )


def test_tier_2_with_default_schema_returns_default() -> None:
    assert resolve_schema_for_tier("2", "default") == "default"


def test_tier_2_with_none_schema_returns_default() -> None:
    assert resolve_schema_for_tier("2", None) == "default"


def test_tier_2_with_non_default_schema_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    # Kept as a SystemExit(2) Click-style hard-fail rather than a
    # classified McsError — the message is a human usage error, and
    # rolling it into SchemaRequiredError would erase the distinction
    # between "you passed something that can never be valid" and "you
    # didn't pass enough to disambiguate".
    with pytest.raises(SystemExit) as exc_info:
        resolve_schema_for_tier("2", "custom_schema")
    assert exc_info.value.code == 2
    assert "must be 'default'" in capsys.readouterr().err


def test_tier_3_with_explicit_schema_returns_value() -> None:
    assert resolve_schema_for_tier("3", "my_schema") == "my_schema"


def test_tier_3_with_explicit_default_returns_default() -> None:
    # 3-level "default" is a real schema name (where MC parks flat
    # tables after a 2→3 upgrade), distinct from the no-schema sentinel.
    # Regression guard: the CI connectivity probe relies on this.
    assert resolve_schema_for_tier("3", "default") == "default"


def test_tier_3_no_schema_single_source_profile_auto_fills() -> None:
    # The common shape: every ``mcs profile create`` produces a
    # 1-source profile, so 3-level callers don't need to thread
    # ``--schema`` when their cwd is bound to a single-source profile.
    profile = _make_profile((DataSource(project="p", schema="ns_a", tables="*"),))
    assert resolve_schema_for_tier("3", None, profile=profile) == "ns_a"


def test_tier_3_no_schema_no_profile_raises_schema_required() -> None:
    with pytest.raises(SchemaRequiredError) as exc_info:
        resolve_schema_for_tier("3", None, profile=None)
    err = exc_info.value
    assert err.code == "SchemaRequired"
    assert err.exit_code == 2
    assert "pass --schema" in err.remediation.lower()
    assert "mcs link bind" in err.remediation


def test_tier_3_no_schema_multi_source_profile_raises_with_choices() -> None:
    # Multi-source profile + no --schema → can't auto-pick. The
    # remediation MUST name the schemas so the agent doesn't have
    # to round-trip through `mcs meta list-schemas` just to discover
    # what choices exist.
    profile = _make_profile(
        (
            DataSource(project="p", schema="alpha", tables="*"),
            DataSource(project="p", schema="beta", tables="*"),
        )
    )
    with pytest.raises(SchemaRequiredError) as exc_info:
        resolve_schema_for_tier("3", None, profile=profile)
    err = exc_info.value
    assert err.code == "SchemaRequired"
    assert "alpha" in err.remediation
    assert "beta" in err.remediation
    assert err.context.get("available_schemas") == ["alpha", "beta"]


def test_tier_3_no_schema_empty_sources_raises() -> None:
    # Env-var-anonymous profile (no on-disk yaml) — sources tuple
    # is empty. Same hard-fail as the multi-source case.
    profile = _make_profile(())
    with pytest.raises(SchemaRequiredError):
        resolve_schema_for_tier("3", None, profile=profile)


# ── resolve_project_for_profile ──────────────────────────────────────────


class TestResolveProjectForProfile:
    def test_explicit_project_wins(self) -> None:
        profile = _make_profile((DataSource(project="source_proj", schema="s", tables="*"),))
        assert resolve_project_for_profile("cli_proj", profile=profile) == "cli_proj"

    def test_single_source_auto_fills(self) -> None:
        profile = _make_profile((DataSource(project="source_proj", schema="s", tables="*"),))
        assert resolve_project_for_profile(None, profile=profile) == "source_proj"

    def test_multi_source_uses_first(self) -> None:
        profile = _make_profile(
            (
                DataSource(project="first_proj", schema="alpha", tables="*"),
                DataSource(project="second_proj", schema="beta", tables="*"),
            )
        )
        assert resolve_project_for_profile(None, profile=profile) == "first_proj"

    def test_no_profile_returns_empty(self) -> None:
        assert resolve_project_for_profile(None, profile=None) == ""

    def test_no_sources_returns_empty(self) -> None:
        profile = _make_profile(())
        assert resolve_project_for_profile(None, profile=profile) == ""
