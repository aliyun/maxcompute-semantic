# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for cli.py -- global flags + group registration."""

from __future__ import annotations

import contextlib
import json
import sys as _sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from maxcompute_semantic.cli import _cli_main, cli


def test_version_flag(isolated_config: Path) -> None:
    from maxcompute_semantic import __version__

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "mcs" in result.output
    assert __version__ in result.output


def test_help_lists_subcommand_groups(isolated_config: Path) -> None:
    """The top-level ``mcs --help`` output lists the command
    groups the CLI registers. After the post-v0.4 cleanup:

      - ``profile``: the saved-profile lifecycle (``list`` /
        ``show`` / ``create`` / ``update`` / ``remove`` / the
        live-identity ``whoami`` / the agent-discovery
        ``spec-template`` / the onboarding-shortcut
        ``import-creds`` / the cross-machine ``export`` and
        ``import``). The ``use`` verb that wrote a machine-
        global default is gone — see the CHANGELOG.

      - ``link``: the per-cwd active-profile binding
        (``bind`` / ``status`` / ``unlink``).

      - ``meta``: the new top-level catalog-discovery group
        promoted out of ``mcs sql``'s sub-group hierarchy. The
        eight verbs (``list-projects`` / ``list-schemas`` /
        ``list-tables`` / ``describe-table`` / ``search-tables``
        / ``search-columns`` / ``list-partitions`` /
        ``freshness``) cover the four-tier catalog hierarchy
        uniformly.

      - ``sql``: now narrowly the three execution verbs
        (``execute`` / ``cost`` / ``explain``); the metadata
        verbs were factored out into ``meta``.

      - ``memory`` / ``udf`` / ``skill``: the
        domain-specific groups.

    The ``auth`` group that v0.4 carried (``whoami`` + ``test``)
    is gone; the identity verb lives under ``profile`` as
    ``mcs profile whoami`` and the 3-step credential probe that
    ``auth test`` exposed is a private helper now
    (``commands._auth_probe._run_auth_test``), invoked from
    inside the create / update wizards but not surfaced as a
    CLI command. The negative-assert pins the absence.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "profile" in result.output
    assert "link" in result.output
    # New top-level catalog-discovery group introduced when the
    # ``mcs sql meta`` sub-group was promoted out of ``sql`` and
    # the two ``mcs profile list-projects`` / ``list-schemas``
    # verbs were graduated up to join it.
    assert "meta" in result.output
    # Group-removal negative-asserts from the prior cleanup.
    output_tokens = result.output.lower().split()
    assert "auth" not in output_tokens


def test_profile_subcommand_reachable(isolated_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["profile", "list"])
    assert result.exit_code == 0  # empty list, but command runs


def test_link_subcommand_status_reachable(isolated_config: Path, monkeypatch) -> None:
    monkeypatch.chdir(isolated_config)
    runner = CliRunner()
    result = runner.invoke(cli, ["link", "status"])
    assert result.exit_code == 0


def test_global_format_flag_propagates(isolated_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--format", "json", "profile", "list"])
    assert result.exit_code == 0
    import json

    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_global_debug_flag_sets_logger_level(isolated_config: Path) -> None:
    import logging

    runner = CliRunner()
    runner.invoke(cli, ["--debug", "profile", "list"])
    # Logger level should be DEBUG after --debug flag
    assert logging.getLogger("maxcompute_semantic").level == logging.DEBUG


def test_global_json_format_sets_logger_error_level(isolated_config: Path) -> None:
    """--format json should set logger level to ERROR (not the default WARNING)."""
    import logging

    runner = CliRunner()
    runner.invoke(cli, ["--format", "json", "profile", "list"])
    assert logging.getLogger("maxcompute_semantic").level == logging.ERROR


def test_global_quiet_flag_propagates(isolated_config: Path) -> None:
    """--quiet flag should propagate to ctx.obj['quiet'] and suppress output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--quiet", "profile", "list"])
    assert result.exit_code == 0


