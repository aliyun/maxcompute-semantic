# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the multi-level file-browser-style profile editor.

The editor is a state-machine of nested ``while True:`` loops; each
section editor returns a new ``Profile`` (via ``dataclasses.replace``)
or the unchanged draft. Tests mock the ``questionary.select`` /
``questionary.checkbox`` UI primitives and feed predetermined choice
sequences via ``side_effect`` to walk the user through specific paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    ProcessAuth,
    Profile,
)
from maxcompute_semantic.commands._profile_editor import edit_profile


def _profile(
    name: str = "test",
    *,
    sources: tuple[DataSource, ...] = (),
    auth=None,
    tags: tuple[str, ...] = (),
) -> Profile:
    return Profile(
        name=name,
        compute_project="acme",
        endpoint="https://x.aliyun.com/api",
        auth=auth or AkAuth(access_key_id="${env:AK_ID}", access_key_secret="${env:AK_SECRET}"),
        sources=sources,
        tags=tags,
    )


# ── top-level navigation ──────────────────────────────────────────────


class TestTopLevel:
    def test_done_returns_unchanged_profile(self) -> None:
        """Open editor → Save immediately → returns same Profile."""
        p = _profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor._top_level_select",
            return_value="DONE",
        ):
            result = edit_profile(p, client)
        assert result is not None
        assert result == p

    def test_cancel_returns_none(self) -> None:
        p = _profile()
        client = MagicMock()
        with (
            patch(
                "maxcompute_semantic.commands._profile_editor._top_level_select",
                return_value="CANCEL",
            ),
            patch(
                "maxcompute_semantic.commands._profile_editor.click.confirm",
                return_value=True,
            ),
        ):
            result = edit_profile(p, client)
        assert result is None

    def test_esc_at_top_re_renders_does_not_discard(self) -> None:
        p = _profile()
        client = MagicMock()
        with patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top:
            mock_top.side_effect = [None, "DONE"]
            result = edit_profile(p, client)
        assert result is p
        assert mock_top.call_count == 2


# ── section editors ──────────────────────────────────────────────────


class TestEditComputeProject:
    def test_replaces_field(self, mock_picker: list[object]) -> None:
        p = _profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            return_value="new_proj",
        ):
            # _pick_choice queue: drill into compute_project → DONE.
            mock_picker.append("compute_project")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.compute_project == "new_proj"

    def test_empty_input_reprompts(self, mock_picker: list[object]) -> None:
        """Empty + whitespace + real value → final result has real value."""
        p = _profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            # Empty → reprompt; whitespace → reprompt; "real" accepted
            side_effect=["", "  ", "real_proj"],
        ):
            mock_picker.append("compute_project")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.compute_project == "real_proj"


class TestEditEndpoint:
    def test_rejects_no_scheme(self, mock_picker: list[object]) -> None:
        p = _profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            # No scheme → reprompt; with scheme → accept
            side_effect=["odps.aliyun.com/api", "https://odps.aliyun.com/api"],
        ):
            mock_picker.append("endpoint")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.endpoint == "https://odps.aliyun.com/api"


