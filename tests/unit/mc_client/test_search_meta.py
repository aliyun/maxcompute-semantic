# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for MaxComputeClient search_tables, search_columns, list_partitions,
freshness_info, and catalog_search_tables integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, PropertyMock, patch

from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.mc_client.client import MaxComputeClient


def _make_profile() -> Profile:
    return Profile(
        name="test",
        compute_project="test_project",
        endpoint="https://odps_endpoint",
        auth=AkAuth(access_key_id="ak_id", access_key_secret="ak_secret"),
        sources=(DataSource(project="test_project", schema="default", tables="*"),),
    )


def _make_client_with_mock_odps() -> tuple[MaxComputeClient, MagicMock]:
    """Return a MaxComputeClient with a mocked ODPS instance."""
    profile = _make_profile()
    client = MaxComputeClient(profile)
    odps_mock = MagicMock()
    client._odps = odps_mock
    client._creds_expiration = datetime.now(timezone.utc) + timedelta(hours=12)
    return client, odps_mock


# ─── search_tables ───


def test_search_tables_catalog_available() -> None:
    """When Catalog API returns results, those are used directly."""
    client, _odps_mock = _make_client_with_mock_odps()

    # Mock catalog_search_tables returning results.
    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=[
            {"name": "orders", "schema": "default", "comment": "Order data", "owner": ""},
            {"name": "users", "schema": "default", "comment": "User profiles", "owner": ""},
        ],
    ):
        results = client.search_tables("order")
        assert len(results) == 2
        assert results[0]["table_name"] == "orders"
        assert results[0]["score"] == 5


def test_search_tables_catalog_unavailable_fallback() -> None:
    """When Catalog API returns None, fallback to client-side iteration."""
    client, odps_mock = _make_client_with_mock_odps()

    # Mock catalog_search_tables returning None (unavailable).
    # Mock ODPS list_tables for client-side fallback.
    table1 = MagicMock()
    table1.name = "orders"
    table1.comment = "Order data table"
    table2 = MagicMock()
    table2.name = "users"
    table2.comment = "User profiles"

    odps_mock.list_tables.return_value = [table1, table2]

    # Need to mock table.table_schema.columns for the column-search fallback.
    col1 = MagicMock()
    col1.name = "order_id"
    col1.comment = "Order identifier"
    col2 = MagicMock()
    col2.name = "user_id"
    col2.comment = ""
    table1.table_schema.columns = [col1]
    table2.table_schema.columns = [col2]

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ):
        results = client.search_tables("order")
        # "order" matches "orders" table name + "Order data table" comment.
        assert len(results) >= 1
        found_orders = any(r["table_name"] == "orders" for r in results)
        assert found_orders


def test_search_tables_client_side_multi_token() -> None:
    """Multi-word keyword splits into tokens; each token scores independently."""
    client, odps_mock = _make_client_with_mock_odps()

    table = MagicMock()
    table.name = "order_detail"
    table.comment = "Detailed order information"

    odps_mock.list_tables.return_value = [table]

    with patch(
        "maxcompute_semantic.mc_client.catalog.catalog_search_tables",
        return_value=None,
    ):
        results = client.search_tables("order detail")
        # Both "order" and "detail" should match.
        assert len(results) == 1
        assert results[0]["table_name"] == "order_detail"
        assert results[0]["score"] > 5  # at least 5 per token * 2 tokens


# ─── search_columns ───


def test_search_columns_basic() -> None:
    """Search columns returns column matches with scoring."""
    client, odps_mock = _make_client_with_mock_odps()

    table = MagicMock()
    table.name = "orders"
    col = MagicMock()
    col.name = "order_id"
    col.type = "BIGINT"
    col.comment = "Primary key"
    table.table_schema.columns = [col]

    odps_mock.list_tables.return_value = [table]

    results = client.search_columns("order")
    assert len(results) == 1
    assert results[0]["column_name"] == "order_id"
    assert results[0]["table_name"] == "orders"
    assert results[0]["type"] == "BIGINT"
    assert results[0]["score"] > 0


def test_search_columns_no_match() -> None:
    """Search columns with no matching keyword returns empty list."""
    client, odps_mock = _make_client_with_mock_odps()

    table = MagicMock()
    table.name = "orders"
    col = MagicMock()
    col.name = "order_id"
    col.type = "BIGINT"
    col.comment = ""
    table.table_schema.columns = [col]

    odps_mock.list_tables.return_value = [table]

    results = client.search_columns("xyz_no_match")
    assert len(results) == 0


# ─── list_partitions ───