class TestHoistGlobalFlags:
    """Pre-tokenization argv rewriter that moves global flags placed
    after the subcommand to the front so ``mcs show -f json`` is
    equivalent to ``mcs -f json show``. Agents (Sonnet, qwen, glm)
    routinely transpose the flag — historical benchmark transcripts
    showed cases where the wrong order forced a 43 KB
    YAML fallback and the agent guessed the wrong primary identifier.
    """

    def test_short_format_flag_after_subcommand_hoisted(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["show", "-f", "json", "--table", "T"])
        assert result == ["-f", "json", "show", "--table", "T"]

    def test_long_format_flag_after_subcommand_hoisted(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["show", "--format", "json", "--table", "T"])
        assert result == ["--format", "json", "show", "--table", "T"]

    def test_equals_form_hoisted_as_single_token(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["meta", "--format=json", "list-tables"])
        assert result == ["--format=json", "meta", "list-tables"]

    def test_global_flag_before_subcommand_left_in_place(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["-f", "json", "show", "--table", "T"])
        assert result == ["-f", "json", "show", "--table", "T"]

    def test_global_flag_before_subcommand_keeps_value_when_later_options_exist(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["-f", "json", "link", "status", "--verbose"])
        assert result == ["-f", "json", "link", "status", "--verbose"]

    def test_command_specific_verbose_is_not_hoisted(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["link", "status", "--verbose"])
        assert result == ["link", "status", "--verbose"]

    def test_root_verbose_after_command_without_verbose_is_still_hoisted(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["profile", "list", "--verbose"])
        assert result == ["--verbose", "profile", "list"]

    def test_subcommand_only_flags_pass_through(self) -> None:
        """Per-command options like ``--table`` / ``--profile`` are
        not touched."""
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["show", "--table", "T", "--profile", "P"])
        assert result == ["show", "--table", "T", "--profile", "P"]

    def test_quiet_and_debug_after_subcommand_hoisted(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["sql", "execute", "--quiet", "--debug", "select 1"])
        # ``execute`` is the subcommand; ``sql`` is the group.
        # Both global flags hoist to before ``sql``.
        assert result == [
            "--quiet",
            "--debug",
            "sql",
            "execute",
            "select 1",
        ]

    def test_double_dash_terminates_hoist_scan(self) -> None:
        """Tokens after ``--`` are positional by POSIX convention and
        must not be hoisted even if they look like flags."""
        from maxcompute_semantic.cli import _hoist_global_flags

        result = _hoist_global_flags(["sql", "execute", "--", "-f", "json"])
        assert result == ["sql", "execute", "--", "-f", "json"]

    def test_no_args_returns_unchanged(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        assert _hoist_global_flags([]) == []

    def test_only_subcommand_returns_unchanged(self) -> None:
        from maxcompute_semantic.cli import _hoist_global_flags

        assert _hoist_global_flags(["status"]) == ["status"]

    def test_format_after_subcommand_actually_works_e2e(self, isolated_config: Path) -> None:
        """End-to-end: ``mcs profile list -f json`` (wrong order) now
        produces JSON envelope just like ``mcs -f json profile list``."""
        import sys

        from maxcompute_semantic.cli import _cli_main

        old_argv = sys.argv[:]
        try:
            sys.argv = ["mcs", "profile", "list", "-f", "json"]
            try:
                _cli_main()
            except SystemExit as e:
                assert e.code == 0
        finally:
            sys.argv = old_argv


def test_update_subcommand_is_registered() -> None:
    """``mcs --help`` lists ``update``, and ``mcs update --help`` returns
    cleanly with the documented flags."""
    runner = CliRunner()
    top = runner.invoke(cli, ["--help"])
    assert top.exit_code == 0
    assert "update" in top.output

    sub = runner.invoke(cli, ["update", "--help"])
    assert sub.exit_code == 0
    assert "--check" in sub.output
    assert "--no-check" in sub.output
    assert "--version" in sub.output
    # The verb's short description shows up in `mcs --help`'s
    # one-line column.
    # (Click renders the docstring's first paragraph by default.)
    assert "wheel" in sub.output.lower() or "reinstall" in sub.output.lower()


def test_package_command_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["package", "--help"])
    assert result.exit_code == 0
    assert "Semantic package proposal workflow" in result.output


def test_metric_command_registered_and_ordered_with_semantic_commands() -> None:
    """Top-level metrics are a semantic-package surface, not a rare admin verb."""
    from maxcompute_semantic.cli import _COMMAND_ORDER

    runner = CliRunner()
    result = runner.invoke(cli, ["metric", "--help"])
    assert result.exit_code == 0
    assert "Manage profile-level" in result.output
    assert "metric" in _COMMAND_ORDER
    assert _COMMAND_ORDER.index("package") < _COMMAND_ORDER.index("metric")
    assert _COMMAND_ORDER.index("metric") < _COMMAND_ORDER.index("memory")