class TestEditAuth:
    def test_back_keeps_existing(self, mock_picker: list[object]) -> None:
        p = _profile(auth=AkAuth("OLD_ID", "OLD_SECRET"))
        client = MagicMock()
        # top: auth → auth-type: BACK → top: DONE
        mock_picker.append("auth")
        mock_picker.append("BACK")
        mock_picker.append("DONE")
        result = edit_profile(p, client)
        assert result is not None
        assert isinstance(result.auth, AkAuth)
        assert result.auth.access_key_id == "OLD_ID"

    def test_switch_to_process_auth(self, mock_picker: list[object]) -> None:
        p = _profile(auth=AkAuth("OLD_ID", "OLD_SECRET"))
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            side_effect=["my_auth_cmd", 60],
        ):
            mock_picker.append("auth")
            mock_picker.append("process")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert isinstance(result.auth, ProcessAuth)
        assert result.auth.command == "my_auth_cmd"
        assert result.auth.timeout == 60

    def test_ak_auth_edit_replaces_id_and_secret(self, mock_picker: list[object]) -> None:
        p = _profile(auth=AkAuth("OLD_ID", "OLD_SECRET"))
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            side_effect=["NEW_ID", "NEW_SECRET"],
        ):
            mock_picker.append("auth")
            mock_picker.append("ak")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert isinstance(result.auth, AkAuth)
        assert result.auth.access_key_id == "NEW_ID"
        assert result.auth.access_key_secret == "NEW_SECRET"

    def test_auth_type_default_passes_value_not_title(self) -> None:
        r"""Regression: ``_edit_auth`` used to pass the Choice ``title``
        as ``default=`` to ``questionary.select``, which raises
        ``Invalid \`default\` value passed`` because questionary
        validates against ``Choice.value`` (not title). Now the
        default is the bare value ``"ak"`` / ``"process"``.

        Implemented via ``_pick_choice``'s ``default=`` kwarg; the
        invariant still holds — verify by inspecting the call-args.
        """
        p = _profile(auth=AkAuth("FooAKID", "secret"))
        client = MagicMock()
        # Patch _pick_choice with a MagicMock so we can inspect call_args.
        pick_mock = MagicMock(side_effect=["auth", "BACK", "DONE"])
        with patch(
            "maxcompute_semantic.commands._profile_editor._pick_choice",
            pick_mock,
        ):
            edit_profile(p, client)
        # Calls: 0=top-level menu, 1=auth-type picker, 2=top-level menu.
        assert pick_mock.call_count >= 2
        auth_call_kwargs = pick_mock.call_args_list[1].kwargs
        assert auth_call_kwargs["default"] == "ak"


# Synthetic 24-char-ish placeholder for an AK id. Never a live
# credential, and split across two literals so cspell doesn't flag
# the run-on, and so the file contains no real-looking AK prefix.
_FAKE_AK = "FAKE_AK_" + "ID_TEST_ONLY_24CH"


class TestFormatAuthAndPrincipal:
    """Direct unit tests for the auth-row display helpers."""

    def test_format_auth_masks_literal_ak(self) -> None:
        from maxcompute_semantic.commands._profile_editor import _format_auth

        rendered = _format_auth(AkAuth(_FAKE_AK, "literal_secret"))
        # Literal AK id never appears verbatim.
        assert _FAKE_AK not in rendered
        # Mask shape: first-4 + *** + last-4.
        expected_mask = f"{_FAKE_AK[:4]}***{_FAKE_AK[-4:]}"
        assert expected_mask in rendered

    def test_format_auth_preserves_env_ref(self) -> None:
        from maxcompute_semantic.commands._profile_editor import _format_auth

        rendered = _format_auth(AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"))
        assert "${env:MY_AK_ID}" in rendered

    def test_format_auth_short_id_collapses_to_stars(self) -> None:
        """Short ids (≤8 chars) collapse to ``***`` — no first-4/last-4
        leak when masking would leave nothing in the middle.
        """
        from maxcompute_semantic.commands._profile_editor import _format_auth, _mask_ak_id

        assert _mask_ak_id("ABCDEFGH") == "***"  # exactly 8
        assert _mask_ak_id("AB") == "***"  # very short
        rendered = _format_auth(AkAuth("ABCDEFGH", "s"))
        assert "ABCDEFGH" not in rendered
        assert "***" in rendered

    def test_live_identity_ak_happy_path(self) -> None:
        """``commands._identity.live_identity`` is the shared helper
        the new ``mcs profile whoami`` verb dispatches through. For
        AK profiles it builds a one-shot client and pulls the
        principal display string out of the ODPS whoami response.
        """
        from maxcompute_semantic.commands import _identity as identity_mod

        p = _profile(auth=AkAuth("FAKE_AK_" + "ID_HAPPY", "FAKE_SECRET"))
        odps = MagicMock()
        odps.execute_security_query.return_value = {
            "DisplayName": "RAM$test-role:test-user",
        }
        fake_client = MagicMock()
        fake_client._ensure_odps.return_value = odps

        from maxcompute_semantic.mc_client import client as mc_client_mod

        with patch.object(mc_client_mod, "MaxComputeClient", return_value=fake_client):
            got = identity_mod.live_identity(p)
        assert got == "RAM$test-role:test-user"

    def test_live_identity_ak_construction_failure_propagates_mcs_error(self) -> None:
        """Classified McsError during client construction (e.g. an
        env-ref pointing at an unset variable) propagates so the
        caller can render the specific code and remediation instead
        of a generic "no identity" message.
        """
        from maxcompute_semantic.auth.errors import ConfigEnvNotSetError
        from maxcompute_semantic.commands._identity import live_identity

        # The env-vars below are absent in the test process env;
        # resolving them raises ConfigEnvNotSetError (McsError subclass).
        p = _profile(auth=AkAuth("${env:MCS_TEST_NOPE_ID}", "${env:MCS_TEST_NOPE_SECRET}"))
        with pytest.raises(ConfigEnvNotSetError):
            live_identity(p)

    def test_live_identity_process_auth(self) -> None:
        """ProcessAuth path: delegate to ``ncs.whoami()`` and format
        the returned record as the ``mcs auth whoami`` verb used to
        print it (``"<identity_name> (employee.<id>)"``).
        """
        from maxcompute_semantic.auth import ncs as ncs_mod
        from maxcompute_semantic.commands._identity import live_identity

        p = _profile(auth=ProcessAuth(command="ncs create credential dummy", timeout=60))
        fake_info = MagicMock()
        fake_info.identity_name = "alice"
        fake_info.employee_id = "99999"
        with patch.object(ncs_mod, "whoami", return_value=fake_info):
            got = live_identity(p)
        assert got == "alice (employee.99999)"

    def test_live_identity_process_auth_no_info_returns_none(self) -> None:
        """The ncs helper returns ``None`` when there's no active
        login — we surface that as the standard None failure path,
        not a crash."""
        from maxcompute_semantic.auth import ncs as ncs_mod
        from maxcompute_semantic.commands._identity import live_identity

        p = _profile(auth=ProcessAuth(command="ncs create credential dummy", timeout=60))
        with patch.object(ncs_mod, "whoami", return_value=None):
            assert live_identity(p) is None


class TestEditCostThresholds:
    def test_sets_new_thresholds(self, mock_picker: list[object]) -> None:
        p = _profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor.click.confirm", return_value=True),
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=[5.0, 50.0],
            ),
        ):
            mock_picker.append("cost")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.cost_thresholds == CostThresholds(confirm_cny=5.0, blocked_cny=50.0)

    def test_rejects_confirm_ge_blocked(self, mock_picker: list[object]) -> None:
        """If confirm >= blocked, the section editor doesn't replace."""
        p = _profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor.click.confirm", return_value=True),
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=[100.0, 50.0],
            ),
        ):
            mock_picker.append("cost")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        # Defaults preserved; bad input rejected silently this round
        assert result is not None
        assert result.cost_thresholds == CostThresholds()


