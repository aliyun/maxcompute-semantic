# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

import logging

import pytest
from maxcompute_semantic.versioning import env as env_mod
from maxcompute_semantic.versioning.env import (
    is_git_available,
    is_versioning_disabled,
    warn_git_missing_once,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("garbage", False),
        ("  1  ", True),  # whitespace tolerated by .strip()
        ("  true\n", True),  # trailing newline tolerated
    ],
)
def test_truthy_falsy_matrix(value: str, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCS_NO_VERSIONING", value)
    assert is_versioning_disabled() is expected


def test_unset_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    assert is_versioning_disabled() is False


def test_is_git_available_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutil.which finds git → returns True."""
    monkeypatch.setattr(
        "maxcompute_semantic.versioning.env.shutil.which",
        lambda name: "/usr/bin/git",
    )
    assert is_git_available() is True


def test_is_git_available_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutil.which returns None → returns False."""
    monkeypatch.setattr(
        "maxcompute_semantic.versioning.env.shutil.which",
        lambda name: None,
    )
    assert is_git_available() is False


def test_warn_git_missing_once_emits_then_debugs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """First call → WARNING; subsequent calls → DEBUG only."""
    # Reset the module-level latch so the test is independent of order.
    monkeypatch.setattr(env_mod, "_git_missing_warned", False, raising=False)

    with caplog.at_level(logging.DEBUG, logger=env_mod.log.name):
        warn_git_missing_once()
        warn_git_missing_once()
        warn_git_missing_once()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert "git binary not found" in warnings[0].message
    assert "MCS_NO_VERSIONING=1" in warnings[0].message
    assert len(debugs) == 2
    assert all("git still missing" in r.message for r in debugs)
