# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for mc_client/client.py — execute_sql + list_schemas + list_tables
+ describe_table + cost_estimate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.envelope import Envelope
from maxcompute_semantic.mc_client.errors import (
    CostBlockedError,
    PermissionDeniedError,
    TableNotFoundError,
    WriteOpRejectedError,
)
from maxcompute_semantic.mc_client.errors import (
    TimeoutError as McsTimeoutError,
)


def _make_profile(*, cost_thresholds: CostThresholds | None = None) -> Profile:
    # Default cost_thresholds are disabled (enabled=False) so the execute_sql
    # cost gate is a no-op for tests that don't care about it — they cover
    # the read/wait path, not the gate itself (which has its own
    # test_cost_gate.py + a dedicated integration test below). The
    # cost_estimate tests pass an explicit enabled CostThresholds(10, 100)
    # to exercise the verdict branches.
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
        cost_thresholds=cost_thresholds or CostThresholds(enabled=False),
    )


def _make_client(*, cost_thresholds: CostThresholds | None = None) -> MaxComputeClient:
    return MaxComputeClient(_make_profile(cost_thresholds=cost_thresholds))


_ENABLED_THRESHOLDS = CostThresholds(confirm_cny=10.0, blocked_cny=100.0)


def _fake_instance(*, rows=None, schema_cols=None, logview="http://logview") -> MagicMock:
    """Build a fake pyodps instance with reader returning given rows + schema."""
    if rows is None:
        rows = [[1, "a"], [2, "b"]]
    if schema_cols is None:
        schema_cols = [("id", "bigint"), ("name", "string")]

    instance = MagicMock()
    instance.get_logview_address.return_value = logview

    # Build fake reader
    reader = MagicMock()
    col_objs = []
    for name, typ in schema_cols:
        c = MagicMock()
        c.name = name
        c.type = typ
        col_objs.append(c)
    reader.schema.columns = col_objs

    # Build fake records
    records = []
    for row in rows:
        rec = MagicMock()
        for _i, _val in enumerate(row):
            rec.__getitem__ = lambda self, idx, _vals=row: _vals[idx]
        # Make record subscriptable via side_effect
        rec = MagicMock(side_effect=lambda idx, _row=row: _row[idx])
        records.append(rec)
    reader.__iter__ = MagicMock(return_value=iter(records))

    # open_reader returns a context manager
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=reader)
    ctx.__exit__ = MagicMock(return_value=False)
    instance.open_reader.return_value = ctx

    return instance


def _fake_instance_with_reader_rows(*, row_count: int, schema_cols=None) -> MagicMock:
    """Build a lightweight fake instance for large result-window tests."""
    if schema_cols is None:
        schema_cols = [("id", "bigint")]

    columns = [SimpleNamespace(name=name, type=typ) for name, typ in schema_cols]

    class _Reader:
        schema = SimpleNamespace(columns=columns)

        def __iter__(self):
            return (_Record(i) for i in range(row_count))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Record:
        def __init__(self, value: int) -> None:
            self.value = value

        def __getitem__(self, index: int) -> int:
            if index != 0:
                raise IndexError(index)
            return self.value

    instance = MagicMock()
    instance.open_reader.return_value = _Reader()
    instance.get_logview_address.return_value = "http://logview"
    return instance


# ─── execute_sql: success ───


def test_success_returns_envelope() -> None:
    c = _make_client()
    c._tier = "3"
    fake_inst = _fake_instance()

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    result = c.execute_sql("SELECT * FROM t")
    assert isinstance(result, Envelope)
    assert result.status == "success"
    assert result.data["row_count"] == 2
    assert result.data["logview_url"] == "http://logview"


def test_execute_sql_caps_result_rows_without_rewriting_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCS_SQL_RESULT_MAX_ROWS", raising=False)

    c = _make_client()
    c._tier = "3"
    fake_inst = _fake_instance_with_reader_rows(row_count=10_002)

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    sql = "SELECT * FROM t"
    result = c.execute_sql(sql)

    odps_mock.run_sql.assert_called_once()
    assert odps_mock.run_sql.call_args.args[0] == sql
    assert result.data["row_count"] == 10_000
    assert len(result.data["rows"]) == 10_000
    assert result.data["result_max_rows"] == 10_000
    assert result.data["truncated"] is True
    assert result.data["has_more"] is True


