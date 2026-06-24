# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``mcs update`` self-upgrade command.

The Python entry point lives at
``maxcompute_semantic/commands/update.py``. There is a separate
``mcs profile update`` command (the existing
``test_update_cmd.py`` file in this directory covers that), so this
file is named ``test_mcs_update.py`` to disambiguate.

See spec §"commands/update.py" for the public surface.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


class TestInstallMode:
    @pytest.mark.parametrize(
        "exec_path,expected",
        [
            # uv tool install — the bin shim lives under the tool's
            # venv. On Linux/macOS the path contains "/uv/tools/" with
            # the wheel name as the next segment; on Windows it's
            # "\\uv\\tools\\\\".
            ("/Users/alice/.local/share/uv/tools/maxcompute-semantic/bin/mcs", "uv-tool"),
            ("/home/bob/.local/share/uv/tools/maxcompute-semantic/bin/mcs", "uv-tool"),
            # uv normalizes the package name in either direction
            # depending on the version — accept the underscored form too.
            ("/Users/alice/.local/share/uv/tools/maxcompute_semantic/bin/mcs", "uv-tool"),
            # pipx — venvs live under ~/.local/pipx/venvs/<name>/bin/.
            ("/home/bob/.local/pipx/venvs/maxcompute-semantic/bin/mcs", "pipx"),
            ("/Users/c/.local/pipx/venvs/maxcompute_semantic/bin/mcs", "pipx"),
            # pip --user — sys.executable is the system python, the
            # ``mcs`` script lives in ~/.local/bin, but
            # sys.executable.startswith(site.getuserbase()) is the
            # canonical test (the system python is shared, so the
            # marker is purely "did the script land in the user-base
            # bin dir"). The detector uses argv[0]/sys.executable
            # heuristics described below.
            # This case is asserted with mocked argv0 in the dedicated
            # test_pip_user test below.
            # System pip — anything under /usr or the active venv that
            # isn't the uv-tool / pipx pattern.
            ("/usr/bin/python3", "pip"),
            ("/opt/homebrew/bin/python3.11", "pip"),
            ("/Users/alice/work/proj/.venv/bin/python", "pip"),
            # Editable install (`pip install -e ...`) of the source
            # tree shows up with sys.prefix == the repo's venv.
            # That's still "pip" mode in the sense that
            # `python -m pip install --upgrade <wheel>` is the right
            # command — the editable flag is per-file metadata, not a
            # different install method.
            # Unknown — a frozen-binary path that has none of the
            # marker substrings.
            ("/opt/mcs-bundle/mcs", "unknown"),
        ],
    )
    def test_detect_from_executable_path(
        self, exec_path: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maxcompute_semantic.commands.update import (
            detect_install_mode,
        )

        # The detector inspects sys.executable. (For uv tool, the bin
        # script is a thin wrapper — the venv's python is in the same
        # tools/<name>/ subtree, so the same substring match works on
        # sys.executable.) The argv[0] inspection is for the pip-user
        # case where sys.executable is the shared system python.
        monkeypatch.setattr(sys, "executable", exec_path)
        # Make sure argv[0] doesn't accidentally trigger the pip-user
        # detection for the non-pip-user cases.
        monkeypatch.setattr(sys, "argv", [exec_path, "update"])
        mode = detect_install_mode()
        assert mode.value == expected, f"got {mode.value!r} for {exec_path!r}"

    def test_detect_pip_user_via_argv0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``pip install --user`` puts the ``mcs`` console_script in
        the user-site bin dir (``site.getuserbase()/bin``). The
        sys.executable is the shared system python, so the user-mode
        marker is the argv[0] path."""
        import site

        userbase = site.getuserbase()
        argv0 = str(Path(userbase) / "bin" / "mcs")

        monkeypatch.setattr(sys, "executable", "/usr/bin/python3.11")
        monkeypatch.setattr(sys, "argv", [argv0, "update"])

        from maxcompute_semantic.commands.update import (
            InstallMode,
            detect_install_mode,
        )

        assert detect_install_mode() is InstallMode.PIP_USER

    def test_detect_returns_enum_member(self) -> None:
        """The function returns an ``InstallMode`` enum, not a bare
        string — call sites pattern-match on the enum."""
        from maxcompute_semantic.commands.update import (
            InstallMode,
            detect_install_mode,
        )

        result = detect_install_mode()
        assert isinstance(result, InstallMode)
        # And the enum carries the five documented values.
        names = {m.name for m in InstallMode}
        assert names == {"UV_TOOL", "PIPX", "PIP_USER", "PIP", "UNKNOWN"}
        values = {m.value for m in InstallMode}
        assert values == {"uv-tool", "pipx", "pip-user", "pip", "unknown"}


class TestBuildUpgradeArgv:
    _TARGET = "maxcompute-semantic==0.17.3"

    def test_uv_tool(self) -> None:
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        argv = build_upgrade_argv(InstallMode.UV_TOOL, install_target=self._TARGET)
        assert argv == ["uv", "tool", "install", "--reinstall", self._TARGET]

    def test_pipx(self) -> None:
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        assert build_upgrade_argv(InstallMode.PIPX, install_target=self._TARGET) == [
            "pipx",
            "install",
            "--force",
            self._TARGET,
        ]

    def test_pip_user_uses_current_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3.11")
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        assert build_upgrade_argv(InstallMode.PIP_USER, install_target=self._TARGET) == [
            "/usr/bin/python3.11",
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            self._TARGET,
        ]

    def test_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "executable", "/Users/a/work/.venv/bin/python")
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        assert build_upgrade_argv(InstallMode.PIP, install_target=self._TARGET) == [
            "/Users/a/work/.venv/bin/python",
            "-m",
            "pip",
            "install",
            "--upgrade",
            self._TARGET,
        ]

    def test_unknown_returns_none(self) -> None:
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        assert build_upgrade_argv(InstallMode.UNKNOWN, install_target=self._TARGET) is None

    def test_install_target_passes_through_verbatim(self) -> None:
        """The ``--version 0.4.0a50`` flag on ``mcs update`` rewrites
        the install target upstream. The argv builder just passes the
        target through verbatim, with no shell escaping behavior."""
        from maxcompute_semantic.commands.update import (
            InstallMode,
            build_upgrade_argv,
        )

        target_with_spaces = "/tmp/maxcompute semantic/local wheel.whl"
        argv = build_upgrade_argv(InstallMode.PIP, install_target=target_with_spaces)
        assert argv is not None
        # The target is the last positional. No shell quoting because we
        # don't run through a shell (subprocess.run with a list argv).
        assert argv[-1] == target_with_spaces


class TestManualHint:
    def test_includes_all_four_installers(self) -> None:
        from maxcompute_semantic.commands.update import manual_upgrade_hint

        hint = manual_upgrade_hint("maxcompute-semantic==0.17.3")
        assert "uv tool install" in hint
        assert "pipx install" in hint
        assert "-m pip install --upgrade" in hint
        assert "-m pip install --user --upgrade" in hint
        # The target is shell-quoted (single quotes around it because the
        # default target doesn't contain quote chars; this assertion is
        # tolerant of the unquoted form for very plain targets since
        # shlex.quote elides quotes when there's nothing to escape).
        assert "maxcompute-semantic==0.17.3" in hint


class TestCmdUpdate:
    """Tests for the ``mcs update`` click command. ``subprocess.run``
    and ``os.execvp`` are monkeypatched so no real process spawn
    occurs.

    The fixtures from ``conftest.py`` (``pypi_json_server``,
    ``isolated_config``) carry the PyPI JSON stub — the test sets a
    payload on the stub server and the command's fetcher hits it.
    """

    from click.testing import CliRunner

    @pytest.fixture
    def _no_real_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        """Record subprocess.run argv lists, return success."""
        calls: list[list[str]] = []

        def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(list(argv))

            class _CP:
                returncode = 0
                stdout = b""
                stderr = b""

            return _CP()

        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    @pytest.fixture
    def _no_real_exec(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        """Record os.execvp argv lists. Unlike the real exec, we don't
        replace the process — the function just records and returns."""
        calls: list[list[str]] = []

        def fake_exec(file, argv):  # type: ignore[no-untyped-def]
            calls.append([file] + list(argv[1:]))
            # The real os.execvp doesn't return on success; the
            # SystemExit here mimics the not-returning effect for
            # the calling click handler.
            raise SystemExit(0)

        monkeypatch.setattr(os, "execvp", fake_exec)
        return calls

    @pytest.fixture
    def _force_uv_tool_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pin the install-mode detector at UV_TOOL so the argv-builder
        result is deterministic for the subprocess-call assertion."""
        from maxcompute_semantic.commands import update as upd

        monkeypatch.setattr(upd, "detect_install_mode", lambda: upd.InstallMode.UV_TOOL)

    @pytest.fixture
    def _ver_payload(
        self, pypi_project_payload, monkeypatch: pytest.MonkeyPatch
    ):
        """The standard PyPI JSON shape pointing at a higher version
        than what the running mcs is at, so the command takes the
        upgrade path. The current mcs version is read at runtime
        via the version_for_test fixture below."""
        import urllib.request

        class _PayloadFactory:
            def __init__(self) -> None:
                self.retrieved_urls: list[str] = []

            def __call__(self, version: str = "0.4.0a99") -> dict:
                return pypi_project_payload(version)

        factory = _PayloadFactory()

        def fake_urlretrieve(url: str, filename: str):  # type: ignore[no-untyped-def]
            factory.retrieved_urls.append(url)
            raise AssertionError("mcs update should let the installer resolve package artifacts")

        monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)

        return factory

    def test_already_on_latest_no_subprocess(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _ver_payload,
    ) -> None:
        """When the published latest equals the running version, the
        command prints a confirmation and exits 0 without spawning a
        subprocess."""
        from maxcompute_semantic import __version__
        from maxcompute_semantic.commands.update import cmd_update

        _, setter = pypi_json_server
        # Publisher's "latest" is exactly our version.
        setter(_ver_payload(__version__))

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        assert result.exit_code == 0, result.output
        assert "already on latest" in result.output.lower() or __version__ in result.output
        assert _no_real_subprocess == []
        assert _no_real_exec == []

    def test_happy_path_uv_tool(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        """End-to-end: fetch metadata, build the uv argv, "run" the
        install subprocess, "run" the skill-update subprocess,
        "exec" the verification step."""
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))  # well ahead of the running version.

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        # The exec is mocked to raise SystemExit(0), so the click
        # runner sees exit code 0.
        assert result.exit_code == 0, (result.output, result.exception)

        # Two subprocess calls: the installer, then `mcs skill update --all`.
        assert len(_no_real_subprocess) == 2
        installer_argv = _no_real_subprocess[0]
        assert installer_argv[:4] == ["uv", "tool", "install", "--reinstall"]
        assert installer_argv[-1] == "maxcompute-semantic==9.9.9"
        assert _ver_payload.retrieved_urls == []
        assert "SHA256 OK" not in result.output

        skill_argv = _no_real_subprocess[1]
        # The skill-update call uses the script path that started us
        # (sys.argv[0]), which the test runner sets to the click
        # runner's name. We just confirm the verb shape.
        assert "skill" in skill_argv
        assert "update" in skill_argv
        assert "--all" in skill_argv

        # One execvp call: the post-install version-print.
        assert len(_no_real_exec) == 1
        verify_argv = _no_real_exec[0]
        assert verify_argv[-1] == "--version"

    def test_post_install_prints_refresh_hint(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        """After a successful upgrade, the command prints a one-line
        hint telling the user that any built profiles may have a stale
        inference layer and that ``mcs build --refresh`` reconciles
        it offline. The line lands on stderr (via ``err=True``) so it
        doesn't pollute scripted callers reading stdout, but the
        ``CliRunner`` collapses both streams into ``result.output``."""
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        assert result.exit_code == 0, (result.output, result.exception)
        assert "stale inference layer" in result.output
        assert "mcs build --refresh" in result.output

    def test_check_prompt_no_aborts(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        """Without ``--no-check`` (i.e., with the default ``--check``),
        the command prompts for confirmation. Answering "n" aborts
        before any subprocess fires."""
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        # click.confirm reads from stdin; feed "n\n".
        result = runner.invoke(cmd_update, [], input="n\n")

        # click.confirm with abort=True on the default-False answer
        # exits with click's standard abort code (1).
        assert result.exit_code != 0
        assert "Aborted" in result.output or "abort" in result.output.lower()
        assert _no_real_subprocess == []
        assert _no_real_exec == []

    def test_check_prompt_yes_runs(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, [], input="y\n")
        assert result.exit_code == 0, result.output
        assert len(_no_real_subprocess) == 2  # installer + skill update
        assert len(_no_real_exec) == 1  # version-verify

    def test_version_pin_overrides_latest(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        """``--version 0.4.0a50`` makes the installer pull that
        specific PyPI version spec even when the metadata endpoint is down."""
        _, setter = pypi_json_server
        setter(503)

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check", "--version", "0.4.0a50"])
        assert result.exit_code == 0, result.output
        assert len(_no_real_subprocess) >= 1
        installer_argv = _no_real_subprocess[0]
        assert installer_argv[-1] == "maxcompute-semantic==0.4.0a50"

    def test_version_pin_uses_pypi_package_spec_by_default(
        self,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The default publisher is PyPI, so an explicit version pin
        should install a package requirement."""
        monkeypatch.delenv("MCS_UPDATE_BASE_URL", raising=False)

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check", "--version", "0.17.2"])

        assert result.exit_code == 0, result.output
        assert len(_no_real_subprocess) >= 1
        installer_argv = _no_real_subprocess[0]
        assert installer_argv == [
            "uv",
            "tool",
            "install",
            "--reinstall",
            "maxcompute-semantic==0.17.2",
        ]
        assert _no_real_exec

    def test_metadata_fetch_failure_exits_one(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
    ) -> None:
        """If the publisher's metadata endpoint is unreachable and the
        user didn't pin ``--version``, the command bails before
        spawning anything."""
        _, setter = pypi_json_server
        setter(503)

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        assert result.exit_code != 0
        out_lower = result.output.lower()
        assert (
            "could not fetch" in out_lower
            or "unreachable" in out_lower
            or "pypi" in out_lower
        )
        assert _no_real_subprocess == []
        assert _no_real_exec == []

    def test_latest_update_lets_installer_resolve_distribution_source(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _no_real_exec: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
    ) -> None:
        """The no-pin path uses PyPI metadata only to learn the latest
        version. The installer receives a package requirement, not the
        PyPI artifact URL, so pip/uv/pipx index configuration and
        mirrors remain in control of the actual distribution source."""
        _, setter = pypi_json_server
        payload = _ver_payload("9.9.9")
        wheel = payload["urls"][1]
        assert isinstance(wheel, dict)
        wheel["url"] = "https://example.test/packages/maxcompute_semantic-9.9.9-py3-none-any.whl"
        setter(payload)

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        assert result.exit_code == 0, result.output
        assert _no_real_subprocess[0][-1] == "maxcompute-semantic==9.9.9"
        assert _ver_payload.retrieved_urls == []
        assert _no_real_exec

    def test_installer_failure_exits_one_and_skips_skill_update_and_exec(
        self,
        pypi_json_server,
        _force_uv_tool_mode: None,
        _ver_payload,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the install subprocess returns non-zero, the command
        prints the stderr and exits 1. The post-install
        ``skill update --all`` and the verifying ``--version`` exec
        do NOT run."""
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        import subprocess

        def fake_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            class _CP:
                returncode = 7
                stdout = b""
                stderr = b"simulated installer failure: no network\n"

            return _CP()

        exec_calls: list[tuple] = []

        def fake_exec(file, argv):  # type: ignore[no-untyped-def]
            exec_calls.append((file, tuple(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(os, "execvp", fake_exec)

        from maxcompute_semantic.commands.update import cmd_update

        runner = self.CliRunner()
        result = runner.invoke(cmd_update, ["--no-check"])

        assert result.exit_code != 0
        # The implementation surfaces the installer name and exit
        # code in its ClickException (the installer's stderr itself
        # goes straight to the terminal because subprocess.run is
        # called with ``capture_output=False`` — see the function's
        # design note). We assert on the parts that ARE in the
        # captured output.
        assert "exited 7" in result.output
        assert "uv" in result.output
        # Neither the skill-update step nor the verify-exec ran.
        assert exec_calls == []

    def test_unknown_install_mode_prints_manual_hint(
        self,
        pypi_json_server,
        _ver_payload,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        from maxcompute_semantic.commands import update as upd

        monkeypatch.setattr(upd, "detect_install_mode", lambda: upd.InstallMode.UNKNOWN)
        # subprocess.run shouldn't be called in this path; if it is,
        # the test fails because the side-effect-free assertion below
        # also confirms the absence of the four-line hint.
        run_calls: list[object] = []

        import subprocess

        def fake_run(*a, **kw):  # type: ignore[no-untyped-def]
            run_calls.append((a, kw))

            class _CP:
                returncode = 0

            return _CP()

        monkeypatch.setattr(subprocess, "run", fake_run)

        runner = self.CliRunner()
        result = runner.invoke(upd.cmd_update, ["--no-check"])

        # Manual-hint exit code is non-zero (the command did not
        # actually upgrade anything). The four canonical commands all
        # appear in the output.
        assert result.exit_code != 0
        out = result.output
        assert "uv tool install" in out
        assert "pipx install" in out
        assert "-m pip install --upgrade" in out
        assert "-m pip install --user --upgrade" in out
        assert run_calls == []

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows-specific self-replace fallback",
    )
    def test_windows_skips_execvp_and_prints_restart_hint(
        self,
        pypi_json_server,
        _no_real_subprocess: list[list[str]],
        _force_uv_tool_mode: None,
        _ver_payload,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On Windows the running .exe file is locked, so
        ``os.execvp`` won't cleanly replace the process. The command
        prints "restart your shell" and exits 0 instead.

        Tested under the platform-skip marker — the assertion runs
        only when the test suite is itself running on Windows. The
        Aone CI matrix is Linux, so this test is effectively a
        documentation aid; the Windows-side behavior is verified by
        the manual smoke checklist in the spec's "Open questions /
        install.ps1 testing."
        """
        _, setter = pypi_json_server
        setter(_ver_payload("9.9.9"))

        from maxcompute_semantic.commands import update as upd

        execvp_calls: list[object] = []

        def fake_exec(*a, **kw):  # type: ignore[no-untyped-def]
            execvp_calls.append((a, kw))

        monkeypatch.setattr(upd.os, "execvp", fake_exec)

        runner = self.CliRunner()
        result = runner.invoke(upd.cmd_update, ["--no-check"])
        assert result.exit_code == 0
        assert "restart" in result.output.lower()
        assert execvp_calls == []
