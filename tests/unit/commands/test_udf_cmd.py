# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/udf.py -- udf list/show/search/create/test/remove
+ resource list/show/remove.

Mocks ProfileContext, MaxComputeClient, PackageDB, and pyodps ODPS so no
live MaxCompute needed. Verifies:
  - udf list reads from PackageDB
  - udf show looks up in PackageDB + enriches from pyodps
  - udf search filters by substring match
  - udf create --inline-python executes SQL + updates PackageDB
  - udf test constructs SELECT + executes via client
  - udf remove drops function + deletes from PackageDB
  - udf resource list/show/remove use pyodps ODPS
  - Profile resolution errors
  - JSON output mode
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.commands.udf import udf_group
from maxcompute_semantic.mc_client.envelope import Envelope


def _make_udf(
    name: str = "my_udf",
    kind: str = "python",
    signature: str = "",
    class_name: str = "",
    description: str = "",
    created_locally: int = 0,
    last_seen_at: str = "2026-01-01",
    id: int = 1,
) -> dict:
    """Build a single udf dict matching PackageDB row shape."""
    return {
        "id": id,
        "name": name,
        "kind": kind,
        "signature": signature,
        "class_name": class_name,
        "description": description,
        "created_locally": created_locally,
        "last_seen_at": last_seen_at,
    }


def _invoke(args: list[str], obj: dict | None = None) -> object:
    runner = CliRunner()
    return runner.invoke(udf_group, args, obj=obj or {"format": "json"})


def _mock_profile(name: str = "my_proj", project: str = "my_proj"):
    """Create a mock Profile."""
    from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, DataSource, Profile

    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="test_ak", access_key_secret="test_secret"),
        cost_thresholds=CostThresholds(),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def _mock_client(profile=None):
    """Create a mock MaxComputeClient."""
    if profile is None:
        profile = _mock_profile()
    client = MagicMock()
    client.profile = profile
    client._tier = None
    return client


def _mock_db(udfs=None):
    """Create a mock PackageDB with optional udf entries."""
    db = MagicMock()
    if udfs is None:
        udfs = []
    db.list_udfs.return_value = udfs
    db._conn = MagicMock()
    db._conn.execute = MagicMock()
    db._conn.commit = MagicMock()
    return db


@contextmanager
def _patch_udf_context(mock_profile, mock_client, mock_db):
    """Patch ProfileContext.resolve, open_db, and MaxComputeClient for UDF tests."""
    import maxcompute_semantic.commands._profile_command as pc_mod

    def mock_resolve(
        *,
        profile_name=None,
        project=None,
        schema=None,
        renderer=None,
    ):
        return ProfileContext(
            profile=mock_profile,
            project_override=project,
            schema_override=schema,
            renderer=renderer or Renderer(),
        )

    def mock_reject_if_fork(self):
        pass

    def mock_open_db(self):
        return mock_db

    def mock_commit(prof, *, action, summary):
        pass

    with (
        patch.object(ProfileContext, "resolve", classmethod(lambda cls, **kw: mock_resolve(**kw))),
        patch.object(ProfileContext, "reject_if_fork", mock_reject_if_fork),
        patch.object(ProfileContext, "open_db", mock_open_db),
        patch("maxcompute_semantic.commands.udf.MaxComputeClient", return_value=mock_client),
        patch.object(pc_mod, "commit_after_command", mock_commit),
    ):
        yield


# -- udf list ---------------------------------------------------------------