def test_update_subcommand_ordering() -> None:
    """The command-ordering list places ``update`` at the bottom-right
    of the help, alongside ``doctor``, since both are
    maintenance/diagnostic verbs the user reaches for rarely."""
    from maxcompute_semantic.cli import _COMMAND_ORDER

    assert "update" in _COMMAND_ORDER
    # The relative position: ``update`` should come right before
    # ``doctor`` so the help screen reads "...skill / udf / update /
    # doctor" — both are infrequent administrative verbs and they sit
    # together at the end of the menu. ``doctor`` stays last because
    # it's the canonical "is everything OK" diagnostic.
    assert _COMMAND_ORDER.index("update") < _COMMAND_ORDER.index("doctor")
    assert _COMMAND_ORDER.index("update") == _COMMAND_ORDER.index("doctor") - 1


def test_banner_renders_on_stderr_when_cache_says_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_config: Path,
) -> None:
    """With a primed cache and stderr looking like a TTY, the banner
    appears on stderr after a successful command dispatch."""
    cdir = tmp_path / "cache"
    monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
    monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_sys.stderr, "isatty", lambda: True)

    from maxcompute_semantic._internal.update_check import (
        CacheEntry,
        _utcnow_iso,
        write_cache,
    )

    write_cache(
        CacheEntry(
            checked_at=_utcnow_iso(),
            current_at_check="0.4.0a38",
            latest_version="9.9.9",
            wheel_url=(
                "https://files.pythonhosted.org/packages/py3/maxcompute_semantic-9.9.9-py3-none-any.whl"
            ),
            min_supported="0.4.0",
            disabled=(),
            notice="",
            fetch_error="",
        )
    )

    monkeypatch.setattr(_sys, "argv", ["mcs", "--help"])
    with pytest.raises(SystemExit):
        _cli_main()
    captured = capsys.readouterr()
    assert "new release" not in captured.err.lower()

    monkeypatch.setattr(_sys, "argv", ["mcs", "profile", "list"])
    capsys.readouterr()  # clear
    with contextlib.suppress(SystemExit):
        _cli_main()
    captured2 = capsys.readouterr()
    assert "mcs" in captured2.err.lower()
    assert "9.9.9" in captured2.err
    assert "mcs update" in captured2.err.lower()


def test_banner_suppressed_under_quiet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_config: Path,
) -> None:
    cdir = tmp_path / "cache"
    monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
    monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_sys.stderr, "isatty", lambda: True)

    from maxcompute_semantic._internal.update_check import (
        CacheEntry,
        _utcnow_iso,
        write_cache,
    )

    write_cache(
        CacheEntry(
            checked_at=_utcnow_iso(),
            current_at_check="0.4.0a38",
            latest_version="9.9.9",
            wheel_url="",
            min_supported="0.4.0",
            disabled=(),
            notice="",
            fetch_error="",
        )
    )

    monkeypatch.setattr(_sys, "argv", ["mcs", "-q", "profile", "list"])
    with contextlib.suppress(SystemExit):
        _cli_main()
    captured = capsys.readouterr()
    assert "9.9.9" not in captured.err
    assert "new release" not in captured.err.lower()


def test_hard_block_overrides_command_exit_code_to_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_config: Path,
) -> None:
    """When the cache says the running version is disabled, the wrapped
    entry point exits 2 regardless of what the inner command would
    have exited with."""
    cdir = tmp_path / "cache"
    monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
    monkeypatch.setenv("MCS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(_sys.stderr, "isatty", lambda: False)

    from maxcompute_semantic import __version__
    from maxcompute_semantic._internal.update_check import (
        CacheEntry,
        _utcnow_iso,
        write_cache,
    )

    write_cache(
        CacheEntry(
            checked_at=_utcnow_iso(),
            current_at_check=__version__,
            latest_version="9.9.9",
            wheel_url=(
                "https://files.pythonhosted.org/packages/py3/maxcompute_semantic-9.9.9-py3-none-any.whl"
            ),
            min_supported="9.0.0",
            disabled=(),
            notice="",
            fetch_error="",
        )
    )

    monkeypatch.setattr(_sys, "argv", ["mcs", "--help"])
    with pytest.raises(SystemExit) as ei:
        _cli_main()
    assert ei.value.code == 0  # --help suppressed even for hard-block
    out = capsys.readouterr()
    assert "9.9.9" not in out.err

    monkeypatch.setattr(_sys, "argv", ["mcs", "status"])
    with pytest.raises(SystemExit) as ei2:
        _cli_main()
    assert ei2.value.code == 2
    out2 = capsys.readouterr()
    err_lc = out2.err.lower()
    assert (
        "disabled" in err_lc
        or "min_supported" in err_lc
        or "below" in err_lc
        or "minimum" in err_lc
        or "required" in err_lc
    )


def test_banner_uses_sparkle_prefix_with_leading_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_config: Path,
) -> None:
    """The rendered banner starts with a newline (so it doesn't crowd
    whatever the wrapped command printed last) and uses the ``✨``
    visual marker instead of the older ``[mcs]`` bracketed tag."""
    cdir = tmp_path / "cache"
    monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
    monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_sys.stderr, "isatty", lambda: True)

    from maxcompute_semantic._internal.update_check import (
        CacheEntry,
        _utcnow_iso,
        write_cache,
    )

    write_cache(
        CacheEntry(
            checked_at=_utcnow_iso(),
            current_at_check="0.4.0a38",
            latest_version="9.9.9",
            wheel_url=(
                "https://files.pythonhosted.org/packages/py3/maxcompute_semantic-9.9.9-py3-none-any.whl"
            ),
            min_supported="0.4.0",
            disabled=(),
            notice="",
            fetch_error="",
        )
    )

    monkeypatch.setattr(_sys, "argv", ["mcs", "profile", "list"])
    with contextlib.suppress(SystemExit):
        _cli_main()
    captured = capsys.readouterr()
    assert "✨" in captured.err, f"banner is missing the sparkle marker: stderr={captured.err!r}"
    assert "[mcs]" not in captured.err, "banner still carries the old [mcs] bracketed prefix"
    # The banner line itself should start with the sparkle, with a
    # newline immediately before it so it stands off from any prior
    # stderr output.
    assert "\n✨" in captured.err, (
        f"banner is not separated from prior output by a leading newline: stderr={captured.err!r}"
    )


