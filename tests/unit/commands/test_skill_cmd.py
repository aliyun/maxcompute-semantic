# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/skill.py — mcs skill CLI group (multi-platform symlink)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from maxcompute_semantic.cli import cli
from maxcompute_semantic.commands.skill import (
    _SKILL_NAME,
    _detect_platforms,
    _unique_platforms,
)

# Curated subset for parametrized per-platform tests (avoids running
# 55×4=220 tests; the dedup and registry tests cover the full list).
_CORE_PLATFORMS = [
    "agents",
    "claude-code",
    "cursor",
    "codex",
    "gemini-cli",
    "qwen-code",
    "opencode",
    "windsurf",
    "trae",
    "kiro-cli",
]


def _make_skill_root(tmp_path: Path) -> Path:
    """Create a minimal _skill/ directory structure for testing."""
    skill = tmp_path / "_skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# maxcompute-semantic skill\n")

    refs = skill / "references"
    refs.mkdir()
    (refs / "sql.md").write_text("# SQL reference\n")
    (refs / "memory.md").write_text("# Memory reference\n")

    return skill


def _patched_globals(tmp_path: Path) -> dict[str, Path]:
    """Build patched _GLOBAL_PATHS for tests (core platforms under tmp_path)."""
    return {
        "claude-code": tmp_path / "global" / ".claude" / "skills" / _SKILL_NAME,
        "cursor": tmp_path / "global" / ".cursor" / "skills" / _SKILL_NAME,
        "codex": tmp_path / "global" / ".agents" / "skills" / _SKILL_NAME,
        "gemini-cli": tmp_path / "global" / ".gemini" / "skills" / _SKILL_NAME,
        "qwen-code": tmp_path / "global" / ".qwen" / "skills" / _SKILL_NAME,
        "opencode": tmp_path / "global" / ".config" / "opencode" / "skills" / _SKILL_NAME,
        "agents": tmp_path / "global" / ".agents" / "skills" / _SKILL_NAME,
        "windsurf": tmp_path / "global" / ".codeium" / "windsurf" / "skills" / _SKILL_NAME,
        "trae": tmp_path / "global" / ".trae" / "skills" / _SKILL_NAME,
        "kiro-cli": tmp_path / "global" / ".kiro" / "skills" / _SKILL_NAME,
    }


def _patched_local_dirs() -> dict[str, str]:
    """Build patched _LOCAL_DIRS for tests (core platforms)."""
    return {
        "claude-code": ".claude/skills",
        "cursor": ".cursor/skills",
        "codex": ".agents/skills",
        "gemini-cli": ".gemini/skills",
        "qwen-code": ".qwen/skills",
        "opencode": ".opencode/skills",
        "agents": ".agents/skills",
        "windsurf": ".windsurf/skills",
        "trae": ".trae/skills",
        "kiro-cli": ".kiro/skills",
    }