def test_list_partitions_non_partitioned() -> None:
    """Non-partitioned table returns is_partitioned=False."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    table_mock.table_schema.partitions = []
    odps_mock.get_table.return_value = table_mock

    result = client.list_partitions("my_table")
    assert result["is_partitioned"] is False
    assert result["partitions"] == []
    assert result["table_name"] == "my_table"


def test_list_partitions_partitioned_with_partitions() -> None:
    """Partitioned table returns partitions and latest_partition."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]  # has partition columns

    # Mock iterate_partitions.
    p1 = MagicMock()
    p1.partition_spec = "ds=20240101"
    p2 = MagicMock()
    p2.partition_spec = "ds=20240102"
    table_mock.iterate_partitions.return_value = iter([p1, p2])

    # Mock get_max_partition.
    max_part = MagicMock()
    max_part.partition_spec = "ds=20240102"
    table_mock.get_max_partition = MagicMock(return_value=max_part)

    odps_mock.get_table.return_value = table_mock

    result = client.list_partitions("my_table", limit=100)
    assert result["is_partitioned"] is True
    assert result["visible_count"] == 2
    assert result["has_more"] is False
    assert result["latest_partition"] == "ds=20240102"


def test_list_partitions_has_more() -> None:
    """When partition count exceeds limit, has_more=True."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]

    partitions = [MagicMock(partition_spec=f"ds={i}") for i in range(105)]
    table_mock.iterate_partitions.return_value = iter(partitions)
    table_mock.get_max_partition = MagicMock(side_effect=Exception("no max"))

    odps_mock.get_table.return_value = table_mock

    result = client.list_partitions("my_table", limit=100)
    assert result["has_more"] is True
    assert result["visible_count"] == 100


# ─── freshness_info ───


def test_freshness_info_non_partitioned() -> None:
    """Non-partitioned table returns is_partitioned=False with last_modified."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    table_mock.table_schema.partitions = []
    table_mock.last_data_modified_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    odps_mock.get_table.return_value = table_mock

    result = client.freshness_info("my_table")
    assert result["is_partitioned"] is False
    assert result["latest_partition"] is None
    assert result["last_modified_time"] is not None


