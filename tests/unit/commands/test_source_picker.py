# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/_source_picker.py — drill-down picker + parsers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maxcompute_semantic.auth.schema import DataSource, TableSpec
from maxcompute_semantic.commands._source_picker import (
    _is_dev_name,
    _prod_counterpart,
    _reorder_for_role,
    pick_source,
)

# ─── pick_source (drill-down) ───

# NOTE: _pick_one/_pick_many always try _iterfzf first (when available).
# Tests mock _source_picker._iterfzf to prevent the real fzf binary from
# launching.  _pick_columns (column picker) always uses questionary.checkbox
# regardless — those tests mock questionary directly.
#
# When _pick_schema has only 1 schema, it auto-picks without calling
# _iterfzf, so the side_effect list must skip that expected call.

_FZF_MODE_ALL = "all (wildcard '*' — future tables auto-included)"
_FZF_MODE_SPECIFIC = "pick specific tables"


def _mock_client_with(
    *,
    projects: list[str] | None = None,
    schemas: list[str] | None = None,
    tables: list[str] | None = None,
    table_columns: dict | None = None,
) -> MagicMock:
    client = MagicMock()
    client.list_projects.return_value = projects or []
    client.list_schemas.return_value = schemas or []
    client.list_tables.return_value = tables or []
    if table_columns is not None:
        client.describe_table.side_effect = lambda name, **_: {
            "table": {
                "name": name,
                "schema": [
                    {"name": c, "type": "STRING", "comment": ""} for c in table_columns[name]
                ],
                "partition_columns": [],
            }
        }
    return client