class TestSkillList:
    def test_skill_list_shows_platforms(self, tmp_path: Path) -> None:
        """skill list shows each unique-path platform once. codex and
        agents collapse to the same global path (.agents/skills) and
        only one of them appears (B15: dedup behavior matches install
        / path / uninstall)."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        runner = CliRunner()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            result = runner.invoke(cli, ["skill", "list"])
        assert result.exit_code == 0
        # Exactly one of {codex, agents} is in the output (whichever
        # sorts first in dict iteration); the other dedups out.
        codex_present = "codex:" in result.output
        agents_present = "agents:" in result.output
        assert codex_present ^ agents_present, (
            "codex and agents share path .agents/skills — expect exactly one"
        )
        # Other platforms with unique paths are always present.
        for platform in ("claude-code", "cursor", "gemini-cli", "qwen-code", "opencode"):
            assert f"{platform}:" in result.output


class TestSkillInstall:
    def test_install_local_default(self, tmp_path: Path) -> None:
        """Default install creates local symlink at .claude/skills/."""
        skill_root = _make_skill_root(tmp_path)
        cwd = tmp_path / "project"
        cwd.mkdir()
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            with runner.isolated_filesystem(temp_dir=tmp_path):
                os.makedirs(".claude/skills", exist_ok=True)
                result = runner.invoke(cli, ["skill", "install"])
        assert result.exit_code == 0
        assert "installed" in result.output

    def test_install_global(self, tmp_path: Path) -> None:
        """-g installs to global (home) path for default platform (agents)."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "-g"])
        assert result.exit_code == 0
        target = gp["agents"]
        assert target.is_symlink()
        assert (target / "SKILL.md").is_file()

    def test_install_with_target(self, tmp_path: Path) -> None:
        """--target installs to custom path when basename matches SKILL_NAME."""
        skill_root = _make_skill_root(tmp_path)
        custom_target = tmp_path / "custom" / "path" / _SKILL_NAME
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        with p1:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--target", str(custom_target)])
        assert result.exit_code == 0
        assert custom_target.is_symlink()
        assert (custom_target / "SKILL.md").is_file()

    def test_install_target_auto_appends_skill_name(self, tmp_path: Path) -> None:
        """--target pointing at a 'skills' parent auto-appends maxcompute-semantic.

        Regression guard: an agent that passed `--target ~/.qoderwork/skills/`
        previously caused the install branch to ``rmtree`` the whole
        ``skills/`` directory, wiping every co-located skill. The
        normalization at the CLI boundary now appends ``_SKILL_NAME`` and
        echoes a one-line note so the symlink lands at
        ``<dir>/maxcompute-semantic`` instead.
        """
        skill_root = _make_skill_root(tmp_path)
        skills_parent = tmp_path / "qoderwork" / "skills"
        skills_parent.mkdir(parents=True)
        # Co-located peer skill that MUST survive the install.
        peer = skills_parent / "other-skill"
        peer.mkdir()
        (peer / "SKILL.md").write_text("# other skill\n")

        with patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--target", str(skills_parent)])

        assert result.exit_code == 0, result.output
        assert "does not end in" in result.output
        normalized = skills_parent / _SKILL_NAME
        assert normalized.is_symlink()
        assert (normalized / "SKILL.md").is_file()
        # Peer skill untouched.
        assert peer.is_dir()
        assert (peer / "SKILL.md").is_file()

    def test_remove_existing_refuses_non_empty_non_skill_dir(self, tmp_path: Path) -> None:
        """_remove_existing refuses to rmtree a non-empty directory whose
        basename is NOT _SKILL_NAME. Defense-in-depth for any code path
        that bypasses the CLI --target normalization (the production
        install / update / uninstall verbs all go through
        _normalize_target, but the guard means a future refactor or
        direct caller cannot silently wipe a peer-skill dir)."""
        from maxcompute_semantic.commands.skill import _remove_existing

        target = tmp_path / "skills"
        target.mkdir()
        peer = target / "other-skill"
        peer.mkdir()
        (peer / "SKILL.md").write_text("# peer skill\n")

        with pytest.raises(SystemExit) as excinfo:
            _remove_existing(target)
        assert excinfo.value.code == 1
        # Peer skill untouched.
        assert peer.is_dir()
        assert (peer / "SKILL.md").is_file()

    def test_install_refuses_non_empty_real_skill_dir(self, tmp_path: Path) -> None:
        """Install must not delete a non-empty hand-managed skill dir."""
        skill_root = _make_skill_root(tmp_path)
        target = tmp_path / "global" / ".agents" / "skills" / _SKILL_NAME
        target.mkdir(parents=True)
        old_file = target / "old_file.txt"
        old_file.write_text("old content")
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "-g"])
        assert result.exit_code == 1
        assert "refusing to overwrite" in result.output
        assert target.is_dir()
        assert not target.is_symlink()
        assert old_file.read_text() == "old content"

    def test_install_replaces_empty_real_skill_dir(self, tmp_path: Path) -> None:
        """An empty directory at the target can be safely replaced."""
        skill_root = _make_skill_root(tmp_path)
        target = tmp_path / "global" / ".agents" / "skills" / _SKILL_NAME
        target.mkdir(parents=True)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "-g"])
        assert result.exit_code == 0, result.output
        assert target.is_symlink()