class TestEditTags:
    def test_parses_comma_separated(self, mock_picker: list[object]) -> None:
        p = _profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            return_value="prod, team-a, ",
        ):
            mock_picker.append("tags")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.tags == ("prod", "team-a")

    def test_empty_clears_tags(self, mock_picker: list[object]) -> None:
        p = _profile(tags=("prod",))
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            return_value="",
        ):
            mock_picker.append("tags")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert result.tags == ()


# ── sources sub-picker ──────────────────────────────────────────────


class TestEditSources:
    def test_back_from_sources_no_change(self, mock_picker: list[object]) -> None:
        p = _profile(sources=(DataSource("acme", "default", tables="*"),))
        client = MagicMock()
        # top: sources → sources: BACK → top: DONE
        mock_picker.append("sources")
        mock_picker.append("BACK")
        mock_picker.append("DONE")
        result = edit_profile(p, client)
        assert result is not None
        assert result == p

    def test_add_new_source_grows_sources(self, mock_picker: list[object]) -> None:
        """ADD-SOURCE flow: pick project + schema → empty source created
        + dropped into _edit_source. We mock _pick_project and _pick_schema
        and have _edit_source ESC out (returning unchanged)."""
        p = _profile()
        client = MagicMock()
        with (
            patch(
                "maxcompute_semantic.commands._profile_editor._pick_project",
                return_value="acme",
            ),
            patch(
                "maxcompute_semantic.commands._profile_editor._pick_schema",
                return_value="s1",
            ),
        ):
            # top: sources → sources: ADD → (project + schema via mocks)
            # → enter _edit_source → ESC (None) → back to sources list
            # → BACK to top → DONE
            mock_picker.append("sources")
            mock_picker.append("ADD")
            mock_picker.append(None)  # _edit_source's first picker → Esc
            mock_picker.append("BACK")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert len(result.sources) == 1
        assert result.sources[0].project == "acme"
        assert result.sources[0].schema == "s1"
        assert result.sources[0].tables == ()  # empty source — user backed out before adding tables

    def test_add_duplicate_source_rejected(self, mock_picker: list[object]) -> None:
        """ADD with (project, schema) matching an existing source → warning,
        no append."""
        existing = DataSource("acme", "s1", tables="*")
        p = _profile(sources=(existing,))
        client = MagicMock()
        with (
            patch(
                "maxcompute_semantic.commands._profile_editor._pick_project",
                return_value="acme",
            ),
            patch(
                "maxcompute_semantic.commands._profile_editor._pick_schema",
                return_value="s1",  # duplicate key
            ),
        ):
            mock_picker.append("sources")
            mock_picker.append("ADD")
            mock_picker.append("BACK")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        # Duplicate not appended; original profile preserved
        assert result is not None
        assert len(result.sources) == 1

    def test_remove_source_filters_at_index(self, mock_picker: list[object]) -> None:
        s1 = DataSource("acme", "s1", tables="*")
        s2 = DataSource("prod", "sales", tables="*")
        p = _profile(sources=(s1, s2))
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.confirm",
            return_value=True,  # confirm REMOVE
        ):
            # top: sources → sources: pick idx 0 → source: REMOVE
            #   → sources: BACK → top: DONE
            mock_picker.append("sources")
            mock_picker.append(0)
            mock_picker.append("REMOVE")
            mock_picker.append("BACK")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert len(result.sources) == 1
        assert result.sources[0] == s2

    def test_remove_source_decline_confirm_keeps(self, mock_picker: list[object]) -> None:
        s1 = DataSource("acme", "s1", tables="*")
        p = _profile(sources=(s1,))
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor.click.confirm",
            return_value=False,  # decline REMOVE
        ):
            mock_picker.append("sources")
            mock_picker.append(0)
            mock_picker.append("REMOVE")
            mock_picker.append("BACK")
            mock_picker.append("BACK")
            mock_picker.append("DONE")
            result = edit_profile(p, client)
        assert result is not None
        assert len(result.sources) == 1