class TestPickSource:
    def test_wildcard_path(self) -> None:
        """Most common path: pick project + schema + wildcard tables."""
        client = _mock_client_with(
            projects=["proj_a", "proj_b"],
            schemas=["default", "sales_x"],
            tables=["t1", "t2"],
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # 2 schemas → _pick_one calls _iterfzf for schema too
            mock_fzf.side_effect = [
                "proj_a",  # _pick_one: project
                "sales_x",  # _pick_one: schema (2 schemas, so prompted)
                _FZF_MODE_ALL,  # _pick_one: mode
            ]
            ds = pick_source(client, default_project="proj_a")

        assert ds is not None
        assert ds.project == "proj_a"
        assert ds.schema == "sales_x"
        assert ds.tables == "*"

    def test_specific_tables_no_col_scope(self) -> None:
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["t1", "t2", "t3"],
        )
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.confirm", return_value=False),
        ):
            # 1 schema → auto-picked, no _iterfzf call for schema
            mock_fzf.side_effect = [
                "proj_a",  # _pick_one: project
                _FZF_MODE_SPECIFIC,  # _pick_one: mode
                ["t1", "t3"],  # _pick_many: tables (multi mode)
            ]
            ds = pick_source(client)

        assert ds is not None
        assert ds.project == "proj_a"
        assert isinstance(ds.tables, tuple)
        names = tuple(ts.name for ts in ds.tables)
        assert names == ("t1", "t3")
        # No column scoping → empty column lists
        for ts in ds.tables:
            assert ts.columns is None
            assert ts.columns_exclude == ()

    def test_specific_tables_with_col_exclusion(self) -> None:
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["users", "orders"],
            table_columns={
                "users": ["id", "email", "password", "ssn"],
                "orders": ["id", "total"],
            },
        )
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.confirm",
                # Tables in sorted order: ["orders", "users"]
                # First click.confirm: configure cols for `orders`? = False
                # Second: configure cols for `users`? = True
                side_effect=[False, True],
            ),
        ):
            # 1 schema → auto-picked, no _iterfzf call for schema
            # Column picker for `users` now uses fzf-multi (mark-to-hide):
            # user marks password and ssn rows to hide them.
            mock_fzf.side_effect = [
                "proj_a",  # _pick_one: project
                _FZF_MODE_SPECIFIC,  # _pick_one: mode
                ["users", "orders"],  # _pick_many: tables
                [  # _pick_columns_to_hide: fzf marks for `users`
                    "password                       STRING",
                    "ssn                            STRING",
                ],
            ]
            ds = pick_source(client)

        assert ds is not None
        names = {ts.name: ts for ts in ds.tables}
        assert names["users"].columns_exclude == ("password", "ssn")
        assert names["orders"].columns_exclude == ()  # not configured

    def test_user_aborts_returns_none(self) -> None:
        client = _mock_client_with(projects=["p"], schemas=["s"], tables=["t"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = None  # Ctrl-C on project picker
            ds = pick_source(client)
        assert ds is None

    def test_two_level_auto_picks_default(self) -> None:
        """2-level project: list_schemas returns just ['default'] → no schema prompt."""
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["t1"],
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # 1 schema → auto-picked, no _iterfzf call for schema
            mock_fzf.side_effect = [
                "proj_a",
                _FZF_MODE_ALL,
            ]
            ds = pick_source(client)
        assert ds is not None
        assert ds.schema == "default"

    def test_existing_whitelist_preserved_not_demoted(self) -> None:
        """Updating a source whose table has ``columns=[...]`` whitelist
        must NOT silently demote it to ``columns_exclude`` (blacklist).
        """
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["users", "orders"],
            table_columns={
                "users": ["id", "email", "password", "ssn"],
                "orders": ["id", "total"],
            },
        )
        existing = DataSource(
            project="proj_a",
            schema="default",
            tables=(
                TableSpec(name="users", columns=("id", "email")),
                TableSpec(name="orders", columns_exclude=("total",)),
            ),
        )
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.confirm",
                # Tables in sorted order: ["orders", "users"]
                # `orders` reaches click.confirm → False (keep blacklist)
                # `users` whitelist short-circuits BEFORE the prompt
                side_effect=[False],
            ),
        ):
            # 1 schema → auto-picked
            mock_fzf.side_effect = [
                "proj_a",
                _FZF_MODE_SPECIFIC,
                ["users", "orders"],  # _pick_many: tables multi
            ]
            ds = pick_source(client, existing=existing)

        assert ds is not None
        by_name = {ts.name: ts for ts in ds.tables}
        assert by_name["users"].columns == ("id", "email")
        assert by_name["users"].columns_exclude == ()
        assert by_name["orders"].columns_exclude == ("total",)
        assert by_name["orders"].columns is None

    def test_list_projects_failure_falls_back_to_prompt(self) -> None:
        """list_projects raises + user declines compute_project default
        → click.prompt for manual entry takes over."""
        client = MagicMock()
        client.list_projects.side_effect = RuntimeError("no list-projects API")
        client.list_schemas.return_value = ["default"]
        client.list_tables.return_value = ["t1"]
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.confirm",
                return_value=False,
            ),
            patch(
                "maxcompute_semantic.commands._source_picker.click.prompt",
                return_value="manual_proj",
            ),
        ):
            # No _iterfzf for project (manual fallback), 1 schema → auto-picked
            # Only _iterfzf call is for mode
            mock_fzf.side_effect = [_FZF_MODE_ALL]
            ds = pick_source(client, default_project="suggested")
        assert ds is not None
        assert ds.project == "manual_proj"

    def test_list_projects_failure_accepts_compute_project_default(self) -> None:
        """list_projects fails + user accepts compute_project default."""
        client = MagicMock()
        client.list_projects.side_effect = RuntimeError("no list-projects API")
        client.list_schemas.return_value = ["default"]
        client.list_tables.return_value = ["t1"]
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.confirm",
                return_value=True,
            ),
        ):
            # No _iterfzf for project (click.confirm fallback), 1 schema → auto-picked
            mock_fzf.side_effect = [_FZF_MODE_ALL]
            ds = pick_source(client, default_project="my_compute_proj")
        assert ds is not None
        assert ds.project == "my_compute_proj"

    def test_pick_project_empty_string_reprompts(self) -> None:
        """Empty-string on manual-entry fallback re-prompts."""
        client = MagicMock()
        client.list_projects.side_effect = RuntimeError("no list-projects API")
        client.list_schemas.return_value = ["default"]
        client.list_tables.return_value = ["t1"]
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.prompt",
                side_effect=["", "  ", "real_proj"],
            ),
        ):
            # No _iterfzf for project (manual fallback), 1 schema → auto-picked
            mock_fzf.side_effect = [_FZF_MODE_ALL]
            ds = pick_source(client)
        assert ds is not None
        assert ds.project == "real_proj"

    def test_list_schemas_failure_falls_back_to_manual_entry(self) -> None:
        """list_schemas raises → fallback to manual schema-name prompt."""
        client = MagicMock()
        client.list_projects.return_value = ["proj_a"]
        client.list_schemas.side_effect = RuntimeError("auth scope issue")
        client.list_tables.return_value = ["t1"]
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch(
                "maxcompute_semantic.commands._source_picker.click.prompt",
                return_value="my_typed_schema",
            ),
        ):
            # Schema fallback uses click.prompt, not _iterfzf
            mock_fzf.side_effect = [
                "proj_a",  # _pick_one: project
                _FZF_MODE_ALL,
            ]
            ds = pick_source(client)
        assert ds is not None
        assert ds.schema == "my_typed_schema"

    def test_list_schemas_2level_project_returns_default(self) -> None:
        """2-level project → auto-pick 'default'."""
        client = MagicMock()
        client.list_projects.return_value = ["two_level_proj"]
        client.list_schemas.side_effect = RuntimeError(
            "Project two_level_proj is not 3-tier model project."
        )
        client.list_tables.return_value = ["t1"]
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # Schema auto-picks "default" (2-level detection), no _iterfzf
            mock_fzf.side_effect = [
                "two_level_proj",
                _FZF_MODE_ALL,
            ]
            ds = pick_source(client)
        assert ds is not None
        assert ds.schema == "default"

    def test_list_tables_empty_falls_back_to_wildcard(self) -> None:
        """0-table list → wildcard '*', not abort."""
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=[],  # empty
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # Empty tables → _pick_tables returns "*" immediately (no mode picker)
            mock_fzf.side_effect = ["proj_a"]
            ds = pick_source(client)
        assert ds is not None
        assert ds.tables == "*"

    def test_pick_tables_zero_selected_falls_back_to_wildcard(self) -> None:
        """User confirms multi-select with 0 selections → wildcard."""
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["t1", "t2"],
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # 1 schema → auto-picked
            mock_fzf.side_effect = [
                "proj_a",
                _FZF_MODE_SPECIFIC,
                [],  # _pick_many: multi mode returns empty list
            ]
            ds = pick_source(client)
        assert ds is not None
        assert ds.tables == "*"

    def test_cached_projects_skips_api_call(self) -> None:
        """Passing cached_projects avoids re-querying list_projects."""
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["t1"],
        )
        client.list_projects.reset_mock()
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # 1 schema → auto-picked
            mock_fzf.side_effect = [
                "proj_a",
                _FZF_MODE_ALL,
            ]
            ds = pick_source(client, cached_projects=["proj_a"])
        assert ds is not None
        assert ds.project == "proj_a"
        client.list_projects.assert_not_called()

    def test_fallback_to_questionary_without_iterfzf(self) -> None:
        """When iterfzf is None, _pick_one falls back to questionary.select."""
        client = _mock_client_with(
            projects=["proj_a"],
            schemas=["default"],
            tables=["t1"],
        )
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf", None),
            patch("maxcompute_semantic.commands._source_picker.questionary") as q,
        ):
            # 1 schema → auto-picked
            q.select.return_value.ask.side_effect = [
                "proj_a",
                _FZF_MODE_ALL,
            ]
            ds = pick_source(client)
        assert ds is not None
        assert ds.project == "proj_a"