class TestSkillUpdate:
    def test_update_refreshes_symlink(self, tmp_path: Path) -> None:
        """Update re-creates symlink (identical to install)."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "-g"])
            assert result.exit_code == 0
            result = runner.invoke(cli, ["skill", "update", "-g"])
        assert result.exit_code == 0


class TestSkillUninstall:
    def test_uninstall_global(self, tmp_path: Path) -> None:
        """Uninstall -g removes global symlink."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "-g"])
            result = runner.invoke(cli, ["skill", "uninstall", "-g", "--yes"])
        assert result.exit_code == 0
        assert not gp["claude-code"].exists()

    def test_uninstall_not_installed(self, tmp_path: Path) -> None:
        """Uninstall on non-existent path shows error."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "uninstall", "-g", "--yes"])
        assert result.exit_code == 1

    def test_uninstall_refuses_real_directory(self, tmp_path: Path) -> None:
        """Uninstall only owns links/junctions, not arbitrary real dirs."""
        target = tmp_path / "skills" / _SKILL_NAME
        target.mkdir(parents=True)
        keep = target / "notes.md"
        keep.write_text("handwritten\n", encoding="utf-8")

        result = CliRunner().invoke(
            cli,
            ["skill", "uninstall", "--target", str(target), "--yes"],
        )

        assert result.exit_code == 1
        assert "refusing" in result.output.lower()
        assert keep.read_text(encoding="utf-8") == "handwritten\n"


class TestSkillPath:
    def test_path_global(self, tmp_path: Path) -> None:
        """skill path -g prints global path for default platform."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "path", "-g"])
        assert result.exit_code == 0
        assert str(gp["agents"]) in result.output

    def test_path_with_target(self) -> None:
        """skill path --target prints custom path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["skill", "path", "--target", "/custom/path"])
        assert result.exit_code == 0
        assert "/custom/path" in result.output


class TestSkillDiff:
    def test_diff_symlink_matches(self, tmp_path: Path) -> None:
        """Diff shows symlink matches current package."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "-g"])
            result = runner.invoke(cli, ["skill", "diff", "-g"])
        assert result.exit_code == 0
        assert "matches" in result.output

    def test_diff_stale_symlink(self, tmp_path: Path) -> None:
        """Diff shows STALE when symlink points to old package."""
        old_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=old_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "-g"])
        new_root = _make_skill_root(tmp_path / "new_skill")
        p4 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=new_root)
        p5 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p6 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p4, p5, p6:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "diff", "-g"])
        assert result.exit_code == 0
        assert "STALE" in result.output

    def test_diff_not_installed(self, tmp_path: Path) -> None:
        """Diff on non-existent path shows error."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "diff", "-g"])
        assert result.exit_code == 1


@pytest.mark.parametrize("platform", _CORE_PLATFORMS)
class TestSkillInstallPerPlatform:
    """Verify install works for each supported platform."""

    def test_local_install(self, tmp_path: Path, platform: str) -> None:
        """--platform <X> installs symlink to correct local dir."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        cwd = tmp_path / "project"
        cwd.mkdir()
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "install", "--platform", platform, "--cwd", str(cwd)],
            )
        assert result.exit_code == 0
        local_dir = cwd / ld[platform] / _SKILL_NAME
        assert local_dir.is_symlink()
        assert local_dir.resolve() == skill_root.resolve()

    def test_global_install(self, tmp_path: Path, platform: str) -> None:
        """--platform <X> -g installs symlink to correct global path."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "install", "--platform", platform, "-g"],
            )
        assert result.exit_code == 0
        target = gp[platform]
        assert target.is_symlink()
        assert (target / "SKILL.md").is_file()

    def test_path_local(self, tmp_path: Path, platform: str) -> None:
        """skill path --platform <X> prints correct local path."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "path", "--platform", platform, "--cwd", str(cwd)],
            )
        assert result.exit_code == 0
        expected = str(cwd / ld[platform] / _SKILL_NAME)
        assert expected in result.output

    def test_diff_local(self, tmp_path: Path, platform: str) -> None:
        """skill diff --platform <X> shows matches after install."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(
                cli,
                ["skill", "install", "--platform", platform, "--cwd", str(cwd)],
            )
            result = runner.invoke(
                cli,
                ["skill", "diff", "--platform", platform, "--cwd", str(cwd)],
            )
        assert result.exit_code == 0
        assert "matches" in result.output


class TestSkillInstallAll:
    """Test --all flag installs to every unique platform directory."""

    def test_install_all_local(self, tmp_path: Path) -> None:
        """--all installs symlink into every local platform dir."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "install", "--all", "--cwd", str(cwd)],
            )
            assert result.exit_code == 0
            # Check every *unique* platform (codex == agents share path)
            for platform in _unique_platforms():
                link = cwd / ld[platform] / _SKILL_NAME
                assert link.is_symlink(), f"missing symlink for {platform}"
                assert link.resolve() == skill_root.resolve()

    def test_install_all_global(self, tmp_path: Path) -> None:
        """--all -g installs symlink into every global path."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--all", "-g"])
            assert result.exit_code == 0
            for platform in _unique_platforms():
                target = gp[platform]
                assert target.is_symlink(), f"missing global symlink for {platform}"
                assert (target / "SKILL.md").is_file()

    def test_uninstall_all_local(self, tmp_path: Path) -> None:
        """--all uninstall removes symlinks from every local dir."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "--all", "--cwd", str(cwd)])
            result = runner.invoke(
                cli,
                ["skill", "uninstall", "--all", "--cwd", str(cwd), "--yes"],
            )
            assert result.exit_code == 0
            for platform in _unique_platforms():
                link = cwd / ld[platform] / _SKILL_NAME
                assert not link.exists(), f"symlink still exists for {platform}"

    def test_path_all(self, tmp_path: Path) -> None:
        """--all path prints paths for every platform."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "path", "--all", "--cwd", str(cwd)],
            )
        assert result.exit_code == 0
        for platform in gp:
            assert platform in result.output

    def test_diff_all(self, tmp_path: Path) -> None:
        """--all diff shows matches for every installed platform."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "--all", "-g"])
            result = runner.invoke(cli, ["skill", "diff", "--all", "-g"])
        assert result.exit_code == 0
        assert "matches" in result.output

    def test_update_all(self, tmp_path: Path) -> None:
        """--all update re-symlinks every platform."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            runner.invoke(cli, ["skill", "install", "--all", "-g"])
            result = runner.invoke(cli, ["skill", "update", "--all", "-g"])
            assert result.exit_code == 0
            for platform in _unique_platforms():
                target = gp[platform]
                assert target.is_symlink()


class TestSkillDetect:
    """Test --detect flag installs only to agents present on the system."""

    def test_install_detect_global(self, tmp_path: Path) -> None:
        """--detect -g installs only to platforms with existing config dirs."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        (gp["claude-code"].parent.parent).mkdir(parents=True)
        (gp["cursor"].parent.parent).mkdir(parents=True)
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--detect", "-g"])
            assert result.exit_code == 0
            assert gp["claude-code"].is_symlink()
            assert gp["cursor"].is_symlink()
            assert not gp["gemini-cli"].exists()
            assert not gp["qwen-code"].exists()

    def test_install_detect_local(self, tmp_path: Path) -> None:
        """--detect installs only to platforms with project dot-dirs."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        cwd = tmp_path / "project"
        cwd.mkdir()
        (cwd / ".claude").mkdir()
        (cwd / ".cursor").mkdir()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["skill", "install", "--detect", "--cwd", str(cwd)],
            )
            assert result.exit_code == 0
            assert (cwd / ".claude" / "skills" / _SKILL_NAME).is_symlink()
            assert (cwd / ".cursor" / "skills" / _SKILL_NAME).is_symlink()
            assert not (cwd / ".gemini" / "skills" / _SKILL_NAME).exists()

    def test_install_detect_none_found(self, tmp_path: Path) -> None:
        """--detect exits 1 when no platforms are detected."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--detect", "-g"])
            assert result.exit_code == 1
            assert "No agent platforms detected" in result.output

    def test_list_detect(self, tmp_path: Path) -> None:
        """--detect list shows only detected platforms."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        (gp["claude-code"].parent.parent).mkdir(parents=True)
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "list", "--detect"])
            assert result.exit_code == 0
            assert "claude-code:" in result.output
            assert "cursor:" not in result.output
            assert "gemini-cli:" not in result.output

    def test_detect_with_all_same_as_detect(self, tmp_path: Path) -> None:
        """--all --detect is equivalent to --detect alone."""
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        (gp["claude-code"].parent.parent).mkdir(parents=True)
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--all", "--detect", "-g"])
            assert result.exit_code == 0
            assert gp["claude-code"].is_symlink()
            assert not gp["cursor"].exists()


class TestSkillAliases:
    """Deprecated platform names resolve and warn."""

    def test_gemini_alias_resolves(self, tmp_path: Path) -> None:
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "path", "-p", "gemini", "-g"])
            assert result.exit_code == 0
            assert str(gp["gemini-cli"]) in result.output

    def test_qwen_alias_resolves(self, tmp_path: Path) -> None:
        skill_root = _make_skill_root(tmp_path)
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp)
        p3 = patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld)
        with p1, p2, p3:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "path", "-p", "qwen", "-g"])
            assert result.exit_code == 0
            assert str(gp["qwen-code"]) in result.output

    def test_deprecated_alias_warns(self, tmp_path: Path) -> None:
        """Deprecated alias emits a warning on stderr."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "path", "-p", "gemini", "-g"])
            assert "deprecated" in result.output


