# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client/tier.py — get_tier with per-(profile, project) cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maxcompute_semantic._internal.paths import tier_cache_path
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.mc_client.tier import _probe, get_tier

_TEST_PROJECT = "acme_warehouse"


def _make_profile() -> Profile:
    return Profile(
        name="test",
        compute_project=_TEST_PROJECT,
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project=_TEST_PROJECT, schema="default", tables="*"),),
    )


def _make_client_with_odps(odps_mock: MagicMock) -> MagicMock:
    client = MagicMock()
    client._ensure_odps.return_value = odps_mock
    return client


# ─── Environment override tests ───


def test_env_override_2(monkeypatch) -> None:
    monkeypatch.setenv("MCS_TIER_OVERRIDE", "2")
    assert get_tier(_make_profile(), _TEST_PROJECT) == "2"


def test_env_override_3(monkeypatch) -> None:
    monkeypatch.setenv("MCS_TIER_OVERRIDE", "3")
    assert get_tier(_make_profile(), _TEST_PROJECT) == "3"


def test_invalid_env_falls_through(isolated_config, monkeypatch) -> None:
    monkeypatch.setenv("MCS_TIER_OVERRIDE", "invalid")
    # Falls through to cache / probe
    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="s1")]
    client = _make_client_with_odps(odps_mock)
    result = get_tier(_make_profile(), _TEST_PROJECT, client=client)
    assert result == "3"


# ─── Cache tests ───


def test_uses_cache_when_present(isolated_config) -> None:
    p = _make_profile()
    cache_path = tier_cache_path(p.name, _TEST_PROJECT)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("3", encoding="utf-8")

    # Should not need a client at all — cache returns "3"
    result = get_tier(p, _TEST_PROJECT)
    assert result == "3"


def test_corrupted_cache_triggers_probe(isolated_config) -> None:
    p = _make_profile()
    cache_path = tier_cache_path(p.name, _TEST_PROJECT)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("garbage", encoding="utf-8")

    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="s1")]
    client = _make_client_with_odps(odps_mock)

    result = get_tier(p, _TEST_PROJECT, client=client)
    assert result == "3"


def test_cache_keyed_per_project(isolated_config) -> None:
    """Two projects under the same profile maintain independent cached tiers."""
    p = _make_profile()
    cache_a = tier_cache_path(p.name, "proj_a")
    cache_b = tier_cache_path(p.name, "proj_b")
    cache_a.parent.mkdir(parents=True, exist_ok=True)
    cache_a.write_text("3", encoding="utf-8")
    cache_b.write_text("2", encoding="utf-8")

    assert get_tier(p, "proj_a") == "3"
    assert get_tier(p, "proj_b") == "2"


# ─── Probe tests ───


def test_probe_3_level_nonempty() -> None:
    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="schema_a")]
    client = _make_client_with_odps(odps_mock)
    assert _probe(client, _TEST_PROJECT) == "3"


def test_probe_passes_project_to_list_schemas() -> None:
    """_probe must thread the explicit project= kwarg into pyodps."""
    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="schema_a")]
    client = _make_client_with_odps(odps_mock)
    _probe(client, "another_project")
    odps_mock.list_schemas.assert_called_once_with(project="another_project")


def test_probe_2_level_not_supported() -> None:
    from odps import errors as odps_errors

    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.NotSupportedError("not supported")
    client = _make_client_with_odps(odps_mock)
    assert _probe(client, _TEST_PROJECT) == "2"


def test_probe_no_permission_assumes_3() -> None:
    from odps import errors as odps_errors

    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.NoPermission("no permission")
    client = _make_client_with_odps(odps_mock)
    result = _probe(client, _TEST_PROJECT)
    assert result == "3"


def test_probe_2_level_internal_server_error() -> None:
    """2-level project raises InternalServerError 'not 3-tier model'."""
    from odps import errors as odps_errors

    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.InternalServerError(
        "Project my_project is not 3-tier model project."
    )
    client = _make_client_with_odps(odps_mock)
    assert _probe(client, _TEST_PROJECT) == "2"


def test_probe_internal_server_error_other_raises() -> None:
    """InternalServerError that isn't about tier should propagate."""
    from odps import errors as odps_errors

    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.InternalServerError(
        "Something else went wrong"
    )
    client = _make_client_with_odps(odps_mock)
    try:
        _probe(client, _TEST_PROJECT)
    except odps_errors.InternalServerError:
        pass  # expected
    else:
        raise AssertionError("expected InternalServerError to propagate")


# What pyodps's REST /schemas fallback produces: it runs
# `SHOW SCHEMAS IN <project>` as a DDL task, and a flat-namespace project
# answers with the generic ddltask failure.
_TWO_TIER_DDL_FALLBACK_MESSAGE = (
    "ODPS-0110061: InstanceId: 20260101000000000aaaaaa000001\n"
    "ODPS-0110061: Failed to run ddltask - "
    "odps_metadata/ddlengine/ddl/sql_ddl_action.cpp(4102): ExceptionBase: "
    "Invalid database operations on two-tier model\n"
)