class TestPickerEcho:
    """Echo line printed to stdout after a successful fzf pick."""

    def test_pick_one_echoes_label_with_emoji_when_fzf(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "proj_a"
            result = _pick_one(
                "Project:",
                choices=["proj_a", "proj_b"],
                echo_label="Project",
                echo_emoji="🎯",
            )
        assert result == "proj_a"
        out = capsys.readouterr().out
        assert "✓ 🎯 Project: proj_a" in out

    def test_pick_one_echoes_label_only_when_no_emoji(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "proj_a"
            _pick_one("Project:", choices=["proj_a"], echo_label="Project")
        out = capsys.readouterr().out
        assert "✓ Project: proj_a" in out
        assert "🎯" not in out

    def test_pick_one_skips_echo_when_no_label(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "proj_a"
            _pick_one("Project:", choices=["proj_a"])
        assert "✓" not in capsys.readouterr().out

    def test_pick_one_skips_echo_when_user_cancels(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = None  # user pressed Esc
            result = _pick_one("Project:", choices=["proj_a"], echo_label="Project")
        assert result is None
        assert "✓" not in capsys.readouterr().out

    def test_pick_many_echoes_count_and_first_three(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_many

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = ["a", "b", "c", "d", "e"]
            _pick_many(
                "Tables:", items=["a", "b", "c", "d", "e"], echo_label="Tables", echo_emoji="📄"
            )
        out = capsys.readouterr().out
        assert "✓ 📄 Tables (5): a, b, c, …" in out

    def test_pick_many_echoes_full_when_le_three(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_many

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = ["a", "b"]
            _pick_many("Tables:", items=["a", "b"], echo_label="Tables", echo_emoji="📄")
        assert "✓ 📄 Tables (2): a, b" in capsys.readouterr().out

    def test_pick_choice_echoes_title_not_value(self, capsys) -> None:
        import questionary
        from maxcompute_semantic.commands._source_picker import _pick_choice

        choices = [
            questionary.Choice(title="🔑 AK (AccessKey pair)", value="ak"),
            questionary.Choice(title="🛡 Process", value="process"),
        ]
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "🔑 AK (AccessKey pair)"
            result = _pick_choice(
                "Auth type:", choices=choices, echo_label="Auth type", echo_emoji="🔑"
            )
        assert result == "ak"
        out = capsys.readouterr().out
        assert "✓ 🔑 Auth type: 🔑 AK (AccessKey pair)" in out

    def test_pick_choice_records_fzf_query(self) -> None:
        import questionary
        from maxcompute_semantic.commands._source_picker import _pick_choice, last_fzf_query

        choices = [questionary.Choice(title="orders", value="orders")]
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = ("ord", "orders")
            result = _pick_choice("Table:", choices=choices, query="o")
        assert result == "orders"
        assert last_fzf_query() == "ord"

    def test_questionary_fallback_skips_echo(self, capsys) -> None:
        """Questionary echoes its own answer line; we don't double-echo."""
        from maxcompute_semantic.commands._source_picker import _pick_one

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf", None),
            patch("maxcompute_semantic.commands._source_picker.questionary.select") as mock_sel,
        ):
            mock_sel.return_value.ask.return_value = "proj_a"
            _pick_one("Project:", choices=["proj_a"], echo_label="Project", echo_emoji="🎯")
        assert "✓" not in capsys.readouterr().out


class TestPickerKeyboardInterrupt:
    """Esc and Ctrl+C both return None — both mean "go back one level"."""

    def test_pick_one_esc_returns_none(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = None  # iterfzf returns None on Esc
            assert _pick_one("Q:", choices=["a"]) is None

    def test_pick_one_ctrl_c_returns_none(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.side_effect = KeyboardInterrupt
            assert _pick_one("Q:", choices=["a"]) is None

    def test_pick_many_ctrl_c_returns_none(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_many

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.side_effect = KeyboardInterrupt
            assert _pick_many("Q:", items=["a"]) is None

    def test_pick_choice_ctrl_c_returns_none(self) -> None:
        import questionary
        from maxcompute_semantic.commands._source_picker import _pick_choice

        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.side_effect = KeyboardInterrupt
            assert _pick_choice("Q:", choices=[questionary.Choice("X", value="x")]) is None

    def test_questionary_fallback_ctrl_c_returns_none(self) -> None:
        """questionary fallback: .ask() returns None on both Esc and Ctrl+C."""
        from maxcompute_semantic.commands._source_picker import _pick_one

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf", None),
            patch("maxcompute_semantic.commands._source_picker.questionary.select") as mock_sel,
        ):
            mock_sel.return_value.ask.return_value = None
            assert _pick_one("Q:", choices=["a"]) is None

    def test_questionary_fallback_esc_returns_none(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_one

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf", None),
            patch("maxcompute_semantic.commands._source_picker.questionary.select") as mock_sel,
        ):
            mock_sel.return_value.ask.return_value = None
            assert _pick_one("Q:", choices=["a"]) is None


class TestPickColumnsToHide:
    """fzf-multi mark-to-hide column picker — marks become exclude list."""

    def _client_with_cols(
        self, cols: list[tuple[str, str]], part_cols: list[str] = None
    ) -> MagicMock:
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "schema": [{"name": n, "type": t, "comment": ""} for n, t in cols],
                "partition_columns": [{"name": p, "type": "STRING"} for p in (part_cols or [])],
            }
        }
        return client

    def test_marks_become_exclude_list(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols(
            [("id", "BIGINT"), ("pii_phone", "STRING"), ("pii_email", "STRING"), ("name", "STRING")]
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = [
                "pii_phone                      STRING",
                "pii_email                      STRING",
            ]
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert excluded == ["pii_phone", "pii_email"]
        # Echo line confirms what was hidden.
        assert "✓ ✂️ Hide cols (2): pii_phone, pii_email" in capsys.readouterr().out

    def test_empty_marks_means_no_exclude(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT"), ("name", "STRING")])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = []  # user pressed Enter without marking
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert excluded == []
        out = capsys.readouterr().out
        assert "✓ 📋 Hide cols: none (all visible)" in out

    def test_edit_path_prints_banner(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols(
            [("id", "BIGINT"), ("pii_phone", "STRING"), ("pii_email", "STRING")]
        )
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = ["pii_phone                      STRING"]
            _pick_columns_exclude(
                client,
                project="p",
                schema="s",
                table_name="t",
                pre_excluded=("pii_phone", "pii_email"),
            )
        # Banner appears before the picker, listing currently-hidden cols.
        captured = capsys.readouterr()
        assert "Currently hiding 2 col(s)" in captured.err
        assert "pii_phone" in captured.err
        assert "pii_email" in captured.err

    def test_user_cancels_returns_none(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT")])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = None  # Esc
            result = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert result is None

    def test_partition_columns_can_be_marked_to_hide(self) -> None:
        """Partition cols stay markable (footgun, not enforced — matches today's stance)."""
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT"), ("ds", "STRING")], part_cols=["ds"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            # User can Tab the partition row even though it's annotated [partition].
            mock_fzf.return_value = ["ds                             STRING  [partition]"]
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert excluded == ["ds"]

    def test_questionary_fallback_keeps_uncheck_to_hide_semantic(self) -> None:
        """When iterfzf is unavailable, fall through to questionary checkbox with
        'all pre-checked, uncheck to hide' semantics — unchanged from today."""
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT"), ("pii_phone", "STRING")])
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf", None),
            patch("maxcompute_semantic.commands._source_picker.questionary.checkbox") as mock_cb,
        ):
            mock_cb.return_value.ask.return_value = ["id"]  # only id stays visible
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        # Visible = ["id"]; all_cols = ["id", "pii_phone"] → excluded = ["pii_phone"]
        assert excluded == ["pii_phone"]


class TestPickColumnsManualEntry:
    """Manual-entry sentinel `<other:>` row + describe-denied fallback."""

    _SENTINEL = "<other: type a column name to hide>"

    def _client_with_cols(self, cols: list[tuple[str, str]]) -> MagicMock:
        client = MagicMock()
        client.describe_table.return_value = {
            "table": {
                "schema": [{"name": n, "type": t, "comment": ""} for n, t in cols],
                "partition_columns": [],
            }
        }
        return client

    def test_other_sentinel_in_choice_list(self) -> None:
        """Sentinel row is appended to fzf items so the user can pick it."""
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT")])
        captured_items = []

        def _fake_fzf(items, **_):
            captured_items.append(list(items))
            return []

        with patch("maxcompute_semantic.commands._source_picker._iterfzf", side_effect=_fake_fzf):
            _pick_columns_exclude(client, project="p", schema="s", table_name="t", pre_excluded=())
        assert any(self._SENTINEL in i for i in captured_items[0])

    def test_other_sentinel_marked_prompts_for_name(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT"), ("pii", "STRING")])
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.prompt") as mock_prompt,
            patch("maxcompute_semantic.commands._source_picker.click.confirm") as mock_conf,
        ):
            mock_fzf.return_value = [
                "pii                            STRING",
                self._SENTINEL,
            ]
            mock_prompt.return_value = "extra_col"
            mock_conf.return_value = False
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert excluded == ["pii", "extra_col"]
        mock_prompt.assert_called()

    def test_other_sentinel_loop_until_user_says_no(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude

        client = self._client_with_cols([("id", "BIGINT")])
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.prompt") as mock_prompt,
            patch("maxcompute_semantic.commands._source_picker.click.confirm") as mock_conf,
        ):
            mock_fzf.return_value = [self._SENTINEL]
            mock_prompt.side_effect = ["col_a", "col_b"]
            mock_conf.side_effect = [True, False]
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        assert excluded == ["col_a", "col_b"]

    def test_describe_denied_falls_back_to_manual_only(self, capsys) -> None:
        """McsError from describe_table → no fzf, prompt loop only."""
        from maxcompute_semantic.commands._source_picker import _pick_columns_exclude
        from maxcompute_semantic.mc_client.errors import McsError

        client = MagicMock()
        client.describe_table.side_effect = McsError(
            code="permission_denied",
            message="describe denied",
            remediation="Ask DBA for SHOW permission.",
        )
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.prompt") as mock_prompt,
            patch("maxcompute_semantic.commands._source_picker.click.confirm") as mock_conf,
        ):
            mock_prompt.return_value = "secret_col"
            mock_conf.return_value = False
            excluded = _pick_columns_exclude(
                client, project="p", schema="s", table_name="t", pre_excluded=()
            )
        mock_fzf.assert_not_called()
        assert excluded == ["secret_col"]
        err = capsys.readouterr().err
        assert "describe_table denied" in err


class TestPickProjectOtherSentinel:
    """`<other:>` row always offered, even when the suggested project is in the list."""

    def test_other_row_present_when_suggested_in_list(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = MagicMock()
        client.list_projects.return_value = ["proj_a", "proj_b"]
        captured_items = []

        def _fake_fzf(items, **_):
            captured_items.append(list(items))
            return "proj_a"

        with patch("maxcompute_semantic.commands._source_picker._iterfzf", side_effect=_fake_fzf):
            _pick_project(client, default="proj_a", existing=None, cached_projects=None)
        assert captured_items[0] == [
            "proj_a",
            "proj_b",
            "<other: type project name manually>",
        ]

    def test_other_row_pick_drops_into_manual_prompt(self) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = MagicMock()
        client.list_projects.return_value = ["proj_a", "proj_b"]
        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.prompt") as mock_prompt,
            patch("maxcompute_semantic.commands._source_picker.click.confirm") as mock_conf,
        ):
            mock_fzf.return_value = "<other: type project name manually>"
            mock_conf.return_value = False
            mock_prompt.return_value = "manual_proj"
            answer = _pick_project(client, default="proj_a", existing=None, cached_projects=None)
        assert answer == "manual_proj"


class TestEchoLabelWiring:
    """Verify the label/emoji kwargs reach the picker from _pick_project /
    _pick_schema (sanity check that we didn't forget to thread them through)."""

    def test_pick_schema_passes_echo_label_and_emoji(self, capsys) -> None:
        from maxcompute_semantic.commands._source_picker import _pick_schema

        client = MagicMock()
        client.list_schemas.return_value = ["s1", "s2"]
        with patch(
            "maxcompute_semantic.commands._source_picker._iterfzf",
            return_value="s1",
        ):
            _pick_schema(client, project="p", existing=None)
        assert "✓ 🗂 Schema: s1" in capsys.readouterr().out


class TestDevProdHelpers:
    def test_is_dev_name_recognizes_dev_suffix(self):
        assert _is_dev_name("acme_dev") is True
        assert _is_dev_name("foo_bar_dev") is True

    def test_is_dev_name_rejects_non_dev(self):
        assert _is_dev_name("acme") is False
        assert _is_dev_name("acme_dev_v2") is False
        assert _is_dev_name("dev") is False  # bare "dev" is not a suffix
        assert _is_dev_name("") is False
        assert _is_dev_name("_dev") is False  # empty stem rejected

    def test_prod_counterpart_strips_dev_suffix(self):
        assert _prod_counterpart("acme_dev") == "acme"
        assert _prod_counterpart("foo_bar_dev") == "foo_bar"

    def test_prod_counterpart_returns_none_when_not_dev(self):
        assert _prod_counterpart("acme") is None
        assert _prod_counterpart("dev") is None
        assert _prod_counterpart("") is None

    def test_reorder_for_compute_role_puts_dev_projects_first(self):
        out = _reorder_for_role(
            ["acme", "beta", "acme_dev", "gamma_dev"],
            role="compute",
            default=None,
        )
        # *_dev projects come first; their relative order preserved;
        # non-dev projects come after, relative order preserved.
        assert out == ["acme_dev", "gamma_dev", "acme", "beta"]

    def test_reorder_for_source_role_puts_prod_counterpart_first(self):
        out = _reorder_for_role(
            ["acme", "beta", "acme_dev", "gamma"],
            role="source",
            default="acme_dev",
        )
        # Prod counterpart of default (acme) leads, then dev itself
        # (acme_dev) so user can pick either, then everything else.
        assert out == ["acme", "acme_dev", "beta", "gamma"]

    def test_reorder_for_source_role_no_dev_default_is_passthrough(self):
        # When default isn't a *_dev name, no special reordering.
        out = _reorder_for_role(
            ["acme", "beta", "gamma"],
            role="source",
            default="acme",
        )
        assert out == ["acme", "beta", "gamma"]

    def test_reorder_for_source_role_prod_not_in_list_keeps_dev_first(self):
        # Prod counterpart 'acme' isn't in the list (AK can't enumerate
        # it but can SELECT it). Dev still gets surfaced; user must use
        # <other:> escape hatch for prod.
        out = _reorder_for_role(
            ["acme_dev", "beta", "gamma"],
            role="source",
            default="acme_dev",
        )
        assert out == ["acme_dev", "beta", "gamma"]

    def test_reorder_for_role_none_is_passthrough(self):
        out = _reorder_for_role(["a", "b", "c"], role=None, default=None)
        assert out == ["a", "b", "c"]


class TestPickProjectRoleHints:
    def _client(self, projects):
        c = MagicMock()
        c.list_projects.return_value = projects
        return c

    def test_compute_role_passes_dev_tip_as_fzf_header(self):
        """The compute-role tip is rendered inside fzf via the ``header=``
        kwarg so it stays visible during the full-screen picker, not
        printed to stderr (which fzf scrolls past)."""
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["acme", "acme_dev"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "acme_dev"
            result = _pick_project(
                client,
                default=None,
                existing=None,
                cached_projects=None,
                role="compute",
            )
        assert result == "acme_dev"
        header = mock_fzf.call_args.kwargs.get("header", "")
        assert "ⓘ" in header
        assert "SQL execution" in header or "compute" in header.lower()
        # The tip must mention the dev convention.
        assert "_dev" in header or "dev project" in header.lower()
        # And explain what permission level is expected here.
        assert "permission" in header.lower() or "run jobs" in header.lower()
        # Prompt label should also be role-specific (not the generic
        # "data source's MaxCompute project" wording).
        prompt = mock_fzf.call_args.kwargs.get("prompt", "")
        assert "Compute" in prompt or "SQL executes" in prompt

    def test_source_role_passes_prod_tip_as_fzf_header(self):
        """The source-role tip is rendered inside fzf via ``header=``."""
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["acme", "acme_dev"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "acme"
            result = _pick_project(
                client,
                default="acme_dev",
                existing=None,
                cached_projects=None,
                role="source",
            )
        assert result == "acme"
        header = mock_fzf.call_args.kwargs.get("header", "")
        assert "ⓘ" in header
        # Tip should mention prod / production and reference the
        # _dev → prod stripping convention.
        assert "production" in header.lower() or "prod" in header.lower()
        # And explain that the source is read-only data.
        assert "read-only" in header.lower() or "read only" in header.lower()
        # Prompt label should reflect the source role.
        prompt = mock_fzf.call_args.kwargs.get("prompt", "")
        assert "Data source" in prompt or "production" in prompt.lower()

    def test_no_role_passes_no_header(self):
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["a", "b"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "a"
            _pick_project(
                client,
                default=None,
                existing=None,
                cached_projects=None,
            )
        # role=None → no tip header; _pick_one collapses None → "".
        header = mock_fzf.call_args.kwargs.get("header", "")
        assert "ⓘ" not in header  # no tip in default behavior
        # Generic prompt label preserved for role=None callers.
        prompt = mock_fzf.call_args.kwargs.get("prompt", "")
        assert "data source's MaxCompute project" in prompt

    def test_compute_role_reorders_choices_dev_first(self):
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["acme", "beta", "acme_dev"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "acme_dev"
            _pick_project(
                client,
                default=None,
                existing=None,
                cached_projects=None,
                role="compute",
            )
        # The first positional arg of _iterfzf is the choices list.
        choices_passed = mock_fzf.call_args.args[0]
        # *_dev rises to the top; <other:> stays last.
        assert choices_passed[0] == "acme_dev"
        assert choices_passed[-1].startswith("<other:")

    def test_source_role_reorders_prod_first_dev_second(self):
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["acme_dev", "acme", "beta"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "acme"
            _pick_project(
                client,
                default="acme_dev",
                existing=None,
                cached_projects=None,
                role="source",
            )
        choices_passed = mock_fzf.call_args.args[0]
        assert choices_passed[0] == "acme"  # prod first
        assert choices_passed[1] == "acme_dev"  # dev second
        assert choices_passed[-1].startswith("<other:")

    def test_role_none_preserves_existing_order(self):
        from maxcompute_semantic.commands._source_picker import _pick_project

        client = self._client(["beta", "acme", "acme_dev"])
        with patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf:
            mock_fzf.return_value = "beta"
            _pick_project(
                client,
                default=None,
                existing=None,
                cached_projects=None,
            )
        choices_passed = mock_fzf.call_args.args[0]
        # Original order preserved (plus <other:> appended).
        assert choices_passed[:3] == ["beta", "acme", "acme_dev"]


class TestPickSourceDevProdNudge:
    def test_pick_source_passes_source_role_and_reorders_prod_first(self):
        client = MagicMock()
        client.list_projects.return_value = ["acme_dev", "acme", "beta"]
        # Two schemas so the schema picker actually invokes fzf
        # (1-schema case auto-picks without an _iterfzf call).
        client.list_schemas.return_value = ["default", "other"]
        client.list_tables.return_value = ["t1", "t2"]

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.confirm", return_value=True),
        ):
            # Picker calls in order: project, schema, mode (wildcard).
            mock_fzf.side_effect = [
                "acme",  # project pick
                "default",  # schema pick
                _FZF_MODE_ALL,  # tables mode (wildcard sentinel)
            ]
            ds = pick_source(client, default_project="acme_dev")

        assert ds is not None
        assert ds.project == "acme"
        # First fzf call is _pick_project: choices should have prod first.
        first_call_choices = mock_fzf.call_args_list[0].args[0]
        assert first_call_choices[0] == "acme"
        assert first_call_choices[1] == "acme_dev"

    def test_pick_source_no_dev_default_passthrough(self):
        """When compute_project doesn't end with _dev, no reorder happens."""
        client = MagicMock()
        client.list_projects.return_value = ["alpha", "beta", "gamma"]
        client.list_schemas.return_value = ["default", "other"]
        client.list_tables.return_value = ["t1"]

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.confirm", return_value=True),
        ):
            mock_fzf.side_effect = ["alpha", "default", _FZF_MODE_ALL]
            ds = pick_source(client, default_project="alpha")

        assert ds is not None
        first_call_choices = mock_fzf.call_args_list[0].args[0]
        # Original order preserved (no _dev suffix on default).
        assert first_call_choices[:3] == ["alpha", "beta", "gamma"]

    def test_pick_source_passes_source_role_tip_as_fzf_header(self):
        """``pick_source`` wires ``role="source"`` into ``_pick_project``,
        which surfaces the tip via fzf's ``header=`` kwarg (so it stays
        visible during full-screen selection, not invisible-after-print)."""
        client = MagicMock()
        client.list_projects.return_value = ["acme_dev", "acme"]
        client.list_schemas.return_value = ["default", "other"]
        client.list_tables.return_value = ["t1"]

        with (
            patch("maxcompute_semantic.commands._source_picker._iterfzf") as mock_fzf,
            patch("maxcompute_semantic.commands._source_picker.click.confirm", return_value=True),
        ):
            mock_fzf.side_effect = ["acme", "default", _FZF_MODE_ALL]
            pick_source(client, default_project="acme_dev")

        # First fzf call is the project picker — that's where the
        # source-role header lands.
        first_call = mock_fzf.call_args_list[0]
        header = first_call.kwargs.get("header", "")
        assert "ⓘ" in header
        assert "production" in header.lower() or "prod" in header.lower()