class TestSkillDedupExpanded:
    """Dedup behavior with the expanded platform list."""

    def test_unique_platforms_dedups_shared_paths(self):
        """Platforms sharing the same global path collapse to one entry."""
        platforms = _unique_platforms()
        # agents and cline/codex/dexto/warp all share ~/.agents/skills/ →
        # agents is the canonical name (listed first) and the others fold
        # into it. Per CLAUDE.md's "Skill installation" table, codex's
        # global is documented as ``~/.agents/skills/`` (not ``~/.codex/``),
        # matching what the multi-agent-install CI yaml pins.
        assert "agents" in platforms
        # codex / cline / dexto / warp share ~/.agents/skills/ with agents
        # → deduped out of the canonical list (still routable via -p codex)
        assert "codex" not in platforms
        assert "cline" not in platforms
        assert "dexto" not in platforms

    def test_trae_and_trae_cn_are_separate(self):
        """trae and trae-cn have different global dirs -> both listed."""
        platforms = _unique_platforms()
        assert "trae" in platforms
        assert "trae-cn" in platforms

    def test_qoder_and_qoderwork_are_separate(self):
        """qoder (~/.qoder/skills) and qoderwork (~/.qoderwork/skills) are
        distinct products with distinct skill directories — both must be
        listed so ``--all`` / ``--detect`` covers each."""
        platforms = _unique_platforms()
        assert "qoder" in platforms
        assert "qoderwork" in platforms

    def test_detect_respects_shared_dirs(self, tmp_path: Path) -> None:
        """All .agents/skills/ platforms detected when .agents/ exists globally."""
        gp = _patched_globals(tmp_path)
        ld = _patched_local_dirs()
        (Path.home() / ".agents").mkdir(parents=True, exist_ok=True)
        with (
            patch("maxcompute_semantic.commands.skill._GLOBAL_PATHS", gp),
            patch("maxcompute_semantic.commands.skill._LOCAL_DIRS", ld),
            patch(
                "maxcompute_semantic.commands.skill._GLOBAL_PATHS",
                {
                    k: Path.home() / ".agents" / "skills" / _SKILL_NAME
                    for k in ["agents", "codex", "cline", "dexto", "warp"]
                },
            ),
        ):
            detected = _detect_platforms(Path.cwd(), True)
            assert "agents" in detected


