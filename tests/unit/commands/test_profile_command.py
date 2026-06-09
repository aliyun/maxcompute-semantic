"""Tests for ``commands/_profile_command.py`` — the
``@profile_command`` decorator.

Verifies the click flag triple is registered, the read vs write
paths run ``reject_if_fork`` correctly, ``McsError`` propagates
through the decorator's ladder with the right exit code, and the
``commit_after_command`` hook fires only when ``action`` is set
*and* the body wrote a ``commit_summary`` via ``pctx.success``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import click
from click.testing import CliRunner
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.errors.base import ErrorCode, McsError


def _make_profile(name: str = "alpha", project: str = "acme_warehouse") -> Profile:
    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service-corp.odps.aliyun-inc.com/api",
        auth=AkAuth(access_key_id="FAKE", access_key_secret="SECRET"),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def _runner_invoke(cmd: click.BaseCommand, args: list[str]) -> Any:
    runner = CliRunner()

    @click.group()
    @click.pass_context
    def root(ctx: click.Context) -> None:
        ctx.ensure_object(dict)
        ctx.obj["format"] = "json"
        ctx.obj["quiet"] = False

    root.add_command(cmd)
    return runner.invoke(root, [cmd.name, *args])


class TestFlagRegistration:
    def test_default_registers_full_triple(self) -> None:
        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "foo")
        def foo(pctx: ProfileContext) -> None:
            pass

        params = {p.name for p in grp.commands["foo"].params}
        assert {"project", "profile", "schema"}.issubset(params)

    def test_accepts_schema_false_drops_schema(self) -> None:
        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "bar", accepts_schema=False)
        def bar(pctx: ProfileContext) -> None:
            pass

        params = {p.name for p in grp.commands["bar"].params}
        assert "schema" not in params
        assert {"project", "profile"}.issubset(params)


class TestReadVerb:
    def test_read_verb_no_reject_if_fork(self, isolated_config: Path) -> None:
        """No ``action`` ⇒ writeable=False ⇒ reject_if_fork not called."""
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "show")
        def show(pctx: ProfileContext) -> None:
            pctx.success({"name": pctx.profile.name})

        with patch("maxcompute_semantic.auth.context.ProfileContext.reject_if_fork") as reject:
            result = _runner_invoke(grp.commands["show"], ["--profile", "alpha"])

        assert result.exit_code == 0, result.output
        reject.assert_not_called()

    def test_read_verb_no_commit(self, isolated_config: Path) -> None:
        """No ``action`` ⇒ commit_after_command never fires, even on success."""
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "show")
        def show(pctx: ProfileContext) -> None:
            pctx.success({"ok": True}, commit_summary="this should be ignored")

        with patch("maxcompute_semantic.commands._profile_command.commit_after_command") as commit:
            result = _runner_invoke(grp.commands["show"], ["--profile", "alpha"])

        assert result.exit_code == 0, result.output
        commit.assert_not_called()


class TestWriteVerb:
    def test_write_verb_runs_reject_if_fork(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "do_it", action="memory")
        def do_it(pctx: ProfileContext) -> None:
            pctx.success({"id": 1}, commit_summary="do_it 1")

        with (
            patch("maxcompute_semantic.auth.context.ProfileContext.reject_if_fork") as reject,
            patch("maxcompute_semantic.commands._profile_command.commit_after_command") as commit,
        ):
            result = _runner_invoke(grp.commands["do_it"], ["--profile", "alpha"])

        assert result.exit_code == 0, result.output
        reject.assert_called_once()
        commit.assert_called_once()
        kwargs = commit.call_args.kwargs
        assert kwargs["action"] == "memory"
        assert kwargs["summary"] == "do_it 1"

    def test_write_verb_skips_commit_when_summary_missing(self, isolated_config: Path) -> None:
        """``action`` set but body never wrote a commit_summary →
        no commit (verb chose not to mark the run committable)."""
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "do_it", action="memory")
        def do_it(pctx: ProfileContext) -> None:
            pctx.success({"id": 1})  # no commit_summary

        with (
            patch("maxcompute_semantic.auth.context.ProfileContext.reject_if_fork"),
            patch("maxcompute_semantic.commands._profile_command.commit_after_command") as commit,
        ):
            result = _runner_invoke(grp.commands["do_it"], ["--profile", "alpha"])

        assert result.exit_code == 0, result.output
        commit.assert_not_called()


class TestErrorLadder:
    def test_resolution_error_emits_envelope_and_exits(self, isolated_config: Path) -> None:
        """``--profile ghost`` raises ProfileNotFoundError before
        the body runs; decorator catches and exits with the McsError
        exit code."""

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "show")
        def show(pctx: ProfileContext) -> None:
            raise AssertionError("body should not run")

        result = _runner_invoke(grp.commands["show"], ["--profile", "ghost"])
        assert result.exit_code != 0
        payload = json.loads(result.output)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "ProfileNotFound"

    def test_body_mcserror_propagates_through_decorator(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "fail")
        def fail(pctx: ProfileContext) -> None:
            raise McsError("nope", code=ErrorCode.AUTH_FAILED, exit_code=4)

        result = _runner_invoke(grp.commands["fail"], ["--profile", "alpha"])
        assert result.exit_code == 4
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "AuthFailed"

    def test_body_mcserror_skips_commit(self, isolated_config: Path) -> None:
        """A write verb whose body raises must not fire the commit hook."""
        upsert(_make_profile("alpha"))

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "fail", action="memory")
        def fail(pctx: ProfileContext) -> None:
            raise McsError("nope", code=ErrorCode.AUTH_FAILED, exit_code=4)

        with (
            patch("maxcompute_semantic.auth.context.ProfileContext.reject_if_fork"),
            patch("maxcompute_semantic.commands._profile_command.commit_after_command") as commit,
        ):
            result = _runner_invoke(grp.commands["fail"], ["--profile", "alpha"])

        assert result.exit_code == 4
        commit.assert_not_called()


class TestSchemaPassthrough:
    def test_schema_flag_lands_on_pctx(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        captured: dict[str, str | None] = {}

        @click.group()
        def grp() -> None:
            pass

        @profile_command(grp, "show")
        def show(pctx: ProfileContext) -> None:
            captured["schema"] = pctx.schema_override
            pctx.success({"ok": True})

        result = _runner_invoke(
            grp.commands["show"], ["--profile", "alpha", "--schema", "my_schema"]
        )
        assert result.exit_code == 0, result.output
        assert captured["schema"] == "my_schema"