# ── multi-section edits ────────────────────────────────────────────────


def test_cancel_after_section_edits_discards_all(mock_picker: list[object]) -> None:
    """Edit multiple sections, Cancel at top → returns None.

    The draft is local to ``edit_profile``; section edits modify the
    draft variable, but Cancel returns None without committing. The
    outer caller (``update_cmd``) decides what to do with None.
    """
    p = _profile()
    client = MagicMock()
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.click.prompt",
            return_value="changed_proj",
        ),
        patch(
            "maxcompute_semantic.commands._profile_editor.click.confirm",
            return_value=True,
        ),
    ):
        mock_picker.append("compute_project")
        mock_picker.append("CANCEL")
        result = edit_profile(p, client)
    assert result is None


def test_back_at_section_preserves_no_changes(mock_picker: list[object]) -> None:
    """Back from auth sub-picker without choosing anything keeps draft."""
    p = _profile()
    client = MagicMock()
    mock_picker.append("auth")
    mock_picker.append("BACK")
    mock_picker.append("DONE")
    result = edit_profile(p, client)
    assert result is not None
    assert result == p


# ── Esc / Cancel behavior ──────────────────────────────────────────────


class TestEditProfileEscDoesNotDiscard:
    """Esc at top-level menu must NOT discard the draft — it re-renders the
    menu. Only an explicit ``❌ Cancel`` (with confirmation) discards.

    Cross-references the Esc behavior matrix in the spec at
    docs/superpowers/specs/2026-05-19-mcs-profile-picker-ux.md.
    """

    def _make_profile(self) -> object:
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            Profile,
        )

        return Profile(
            name="test",
            compute_project="proj_a",
            endpoint="http://example/api",
            auth=AkAuth(access_key_id="FooAKID", access_key_secret="abc"),
            sources=(),
            cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
            tags=(),
        )

    def test_top_level_esc_re_renders_menu_does_not_discard(self) -> None:
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top:
            # Esc → None → re-render → user picks DONE on second iteration.
            mock_top.side_effect = [None, "DONE"]
            result = edit_profile(profile, client)
        assert result is profile  # draft preserved, not discarded
        assert mock_top.call_count == 2

    def test_explicit_cancel_with_confirm_no_re_renders(self) -> None:
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top,
            patch(
                "maxcompute_semantic.commands._profile_editor.click.confirm",
                return_value=False,
            ),
        ):
            mock_top.side_effect = ["CANCEL", "DONE"]
            result = edit_profile(profile, client)
        assert result is profile
        assert mock_top.call_count == 2

    def test_explicit_cancel_with_confirm_yes_returns_none(self) -> None:
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top,
            patch(
                "maxcompute_semantic.commands._profile_editor.click.confirm",
                return_value=True,
            ),
        ):
            mock_top.return_value = "CANCEL"
            result = edit_profile(profile, client)
        assert result is None

    def test_done_returns_draft(self) -> None:
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with patch(
            "maxcompute_semantic.commands._profile_editor._top_level_select",
            return_value="DONE",
        ):
            result = edit_profile(profile, client)
        assert result is profile