class TestWindowsJunctionFallback:
    """Windows junction fallback when symlink creation is denied.

    Stock Windows (no Developer Mode, no admin) refuses os.symlink with
    OSError(WinError 1314). The skill installer should fall back to a
    directory junction (mklink /J), which works without elevation. The
    real ``mklink`` binary is Windows-only, so these tests mock both
    sys.platform and subprocess.run.
    """

    def test_install_falls_back_to_junction_on_windows(self, tmp_path: Path) -> None:
        """OSError on symlink → mklink /J fallback → success message."""
        skill_root = _make_skill_root(tmp_path)
        target = tmp_path / "fake-claude" / _SKILL_NAME

        from unittest.mock import MagicMock

        mklink_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            mklink_calls.append(cmd)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir()  # simulate junction-as-directory
            result = MagicMock()
            result.returncode = 0
            result.stdout = f"Junction created for {target} <<===>> {skill_root.resolve()}"
            result.stderr = ""
            return result

        def fake_symlink(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError(1314, "A required privilege is not held by the client")

        # Pretend we're on Windows so the fallback branch triggers.
        # Patch _is_junction so the post-mklink output formatting works
        # without needing real Win32 reparse-point attributes.
        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill.sys.platform", "win32")
        p3 = patch("maxcompute_semantic.commands.skill.os.symlink", side_effect=fake_symlink)
        p4 = patch("subprocess.run", side_effect=fake_run)
        with p1, p2, p3, p4:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--target", str(target)])
        assert result.exit_code == 0, result.output
        assert "directory junction" in result.output
        assert "symlink unavailable" in result.output
        # subprocess.run was called with mklink /J <target> <source>
        assert len(mklink_calls) == 1
        cmd = mklink_calls[0]
        assert cmd[:3] == ["cmd", "/c", "mklink"]
        assert cmd[3] == "/J"
        assert cmd[4] == str(target)
        assert cmd[5] == str(skill_root.resolve())

    def test_install_errors_when_both_fail_on_windows(self, tmp_path: Path) -> None:
        """Both symlink AND mklink failing surfaces both error messages."""
        skill_root = _make_skill_root(tmp_path)
        target = tmp_path / "fake-claude" / _SKILL_NAME

        from unittest.mock import MagicMock

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "Local volumes are required to complete the operation."
            return result

        def fake_symlink(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError(1314, "A required privilege is not held by the client")

        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill.sys.platform", "win32")
        p3 = patch("maxcompute_semantic.commands.skill.os.symlink", side_effect=fake_symlink)
        p4 = patch("subprocess.run", side_effect=fake_run)
        with p1, p2, p3, p4:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--target", str(target)])
        assert result.exit_code == 1
        assert "junction fallback" in result.output
        assert "Local volumes" in result.output
        # Hint about Developer Mode / Administrator surfaces only when
        # both attempts fail (the typical "different drive" case).
        assert "Developer Mode" in result.output or "Administrator" in result.output

    def test_install_posix_symlink_failure_still_errors(self, tmp_path: Path) -> None:
        """Non-Windows symlink failure must NOT trigger junction fallback."""
        skill_root = _make_skill_root(tmp_path)
        target = tmp_path / "fake-target" / _SKILL_NAME

        run_called = []

        def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
            run_called.append(args)
            raise AssertionError("subprocess.run should not be called on POSIX")

        def fake_symlink(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("permission denied")

        p1 = patch("maxcompute_semantic.commands.skill._skill_root", return_value=skill_root)
        p2 = patch("maxcompute_semantic.commands.skill.sys.platform", "linux")
        p3 = patch("maxcompute_semantic.commands.skill.os.symlink", side_effect=fake_symlink)
        p4 = patch("subprocess.run", side_effect=fake_run)
        with p1, p2, p3, p4:
            runner = CliRunner()
            result = runner.invoke(cli, ["skill", "install", "--target", str(target)])
        assert result.exit_code == 1
        assert "symlink failed" in result.output
        assert run_called == []


class TestSkillRuntimeCommands:
    def test_skill_get_outputs_named_runtime_skill(self, tmp_path: Path) -> None:
        root = tmp_path / "_skill_data"
        query = root / "query"
        query.mkdir(parents=True)
        (query / "SKILL.md").write_text(
            "---\nname: query\ndescription: Query flow.\n---\n\n# Query Runtime\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"MCS_SKILL_DATA_DIR": str(root)}):
            result = CliRunner().invoke(cli, ["skill", "get", "query"])
        assert result.exit_code == 0, result.output
        assert "name: query" in result.output
        assert "# Query Runtime" in result.output

    def test_skill_get_full_includes_references(self, tmp_path: Path) -> None:
        root = tmp_path / "_skill_data"
        query = root / "query"
        refs = query / "references"
        refs.mkdir(parents=True)
        (query / "SKILL.md").write_text(
            "---\nname: query\ndescription: Query flow.\n---\n\n# Query\n",
            encoding="utf-8",
        )
        (refs / "cold-start.md").write_text("# Cold Start\n", encoding="utf-8")
        with patch.dict(os.environ, {"MCS_SKILL_DATA_DIR": str(root)}):
            result = CliRunner().invoke(cli, ["skill", "get", "query", "--full"])
        assert result.exit_code == 0, result.output
        assert "--- references/cold-start.md ---" in result.output
        assert "# Cold Start" in result.output

    def test_skill_catalog_json_lists_runtime_skills(self, tmp_path: Path) -> None:
        root = tmp_path / "_skill_data"
        query = root / "query"
        query.mkdir(parents=True)
        (query / "SKILL.md").write_text(
            "---\nname: query\ndescription: Query flow.\n---\n\n# Query\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"MCS_SKILL_DATA_DIR": str(root)}):
            result = CliRunner().invoke(cli, ["skill", "catalog", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == {
            "success": True,
            "data": [{"name": "query", "description": "Query flow."}],
        }

    def test_skill_catalog_honors_global_json_format(self, tmp_path: Path) -> None:
        root = tmp_path / "_skill_data"
        query = root / "query"
        query.mkdir(parents=True)
        (query / "SKILL.md").write_text(
            "---\nname: query\ndescription: Query flow.\n---\n\n# Query\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"MCS_SKILL_DATA_DIR": str(root)}):
            result = CliRunner().invoke(cli, ["-f", "json", "skill", "catalog"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == {
            "status": "success",
            "data": {"skills": [{"name": "query", "description": "Query flow."}]},
        }

    def test_skill_get_honors_global_json_format(self, tmp_path: Path) -> None:
        root = tmp_path / "_skill_data"
        query = root / "query"
        query.mkdir(parents=True)
        (query / "SKILL.md").write_text(
            "---\nname: query\ndescription: Query flow.\n---\n\n# Query Runtime\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"MCS_SKILL_DATA_DIR": str(root)}):
            result = CliRunner().invoke(cli, ["-f", "json", "skill", "get", "query"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == {
            "status": "success",
            "data": {
                "skills": [
                    {
                        "name": "query",
                        "content": (
                            "---\nname: query\ndescription: Query flow.\n---\n\n# Query Runtime\n"
                        ),
                    }
                ]
            },
        }