def test_freshness_info_partitioned_with_max_partition() -> None:
    """Partitioned table uses get_max_partition for freshness."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]

    max_part = MagicMock()
    max_part.partition_spec = "ds=20240115"
    table_mock.get_max_partition = MagicMock(return_value=max_part)

    recent_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    table_mock.last_data_modified_time = recent_time

    odps_mock.get_table.return_value = table_mock

    result = client.freshness_info("my_table")
    assert result["is_partitioned"] is True
    assert result["latest_partition"] == "ds=20240115"
    assert "within the last hour" in result["freshness_summary"]


def test_freshness_info_partitioned_stale() -> None:
    """Old partition triggers stale_warning."""
    client, odps_mock = _make_client_with_mock_odps()

    table_mock = MagicMock()
    part_col = MagicMock()
    table_mock.table_schema.partitions = [part_col]

    max_part = MagicMock()
    max_part.partition_spec = "ds=20230101"
    table_mock.get_max_partition = MagicMock(return_value=max_part)

    # 30 days old.
    stale_time = datetime.now(timezone.utc) - timedelta(days=30)
    table_mock.last_data_modified_time = stale_time

    odps_mock.get_table.return_value = table_mock

    result = client.freshness_info("my_table")
    assert result["is_partitioned"] is True
    assert result["stale_warning"] is not None
    assert "30 days" in result["stale_warning"]


# ─── catalog_search_tables direct ───


def test_catalog_search_tables_returns_none_when_no_rest() -> None:
    """catalog_search_tables returns None when ODPS has no catalog_rest.

    The source contract (catalog.py commit 62416a6) narrowly catches
    ``AttributeError`` — the only legitimately-swallowed case (older
    pyodps builds without the attribute). Any other exception is the
    caller's signal to fall through to the client-side iteration path,
    so this test simulates the attribute-missing branch specifically.
    """
    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock(spec=["catalog_rest"])
    type(odps_mock).catalog_rest = PropertyMock(side_effect=AttributeError("no catalog"))

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is None


def test_catalog_search_tables_returns_none_when_no_tenant() -> None:
    """catalog_search_tables returns None when tenant_id is unavailable."""
    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    odps_mock.catalog_rest = catalog_rest_mock

    # get_project returns project with no tenant_id.
    project_mock = MagicMock()
    project_mock.tenant_id = None
    odps_mock.get_project.return_value = project_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is None


def test_catalog_search_tables_returns_none_empty_endpoint() -> None:
    """catalog_search_tables returns None when catalog_rest has empty endpoint."""
    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = ""
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is None


def test_catalog_search_tables_success_returns_matches() -> None:
    """catalog_search_tables returns parsed entries on success."""
    import json

    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    resp_mock = MagicMock()
    resp_mock.text = json.dumps(
        {
            "entries": [
                {
                    "displayName": "orders",
                    "name": "projects/test_project/schemas/default/tables/orders",
                    "description": "Order data",
                },
                {
                    "displayName": "users",
                    "name": "projects/test_project/schemas/default/tables/users",
                    "description": "User profiles",
                },
            ],
        }
    )
    catalog_rest_mock.request.return_value = resp_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is not None
    assert len(result) == 2
    assert result[0]["name"] == "orders"
    assert result[0]["schema"] == "default"
    assert result[0]["comment"] == "Order data"


def test_catalog_search_tables_schema_filter() -> None:
    """catalog_search_tables filters entries by schema when provided."""
    import json

    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    resp_mock = MagicMock()
    resp_mock.text = json.dumps(
        {
            "entries": [
                {
                    "displayName": "orders",
                    "name": "projects/test_project/schemas/default/tables/orders",
                    "description": "",
                },
                {
                    "displayName": "orders",
                    "name": "projects/test_project/schemas/sales/tables/orders",
                    "description": "",
                },
            ],
        }
    )
    catalog_rest_mock.request.return_value = resp_mock

    result = catalog_search_tables(odps_mock, "test_project", "order", schema="default")
    assert result is not None
    assert len(result) == 1
    assert result[0]["schema"] == "default"


def test_catalog_search_tables_skips_none_entries() -> None:
    """catalog_search_tables skips None entries in response."""
    import json

    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    resp_mock = MagicMock()
    resp_mock.text = json.dumps(
        {
            "entries": [None, {"displayName": "orders", "name": "", "description": ""}],
        }
    )
    catalog_rest_mock.request.return_value = resp_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is not None
    assert len(result) == 1


def test_catalog_search_tables_exception_returns_none() -> None:
    """catalog_search_tables returns None on request exception."""
    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    catalog_rest_mock.request.side_effect = Exception("network error")

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is None


def test_catalog_search_tables_no_entries_returns_empty() -> None:
    """catalog_search_tables returns empty list when entries is empty."""
    import json

    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    resp_mock = MagicMock()
    resp_mock.text = json.dumps({"entries": []})
    catalog_rest_mock.request.return_value = resp_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is not None
    assert len(result) == 0


def test_resolve_tenant_id_success() -> None:
    """_resolve_tenant_id returns tenant_id from project."""
    from maxcompute_semantic.mc_client.catalog import _resolve_tenant_id

    odps_mock = MagicMock()
    project_mock = MagicMock()
    project_mock.tenant_id = "tenant456"
    odps_mock.get_project.return_value = project_mock

    result = _resolve_tenant_id(odps_mock, "test_project")
    assert result == "tenant456"


def test_resolve_tenant_id_no_tenant() -> None:
    """_resolve_tenant_id returns None when project has no tenant_id."""
    from maxcompute_semantic.mc_client.catalog import _resolve_tenant_id

    odps_mock = MagicMock()
    project_mock = MagicMock()
    project_mock.tenant_id = None
    odps_mock.get_project.return_value = project_mock

    result = _resolve_tenant_id(odps_mock, "test_project")
    assert result is None


def test_resolve_tenant_id_exception() -> None:
    """_resolve_tenant_id returns None on exception."""
    from maxcompute_semantic.mc_client.catalog import _resolve_tenant_id

    odps_mock = MagicMock()
    odps_mock.get_project.side_effect = Exception("project error")

    result = _resolve_tenant_id(odps_mock, "test_project")
    assert result is None


def test_catalog_search_tables_resp_content_fallback() -> None:
    """catalog_search_tables handles resp.content when resp.text is missing."""
    import json

    from maxcompute_semantic.mc_client.catalog import catalog_search_tables

    odps_mock = MagicMock()
    catalog_rest_mock = MagicMock()
    catalog_rest_mock.endpoint = "https://catalog.odps.aliyun.com"
    odps_mock.catalog_rest = catalog_rest_mock

    project_mock = MagicMock()
    project_mock.tenant_id = "tenant123"
    odps_mock.get_project.return_value = project_mock

    resp_mock = MagicMock(spec=["content"])
    resp_mock.content = json.dumps(
        {
            "entries": [
                {
                    "displayName": "orders",
                    "name": "projects/p/schemas/s/tables/orders",
                    "description": "",
                },
            ],
        }
    ).encode("utf-8")
    catalog_rest_mock.request.return_value = resp_mock

    result = catalog_search_tables(odps_mock, "test_project", "order")
    assert result is not None
    assert len(result) == 1