class TestSectionEditorAbortPropagates:
    """Ctrl+C inside a section-editor's click.prompt must propagate, not be
    swallowed. (Today's behavior: caught and returns the unchanged draft --
    silently masks Ctrl+C so the user can't abort from a section editor.)
    """

    def _profile(self) -> Profile:
        return Profile(
            name="test",
            compute_project="proj_a",
            endpoint="http://example/api",
            auth=AkAuth(access_key_id="FooAKID", access_key_secret="abc"),
            sources=(),
            cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
            tags=(),
        )

    def test_compute_project_abort_propagates(self) -> None:
        import click as _click
        from maxcompute_semantic.commands._profile_editor import _edit_compute_project

        with (
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=_click.exceptions.Abort,
            ),
            pytest.raises(_click.exceptions.Abort),
        ):
            _edit_compute_project(self._profile())

    def test_endpoint_abort_propagates(self) -> None:
        import click as _click
        from maxcompute_semantic.commands._profile_editor import _edit_endpoint

        with (
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=_click.exceptions.Abort,
            ),
            pytest.raises(_click.exceptions.Abort),
        ):
            _edit_endpoint(self._profile())

    def test_tags_abort_propagates(self) -> None:
        import click as _click
        from maxcompute_semantic.commands._profile_editor import _edit_tags

        with (
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=_click.exceptions.Abort,
            ),
            pytest.raises(_click.exceptions.Abort),
        ):
            _edit_tags(self._profile())

    def test_cost_thresholds_abort_propagates(self) -> None:
        import click as _click
        from maxcompute_semantic.commands._profile_editor import _edit_cost_thresholds

        with (
            patch(
                "maxcompute_semantic.commands._profile_editor.click.confirm",
                side_effect=_click.exceptions.Abort,
            ),
            pytest.raises(_click.exceptions.Abort),
        ):
            _edit_cost_thresholds(self._profile())


