"""Tests for commands/link.py — link / status / unlink / bind."""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import DataSource, ProcessAuth, Profile
from maxcompute_semantic.commands.link import link_group


def _process_profile(name: str = "meta-dev") -> Profile:
    return Profile(
        name=name,
        compute_project="meta_dev",
        endpoint="http://service-corp.odps.aliyun-inc.com/api",
        auth=ProcessAuth(
            command="ncs create credential odpsuser --employee-id 1 -o template -t odpscmd"
        ),
        sources=(DataSource(project="meta_dev", schema="default", tables="*"),),
    )


def _invoke(
    isolated_config: Path, args: list[str], obj: dict | None = None, input: str | None = None
) -> object:
    runner = CliRunner()
    return runner.invoke(link_group, args, obj=obj, input=input)


def test_link_status_no_binding(isolated_config: Path) -> None:
    result = _invoke(isolated_config, ["status"])
    assert result.exit_code == 0
    assert "no binding" in result.output


def test_link_status_existing(isolated_config: Path) -> None:
    upsert(_process_profile())
    from maxcompute_semantic.auth.link_store import set_link

    cwd = os.getcwd()
    set_link(cwd, "meta-dev")
    result = _invoke(isolated_config, ["status"])
    assert result.exit_code == 0
    assert "meta-dev" in result.output


def test_link_with_name(isolated_config: Path) -> None:
    upsert(_process_profile())
    result = _invoke(isolated_config, ["bind", "meta-dev"])
    assert result.exit_code == 0
    assert "meta-dev" in result.output
    # Verify binding was created
    from maxcompute_semantic.auth.link_store import get_link

    assert get_link(os.getcwd()) == "meta-dev"


def test_link_no_profiles_errors(isolated_config: Path) -> None:
    # Bare link invocation with no profiles configured should error
    result = _invoke(isolated_config, [])
    assert result.exit_code == 3


def test_link_missing_profile_errors(isolated_config: Path) -> None:
    # Try to bind to nonexistent profile
    result = _invoke(isolated_config, ["bind", "ghost"])
    assert result.exit_code == 3


def test_link_interactive(isolated_config: Path, mock_picker: list[object]) -> None:
    """Bare invocation prompts via fzf picker and binds the chosen profile."""
    upsert(_process_profile())
    mock_picker.append("meta-dev")
    result = _invoke(isolated_config, [])
    assert result.exit_code == 0
    assert "meta-dev" in result.output


def test_link_unlink(isolated_config: Path) -> None:
    upsert(_process_profile())
    from maxcompute_semantic.auth.link_store import set_link

    cwd = os.getcwd()
    set_link(cwd, "meta-dev")
    # Unlink
    result = _invoke(isolated_config, ["unlink"])
    assert result.exit_code == 0
    # Verify binding was removed
    from maxcompute_semantic.auth.link_store import get_link

    assert get_link(cwd) is None


def test_link_status_stale_profile(isolated_config: Path) -> None:
    """Link status shows 'stale' when bound profile no longer exists."""
    from maxcompute_semantic.auth.link_store import set_link

    cwd = os.getcwd()
    set_link(cwd, "deleted-profile")
    result = _invoke(isolated_config, ["status"])
    assert result.exit_code == 0
    assert "stale" in result.output or "no longer exists" in result.output


def test_cwd_unavailable_raises_working_directory_error(monkeypatch) -> None:
    """When os.getcwd() raises OSError, _cwd() raises WorkingDirectoryError."""
    monkeypatch.setattr("os.getcwd", lambda: (_ for _ in ()).throw(OSError("cwd unlinked")))
    result = _invoke(Path("/tmp"), ["status"])
    assert result.exit_code != 0


class TestLinkQuiet:
    def test_bind_quiet_outputs_profile_name(self, isolated_config: Path) -> None:
        """link bind -q: just the profile name."""
        upsert(_process_profile())
        result = _invoke(
            isolated_config, ["bind", "meta-dev"], obj={"format": "plain", "quiet": True}
        )
        assert result.exit_code == 0
        assert result.output.strip() == "meta-dev"

    def test_status_quiet_outputs_profile_name(self, isolated_config: Path) -> None:
        """link status -q: just the bound profile name."""
        upsert(_process_profile())
        from maxcompute_semantic.auth.link_store import set_link

        cwd = os.getcwd()
        set_link(cwd, "meta-dev")
        result = _invoke(isolated_config, ["status"], obj={"format": "plain", "quiet": True})
        assert result.exit_code == 0
        assert result.output.strip() == "meta-dev"

    def test_status_quiet_no_binding_outputs_none(self, isolated_config: Path) -> None:
        """link status -q with no binding: outputs 'none'."""
        result = _invoke(isolated_config, ["status"], obj={"format": "plain", "quiet": True})
        assert result.exit_code == 0
        assert result.output.strip() == "none"