def test_execute_sql_offsets_result_rows_and_reports_next_page() -> None:
    c = _make_client()
    c._tier = "3"
    fake_inst = _fake_instance_with_reader_rows(row_count=7)

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    sql = "SELECT * FROM t"
    result = c.execute_sql(sql, max_rows=2, result_offset=3)

    odps_mock.run_sql.assert_called_once()
    assert odps_mock.run_sql.call_args.args[0] == sql
    assert result.data["rows"] == [{"id": 3}, {"id": 4}]
    assert result.data["row_count"] == 2
    assert result.data["result_offset"] == 3
    assert result.data["next_offset"] == 5
    assert result.data["truncated"] is True
    assert result.data["has_more"] is True


def test_table_not_found_raises_classified() -> None:
    c = _make_client()

    odps_mock = MagicMock()
    exc = Exception("Table not found - 'x.y'")
    exc.code = "NoSuchObject"
    odps_mock.run_sql.side_effect = exc
    c._odps = odps_mock

    # Need to patch ODPSError so the except clause catches it
    from odps import errors as odps_errors

    # The exc needs to be an ODPSError subclass for the except clause to work.
    # Since we can't easily make a real one, we patch isinstance behavior.
    with patch.object(odps_errors, "ODPSError", (Exception,)):
        # Simpler: just make exc inherit from ODPSError
        pass

    # Actually, pyodps errors are real ODPSError subclasses. Let's use a real one.
    real_exc = odps_errors.NoSuchObject("Table not found - 'x.y'")
    real_exc.code = "NoSuchObject"
    odps_mock.run_sql.side_effect = real_exc
    c._odps = odps_mock

    with pytest.raises(TableNotFoundError):
        c.execute_sql("SELECT * FROM x.y")


def test_permission_denied_raises_classified() -> None:
    c = _make_client()
    from odps import errors as odps_errors

    real_exc = odps_errors.NoPermission("Access Denied - SELECT on Table 't'")
    real_exc.code = "NoPermission"
    odps_mock = MagicMock()
    odps_mock.run_sql.side_effect = real_exc
    c._odps = odps_mock

    with pytest.raises(PermissionDeniedError):
        c.execute_sql("SELECT * FROM t")


def test_execute_sql_timeout_carries_instance_id_for_async_handoff() -> None:
    """A synchronous wait timeout must not lose the running job: the raised
    error carries instance_id + logview so the CLI can hand off to the async
    lifecycle (sql wait / sql result) instead of discarding the submission."""
    c = _make_client()
    c._tier = "3"

    fake_inst = MagicMock()
    fake_inst.id = "20260622083000_inst_001"
    fake_inst.get_logview_address.return_value = "http://logview/xyz"
    fake_inst.wait_for_success.side_effect = TimeoutError("deadline exceeded")

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    with pytest.raises(McsTimeoutError) as excinfo:
        c.execute_sql("SELECT * FROM t", timeout=1)

    ctx = excinfo.value.context
    assert ctx["instance_id"] == "20260622083000_inst_001"
    assert ctx["logview_url"] == "http://logview/xyz"
    # The remediation must steer to the async lifecycle, not a nonexistent
    # --timeout flag on `mcs sql execute`.
    assert "sql wait" in excinfo.value.remediation
    assert "do not resubmit" in excinfo.value.remediation.lower()


def test_execute_sql_timeout_survives_logview_failure() -> None:
    """If get_logview_address() throws, the timeout error still carries the
    instance_id with an empty logview (the defensive except branch)."""
    c = _make_client()
    c._tier = "3"

    fake_inst = MagicMock()
    fake_inst.id = "inst_logview_fail"
    fake_inst.get_logview_address.side_effect = RuntimeError("logview unavailable")
    fake_inst.wait_for_success.side_effect = TimeoutError("deadline exceeded")

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    with pytest.raises(McsTimeoutError) as excinfo:
        c.execute_sql("SELECT * FROM t", timeout=1)

    ctx = excinfo.value.context
    assert ctx["instance_id"] == "inst_logview_fail"
    assert ctx["logview_url"] == ""


def test_uses_interactive_when_requested() -> None:
    c = _make_client()
    c._tier = None
    fake_inst = _fake_instance()

    odps_mock = MagicMock()
    odps_mock.execute_sql_interactive.return_value = fake_inst
    c._odps = odps_mock

    c.execute_sql("SELECT 1", use_interactive=True)
    odps_mock.execute_sql_interactive.assert_called_once()
    odps_mock.run_sql.assert_not_called()