def _build_two_tier_ddl_error():
    from odps import errors as odps_errors

    exc = odps_errors.parse_instance_error(_TWO_TIER_DDL_FALLBACK_MESSAGE)
    # Guard the shape this test exists for: pyodps has no class entry for
    # ODPS-0110061, so it raises the *base* ODPSError. If a future pyodps
    # starts typing it, this assert flags that the probe's catch net can
    # be narrowed back.
    assert type(exc) is odps_errors.ODPSError
    return exc


def test_probe_2_level_show_schemas_ddl_fallback() -> None:
    """A 2-level project that answers the schema enumeration with a
    ddltask failure must still probe as "2".

    Regression: the probe caught only ``InternalServerError`` for the
    "not 3-tier" wording, so this base-``ODPSError`` shape escaped
    unclassified and hard-aborted every cold-path verb — and because a
    failed probe never writes the tier cache, every later call re-probed
    and re-failed.
    """
    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = _build_two_tier_ddl_error()
    client = _make_client_with_odps(odps_mock)
    assert _probe(client, _TEST_PROJECT) == "2"


def test_probe_2_level_ddl_fallback_written_to_cache(isolated_config) -> None:
    """The recognized 2-level answer must be cached like any other, so the
    next command skips the probe."""
    p = _make_profile()
    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = _build_two_tier_ddl_error()
    client = _make_client_with_odps(odps_mock)

    assert get_tier(p, _TEST_PROJECT, client=client) == "2"

    cache_path = tier_cache_path(p.name, _TEST_PROJECT)
    assert cache_path.exists()
    assert cache_path.read_text(encoding="utf-8").strip() == "2"


def test_probe_unrecognized_odps_error_raises() -> None:
    """An ``ODPSError`` with no two-tier signature is not guessed at."""
    from odps import errors as odps_errors

    odps_mock = MagicMock()
    # Same ODPS code as the two-tier case, different reason for failing.
    odps_mock.list_schemas.side_effect = odps_errors.ODPSError(
        "ODPS-0110061: Failed to run ddltask - column count mismatch"
    )
    client = _make_client_with_odps(odps_mock)
    try:
        _probe(client, _TEST_PROJECT)
    except odps_errors.ODPSError:
        pass  # expected
    else:
        raise AssertionError("expected ODPSError to propagate")


# ─── Stage 1: the direct schema-endpoint probe ───
#
# pyodps's list_schemas answers the tier question with a DDL task: its
# with_schema_api_fallback decorator catches the *typed*
# "Project <p> is not 3-tier model project" InvalidParameter and re-asks
# via SHOW SCHEMAS, which a two-tier project always rejects. Stage 1 asks
# the endpoint pyodps was about to give up on, so the probe answers from
# the accurate signal and submits nothing.

_SCHEMA_BASE_URL = "https://service.example/api/projects/acme_warehouse"


def _make_odps_mock() -> MagicMock:
    """An odps double whose stage-1 URL building yields a real string.

    ``Project.resource()`` returns an absolute, endpoint-prefixed URL; the
    probe depends on that, so the double mirrors it rather than the
    ``/projects/x`` relative form it might be guessed to be.
    """
    odps_mock = MagicMock()
    odps_mock.get_project.return_value.resource.return_value = _SCHEMA_BASE_URL
    return odps_mock


def test_stage1_asks_the_schema_endpoint_before_enumerating() -> None:
    """Pins the coupling to pyodps's URL building.

    If a pyodps upgrade changes ``Project.resource()`` or the schema
    collection path, the probe silently loses its fast path; this makes
    that loud.
    """
    odps_mock = _make_odps_mock()
    odps_mock.list_schemas.return_value = [MagicMock(name="schema_a")]
    client = _make_client_with_odps(odps_mock)

    assert _probe(client, _TEST_PROJECT) == "3"
    odps_mock.rest.get.assert_called_once_with(
        f"{_SCHEMA_BASE_URL}/schemas", params={"expectmarker": "true"}
    )


def test_stage1_two_tier_answer_skips_enumeration_entirely() -> None:
    """Fix at the source: answer from the typed REST error, submit no DDL."""
    from odps import errors as odps_errors

    odps_mock = _make_odps_mock()
    odps_mock.rest.get.side_effect = odps_errors.InvalidParameter(
        "InvalidParameter: Project acme_warehouse is not 3-tier model project."
    )
    client = _make_client_with_odps(odps_mock)

    assert _probe(client, _TEST_PROJECT) == "2"
    odps_mock.list_schemas.assert_not_called()


def test_stage1_no_permission_assumes_3_without_re_enumerating() -> None:
    """Enumeration reads the same URL under the same privilege, so asking
    it again could only repeat this answer."""
    from odps import errors as odps_errors

    odps_mock = _make_odps_mock()
    odps_mock.rest.get.side_effect = odps_errors.NoPermission("no schema read privilege")
    client = _make_client_with_odps(odps_mock)

    assert _probe(client, _TEST_PROJECT) == "3"
    odps_mock.list_schemas.assert_not_called()


