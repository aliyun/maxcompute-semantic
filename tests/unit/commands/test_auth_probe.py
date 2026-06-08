# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for commands/_auth_probe._run_auth_test.

Focused on Step 3 (the SELECT 1 probe) — Steps 1 and 2 are covered via
the wizard integration tests in test_profile_create.py.

The fallback was added after a docs review of MaxCompute Query
Accelerator (MCQA v1 / MaxQA 2.0): the interactive channel needs a
configured interactive quota on packaged annual subscriptions, and the
old probe surfaced a quota-config gap as a fake "auth broken" verdict.
The two-stage probe (interactive first, batch second) keeps the
sub-second happy path while not false-negativing a working AK on
projects without interactive quota.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.schema import AkAuth, Profile
from maxcompute_semantic.commands._auth_probe import _run_auth_test
from maxcompute_semantic.mc_client.errors import McsError


def _profile() -> Profile:
    return Profile(
        name="t",
        compute_project="p",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
    )


def _mk_renderer() -> Renderer:
    return Renderer(format="plain")


def test_interactive_path_happy() -> None:
    """Step 3 succeeds on the interactive channel — no fallback attempted."""
    with (
        patch("maxcompute_semantic.commands._auth_probe.resolve_credentials"),
        patch("maxcompute_semantic.commands._auth_probe.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands._auth_probe.MaxComputeClient") as mock_client_cls,
    ):
        client = MagicMock()
        client.execute_sql.return_value = MagicMock(status="success")
        mock_client_cls.return_value = client

        rc = _run_auth_test(_profile(), _mk_renderer())

    assert rc == 0
    assert client.execute_sql.call_count == 1
    kwargs = client.execute_sql.call_args.kwargs
    assert kwargs["use_interactive"] is True


def test_interactive_fails_batch_succeeds_returns_zero() -> None:
    """Interactive errors (e.g. missing quota) fall back to batch SELECT 1.

    When batch succeeds the overall probe still returns 0 — the AK is fine,
    only the interactive quota is missing. This is the regression the
    fallback was added to prevent.
    """
    with (
        patch("maxcompute_semantic.commands._auth_probe.resolve_credentials"),
        patch("maxcompute_semantic.commands._auth_probe.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands._auth_probe.MaxComputeClient") as mock_client_cls,
    ):
        client = MagicMock()
        client.execute_sql.side_effect = [
            McsError("no interactive quota configured", code="QuotaMissing", exit_code=2),
            MagicMock(status="success"),
        ]
        mock_client_cls.return_value = client

        rc = _run_auth_test(_profile(), _mk_renderer())

    assert rc == 0
    assert client.execute_sql.call_count == 2
    first_call_kwargs = client.execute_sql.call_args_list[0].kwargs
    second_call_kwargs = client.execute_sql.call_args_list[1].kwargs
    assert first_call_kwargs["use_interactive"] is True
    assert second_call_kwargs["use_interactive"] is False


def test_both_channels_fail_returns_exit_code_from_batch() -> None:
    """If batch also fails, surface the batch error — that's the real verdict.

    The interactive error is kept around for a parenthetical note in the
    text output but doesn't drive the exit code (the batch error is the
    more authoritative "the AK actually can't do anything" signal).
    """
    with (
        patch("maxcompute_semantic.commands._auth_probe.resolve_credentials"),
        patch("maxcompute_semantic.commands._auth_probe.get_tier", return_value="2"),
        patch("maxcompute_semantic.commands._auth_probe.MaxComputeClient") as mock_client_cls,
    ):
        client = MagicMock()
        client.execute_sql.side_effect = [
            McsError("interactive denied", code="A", exit_code=3),
            McsError("ak rejected", code="AuthFailed", exit_code=7),
        ]
        mock_client_cls.return_value = client

        rc = _run_auth_test(_profile(), _mk_renderer())

    assert rc == 7
    assert client.execute_sql.call_count == 2