def test_passes_tier_hints() -> None:
    c = _make_client()
    c._tier = "3"
    fake_inst = _fake_instance()

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock

    c.execute_sql("SELECT * FROM t", schema="default")
    call_kwargs = odps_mock.run_sql.call_args
    hints = call_kwargs[1]["hints"]  # kwargs
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "default"


def test_timeout_raises_classified() -> None:
    c = _make_client()
    fake_inst = MagicMock()
    fake_inst.wait_for_success.side_effect = TimeoutError("timed out")
    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock

    with pytest.raises(McsTimeoutError) as exc_info:
        c.execute_sql("SELECT * FROM big_table", timeout=30)
    assert "30" in exc_info.value.message


def test_execute_sql_invokes_cost_gate() -> None:
    """execute_sql must run the cost gate before submitting the SQL.

    The whole point of the gate is to refuse / prompt *before* the job
    bills against the project — so a blocked verdict must short-circuit
    execute_sql and odps.run_sql must never be called.
    """
    from maxcompute_semantic.mc_client.errors import CostBlockedError

    # Build a profile with the gate ENABLED (override the disabled
    # default from _make_profile so this test actually exercises the gate).
    profile = Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
        cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
    )
    c = MaxComputeClient(profile)
    c._tier = "3"
    odps_mock = MagicMock()
    c._odps = odps_mock

    # Stub cost_estimate to a blocked verdict — the gate must raise and
    # run_sql must never be invoked.
    with (
        patch.object(
            c,
            "cost_estimate",
            return_value={
                "verdict": "blocked",
                "estimated_cost_cny": 200.0,
                "estimated_input_bytes": 1,
                "thresholds": {"blocked_cny": 100.0},
            },
        ),
        pytest.raises(CostBlockedError),
    ):
        c.execute_sql("SELECT * FROM big_table", assume_yes=True)

    odps_mock.run_sql.assert_not_called()


def test_execute_sql_rejects_write_sql_by_default() -> None:
    c = _make_client()
    c._tier = "3"
    odps_mock = MagicMock()
    c._odps = odps_mock

    with pytest.raises(WriteOpRejectedError):
        c.execute_sql("DROP TABLE t")

    odps_mock.run_sql.assert_not_called()
    odps_mock.execute_sql_cost.assert_not_called()


def test_execute_sql_allow_write_still_runs_cost_gate() -> None:
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    c._tier = "3"
    odps_mock = MagicMock()
    c._odps = odps_mock

    with (
        patch.object(
            c,
            "cost_estimate",
            return_value={
                "verdict": "blocked",
                "estimated_cost_cny": 200.0,
                "estimated_input_bytes": 1,
                "thresholds": {"blocked_cny": 100.0},
            },
        ) as cost_estimate,
        pytest.raises(CostBlockedError),
    ):
        c.execute_sql("DROP TABLE t", allow_write=True, assume_yes=True)

    cost_estimate.assert_called_once()
    odps_mock.run_sql.assert_not_called()


def test_execute_sql_managed_write_can_skip_cost_gate() -> None:
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    c._tier = "3"
    fake_inst = _fake_instance(rows=[])
    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock

    with patch.object(c, "cost_estimate") as cost_estimate:
        result = c.execute_sql(
            "CREATE FUNCTION f AS 'code' USING 'python:F'",
            allow_write=True,
            skip_cost_gate=True,
        )

    assert result.status == "success"
    cost_estimate.assert_not_called()
    odps_mock.run_sql.assert_called_once()


def test_run_sql_async_rejects_write_sql_by_default() -> None:
    c = _make_client()
    c._tier = "3"
    odps_mock = MagicMock()
    c._odps = odps_mock

    with pytest.raises(WriteOpRejectedError):
        c.run_sql_async("DROP TABLE t")

    odps_mock.run_sql.assert_not_called()
    odps_mock.execute_sql_cost.assert_not_called()


# ─── list_schemas ───


def test_list_schemas_returns_strings() -> None:
    c = _make_client()

    odps_mock = MagicMock()
    s1 = MagicMock()
    s1.name = "schema_a"
    s2 = MagicMock()
    s2.name = "schema_b"
    odps_mock.list_schemas.return_value = [s1, s2]
    c._odps = odps_mock

    result = c.list_schemas()
    assert result == ["schema_a", "schema_b"]
    # Default: project= is the profile's compute_project.
    odps_mock.list_schemas.assert_called_once_with(project="test_project")


def test_list_schemas_passes_project_kwarg() -> None:
    c = _make_client()

    odps_mock = MagicMock()
    s1 = MagicMock()
    s1.name = "schema_x"
    odps_mock.list_schemas.return_value = [s1]
    c._odps = odps_mock

    result = c.list_schemas(project="another_project")
    assert result == ["schema_x"]
    odps_mock.list_schemas.assert_called_once_with(project="another_project")


