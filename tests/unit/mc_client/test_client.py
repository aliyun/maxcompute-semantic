# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client/client.py — MaxComputeClient lifecycle + credential refresh."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from maxcompute_semantic.auth.credential import Credentials
from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile
from maxcompute_semantic.mc_client.client import MaxComputeClient
from odps import errors as odps_errors  # type: ignore[import-untyped]


def _make_profile(*, cost_thresholds: CostThresholds | None = None) -> Profile:
    # Default cost_thresholds are disabled (enabled=False) so the execute_sql
    # cost gate is a no-op for tests that don't care about it — they target
    # credential refresh and ODPS init, not the cost-gate path which has
    # its own test_cost_gate.py. The cost_estimate tests pass an explicit
    # enabled CostThresholds(10, 100) to exercise the verdict branches.
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
        cost_thresholds=cost_thresholds or CostThresholds(enabled=False),
    )


_ENABLED_THRESHOLDS = CostThresholds(confirm_cny=10.0, blocked_cny=100.0)


def _make_creds(expiration: datetime | None = None) -> Credentials:
    return Credentials(
        access_key_id="ak_id",
        access_key_secret="ak_secret",
        security_token="token",
        expiration=expiration,
    )


# ─── Lifecycle tests ───


def test_init_lazy_no_odps_created() -> None:
    """_ensure_odps not called at __init__; _odps is None."""
    p = _make_profile()
    c = MaxComputeClient(p)
    assert c._odps is None
    assert c._creds_expiration is None


def test_ensure_odps_creates_once_and_caches() -> None:
    """First call creates ODPS; second returns same instance."""
    p = _make_profile()
    c = MaxComputeClient(p)
    creds = _make_creds()
    odps_instance = MagicMock()
    with (
        patch(
            "maxcompute_semantic.mc_client.client.resolve_credentials", return_value=creds
        ) as resolve_mock,
        patch("maxcompute_semantic.mc_client.client.ODPS", return_value=odps_instance),
    ):
        result1 = c._ensure_odps()
        assert resolve_mock.call_count == 1
        result2 = c._ensure_odps()
        assert resolve_mock.call_count == 1  # not called again
        assert result1 is result2
        assert result1 is odps_instance


def test_ak_always_valid() -> None:
    """AK creds have no expiration → always valid → no refetch."""
    p = _make_profile()
    c = MaxComputeClient(p)
    creds = _make_creds(expiration=None)  # AK: no expiration
    odps_instance = MagicMock()
    with (
        patch("maxcompute_semantic.mc_client.client.resolve_credentials", return_value=creds),
        patch("maxcompute_semantic.mc_client.client.ODPS", return_value=odps_instance),
    ):
        c._ensure_odps()
        assert c._creds_still_valid() is True


def test_future_expiration_valid() -> None:
    """STS expiration far in the future → still valid."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._odps = MagicMock()
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)
    assert c._creds_still_valid() is True


def test_past_expiration_invalid() -> None:
    """STS expiration in the past → invalid → triggers refetch."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._odps = MagicMock()
    c._creds_expiration = datetime.now(timezone.utc) - timedelta(hours=1)
    assert c._creds_still_valid() is False


def test_within_safety_window_invalidated() -> None:
    """STS expiration < 60s from now → within safety window → invalid."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._odps = MagicMock()
    # 30 seconds remaining → within 60s safety margin
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert c._creds_still_valid() is False


def test_expired_creds_trigger_refetch() -> None:
    """Expired STS triggers resolve_credentials again and replaces ODPS."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._odps = MagicMock()
    c._creds_expiration = datetime.now(timezone.utc) - timedelta(hours=1)
    new_creds = _make_creds(expiration=datetime.now(timezone.utc) + timedelta(hours=12))
    new_odps = MagicMock()
    with (
        patch(
            "maxcompute_semantic.mc_client.client.resolve_credentials", return_value=new_creds
        ) as resolve_mock,
        patch("maxcompute_semantic.mc_client.client.ODPS", return_value=new_odps),
    ):
        result = c._ensure_odps()
        assert resolve_mock.call_count == 1
        assert result is new_odps
        assert c._odps is new_odps


# ─── STS security_token in ODPS construction ───


def test_security_token_passed_to_odps() -> None:
    """When creds have security_token, a StsAccount is constructed
    and passed as access_id to ODPS (pyodps doesn't accept
    security_token as a standalone kwarg)."""
    p = _make_profile()
    c = MaxComputeClient(p)
    creds = _make_creds(expiration=datetime.now(timezone.utc) + timedelta(hours=12))
    odps_instance = MagicMock()
    with (
        patch("maxcompute_semantic.mc_client.client.resolve_credentials", return_value=creds),
        patch("maxcompute_semantic.mc_client.client.ODPS", return_value=odps_instance) as odps_cls,
        patch("maxcompute_semantic.mc_client.client.StsAccount") as sts_cls,
    ):
        c._ensure_odps()
        # StsAccount should be called with the three STS fields
        sts_cls.assert_called_once()
        sts_call_args = sts_cls.call_args[0]
        assert sts_call_args[0] == "ak_id"  # access_key_id
        assert sts_call_args[1] == "ak_secret"  # access_key_secret
        assert sts_call_args[2] == "token"  # security_token
        # ODPS should receive the StsAccount as access_id
        call_kwargs = odps_cls.call_args[1]
        assert call_kwargs["access_id"] is sts_cls.return_value
        assert "security_token" not in call_kwargs


# ─── Profile property ───


def test_profile_property() -> None:
    """profile property returns the stored profile."""
    p = _make_profile()
    c = MaxComputeClient(p)
    assert c.profile is p


# ─── list_schemas ───


def test_list_schemas() -> None:
    """list_schemas returns schema names from ODPS."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    schema_mock = MagicMock()
    schema_mock.name = "my_schema"
    odps_mock.list_schemas.return_value = [schema_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_schemas()
    assert result == ["my_schema"]


# ─── list_projects ───


def test_list_projects() -> None:
    """list_projects returns sorted project names from ODPS."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    proj_b = MagicMock()
    proj_b.name = "proj_b"
    proj_a = MagicMock()
    proj_a.name = "proj_a"
    proj_c = MagicMock()
    proj_c.name = "proj_c"
    odps_mock.list_projects.return_value = [proj_b, proj_a, proj_c]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_projects()
    assert result == ["proj_a", "proj_b", "proj_c"]


# ─── list_tables ───