class TestEditSourceIncludeAllListed:
    """The `✅ Include all listed tables` quick action in _edit_source."""

    def _profile_with_empty_source(self):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        return Profile(
            name="test",
            compute_project="proj_a",
            endpoint="http://example/api",
            auth=AkAuth(access_key_id="FooAKID", access_key_secret="abc"),
            sources=(DataSource(project="proj_a", schema="default", tables=()),),
            cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
            tags=(),
        )

    def _profile_with_partial_source(self):
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
            TableSpec,
        )

        return Profile(
            name="test",
            compute_project="proj_a",
            endpoint="http://example/api",
            auth=AkAuth(access_key_id="FooAKID", access_key_secret="abc"),
            sources=(
                DataSource(
                    project="proj_a",
                    schema="default",
                    tables=(TableSpec(name="t1", columns_exclude=("pii",)),),
                ),
            ),
            cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
            tags=(),
        )

    def test_empty_source_include_all_appends_all_listed(self) -> None:
        from maxcompute_semantic.commands._profile_editor import _edit_source

        profile = self._profile_with_empty_source()
        client = MagicMock()
        client.list_tables.return_value = ["t1", "t2", "t3"]
        with patch("maxcompute_semantic.commands._profile_editor._pick_choice") as mock_choice:
            mock_choice.side_effect = ["INCLUDE_ALL_LISTED", "BACK"]
            new_profile = _edit_source(profile, idx=0, client=client)
        new_tables = new_profile.sources[0].tables
        assert isinstance(new_tables, tuple)
        assert {ts.name for ts in new_tables} == {"t1", "t2", "t3"}
        assert all(ts.columns is None and not ts.columns_exclude for ts in new_tables)

    def test_partial_source_include_all_preserves_existing_scope(self) -> None:
        from maxcompute_semantic.commands._profile_editor import _edit_source

        profile = self._profile_with_partial_source()
        client = MagicMock()
        client.list_tables.return_value = ["t1", "t2", "t3"]
        with patch("maxcompute_semantic.commands._profile_editor._pick_choice") as mock_choice:
            mock_choice.side_effect = ["INCLUDE_ALL_LISTED", "BACK"]
            new_profile = _edit_source(profile, idx=0, client=client)
        new_tables = new_profile.sources[0].tables
        assert isinstance(new_tables, tuple)
        by_name = {ts.name: ts for ts in new_tables}
        assert by_name["t1"].columns_exclude == ("pii",)
        assert not by_name["t2"].columns_exclude
        assert not by_name["t3"].columns_exclude

    def test_include_all_row_hidden_when_listing_failed(self) -> None:
        """When list_tables raises, the include-all row isn't offered."""
        from maxcompute_semantic.commands._profile_editor import _edit_source
        from maxcompute_semantic.mc_client.errors import McsError

        profile = self._profile_with_empty_source()
        client = MagicMock()
        client.list_tables.side_effect = McsError(
            code="permission_denied",
            message="list_tables denied",
            remediation="Ask DBA for SHOW.",
        )
        captured_choices = []

        def _capture(question, choices, **_):
            captured_choices.append([getattr(c, "value", None) for c in choices])
            return "BACK"

        with patch(
            "maxcompute_semantic.commands._profile_editor._pick_choice",
            side_effect=_capture,
        ):
            _edit_source(profile, idx=0, client=client)
        assert "INCLUDE_ALL_LISTED" not in captured_choices[0]

    def test_include_all_row_hidden_when_no_tables_listed(self) -> None:
        from maxcompute_semantic.commands._profile_editor import _edit_source

        profile = self._profile_with_empty_source()
        client = MagicMock()
        client.list_tables.return_value = []
        captured_choices = []

        def _capture(question, choices, **_):
            captured_choices.append([getattr(c, "value", None) for c in choices])
            return "BACK"

        with patch(
            "maxcompute_semantic.commands._profile_editor._pick_choice",
            side_effect=_capture,
        ):
            _edit_source(profile, idx=0, client=client)
        assert "INCLUDE_ALL_LISTED" not in captured_choices[0]


class TestEditProfileCtrlCIsEsc:
    """Ctrl+C is treated identically to Esc: both return to the previous level.
    At the top-level menu, both are a silent no-op (re-render)."""

    def _make_profile(self) -> object:
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            Profile,
        )

        return Profile(
            name="test",
            compute_project="proj_a",
            endpoint="http://example/api",
            auth=AkAuth(access_key_id="FooAKID", access_key_secret="abc"),
            sources=(),
            cost_thresholds=CostThresholds(confirm_cny=10.0, blocked_cny=100.0),
            tags=(),
        )

    def test_ctrl_c_at_top_level_continues(self) -> None:
        """Ctrl+C at top-level menu is a no-op (same as Esc)."""
        import click as _click
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top:
            mock_top.side_effect = [
                _click.exceptions.Abort(),  # Ctrl+C -- re-render
                "DONE",  # user picks DONE
            ]
            result = edit_profile(profile, client)
        assert result is profile
        assert mock_top.call_count == 2

    def test_ctrl_c_in_section_editor_goes_back_to_menu(self) -> None:
        """Ctrl+C in a section editor's click.prompt returns to the top menu."""
        import click as _click
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top,
            patch(
                "maxcompute_semantic.commands._profile_editor.click.prompt",
                side_effect=_click.exceptions.Abort,
            ),
        ):
            mock_top.side_effect = [
                "compute_project",  # drill into section -> Ctrl+C -> back to menu
                "DONE",  # user picks DONE
            ]
            result = edit_profile(profile, client)
        assert result is profile

    def test_cancel_still_confirms_before_exit(self) -> None:
        """Explicit Cancel still prompts confirmation (exit gate)."""
        from maxcompute_semantic.commands._profile_editor import edit_profile

        profile = self._make_profile()
        client = MagicMock()
        with (
            patch("maxcompute_semantic.commands._profile_editor._top_level_select") as mock_top,
            patch(
                "maxcompute_semantic.commands._profile_editor.click.confirm",
                return_value=True,
            ),
        ):
            mock_top.return_value = "CANCEL"
            result = edit_profile(profile, client)
        assert result is None