def test_list_schemas_returns_default_for_2_level_not_supported() -> None:
    from odps import errors as odps_errors

    c = _make_client()
    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.NotSupportedError("not supported")
    c._odps = odps_mock

    result = c.list_schemas(project="flat_project")
    assert result == ["default"]


def test_list_schemas_returns_default_for_2_level_internal_server_error() -> None:
    from odps import errors as odps_errors

    c = _make_client()
    odps_mock = MagicMock()
    odps_mock.list_schemas.side_effect = odps_errors.InternalServerError(
        "List schemas failed: Project flat_project is not 3-tier model project."
    )
    c._odps = odps_mock

    result = c.list_schemas(project="flat_project")
    assert result == ["default"]


# ─── list_tables ───


def test_list_tables_returns_names() -> None:
    c = _make_client()
    odps_mock = MagicMock()
    t1 = MagicMock()
    t1.name = "orders"
    t2 = MagicMock()
    t2.name = "customers"
    odps_mock.list_tables.return_value = [t1, t2]
    c._odps = odps_mock

    result = c.list_tables()
    assert result == ["orders", "customers"]
    odps_mock.list_tables.assert_called_once_with(project="test_project", schema=None)


def test_list_tables_with_schema() -> None:
    c = _make_client()
    odps_mock = MagicMock()
    t1 = MagicMock()
    t1.name = "orders"
    odps_mock.list_tables.return_value = [t1]
    c._odps = odps_mock

    result = c.list_tables(schema="sales")
    assert result == ["orders"]
    odps_mock.list_tables.assert_called_once_with(project="test_project", schema="sales")


def test_list_tables_empty() -> None:
    c = _make_client()
    odps_mock = MagicMock()
    odps_mock.list_tables.return_value = []
    c._odps = odps_mock

    result = c.list_tables()
    assert result == []


# ─── describe_table ───


def test_describe_table_returns_metadata() -> None:
    c = _make_client()
    odps_mock = MagicMock()
    table = MagicMock()
    table.name = "orders"
    table.comment = "Order table"
    table.type.value = "MANAGED_TABLE"

    # Build fake columns
    col1 = MagicMock()
    col1.name = "order_id"
    col1.type = "BIGINT"
    col1.comment = "Primary key"
    col2 = MagicMock()
    col2.name = "amount"
    col2.type = "DOUBLE"
    col2.comment = ""
    table.table_schema.columns = [col1, col2]

    # Build fake partition columns
    part1 = MagicMock()
    part1.name = "ds"
    part1.type = "STRING"
    part1.comment = "Business date"
    table.table_schema.partitions = [part1]

    odps_mock.get_table.return_value = table
    c._odps = odps_mock

    result = c.describe_table("orders")
    assert result["table"]["name"] == "orders"
    assert result["table"]["comment"] == "Order table"
    assert result["table"]["type"] == "MANAGED_TABLE"
    assert len(result["table"]["schema"]) == 2
    assert result["table"]["schema"][0] == {
        "name": "order_id",
        "type": "BIGINT",
        "comment": "Primary key",
    }
    assert result["table"]["schema"][1] == {"name": "amount", "type": "DOUBLE", "comment": ""}
    assert len(result["table"]["partition_columns"]) == 1
    assert result["table"]["partition_columns"][0] == {
        "name": "ds",
        "type": "STRING",
        "comment": "Business date",
    }
    assert result["table"]["description"] == "Order table"
    assert result["table"]["primary_key"] == ""
    odps_mock.get_table.assert_called_once_with("orders", project="test_project", schema=None)


def test_describe_table_with_schema() -> None:
    c = _make_client()
    odps_mock = MagicMock()
    table = MagicMock()
    table.name = "orders"
    table.comment = ""
    table.type.value = "MANAGED_TABLE"
    table.table_schema.columns = []
    table.table_schema.partitions = []
    odps_mock.get_table.return_value = table
    c._odps = odps_mock

    result = c.describe_table("orders", schema="sales")
    assert result["table"]["name"] == "orders"
    odps_mock.get_table.assert_called_once_with("orders", project="test_project", schema="sales")