def test_list_tables_with_schema() -> None:
    """list_tables passes schema to ODPS."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "my_table"
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_tables(schema="sales")
    assert result == ["my_table"]
    odps_mock.list_tables.assert_called_once_with(project="test_project", schema="sales")


def test_list_tables_pyodps_error_attributes_source_key() -> None:
    """Raw pyodps NoPermission must surface as PermissionDeniedError
    with a ``[source=...]`` prefix so multi-source profiles can tell which
    source failed."""
    from odps.errors import ODPSError

    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    err = ODPSError("Deny as default")
    err.code = "NoPermission"
    odps_mock.list_tables.side_effect = err
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import PermissionDeniedError

    with pytest.raises(PermissionDeniedError) as ei:
        c.list_tables(schema="sales", project="other_proj")
    assert ei.value.message.startswith("[source=other_proj__sales] ")


# ─── describe_table ───


def test_describe_table_with_schema() -> None:
    """describe_table passes schema to ODPS get_table."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    table_mock.name = "orders"
    table_mock.comment = ""
    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()
    col_mock.comment = ""
    table_mock.table_schema.columns = [col_mock]
    table_mock.table_schema.partitions = []
    table_mock.type = MagicMock()
    table_mock.type.value = "MANAGED_TABLE"
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.describe_table("orders", schema="sales")
    assert "table" in result
    odps_mock.get_table.assert_called_once_with("orders", project="test_project", schema="sales")


# ─── cost_estimate ───


def test_cost_estimate_blocked() -> None:
    """High cost returns 'blocked' verdict."""
    p = _make_profile(cost_thresholds=_ENABLED_THRESHOLDS)
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    cost_mock = MagicMock()
    cost_mock.input_size = 1073741824000  # ~1 TB → 300 CNY → blocked
    odps_mock.execute_sql_cost.return_value = cost_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.cost_estimate("SELECT * FROM huge")
    assert result["verdict"] == "blocked"


def test_cost_estimate_confirm() -> None:
    """Medium cost returns 'confirm' verdict."""
    p = _make_profile(cost_thresholds=_ENABLED_THRESHOLDS)
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    cost_mock = MagicMock()
    cost_mock.input_size = 53687091200  # ~50 GB → 15 CNY → confirm
    odps_mock.execute_sql_cost.return_value = cost_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.cost_estimate("SELECT * FROM medium")
    assert result["verdict"] == "confirm"


# ─── _dt_to_iso ───


def test_dt_to_iso_none() -> None:
    """_dt_to_iso returns None for None input."""
    from maxcompute_semantic.mc_client.client import _dt_to_iso

    assert _dt_to_iso(None) is None


def test_dt_to_iso_naive_datetime() -> None:
    """_dt_to_iso adds UTC timezone for naive datetime."""
    from maxcompute_semantic.mc_client.client import _dt_to_iso

    dt = datetime(2024, 1, 15, 10, 0, 0)
    result = _dt_to_iso(dt)
    assert result is not None
    assert "+00:00" in result


# ─── execute_sql batch mode ───


def test_execute_sql_batch_mode() -> None:
    """execute_sql without use_interactive uses run_sql + wait_for_success."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    # Mock open_reader for result parsing
    reader_mock = MagicMock()
    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()
    reader_mock.schema.columns = [col_mock]
    reader_mock.__iter__ = MagicMock(return_value=iter([]))
    instance_mock.open_reader.return_value = reader_mock
    instance_mock.get_logview_address.return_value = "http://logview"

    result = c.execute_sql("SELECT 1", use_interactive=False, timeout=30)
    assert result.status == "success"
    odps_mock.run_sql.assert_called_once()


# ─── list_partitions get_max_partition TypeError ───


def test_list_partitions_typeerror_fallback() -> None:
    """get_max_partition with TypeError on skip_empty falls through."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    table_mock.iterate_partitions.return_value = iter([p1])
    # get_max_partition raises TypeError on first call (skip_empty=True),
    # then returns p1 on second call (skip_empty=False).
    table_mock.get_max_partition = MagicMock(side_effect=[TypeError("unexpected keyword"), p1])
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_partitions("my_table", limit=100)
    assert result["is_partitioned"] is True
    assert result["latest_partition"] == "ds=20240101"


# ─── freshness_info partitioned with iteration fallback ───


def test_freshness_info_partitioned_iteration_fallback() -> None:
    """When get_max_partition is unavailable, use iterate_partitions."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    # No get_max_partition attribute.
    table_mock.get_max_partition = None
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["is_partitioned"] is True
    assert result["latest_partition"] == "ds=20240115"


# ─── search_tables client-side with schema ───


def test_search_tables_client_side_schema_filter() -> None:
    """Client-side search passes schema to list_tables."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    t_mock.comment = ""
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ):
        c.search_tables("order", schema="sales")
    # list_tables should receive schema kwarg.
    call_kwargs = odps_mock.list_tables.call_args[1]
    assert call_kwargs.get("schema") == "sales"


# ─── search_columns with schema ───