class TestUdfList:
    """Tests for mcs udf list."""

    def test_list_returns_udfs_from_db(self, isolated_config: Path) -> None:
        """udf list reads from PackageDB and returns entries."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(
            udfs=[
                _make_udf(
                    name="my_udf",
                    signature="(string)->string",
                    class_name="MyUDF",
                ),
                _make_udf(
                    name="calc_udf",
                    id=2,
                    signature="(bigint)->bigint",
                    class_name="CalcUDF",
                    description="calculate",
                    last_seen_at="2026-01-02",
                ),
            ]
        )

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["list"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"
        assert output["data"]["count"] == 2
        assert output["data"]["udfs"][0]["name"] == "my_udf"

    def test_list_empty_returns_empty_array(self, isolated_config: Path) -> None:
        """udf list with no UDFs returns empty."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(udfs=[])

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["list"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 0
        assert output["data"]["udfs"] == []

    def test_list_no_profile_error(self, isolated_config: Path) -> None:
        """udf list exits with error when no profile configured."""
        from maxcompute_semantic.auth.errors import NoProfilesConfiguredError

        with patch.object(
            ProfileContext,
            "resolve",
            side_effect=NoProfilesConfiguredError("no profiles"),
        ):
            result = _invoke(["list"])

        assert result.exit_code == 3

    def test_list_json_output_mode(self, isolated_config: Path) -> None:
        """JSON mode returns structured envelope."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(udfs=[_make_udf(name="test_udf")])

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["list"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        # Envelope format: {status: success, data: {...}}
        assert "status" in output
        assert "data" in output


# -- udf show ---------------------------------------------------------------


class TestUdfShow:
    """Tests for mcs udf show."""

    def test_show_returns_udf_details(self, isolated_config: Path) -> None:
        """udf show returns details from PackageDB."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(
            udfs=[
                _make_udf(
                    name="my_udf",
                    signature="(string)->string",
                    class_name="MyUDF",
                    description="my udf",
                ),
            ]
        )
        # Mock pyodps enrichment -- _ensure_odps returns a mock.
        mock_odps = MagicMock()
        mock_func = MagicMock()
        mock_func.owner = "alice"
        mock_func.creation_time = "2026-01-01T00:00:00"
        mock_odps.get_function.return_value = mock_func
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["show", "my_udf"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["udf"]["name"] == "my_udf"
        assert output["data"]["udf"]["owner"] == "alice"

    def test_show_not_found_locally(self, isolated_config: Path) -> None:
        """udf show for nonexistent name returns error."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(udfs=[])

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["show", "nonexistent"])

        assert result.exit_code == 5
        output = json.loads(result.output)
        assert output["status"] == "error"


# -- udf search -------------------------------------------------------------


class TestUdfSearch:
    """Tests for mcs udf search."""

    def test_search_matches_by_name(self, isolated_config: Path) -> None:
        """udf search filters UDFs by name substring."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(
            udfs=[
                _make_udf(name="my_calc_udf"),
                _make_udf(
                    name="format_udf",
                    id=2,
                    description="format strings",
                    last_seen_at="2026-01-02",
                ),
            ]
        )

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["search", "calc"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 1
        assert output["data"]["results"][0]["name"] == "my_calc_udf"

    def test_search_matches_by_description(self, isolated_config: Path) -> None:
        """udf search filters UDFs by description substring."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(
            udfs=[
                _make_udf(
                    name="format_udf",
                    description="format strings for display",
                ),
            ]
        )

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["search", "display"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 1

    def test_search_no_matches(self, isolated_config: Path) -> None:
        """udf search with no matching keyword returns empty."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(udfs=[_make_udf(name="my_udf")])

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["search", "xyz"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 0
        assert output["data"]["results"] == []


# -- udf create -------------------------------------------------------------


class TestUdfCreate:
    """Tests for mcs udf create --inline-python."""

    def test_create_inline_python_success(self, isolated_config: Path, tmp_path: Path) -> None:
        """create --inline-python reads script, executes SQL, updates PackageDB."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        # Create a temp script file.
        script_file = tmp_path / "my_udf.py"
        script_file.write_text(
            "class MyUDF(BaseUDF):\n    def evaluate(self, x):\n        return x\n"
        )

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["create", "my_udf", "--inline-python", str(script_file)])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["name"] == "my_udf"
        assert output["data"]["class_name"] == "MyUDF"
        assert output["data"]["status"] == "created"

        # Verify execute_sql was called with CREATE FUNCTION SQL.
        sql_arg = mock_client.execute_sql.call_args[0][0]
        assert sql_arg.startswith("CREATE FUNCTION my_udf AS '")
        assert "USING 'python:MyUDF'" in sql_arg
        assert mock_client.execute_sql.call_args.kwargs == {
            "assume_yes": True,
            "allow_write": True,
            "skip_cost_gate": True,
        }

        # Verify PackageDB was updated.
        mock_db.upsert_udf.assert_called_once()

    def test_create_inline_python_uses_sql_standard_string_quote(
        self, isolated_config: Path, tmp_path: Path
    ) -> None:
        """Embedded apostrophes in inline code must be doubled for SQL."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()
        mock_client.execute_sql.return_value = Envelope.success(
            {"rows": [], "schema": [], "row_count": 0}
        )

        script_file = tmp_path / "quote_udf.py"
        script_file.write_text(
            "class QuoteUDF(BaseUDF):\n    def evaluate(self):\n        return 'O\\'Reilly'\n",
            encoding="utf-8",
        )

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["create", "quote_udf", "--inline-python", str(script_file)])

        assert result.exit_code == 0, result.output
        sql_arg = mock_client.execute_sql.call_args[0][0]
        assert "O\\''Reilly" in sql_arg
        assert "\\'Reilly" not in sql_arg

    def test_create_failure_sql_error(self, isolated_config: Path, tmp_path: Path) -> None:
        """create with SQL execution failure returns error."""
        from maxcompute_semantic.mc_client.errors import SyntaxErrorMcs

        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_client.execute_sql.side_effect = SyntaxErrorMcs(
            "SQL parse error", remediation="check syntax"
        )

        script_file = tmp_path / "bad_udf.py"
        script_file.write_text("class BadUDF:\n    pass\n")

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["create", "bad_udf", "--inline-python", str(script_file)])

        assert result.exit_code != 0
        output = json.loads(result.output)
        assert output["status"] == "error"

    def test_create_requires_inline_python(self, isolated_config: Path) -> None:
        """create without --inline-python exits with error."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["create", "some_udf"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert "--inline-python" in output["error"]["message"]


# -- udf test ---------------------------------------------------------------


class TestUdfTest:
    """Tests for mcs udf test."""

    def test_test_constructs_select_and_executes(self, isolated_config: Path) -> None:
        """udf test constructs SELECT <name>(args) and executes via client."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_envelope = Envelope.success(
            {
                "rows": [{"_c0": 42}],
                "schema": [{"name": "_c0", "type": "BIGINT"}],
                "row_count": 1,
            }
        )
        mock_client.execute_sql.return_value = mock_envelope

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["test", "my_udf", "--args", "1"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["sql"] == "SELECT my_udf(1)"

        # Verify execute_sql called with correct SQL.
        sql_arg = mock_client.execute_sql.call_args[0][0]
        assert sql_arg == "SELECT my_udf(1)"

    def test_test_with_string_args(self, isolated_config: Path) -> None:
        """udf test converts double-quoted string args to single-quoted SQL."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["test", "my_udf", "--args", '1, "abc"'])

        assert result.exit_code == 0
        sql_arg = mock_client.execute_sql.call_args[0][0]
        assert sql_arg == "SELECT my_udf(1, 'abc')"

    def test_test_with_mixed_args(self, isolated_config: Path) -> None:
        """udf test handles numeric + string + NULL args."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_envelope = Envelope.success({"rows": [], "schema": [], "row_count": 0})
        mock_client.execute_sql.return_value = mock_envelope

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["test", "my_udf", "--args", '42, "hello", NULL'])

        assert result.exit_code == 0
        sql_arg = mock_client.execute_sql.call_args[0][0]
        assert sql_arg == "SELECT my_udf(42, 'hello', NULL)"

    def test_test_rejects_raw_identifier_args(self, isolated_config: Path) -> None:
        """udf test accepts literals only; bare identifiers are not passed through."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["test", "my_udf", "--args", "foo"])

        assert result.exit_code != 0
        assert "unsupported UDF test argument" in result.output
        mock_client.execute_sql.assert_not_called()


# -- udf remove -------------------------------------------------------------


class TestUdfRemove:
    """Tests for mcs udf remove."""

    def test_remove_drops_function_and_deletes_from_db(self, isolated_config: Path) -> None:
        """remove drops function via pyodps and deletes from PackageDB."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_odps = MagicMock()
        mock_func = MagicMock()
        mock_func.resources = []
        mock_odps.get_function.return_value = mock_func
        mock_odps.drop_function = MagicMock()
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["remove", "my_udf"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["name"] == "my_udf"
        assert output["data"]["status"] == "removed"

        mock_odps.drop_function.assert_called_once_with("my_udf", project="my_proj")

    def test_remove_rejects_invalid_identifier(self, isolated_config: Path) -> None:
        """remove validates the UDF name before touching MaxCompute."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["remove", "bad;DROP_TABLE"])

        assert result.exit_code != 0
        assert "invalid SQL identifier" in result.output
        mock_client._ensure_odps.assert_not_called()

    def test_remove_with_delete_resources(self, isolated_config: Path) -> None:
        """remove --delete-resources drops function + associated resources."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_odps = MagicMock()
        mock_func = MagicMock()
        mock_resource = MagicMock()
        mock_resource.name = "my_udf.py"
        mock_func.resources = [mock_resource]
        mock_odps.get_function.return_value = mock_func
        mock_odps.drop_function = MagicMock()
        mock_odps.drop_resource = MagicMock()
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["remove", "my_udf", "--delete-resources"])

        assert result.exit_code == 0
        mock_odps.drop_function.assert_called_once()
        mock_odps.drop_resource.assert_called_once_with("my_udf.py", project="my_proj")

    def test_remove_nonexistent_locally_still_succeeds(self, isolated_config: Path) -> None:
        """remove for a UDF not in local PackageDB still succeeds."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db(udfs=[])

        mock_odps = MagicMock()
        mock_func = MagicMock()
        mock_func.resources = []
        mock_odps.get_function.return_value = mock_func
        mock_odps.drop_function = MagicMock()
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["remove", "cloud_only_udf"])

        assert result.exit_code == 0
        # PackageDB DELETE is still called (idempotent).
        mock_db._conn.execute.assert_called()


# -- udf resource list ------------------------------------------------------


class TestUdfResourceList:
    """Tests for mcs udf resource list."""

    def test_resource_list_returns_resources(self, isolated_config: Path) -> None:
        """resource list returns resources from pyodps."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_odps = MagicMock()
        mock_r1 = MagicMock()
        mock_r1.name = "script.py"
        mock_r1.type = "py"
        mock_r1.size = 1024
        mock_r1.owner = "alice"
        mock_r2 = MagicMock()
        mock_r2.name = "lib.jar"
        mock_r2.type = "jar"
        mock_r2.size = 2048
        mock_r2.owner = "bob"
        mock_odps.list_resources.return_value = [mock_r1, mock_r2]
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["resource", "list"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["count"] == 2
        assert output["data"]["resources"][0]["name"] == "script.py"


# -- udf resource show ------------------------------------------------------


class TestUdfResourceShow:
    """Tests for mcs udf resource show."""

    def test_resource_show_returns_detail(self, isolated_config: Path) -> None:
        """resource show returns detailed resource info."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_odps = MagicMock()
        mock_r = MagicMock()
        mock_r.name = "script.py"
        mock_r.type = "py"
        mock_r.size = 1024
        mock_r.owner = "alice"
        mock_r.comment = "a python script"
        mock_r.creation_time = "2026-01-01"
        mock_r.last_modified_time = "2026-01-02"
        mock_odps.get_resource.return_value = mock_r
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["resource", "show", "script.py"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["resource"]["name"] == "script.py"
        assert output["data"]["resource"]["comment"] == "a python script"


# -- udf resource remove ----------------------------------------------------


class TestUdfResourceRemove:
    """Tests for mcs udf resource remove."""

    def test_resource_remove_succeeds(self, isolated_config: Path) -> None:
        """resource remove drops resource via pyodps."""
        mock_profile = _mock_profile()
        mock_client = _mock_client(mock_profile)
        mock_db = _mock_db()

        mock_odps = MagicMock()
        mock_odps.drop_resource = MagicMock()
        mock_client._ensure_odps.return_value = mock_odps

        with _patch_udf_context(mock_profile, mock_client, mock_db):
            result = _invoke(["resource", "remove", "script.py"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["data"]["name"] == "script.py"
        assert output["data"]["status"] == "removed"

        mock_odps.drop_resource.assert_called_once_with("script.py", project="my_proj")


# -- profile resolution -----------------------------------------------------


class TestProfileResolution:
    """Tests for profile resolution errors."""

    def test_profile_not_found_error(self, isolated_config: Path) -> None:
        """ProfileNotFoundError exits with code 3."""
        from maxcompute_semantic.auth.errors import ProfileNotFoundError

        with patch.object(
            ProfileContext,
            "resolve",
            side_effect=ProfileNotFoundError("profile not found"),
        ):
            result = _invoke(["list"])

        assert result.exit_code == 3

    def test_no_profiles_error(self, isolated_config: Path) -> None:
        """NoProfilesConfiguredError exits with code 3."""
        from maxcompute_semantic.auth.errors import NoProfilesConfiguredError

        with patch.object(
            ProfileContext,
            "resolve",
            side_effect=NoProfilesConfiguredError("no profiles"),
        ):
            result = _invoke(["list"])

        assert result.exit_code == 3


# -- format_args_for_sql ----------------------------------------------------


class TestFormatArgsForSql:
    """Tests for _format_args_for_sql helper."""

    def test_numeric_args(self) -> None:
        """Numbers stay as-is."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql("1, 42") == "1, 42"

    def test_double_quoted_strings(self) -> None:
        """Double-quoted strings become single-quoted SQL literals."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql('"abc"') == "'abc'"

    def test_double_quoted_string_escapes_apostrophe(self) -> None:
        """Converted double-quoted strings use SQL-standard quote doubling."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql('"O\'Reilly"') == "'O''Reilly'"

    def test_mixed_args(self) -> None:
        """Mixed numeric + string + NULL."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        result = _format_args_for_sql('1, "abc", NULL')
        assert result == "1, 'abc', NULL"

    def test_single_quoted_sql_literal(self) -> None:
        """Already single-quoted literals pass through."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql("'abc'") == "'abc'"

    def test_float_args(self) -> None:
        """Float numbers stay as-is."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql("3.14") == "3.14"

    def test_boolean_args(self) -> None:
        """Boolean literals are accepted and normalized."""
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        assert _format_args_for_sql("true, FALSE") == "TRUE, FALSE"

    @pytest.mark.parametrize(
        "args",
        [
            "",
            "foo",
            "id + 1",
            "1; DROP TABLE t",
            "1,,2",
            "'unterminated",
            '"unterminated',
        ],
    )
    def test_rejects_non_literal_args(self, args: str) -> None:
        from maxcompute_semantic.commands.udf import _format_args_for_sql

        with pytest.raises(click.BadParameter):
            _format_args_for_sql(args)


# -- extract_class_name -----------------------------------------------------


class TestExtractClassName:
    """Tests for _extract_class_name helper."""

    def test_class_definition_found(self) -> None:
        """Extract class name from Python class definition."""
        from maxcompute_semantic.commands.udf import _extract_class_name

        script = "class MyUDF(BaseUDF):\n    def evaluate(self, x):\n        return x"
        assert _extract_class_name(script, "my_udf.py") == "MyUDF"

    def test_fallback_to_filename(self) -> None:
        """Fallback to filename stem when no class found."""
        from maxcompute_semantic.commands.udf import _extract_class_name

        script = "def evaluate(x):\n    return x"
        assert _extract_class_name(script, "my_udf.py") == "my_udf"