def test_describe_table_type_no_value_attr() -> None:
    """When table.type is a plain string (not an enum), str() is used."""
    c = _make_client()
    odps_mock = MagicMock()
    table = MagicMock()
    table.name = "t"
    table.comment = ""
    # Simulate type without .value attribute
    type_mock = MagicMock(spec=[])
    table.type = type_mock
    table.table_schema.columns = []
    table.table_schema.partitions = []
    odps_mock.get_table.return_value = table
    c._odps = odps_mock

    result = c.describe_table("t")
    assert result["table"]["type"] == str(type_mock)


# ─── cost_estimate ───


def test_cost_estimate_ok_verdict() -> None:
    """Small input → verdict 'ok'."""
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = 1073741824  # 1 GB → 0.3 CNY → ok
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock
    c._tier = None

    result = c.cost_estimate("SELECT * FROM small_table")
    assert result["verdict"] == "ok"
    assert result["estimated_input_bytes"] == 1073741824
    assert result["estimated_cost_cny"] == 0.3
    assert result["thresholds"]["confirm_cny"] == 10.0
    assert result["thresholds"]["blocked_cny"] == 100.0


def test_cost_estimate_confirm_verdict() -> None:
    """Input bytes at 40 GB → 12 CNY → verdict 'confirm'."""
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = 40 * 1073741824  # 40 GB → 12 CNY → confirm
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock
    c._tier = "3"

    result = c.cost_estimate("SELECT * FROM medium_table")
    assert result["verdict"] == "confirm"
    assert result["estimated_cost_cny"] == 12.0


def test_cost_estimate_blocked_verdict() -> None:
    """Input bytes at 400 GB → 120 CNY → verdict 'blocked'."""
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = 400 * 1073741824  # 400 GB → 120 CNY → blocked
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock
    c._tier = None

    result = c.cost_estimate("SELECT * FROM huge_table")
    assert result["verdict"] == "blocked"


def test_cost_estimate_null_input_size() -> None:
    """ODPS returns None for input_size → treat as 0 → ok."""
    c = _make_client(cost_thresholds=_ENABLED_THRESHOLDS)
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = None
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock

    result = c.cost_estimate("SELECT 1")
    assert result["estimated_input_bytes"] == 0
    assert result["estimated_cost_cny"] == 0.0
    assert result["verdict"] == "ok"


def test_cost_estimate_passes_tier_hints() -> None:
    """3-level tier + schema → hints include namespace + default.schema."""
    c = _make_client()
    c._tier = "3"
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = 0
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock

    c.cost_estimate("SELECT 1", schema="default")
    call_kwargs = odps_mock.execute_sql_cost.call_args
    hints = call_kwargs[1]["hints"]
    assert hints["odps.namespace.schema"] == "true"
    assert hints["odps.default.schema"] == "default"


def test_cost_estimate_custom_thresholds() -> None:
    """Profile with custom cost thresholds."""
    p = Profile(
        name="custom",
        compute_project="proj",
        endpoint="https://ep",
        auth=AkAuth(access_key_id="id", access_key_secret="sec"),
        cost_thresholds=CostThresholds(confirm_cny=5.0, blocked_cny=50.0),
        sources=(DataSource(project="proj", schema="default", tables="*"),),
    )
    c = MaxComputeClient(p)
    odps_mock = MagicMock()
    cost_result = MagicMock()
    cost_result.input_size = 20 * 1073741824  # 20 GB → 6 CNY → confirm (>=5)
    odps_mock.execute_sql_cost.return_value = cost_result
    c._odps = odps_mock

    result = c.cost_estimate("SELECT * FROM t")
    assert result["verdict"] == "confirm"
    assert result["thresholds"]["confirm_cny"] == 5.0
    assert result["thresholds"]["blocked_cny"] == 50.0


def test_cost_estimate_pyodps_error_classified() -> None:
    """ODPS errors are mapped through map_pyodps_exception.

    Pyodps's ``parse_response`` sets ``.code`` from the XML envelope
    when the server responds; constructing ``NoSuchObject(msg)``
    directly leaves ``.code`` unset, so for this test we set it
    explicitly to mirror the real wire-level shape. (The classifier
    is structured-code-driven since 0.5.0a42.)
    """
    c = _make_client()
    from odps import errors as odps_errors

    real_exc = odps_errors.NoSuchObject("Table not found - 'x'")
    real_exc.code = "NoSuchObject"
    odps_mock = MagicMock()
    odps_mock.execute_sql_cost.side_effect = real_exc
    c._odps = odps_mock

    with pytest.raises(TableNotFoundError):
        c.cost_estimate("SELECT * FROM x")


# ─── Remaining NotImpl placeholders ───