def test_search_columns_with_schema() -> None:
    """search_columns passes schema to list_tables."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    col_mock = MagicMock()
    col_mock.name = "order_id"
    col_mock.type = MagicMock()
    col_mock.comment = ""
    t_mock.table_schema.columns = [col_mock]
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    c.search_columns("order_id", schema="sales")
    call_kwargs = odps_mock.list_tables.call_args[1]
    assert call_kwargs.get("schema") == "sales"


# ─── search_columns exception fallback ───


def test_search_columns_table_schema_exception_skipped() -> None:
    """table.table_schema raises ODPSError -> table skipped."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "bad_table"
    # Make table_schema property raise on access
    type(t_mock).table_schema = PropertyMock(side_effect=odps_errors.ODPSError("schema error"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.search_columns("keyword")
    assert result == []


def test_search_columns_table_schema_runtime_error_propagates() -> None:
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "bad_table"
    type(t_mock).table_schema = PropertyMock(side_effect=RuntimeError("programming bug"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with pytest.raises(RuntimeError, match="programming bug"):
        c.search_columns("keyword")


def test_search_columns_schema_odps_error_logs_skip_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "bad_table"
    type(t_mock).table_schema = PropertyMock(side_effect=odps_errors.ODPSError("schema error"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with caplog.at_level("DEBUG", logger="maxcompute_semantic"):
        result = c.search_columns("keyword")

    assert result == []
    assert "search_columns skipped 1 table(s) with unreadable schema" in caplog.text
    assert "bad_table" in caplog.text


# ─── search_tables client-side column fallback ───


def test_search_tables_client_side_column_name_match() -> None:
    """No name/comment match, but column name matches → scored with matched_columns."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    t_mock.comment = "table comment"
    col_mock = MagicMock()
    col_mock.name = "user_id"
    col_mock.comment = ""
    t_mock.table_schema.columns = [col_mock]
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ):
        result = c.search_tables("user_id")
    assert len(result) == 1
    assert result[0]["matched_columns"] == ["user_id"]
    assert result[0]["score"] == 2


def test_search_tables_client_side_column_exception_skipped() -> None:
    """table_schema raises ODPSError on access -> column fallback skipped, table excluded."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    t_mock.comment = "no match here"
    # Make table_schema.columns raise when iterated
    type(t_mock).table_schema = PropertyMock(side_effect=odps_errors.ODPSError("no schema"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ):
        result = c.search_tables("unknown_keyword")
    assert result == []


def test_search_tables_client_side_column_runtime_error_propagates() -> None:
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    t_mock.comment = "no match here"
    type(t_mock).table_schema = PropertyMock(side_effect=RuntimeError("programming bug"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ), pytest.raises(RuntimeError, match="programming bug"):
        c.search_tables("unknown_keyword")


def test_search_tables_column_odps_error_logs_skip_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    t_mock = MagicMock()
    t_mock.name = "orders"
    t_mock.comment = "no match here"
    type(t_mock).table_schema = PropertyMock(side_effect=odps_errors.ODPSError("no schema"))
    odps_mock.list_tables.return_value = [t_mock]
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ), caplog.at_level("DEBUG", logger="maxcompute_semantic"):
        result = c.search_tables("unknown_keyword")

    assert result == []
    assert "search_tables skipped 1 table(s) with unreadable columns" in caplog.text
    assert "orders" in caplog.text


# ─── list_partitions with schema ───


def test_list_partitions_with_schema() -> None:
    """list_partitions passes schema kwarg to get_table."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.get_max_partition = MagicMock(return_value=p1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_partitions("my_table", schema="sales", limit=100)
    assert result["is_partitioned"] is True
    call_kwargs = odps_mock.get_table.call_args[1]
    assert call_kwargs.get("schema") == "sales"


# ─── list_partitions get_max_partition skip_empty success ───


def test_list_partitions_skip_empty_succeeds() -> None:
    """get_max_partition with skip_empty=True succeeds → latest found."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.get_max_partition = MagicMock(return_value=p1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_partitions("my_table", limit=100)
    assert result["latest_partition"] == "ds=20240101"


def test_list_partitions_get_max_generic_exception_falls_through() -> None:
    """get_max_partition raises generic Exception → falls through, uses last from iteration."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.get_max_partition = MagicMock(side_effect=RuntimeError("internal"))
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_partitions("my_table", limit=100)
    assert result["latest_partition"] == "ds=20240101"


# ─── freshness_info ───


def test_freshness_info_non_partitioned_no_modified_time() -> None:
    """Non-partitioned table with no last_data_modified_time."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    table_mock.table_schema.partitions = []
    table_mock.last_data_modified_time = None
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["is_partitioned"] is False
    assert result["freshness_summary"] == "Non-partitioned table; modification time unavailable"


def test_freshness_info_non_partitioned_with_modified_time() -> None:
    """Non-partitioned table with last_data_modified_time."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    table_mock.table_schema.partitions = []
    mod_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    table_mock.last_data_modified_time = mod_time
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["is_partitioned"] is False
    assert "last data modification" in result["freshness_summary"]


def test_freshness_info_partitioned_within_hour() -> None:
    """Partitioned table modified <1 hour ago → 'within the last hour'."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["is_partitioned"] is True
    assert "within the last hour" in result["freshness_summary"]


def test_freshness_info_partitioned_hours_ago() -> None:
    """Partitioned table modified ~6 hours ago → 'X hours ago'."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(hours=6)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert "hours ago" in result["freshness_summary"]


def test_freshness_info_partitioned_days_ago_no_stale() -> None:
    """Partitioned table modified 3 days ago → 'X days ago', no stale warning."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(days=3)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert "days ago" in result["freshness_summary"]
    assert result["stale_warning"] is None


def test_freshness_info_partitioned_stale_over_7_days() -> None:
    """Partitioned table modified >7 days ago → stale warning."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(days=10)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert "days ago" in result["freshness_summary"]
    assert result["stale_warning"] is not None
    assert "stale" in result["stale_warning"]


def test_freshness_info_partitioned_no_modified_time() -> None:
    """Partitioned table with no last_modified → 'Latest partition data unavailable'."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = None
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["freshness_summary"] == "Latest partition data unavailable"


def test_freshness_info_partitioned_naive_datetime() -> None:
    """last_modified is naive datetime → treated as UTC."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    # Naive datetime — no tzinfo; use ~1h ago so freshness is "hours ago".
    # Don't construct from (year, month, day, hour-1) — wraps to -1 at
    # midnight UTC.
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    table_mock.last_data_modified_time = one_hour_ago.replace(tzinfo=None)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert "hours ago" in result["freshness_summary"]


def test_freshness_info_partitioned_with_schema() -> None:
    """freshness_info passes schema to get_table."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=p1)
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(hours=1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    c.freshness_info("my_table", schema="sales")
    call_kwargs = odps_mock.get_table.call_args[1]
    assert call_kwargs.get("schema") == "sales"


def test_freshness_info_skip_empty_typeerror_then_fallback() -> None:
    """get_max_partition raises TypeError on skip_empty=True, then succeeds with {}."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.iterate_partitions.return_value = iter([p1])
    # TypeError on skip_empty=True, success on empty kwargs
    table_mock.get_max_partition = MagicMock(side_effect=[TypeError("unexpected"), p1])
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(hours=1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["latest_partition"] == "ds=20240115"


def test_freshness_info_get_max_generic_exception_falls_to_iteration() -> None:
    """get_max_partition raises generic Exception → falls through to iterate_partitions."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.get_max_partition = MagicMock(side_effect=RuntimeError("internal"))
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(hours=1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["latest_partition"] == "ds=20240115"


def test_freshness_info_no_get_max_partition_attribute() -> None:
    """Table has no get_max_partition attribute → uses iteration."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]
    p1 = MagicMock()
    p1.partition_spec = "ds=20240115"
    table_mock.iterate_partitions.return_value = iter([p1])
    table_mock.get_max_partition = None
    table_mock.last_data_modified_time = datetime.now(timezone.utc) - timedelta(hours=1)
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.freshness_info("my_table")
    assert result["latest_partition"] == "ds=20240115"


# ─── execute_sql interactive mode ───


def test_execute_sql_interactive_mode() -> None:
    """execute_sql with use_interactive=True uses execute_sql_interactive."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "3"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.execute_sql_interactive.return_value = instance_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    reader_mock = MagicMock()
    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()
    reader_mock.schema.columns = [col_mock]
    reader_mock.__iter__ = MagicMock(return_value=iter([]))
    instance_mock.open_reader.return_value = reader_mock
    instance_mock.get_logview_address.return_value = "http://logview"

    result = c.execute_sql("SELECT 1", use_interactive=True)
    assert result.status == "success"
    odps_mock.execute_sql_interactive.assert_called_once()


# ─── execute_sql ODPSError ───


def test_execute_sql_odps_error_mapped() -> None:
    """ODPSError during execution → mapped via map_pyodps_exception.

    The mapping is structured-code-driven (0.5.0a42 removed substring
    fallback), so this test sets a structured ``exc.code`` pyodps's
    classifier knows about; using e.g. an unrecognized code would now
    correctly fall through to UnknownError rather than AuthFailedError.
    """
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    from odps import errors as odps_errors

    exc = odps_errors.ODPSError("InvalidAccessKeyId - bad key")
    exc.code = "InvalidAccessKeyId"
    odps_mock.run_sql.side_effect = exc
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import AuthFailedError

    with pytest.raises(AuthFailedError):
        c.execute_sql("SELECT 1", use_interactive=False)


def test_build_success_envelope_mid_stream_odps_error_mapped() -> None:
    """ODPSError during open_reader/iteration → mapped via map_pyodps_exception."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.return_value = None
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from odps import errors as odps_errors

    # Simulate ODPSError during open_reader
    exc = odps_errors.ODPSError("ConnectionError - network drop")
    exc.code = "ConnectionError"
    instance_mock.open_reader.side_effect = exc
    instance_mock._sql = "SELECT 1"

    from maxcompute_semantic.mc_client.errors import EndpointUnreachableError

    with pytest.raises(EndpointUnreachableError):
        c.execute_sql("SELECT 1", use_interactive=False)


def test_build_success_envelope_iteration_odps_error_mapped() -> None:
    """ODPSError during reader iteration → mapped via map_pyodps_exception."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.return_value = None
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from odps import errors as odps_errors

    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()

    reader_mock = MagicMock()
    reader_mock.schema.columns = [col_mock]

    exc = odps_errors.ODPSError("NoPermission - access denied for column select")
    exc.code = "NoPermission"
    record1 = MagicMock()
    record1.__getitem__ = MagicMock(side_effect=lambda i: ["val"][i])

    class _MidStreamIter:
        def __init__(self):
            self._count = 0

        def __iter__(self):
            return self

        def __next__(self):
            self._count += 1
            if self._count == 1:
                return record1
            raise exc

    reader_mock.__iter__ = MagicMock(return_value=_MidStreamIter())
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    instance_mock.open_reader.return_value = reader_mock
    instance_mock._sql = "SELECT id FROM t"
    instance_mock.get_logview_address.return_value = "http://logview"

    from maxcompute_semantic.mc_client.errors import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        c.execute_sql("SELECT id FROM t", use_interactive=False)


# ─── execute_sql timeout ───


def test_execute_sql_timeout_raises_mcs_timeout() -> None:
    """TimeoutError during wait_for_success → McsTimeoutError."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.side_effect = TimeoutError("timed out")
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import TimeoutError as McsTimeoutError

    with pytest.raises(McsTimeoutError):
        c.execute_sql("SELECT 1", use_interactive=False, timeout=30)


# ─── _build_success_envelope with rows ───


def test_build_success_envelope_with_rows() -> None:
    """_build_success_envelope correctly maps rows to dicts."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.return_value = None
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    col_mock = MagicMock()
    col_mock.name = "name"
    col_mock.type = MagicMock()
    col2_mock = MagicMock()
    col2_mock.name = "val"
    col2_mock.type = MagicMock()

    record1 = MagicMock()
    record1.__getitem__ = MagicMock(side_effect=lambda i: ["alice", 1][i])
    record2 = MagicMock()
    record2.__getitem__ = MagicMock(side_effect=lambda i: ["bob", 2][i])

    reader_mock = MagicMock()
    reader_mock.schema.columns = [col_mock, col2_mock]
    reader_mock.__iter__ = MagicMock(return_value=iter([record1, record2]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    instance_mock.open_reader.return_value = reader_mock
    instance_mock.get_logview_address.return_value = "http://logview"

    result = c.execute_sql("SELECT name, val FROM t", use_interactive=False)
    assert result.status == "success"
    assert len(result.data["rows"]) == 2


# ─── cost_estimate ODPSError ───


def test_cost_estimate_odps_error_mapped() -> None:
    """ODPSError during cost estimate → mapped via map_pyodps_exception."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    from odps import errors as odps_errors

    exc = odps_errors.ODPSError("Project not found - 'nonexistent'")
    exc.code = "ODPS-0130013"
    odps_mock.execute_sql_cost.side_effect = exc
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import ProjectNotFoundError

    with pytest.raises(ProjectNotFoundError):
        c.cost_estimate("SELECT * FROM t")


# ─── cost_estimate ok verdict ───


def test_cost_estimate_ok() -> None:
    """Low cost returns 'ok' verdict."""
    p = _make_profile(cost_thresholds=_ENABLED_THRESHOLDS)
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    cost_mock = MagicMock()
    cost_mock.input_size = 100  # tiny → well below thresholds
    odps_mock.execute_sql_cost.return_value = cost_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.cost_estimate("SELECT 1")
    assert result["verdict"] == "ok"


# ─── search_tables with catalog results ───


def test_search_tables_catalog_results_returned() -> None:
    """When catalog_search_tables returns results, they are converted to unified shape."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    catalog_results = [
        {"name": "orders", "comment": "order table"},
        {"name": "users", "comment": "user table"},
    ]
    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=catalog_results,
    ):
        result = c.search_tables("order")
    assert len(result) == 2
    assert result[0]["table_name"] == "orders"
    assert result[0]["score"] == 5


# ─── list_partitions non-partitioned ───


def test_list_partitions_non_partitioned() -> None:
    """Non-partitioned table returns is_partitioned=False with empty partitions."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    table_mock.table_schema.partitions = []
    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    result = c.list_partitions("my_table")
    assert result["is_partitioned"] is False
    assert result["partitions"] == []
    assert result["latest_partition"] is None


# ─── _ensure_odps security_token branch ───


def test_ensure_odps_no_security_token() -> None:
    """When creds have no security_token, it's not passed to ODPS."""
    p = _make_profile()
    c = MaxComputeClient(p)
    creds = Credentials(
        access_key_id="ak_id",
        access_key_secret="ak_secret",
        security_token="",  # empty → falsy
        expiration=None,
    )
    odps_instance = MagicMock()
    with (
        patch("maxcompute_semantic.mc_client.client.resolve_credentials", return_value=creds),
        patch("maxcompute_semantic.mc_client.client.ODPS", return_value=odps_instance) as odps_cls,
    ):
        c._ensure_odps()
        call_kwargs = odps_cls.call_args[1]
        assert "security_token" not in call_kwargs


# ─── Tier priming in execute_sql / cost_estimate ───


def test_execute_sql_primes_tier_for_3level_project() -> None:
    """Fresh client (no tier) calls get_tier before build_hints; 3-level hints injected."""
    # Override schema so it's set — required for build_hints to inject namespace hints.
    # Disable cost gate so it doesn't try to compute against the MagicMock odps.
    p_with_schema = Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="my_schema", tables="*"),),
        cost_thresholds=CostThresholds(enabled=False),
    )
    c = MaxComputeClient(p_with_schema)
    # _tier is None on fresh client — this is the bug scenario.
    assert c._tier is None

    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.return_value = None
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    reader_mock = MagicMock()
    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()
    reader_mock.schema.columns = [col_mock]
    reader_mock.__iter__ = MagicMock(return_value=iter([]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    instance_mock.open_reader.return_value = reader_mock
    instance_mock.get_logview_address.return_value = "http://logview"

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        result = c.execute_sql("SELECT 1", use_interactive=False, schema="my_schema")
        mock_get_tier.assert_called_once_with(p_with_schema, "test_project", client=c)

    # Tier should now be cached on the client.
    assert c._tier == "3"

    # Verify hints include namespace + default.schema for 3-level project.
    call_args = odps_mock.run_sql.call_args
    hints = call_args[1]["hints"]
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "my_schema"

    assert result.status == "success"


def test_execute_sql_tier_already_set_skips_get_tier() -> None:
    """If _tier is already set, get_tier is NOT called again."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"  # already resolved

    odps_mock = MagicMock()
    instance_mock = MagicMock()
    odps_mock.run_sql.return_value = instance_mock
    instance_mock.wait_for_success.return_value = None
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    reader_mock = MagicMock()
    col_mock = MagicMock()
    col_mock.name = "id"
    col_mock.type = MagicMock()
    reader_mock.schema.columns = [col_mock]
    reader_mock.__iter__ = MagicMock(return_value=iter([]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    instance_mock.open_reader.return_value = reader_mock
    instance_mock.get_logview_address.return_value = "http://logview"

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        c.execute_sql("SELECT 1", use_interactive=False)
        mock_get_tier.assert_not_called()


def test_cost_estimate_primes_tier_for_3level_project() -> None:
    """Fresh client (no tier) calls get_tier before cost_estimate builds hints."""
    p_with_schema = Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="my_schema", tables="*"),),
        cost_thresholds=CostThresholds(enabled=False),
    )
    c = MaxComputeClient(p_with_schema)
    assert c._tier is None

    odps_mock = MagicMock()
    cost_mock = MagicMock()
    cost_mock.input_size = 100  # tiny → ok verdict
    odps_mock.execute_sql_cost.return_value = cost_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        result = c.cost_estimate("SELECT 1", schema="my_schema")
        mock_get_tier.assert_called_once_with(p_with_schema, "test_project", client=c)

    assert c._tier == "3"

    # Verify hints include namespace + default.schema for 3-level project.
    call_args = odps_mock.execute_sql_cost.call_args
    hints = call_args[1]["hints"]
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "my_schema"

    assert result["verdict"] == "ok"


def test_cost_estimate_tier_already_set_skips_get_tier() -> None:
    """If _tier is already set, get_tier is NOT called again in cost_estimate."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"

    odps_mock = MagicMock()
    cost_mock = MagicMock()
    cost_mock.input_size = 100
    odps_mock.execute_sql_cost.return_value = cost_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        c.cost_estimate("SELECT 1")
        mock_get_tier.assert_not_called()


def test_execute_sql_tier_probe_failure_propagates() -> None:
    """If get_tier raises McsError, it propagates from execute_sql."""
    p = _make_profile()
    c = MaxComputeClient(p)
    assert c._tier is None

    c._odps = MagicMock()
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import McsError

    with (
        patch(
            "maxcompute_semantic.mc_client.tier.get_tier",
            side_effect=McsError("tier probe failed"),
        ),
        pytest.raises(McsError, match="tier probe failed"),
    ):
        c.execute_sql("SELECT 1")


def test_cost_estimate_tier_probe_failure_propagates() -> None:
    """If get_tier raises McsError, it propagates from cost_estimate."""
    p = _make_profile()
    c = MaxComputeClient(p)
    assert c._tier is None

    c._odps = MagicMock()
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    from maxcompute_semantic.mc_client.errors import McsError

    with (
        patch(
            "maxcompute_semantic.mc_client.tier.get_tier",
            side_effect=McsError("tier probe failed"),
        ),
        pytest.raises(McsError, match="tier probe failed"),
    ):
        c.cost_estimate("SELECT 1")


# ─── can_access_table ───


def test_can_access_table_allowed() -> None:
    """can_access_table returns allowed=True when cost_estimate succeeds."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    # Mock cost_estimate to return a valid result (no exception).
    c.cost_estimate = MagicMock(
        return_value={
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }
    )
    result = c.can_access_table("my_table", schema=None)
    assert result["allowed"] is True
    assert result["check_mode"] == "cost_estimate"


def test_can_access_table_denied_table_not_found() -> None:
    """can_access_table returns allowed=False with reason=table_not_found."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    from maxcompute_semantic.mc_client.errors import TableNotFoundError

    c.cost_estimate = MagicMock(side_effect=TableNotFoundError("not found", remediation="check"))
    result = c.can_access_table("missing", schema=None)
    assert result["allowed"] is False
    assert result["reason"] == "table_not_found"


def test_can_access_table_denied_permission() -> None:
    """can_access_table returns allowed=False with reason=permission_denied."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    from maxcompute_semantic.mc_client.errors import PermissionDeniedError

    c.cost_estimate = MagicMock(
        side_effect=PermissionDeniedError("denied", remediation="request access")
    )
    result = c.can_access_table("restricted", schema=None)
    assert result["allowed"] is False
    assert result["reason"] == "permission_denied"


def test_can_access_table_other_error() -> None:
    """can_access_table returns allowed=False with reason=other_error for unexpected McsError."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._tier = "2"
    from maxcompute_semantic.mc_client.errors import RateLimitError

    c.cost_estimate = MagicMock(side_effect=RateLimitError("rate limited", remediation="retry"))
    result = c.can_access_table("my_table", schema=None)
    assert result["allowed"] is False
    assert result["reason"] == "other_error"
    assert result["check_error_code"] == "RateLimit"


def test_can_access_table_primes_tier() -> None:
    """can_access_table primes tier via get_tier if _tier is None."""
    p = _make_profile()
    c = MaxComputeClient(p)
    assert c._tier is None
    c.cost_estimate = MagicMock(
        return_value={
            "estimated_input_bytes": 0,
            "estimated_cost_cny": 0.0,
            "verdict": "ok",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }
    )
    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        result = c.can_access_table("my_table", schema=None)
        mock_get_tier.assert_called_once_with(p, "test_project", client=c)
    assert c._tier == "3"
    assert result["allowed"] is True


def test_run_sql_async_primes_tier_for_3level_project() -> None:
    """Fresh client (no tier) calls get_tier before build_hints in run_sql_async."""
    p_with_schema = Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="my_schema", tables="*"),),
        cost_thresholds=CostThresholds(enabled=False),
    )
    c = MaxComputeClient(p_with_schema)
    assert c._tier is None

    mock_instance = MagicMock()
    mock_instance.id = "inst_42"
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.run_sql.return_value = mock_instance

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        result = c.run_sql_async("SELECT 1", schema="my_schema")
        mock_get_tier.assert_called_once_with(p_with_schema, "test_project", client=c)

    assert c._tier == "3"
    assert result == "inst_42"

    call_args = odps.run_sql.call_args
    hints = call_args[1]["hints"]
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "my_schema"


# ─── explain ───


def test_explain_success() -> None:
    """explain() returns plan text + logview_url."""
    p = _make_profile()
    c = MaxComputeClient(p)
    mock_instance = MagicMock()
    mock_instance.get_task_results.return_value = {"sql": "PLAN TEXT HERE"}
    mock_instance.get_logview_address.return_value = "http://logview/123"

    c._ensure_odps = MagicMock()
    c._tier = "2"
    odps = c._ensure_odps.return_value
    odps.run_sql.return_value = mock_instance

    result = c.explain("SELECT * FROM t")
    assert result["plan"] == "PLAN TEXT HERE"
    assert result["logview_url"] == "http://logview/123"


def test_explain_timeout() -> None:
    """explain() raises McsTimeoutError on timeout."""
    from maxcompute_semantic.mc_client.errors import TimeoutError as McsTimeoutError

    p = _make_profile()
    c = MaxComputeClient(p)
    mock_instance = MagicMock()
    mock_instance.wait_for_success.side_effect = TimeoutError("timed out")

    c._ensure_odps = MagicMock()
    c._tier = "2"
    odps = c._ensure_odps.return_value
    odps.run_sql.return_value = mock_instance

    with pytest.raises(McsTimeoutError):
        c.explain("SELECT * FROM t", timeout=10)


def test_explain_primes_tier_for_3level_project() -> None:
    """Fresh client (no tier) calls get_tier before build_hints in explain."""
    p_with_schema = Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="my_schema", tables="*"),),
    )
    c = MaxComputeClient(p_with_schema)
    assert c._tier is None

    mock_instance = MagicMock()
    mock_instance.get_task_results.return_value = {"sql": "PLAN"}
    mock_instance.get_logview_address.return_value = "http://logview"
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.run_sql.return_value = mock_instance

    with patch("maxcompute_semantic.mc_client.tier.get_tier", return_value="3") as mock_get_tier:
        result = c.explain("SELECT 1", schema="my_schema")
        mock_get_tier.assert_called_once_with(p_with_schema, "test_project", client=c)

    assert c._tier == "3"
    assert result["plan"] == "PLAN"

    call_args = odps.run_sql.call_args
    hints = call_args[1]["hints"]
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "my_schema"


# ─── list_functions ───


def test_list_functions_maps_pyodps_metadata() -> None:
    """list_functions() maps pyodps Function objects into package rows."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.list_functions.return_value = [
        SimpleNamespace(
            name="normalize_city",
            class_type="com.example.NormalizeCity",
            program_language="JAVA",
            is_sql_function=False,
            is_embedded_function=False,
        ),
        SimpleNamespace(
            name="sql_metric",
            class_type=None,
            program_language=None,
            is_sql_function=True,
            is_embedded_function=False,
        ),
    ]

    result = c.list_functions()

    odps.list_functions.assert_called_once_with(project="test_project")
    assert result == [
        {
            "name": "normalize_city",
            "kind": "java",
            "signature": None,
            "class_name": "com.example.NormalizeCity",
            "description": None,
        },
        {
            "name": "sql_metric",
            "kind": "sql",
            "signature": None,
            "class_name": None,
            "description": None,
        },
    ]


def test_list_functions_empty_catalog() -> None:
    p = _make_profile()
    c = MaxComputeClient(p)
    c._ensure_odps = MagicMock()
    c._ensure_odps.return_value.list_functions.return_value = []

    assert c.list_functions() == []


# ─── sample_table ───


def test_sample_table_success() -> None:
    """sample_table returns rows as dicts."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    col1 = MagicMock()
    col1.name = "id"
    col2 = MagicMock()
    col2.name = "name"
    table_mock.table_schema.columns = [col1, col2]

    rec1 = MagicMock()
    rec1.__getitem__ = MagicMock(side_effect=lambda i: [1, "a"][i])
    rec2 = MagicMock()
    rec2.__getitem__ = MagicMock(side_effect=lambda i: [2, "b"][i])

    reader_mock = MagicMock()
    reader_mock.__iter__ = MagicMock(return_value=iter([rec1, rec2]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    table_mock.open_reader.return_value = reader_mock

    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    # Mock list_partitions to return non-partitioned
    c.list_partitions = MagicMock(
        return_value={"is_partitioned": False, "latest_partition": None, "partitions": []}
    )

    result = c.sample_table("orders", schema=None, limit=20)
    assert result["table_name"] == "orders"
    assert result["row_count"] == 2
    assert result["rows"][0]["id"] == 1
    assert result["rows"][0]["name"] == "a"
    assert result["partition_used"] is None


def test_sample_table_partitioned_auto_detect() -> None:
    """sample_table auto-detects latest partition when no partition given."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    col1 = MagicMock()
    col1.name = "id"
    table_mock.table_schema.columns = [col1]

    rec1 = MagicMock()
    rec1.__getitem__ = MagicMock(side_effect=lambda i: [42][i])

    reader_mock = MagicMock()
    reader_mock.__iter__ = MagicMock(return_value=iter([rec1]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    table_mock.open_reader.return_value = reader_mock

    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    # Mock list_partitions to return partitioned table with latest partition
    c.list_partitions = MagicMock(
        return_value={
            "is_partitioned": True,
            "latest_partition": "ds=20240115",
            "partitions": ["ds=20240115"],
        }
    )

    result = c.sample_table("orders", schema=None, limit=20)
    assert result["partition_used"] == "ds=20240115"
    # Verify open_reader received partition kwarg
    call_kwargs = table_mock.open_reader.call_args[1]
    assert call_kwargs.get("partition") == "ds=20240115"


def test_sample_table_with_schema() -> None:
    """sample_table passes schema to get_table."""
    p = _make_profile()
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    table_mock = MagicMock()
    col1 = MagicMock()
    col1.name = "id"
    table_mock.table_schema.columns = [col1]

    reader_mock = MagicMock()
    reader_mock.__iter__ = MagicMock(return_value=iter([]))
    reader_mock.__enter__ = MagicMock(return_value=reader_mock)
    reader_mock.__exit__ = MagicMock(return_value=False)
    table_mock.open_reader.return_value = reader_mock

    odps_mock.get_table.return_value = table_mock
    c._odps = odps_mock
    c._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)

    c.list_partitions = MagicMock(
        return_value={"is_partitioned": False, "latest_partition": None, "partitions": []}
    )

    c.sample_table("orders", schema="sales", limit=20)
    call_kwargs = odps_mock.get_table.call_args[1]
    assert call_kwargs.get("schema") == "sales"


# ─── profile_table ───


def test_profile_table_success() -> None:
    """profile_table computes column stats from sample."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c.sample_table = MagicMock(
        return_value={
            "table_name": "orders",
            "rows": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            "row_count": 2,
            "partition_used": None,
        }
    )

    result = c.profile_table("orders", schema=None, limit=20)
    assert result["table_name"] == "orders"
    assert len(result["columns"]) == 2
    assert result["columns"][0]["column_name"] == "id"
    assert result["columns"][0]["distinct_count"] == 2
    assert result["columns"][0]["null_count"] == 0
    assert result["columns"][0]["min"] == 1.0
    assert result["columns"][0]["max"] == 2.0
    assert result["columns"][1]["column_name"] == "name"
    assert result["columns"][1]["distinct_count"] == 2
    assert result["columns"][1]["min"] == "a"
    assert result["columns"][1]["max"] == "b"


def test_profile_table_empty_sample() -> None:
    """profile_table with empty sample returns empty columns list."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c.sample_table = MagicMock(
        return_value={
            "table_name": "orders",
            "rows": [],
            "row_count": 0,
            "partition_used": None,
        }
    )

    result = c.profile_table("orders", schema=None, limit=20)
    assert result["columns"] == []
    assert result["row_count"] == 0


def test_profile_table_with_nulls() -> None:
    """profile_table counts nulls and computes stats on non-null values."""
    p = _make_profile()
    c = MaxComputeClient(p)
    c.sample_table = MagicMock(
        return_value={
            "table_name": "orders",
            "rows": [{"id": 1, "name": "a"}, {"id": None, "name": None}],
            "row_count": 2,
            "partition_used": None,
        }
    )

    result = c.profile_table("orders", schema=None, limit=20)
    assert result["columns"][0]["null_count"] == 1
    assert result["columns"][0]["distinct_count"] == 1
    assert result["columns"][0]["min"] == 1.0
    assert result["columns"][1]["null_count"] == 1


# ─── Async job lifecycle methods ───


def _instance_for_async_status(
    status: object,
    *,
    task_statuses: dict[str, object] | None = None,
) -> MagicMock:
    mock_instance = MagicMock()
    mock_instance.status = status
    mock_instance.start_time = None
    mock_instance.end_time = None
    mock_instance.name = ""
    mock_instance.get_logview_address.return_value = "http://logview/1"
    mock_instance.get_task_statuses.return_value = {
        name: SimpleNamespace(
            name=name,
            type="SQL",
            status=task_status,
            start_time=None,
            end_time=None,
        )
        for name, task_status in (task_statuses or {}).items()
    }
    return mock_instance


def test_run_sql_async_returns_instance_id() -> None:
    """run_sql_async returns instance ID without waiting."""
    mock_instance = MagicMock()
    mock_instance.id = "inst_123"
    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    c._tier = "2"
    odps = c._ensure_odps.return_value
    odps.run_sql.return_value = mock_instance

    result = c.run_sql_async("SELECT * FROM t")
    assert result == "inst_123"
    mock_instance.wait_for_success.assert_not_called()


def test_run_sql_async_enforces_cost_gate_before_submission() -> None:
    """run_sql_async must not bypass confirm-cost protection."""
    from maxcompute_semantic.mc_client.errors import CostConfirmRequiredError

    c = MaxComputeClient(_make_profile(cost_thresholds=_ENABLED_THRESHOLDS))
    c._ensure_odps = MagicMock()
    c._tier = "2"
    c.cost_estimate = MagicMock(
        return_value={
            "estimated_input_bytes": 35791394133,
            "estimated_cost_cny": 15.0,
            "verdict": "confirm",
            "thresholds": {"confirm_cny": 10.0, "blocked_cny": 100.0},
        }
    )

    with pytest.raises(CostConfirmRequiredError):
        c.run_sql_async("SELECT * FROM medium_table")

    c._ensure_odps.assert_not_called()


def test_get_instance_status_success() -> None:
    """get_instance_status returns status dict."""
    mock_instance = _instance_for_async_status(
        "Status.TERMINATED",
        task_statuses={"SQLTask": "TaskStatus.SUCCESS"},
    )
    mock_instance.name = "sql_task"

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.get_instance_status("inst_123")
    assert result["instance_id"] == "inst_123"
    assert result["status"] == "Status.TERMINATED"
    assert result["status_name"] == "TERMINATED"
    assert result["lifecycle_state"] == "success"
    assert result["terminal"] is True
    assert result["successful"] is True
    assert result["task_statuses"] == [
        {
            "name": "SQLTask",
            "type": "SQL",
            "status": "TaskStatus.SUCCESS",
            "status_name": "SUCCESS",
            "start_time": None,
            "end_time": None,
        }
    ]
    mock_instance.reload.assert_called_once()


def test_wait_for_instance_completed() -> None:
    """wait_for_instance returns final status when completed."""
    mock_instance = _instance_for_async_status(
        "Terminated",
        task_statuses={"SQLTask": "SUCCESS"},
    )

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.wait_for_instance("inst_123", timeout=10, interval=1)
    assert result["instance_id"] == "inst_123"
    assert result["status"] == "Terminated"
    assert result["lifecycle_state"] == "success"
    assert result["successful"] is True


def test_wait_for_instance_accepts_pyodps_status_enum_string() -> None:
    """pyodps statuses stringify as Status.TERMINATED in live runs."""
    mock_instance = _instance_for_async_status("Status.TERMINATED")

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.wait_for_instance("inst_123", timeout=1, interval=1)

    assert result["instance_id"] == "inst_123"
    assert result["status"] == "Status.TERMINATED"
    assert result["status_name"] == "TERMINATED"
    assert result["lifecycle_state"] == "terminated"


@pytest.mark.parametrize(
    ("instance_status", "expected_lifecycle", "expected_successful"),
    [
        ("Status.FAILED", "failed", False),
        ("Failed", "failed", False),
        ("Status.CANCELLED", "cancelled", False),
        ("Canceled", "cancelled", False),
        ("Status.SUCCESS", "success", True),
        ("Succeeded", "success", True),
    ],
)
def test_wait_for_instance_accepts_terminal_instance_status_variants(
    instance_status: str,
    expected_lifecycle: str,
    expected_successful: bool,
) -> None:
    """Future / alternate status strings should classify as terminal."""
    mock_instance = _instance_for_async_status(instance_status)

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    c._ensure_odps.return_value.get_instance.return_value = mock_instance

    result = c.wait_for_instance("inst_123", timeout=1, interval=1)

    assert result["terminal"] is True
    assert result["lifecycle_state"] == expected_lifecycle
    assert result["successful"] is expected_successful


@pytest.mark.parametrize(
    ("task_status", "expected_lifecycle", "expected_successful"),
    [
        ("TaskStatus.SUCCESS", "success", True),
        ("TaskStatus.FAILED", "failed", False),
        ("TaskStatus.CANCELLED", "cancelled", False),
        ("TaskStatus.CANCELED", "cancelled", False),
    ],
)
def test_wait_for_instance_reports_terminal_task_lifecycle(
    task_status: str,
    expected_lifecycle: str,
    expected_successful: bool,
) -> None:
    """Terminated MaxCompute instances must expose task-level outcome."""
    mock_instance = _instance_for_async_status(
        "Status.TERMINATED",
        task_statuses={"SQLTask": task_status},
    )

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    c._ensure_odps.return_value.get_instance.return_value = mock_instance

    result = c.wait_for_instance("inst_123", timeout=1, interval=1)

    assert result["terminal"] is True
    assert result["lifecycle_state"] == expected_lifecycle
    assert result["successful"] is expected_successful
    assert result["task_statuses"][0]["status_name"] == task_status.rsplit(".", 1)[-1]


@pytest.mark.parametrize(
    ("instance_status", "expected_lifecycle"),
    [
        ("Status.RUNNING", "running"),
        ("Running", "running"),
        ("Status.SUSPENDED", "suspended"),
        ("Suspended", "suspended"),
    ],
)
def test_get_instance_status_reports_non_terminal_lifecycle(
    instance_status: str,
    expected_lifecycle: str,
) -> None:
    """Running and suspended states stay non-terminal instead of being collapsed."""
    mock_instance = _instance_for_async_status(instance_status)

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    c._ensure_odps.return_value.get_instance.return_value = mock_instance

    result = c.get_instance_status("inst_123")

    assert result["terminal"] is False
    assert result["successful"] is None
    assert result["lifecycle_state"] == expected_lifecycle


def test_wait_for_instance_timeout() -> None:
    """wait_for_instance raises TimeoutError on timeout."""
    from maxcompute_semantic.mc_client.errors import TimeoutError as McsTimeoutError

    mock_instance = _instance_for_async_status("Running")

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    with (
        patch("time.monotonic", side_effect=[0.0, 2.0]),
        patch("time.sleep") as sleep,
        pytest.raises(McsTimeoutError, match="did not complete"),
    ):
        c.wait_for_instance("inst_123", timeout=1, interval=1)

    sleep.assert_not_called()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0, "interval": 1}, "timeout"),
        ({"timeout": 1, "interval": 0}, "interval"),
    ],
)
def test_wait_for_instance_rejects_non_positive_limits(
    kwargs: dict[str, int], message: str
) -> None:
    from maxcompute_semantic.mc_client.errors import McsError

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()

    with pytest.raises(McsError, match=message) as exc_info:
        c.wait_for_instance("inst_123", **kwargs)

    assert str(exc_info.value.code) == "InvalidArgument"
    assert exc_info.value.exit_code == 2
    c._ensure_odps.assert_not_called()


def test_get_instance_result_rows() -> None:
    """get_instance_result returns rows and schema."""
    mock_instance = MagicMock()
    # Build reader with schema + records
    col1 = MagicMock()
    col1.name = "id"
    col1.type = "BIGINT"
    col2 = MagicMock()
    col2.name = "name"
    col2.type = "STRING"

    rec1 = MagicMock()
    rec1.__getitem__ = MagicMock(side_effect=lambda i: [1, "a"][i])
    rec2 = MagicMock()
    rec2.__getitem__ = MagicMock(side_effect=lambda i: [2, "b"][i])

    reader = MagicMock()
    reader.schema.columns = [col1, col2]
    reader.__iter__ = MagicMock(return_value=iter([rec1, rec2]))

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=reader)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_instance.open_reader.return_value = ctx

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.get_instance_result("inst_123")
    assert result["instance_id"] == "inst_123"
    assert result["row_count"] == 2


def test_get_instance_result_honors_max_rows() -> None:
    """get_instance_result caps returned rows and reports truncation."""
    mock_instance = MagicMock()
    col = MagicMock()
    col.name = "id"
    col.type = "BIGINT"

    records = []
    for value in range(3):
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda i, _value=value: [_value][i])
        records.append(record)

    reader = MagicMock()
    reader.schema.columns = [col]
    reader.__iter__ = MagicMock(return_value=iter(records))

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=reader)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_instance.open_reader.return_value = ctx

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.get_instance_result("inst_123", max_rows=2)
    assert result["row_count"] == 2
    assert result["result_max_rows"] == 2
    assert result["truncated"] is True
    assert result["has_more"] is True


def test_get_instance_result_honors_result_offset() -> None:
    """get_instance_result returns a window starting at result_offset."""
    mock_instance = MagicMock()
    col = MagicMock()
    col.name = "id"
    col.type = "BIGINT"

    records = []
    for value in range(5):
        record = MagicMock()
        record.__getitem__ = MagicMock(side_effect=lambda i, _value=value: [_value][i])
        records.append(record)

    reader = MagicMock()
    reader.schema.columns = [col]
    reader.count = 5
    reader.__getitem__ = MagicMock(return_value=iter(records[2:5]))
    reader.__iter__ = MagicMock(return_value=iter(records))

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=reader)
    ctx.__exit__ = MagicMock(return_value=False)
    mock_instance.open_reader.return_value = ctx

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.get_instance_result("inst_123", max_rows=2, result_offset=2)
    assert result["rows"] == [{"id": 2}, {"id": 3}]
    assert result["row_count"] == 2
    assert result["result_offset"] == 2
    assert result["next_offset"] == 4
    assert result["total_row_count"] == 5
    assert result["truncated"] is True
    assert result["has_more"] is True
    reader.__getitem__.assert_called_once_with(slice(2, 5))


def test_cancel_instance() -> None:
    """cancel_instance calls instance.stop(), reloads, and returns actual status."""
    mock_instance = MagicMock()
    mock_instance.status = "Instance.Status.CANCELLED"

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.cancel_instance("inst_123")
    assert result["instance_id"] == "inst_123"
    assert result["status"] == "CANCELLED"
    assert result["cancelled"] is True
    mock_instance.stop.assert_called_once()
    mock_instance.reload.assert_called_once()


@pytest.mark.parametrize("status", ["Instance.Status.SUCCESS", "Instance.Status.FAILED"])
def test_cancel_instance_not_cancelled_when_actual_status_is_not_cancelled(status: str) -> None:
    """cancelled means the actual post-stop state is a cancelled state."""
    mock_instance = MagicMock()
    mock_instance.status = status

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.get_instance.return_value = mock_instance

    result = c.cancel_instance("inst_123")
    assert result["status"] == status.rsplit(".", 1)[-1]
    assert result["cancelled"] is False


def test_list_recent_instances() -> None:
    """list_recent_instances returns list of status dicts."""
    inst1 = MagicMock()
    inst1.id = "inst_1"
    inst1.status = "Terminated"
    inst1.start_time = None
    inst1.name = "task_a"

    inst2 = MagicMock()
    inst2.id = "inst_2"
    inst2.status = "Running"
    inst2.start_time = None
    inst2.name = "task_b"

    c = MaxComputeClient(_make_profile())
    c._ensure_odps = MagicMock()
    odps = c._ensure_odps.return_value
    odps.list_instances.return_value = [inst1, inst2]

    result = c.list_recent_instances(limit=20)
    assert len(result) == 2
    assert result[0]["id"] == "inst_1"
    assert result[0]["status"] == "Terminated"
    assert result[1]["id"] == "inst_2"
    assert result[1]["status"] == "Running"