def test_probe_does_not_block_command_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_config: Path,
) -> None:
    """The daemon probe is fire-and-forget."""
    import time

    cdir = tmp_path / "cache"
    monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
    monkeypatch.delenv("MCS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("MCS_UPDATE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(_sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(_sys, "argv", ["mcs", "--help"])

    t0 = time.perf_counter()
    with pytest.raises(SystemExit):
        _cli_main()
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"command exit took {elapsed:.2f} s — daemon-thread probe is "
        f"apparently blocking the foreground."
    )


def _patch_cli_main_banner_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maxcompute_semantic.cli.banner_suppressed", lambda *_a, **_kw: True)
    monkeypatch.setattr("maxcompute_semantic.cli.read_cache", lambda: None)
    monkeypatch.setattr("maxcompute_semantic.cli.is_hard_block", lambda *_a, **_kw: False)
    monkeypatch.setattr("maxcompute_semantic.cli.format_banner", lambda *_a, **_kw: None)


class TestCliMainExceptionShield:
    def test_click_exit_code_is_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            raise click.exceptions.Exit(7)

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        assert exc.value.code == 7

    def test_abort_maps_to_exit_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            raise click.exceptions.Abort()

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        assert exc.value.code == 1

    def test_click_exception_shows_message_and_uses_exception_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            err = click.ClickException("bad command")
            err.exit_code = 6
            raise err

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        assert exc.value.code == 6
        assert "bad command" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("system_exit_code", "expected"),
        [(None, 0), ("fatal", 1)],
    )
    def test_system_exit_normalizes_non_integer_codes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        system_exit_code: object,
        expected: int,
    ) -> None:
        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            raise SystemExit(system_exit_code)

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        assert exc.value.code == expected

    def test_unclassified_exception_mapped_to_mcs_error_envelope(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from maxcompute_semantic.errors import McsError

        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("raw pyodps-ish error")

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)
        monkeypatch.setattr(
            "maxcompute_semantic.errors.map_pyodps_exception",
            lambda _exc: McsError("mapped error", code="MappedError", exit_code=4),
        )

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        assert exc.value.code == 4
        payload = json.loads(capsys.readouterr().err)
        assert payload["error"]["code"] == "MappedError"
        assert payload["error"]["message"] == "mapped error"

    def test_unmapped_internal_exception_prints_trace_in_debug(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _patch_cli_main_banner_io(monkeypatch)
        monkeypatch.setattr(_sys, "argv", ["mcs", "--debug"])

        def fake_cli(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("raw boom")

        monkeypatch.setattr("maxcompute_semantic.cli.cli", fake_cli)
        monkeypatch.setattr(
            "maxcompute_semantic.errors.map_pyodps_exception",
            lambda _exc: (_ for _ in ()).throw(RuntimeError("mapper boom")),
        )

        with pytest.raises(SystemExit) as exc:
            _cli_main()

        err = capsys.readouterr().err
        assert exc.value.code == 1
        assert "mcs internal error: raw boom" in err
        assert "Traceback" in err