def test_explain_is_implemented() -> None:
    c = _make_client()
    c._ensure_odps = MagicMock()
    c._tier = "2"
    odps = c._ensure_odps.return_value
    mock_instance = MagicMock()
    mock_instance.get_task_results.return_value = {"sql": "PLAN TEXT"}
    mock_instance.get_logview_address.return_value = "http://logview/1"
    odps.run_sql.return_value = mock_instance
    result = c.explain("SELECT 1")
    assert result["plan"] == "PLAN TEXT"
    assert result["logview_url"] == "http://logview/1"


def test_list_functions_returns_empty_catalog() -> None:
    c = _make_client()
    c._ensure_odps = MagicMock()
    c._ensure_odps.return_value.list_functions.return_value = []

    result = c.list_functions()

    c._ensure_odps.return_value.list_functions.assert_called_once_with(project="test_project")
    assert result == []


# ─── InstanceNotFoundError ───


def test_instance_not_found_error_classified() -> None:
    """InstanceNotFoundError has code=InstanceNotFound and exit_code=5."""
    from maxcompute_semantic.mc_client.errors import InstanceNotFoundError

    err = InstanceNotFoundError("instance not found", remediation="check ID")
    assert err.code == "InstanceNotFound"
    assert err.exit_code == 5


# ─── REST result-reader fallback (instance tunnel unavailable) ───


def _fake_instance_with_reader(reader: object) -> MagicMock:
    instance = MagicMock()
    instance.open_reader.return_value = reader
    instance.get_logview_address.return_value = "http://logview"
    return instance


def test_execute_sql_reads_schemaless_rest_fallback_reader() -> None:
    """pyodps falls back to CsvRecordReader (no public ``.schema``) when the
    instance tunnel is unavailable; rows must still parse instead of
    crashing with AttributeError (regression for the reported
    ``'CsvRecordReader' object has no attribute 'schema'``)."""
    from odps.readers import CsvRecordReader

    c = _make_client()
    c._tier = "3"
    instance = _fake_instance_with_reader(CsvRecordReader(None, "id,name\n1,x\n2,y\n"))

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = instance
    c._odps = odps_mock
    c._creds_expiration = None

    result = c.execute_sql("SELECT * FROM t")
    assert result.status == "success"
    assert result.data["rows"] == [{"id": "1", "name": "x"}, {"id": "2", "name": "y"}]
    assert result.data["schema"] == [
        {"name": "id", "type": "STRING"},
        {"name": "name", "type": "STRING"},
    ]
    assert result.data["fetch_path"] == "rest_fallback"


def test_execute_sql_reads_rest_fallback_reader_with_descriptor_schema() -> None:
    """When the service provides a result descriptor, the fallback reader
    carries a typed schema on ``_schema`` and values keep their types."""
    from odps import types as odps_types
    from odps.readers import CsvRecordReader

    c = _make_client()
    c._tier = "3"
    schema = odps_types.OdpsSchema(columns=[odps_types.Column(name="id", typo="bigint")])
    instance = _fake_instance_with_reader(CsvRecordReader(schema, "id\n7\n"))

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = instance
    c._odps = odps_mock
    c._creds_expiration = None

    result = c.execute_sql("SELECT 7 AS id")
    assert result.status == "success"
    assert result.data["rows"] == [{"id": 7}]
    assert result.data["schema"] == [{"name": "id", "type": "BIGINT"}]
    assert result.data["fetch_path"] == "rest_fallback"


def test_execute_sql_rest_fallback_reader_empty_result_body() -> None:
    """An empty REST result body must yield an empty result, not raise."""
    from odps.readers import CsvRecordReader

    c = _make_client()
    c._tier = "3"
    instance = _fake_instance_with_reader(CsvRecordReader(None, ""))

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = instance
    c._odps = odps_mock
    c._creds_expiration = None

    result = c.execute_sql("SELECT * FROM t WHERE 1 = 0")
    assert result.status == "success"
    assert result.data["rows"] == []
    assert result.data["schema"] == []
    assert result.data["fetch_path"] == "rest_fallback"


def test_execute_sql_tunnel_reader_reports_instance_tunnel_fetch_path() -> None:
    c = _make_client()
    c._tier = "3"
    fake_inst = _fake_instance()

    odps_mock = MagicMock()
    odps_mock.run_sql.return_value = fake_inst
    c._odps = odps_mock
    c._creds_expiration = None

    result = c.execute_sql("SELECT * FROM t")
    assert result.data["fetch_path"] == "instance_tunnel"