def test_stage1_two_tier_shape_from_ddl_fallback_still_answers() -> None:
    """The untyped ODPS-0110061 shape is recognized wherever it arrives.

    Stage 1 is a plain GET and is not expected to produce it, but the
    string matcher is deliberately not scoped to one stage: a deployment
    that surfaces the DDL wording from the metadata path is answered
    without needing the enumeration fallback at all.
    """
    odps_mock = _make_odps_mock()
    odps_mock.rest.get.side_effect = _build_two_tier_ddl_error()
    client = _make_client_with_odps(odps_mock)

    assert _probe(client, _TEST_PROJECT) == "2"
    odps_mock.list_schemas.assert_not_called()


@pytest.mark.parametrize(
    "stage1_error_factory",
    [
        # Service predates the REST schema API: legacy enumeration is the
        # only thing that can answer, and it does.
        lambda e: e.MethodNotAllowed("Schema API not supported"),
        # Rejected for a reason that is not about the tier.
        lambda e: e.InvalidParameter("some unrelated parameter was rejected"),
        # A real server failure with no tier information in it.
        lambda e: e.InternalServerError("metastore briefly unavailable"),
        # Not a MaxCompute error at all — e.g. a pyodps too old to expose
        # the accessors stage 1 uses. Stage 1 must not become a new way to
        # break a probe that used to work.
        lambda e: AttributeError("'ODPS' object has no attribute 'rest'"),
    ],
    ids=["method-not-allowed", "invalid-param-other", "internal-error", "no-rest-attr"],
)
def test_stage1_inconclusive_outcomes_defer_to_enumeration(
    stage1_error_factory,
) -> None:
    """Stage 1 is additive: anything inconclusive falls through to the
    mechanism that predates it, so the probe's answer is unchanged."""
    from odps import errors as odps_errors

    odps_mock = _make_odps_mock()
    error = stage1_error_factory(odps_errors)
    # AttributeError isn't an ODPSError, so it goes on the entry point that
    # the probe touches first.
    if isinstance(error, AttributeError):
        odps_mock.get_project.side_effect = error
    else:
        odps_mock.rest.get.side_effect = error
    odps_mock.list_schemas.side_effect = odps_errors.InternalServerError(
        "List schemas failed: Project acme_warehouse is not 3-tier model project."
    )
    client = _make_client_with_odps(odps_mock)

    assert _probe(client, _TEST_PROJECT) == "2"
    odps_mock.list_schemas.assert_called_once_with(project=_TEST_PROJECT)


def test_probe_written_to_cache(isolated_config) -> None:
    p = _make_profile()
    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="schema_a")]
    client = _make_client_with_odps(odps_mock)

    result = get_tier(p, _TEST_PROJECT, client=client)
    assert result == "3"

    cache_path = tier_cache_path(p.name, _TEST_PROJECT)
    assert cache_path.exists()
    assert cache_path.read_text(encoding="utf-8").strip() == "3"


def test_cache_write_failure_still_returns(isolated_config) -> None:
    p = _make_profile()
    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="s1")]
    client = _make_client_with_odps(odps_mock)

    # Patch Path.write_text to raise OSError for the per-project cache file.
    original_write_text = Path.write_text

    def _mock_write_text(self, *args, **kwargs):
        if self.name == _TEST_PROJECT and self.parent.name == "tier_cache":
            raise OSError("mock write failure")
        return original_write_text(self, *args, **kwargs)

    with patch.object(Path, "write_text", _mock_write_text):
        result = get_tier(p, _TEST_PROJECT, client=client)
    assert result == "3"


def test_cache_read_failure_falls_to_probe(isolated_config) -> None:
    """OSError reading cache triggers probe."""
    p = _make_profile()
    cache_path = tier_cache_path(p.name, _TEST_PROJECT)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("3", encoding="utf-8")

    # Make reading the cache fail
    original_read_text = Path.read_text

    def _mock_read_text(self, *args, **kwargs):
        if self.name == _TEST_PROJECT and self.parent.name == "tier_cache":
            raise OSError("mock read failure")
        return original_read_text(self, *args, **kwargs)

    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="s1")]
    client = _make_client_with_odps(odps_mock)

    with patch.object(Path, "read_text", _mock_read_text):
        result = get_tier(p, _TEST_PROJECT, client=client)
    assert result == "3"


def test_get_tier_no_client_creates_one(isolated_config, monkeypatch) -> None:
    """get_tier without client argument creates MaxComputeClient internally."""
    p = _make_profile()
    # Ensure no cache and no override so it probes
    monkeypatch.delenv("MCS_TIER_OVERRIDE", raising=False)

    odps_mock = MagicMock()
    odps_mock.list_schemas.return_value = [MagicMock(name="s1")]
    mock_client = MagicMock()
    mock_client._ensure_odps.return_value = odps_mock

    with patch(
        "maxcompute_semantic.mc_client.client.MaxComputeClient",
        return_value=mock_client,
    ):
        result = get_tier(p, _TEST_PROJECT, client=None)
    assert result == "3"
