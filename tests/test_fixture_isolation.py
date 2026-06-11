# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for shared test-environment isolation."""

from __future__ import annotations

from pathlib import Path


def test_isolated_config_sandboxes_home_and_skill_global_paths(
    isolated_config: Path,
) -> None:
    """Tests must not read the developer's real global skill installs."""
    sandbox_home = isolated_config / "home"
    sandbox_xdg_config = isolated_config / "xdg-config"

    assert Path.home() == sandbox_home

    from maxcompute_semantic.commands import skill

    assert skill._XDG_CONFIG_HOME == sandbox_xdg_config
    assert all(
        path.is_relative_to(sandbox_home) or path.is_relative_to(sandbox_xdg_config)
        for path in skill._GLOBAL_PATHS.values()
    )
