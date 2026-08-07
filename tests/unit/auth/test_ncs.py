# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for auth/ncs.py — thin ncs subprocess helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from maxcompute_semantic.auth.ncs import (
    NcsAuth,
    _extract_field,
    _parse_authorizations,
    _parse_whoami,
    is_available,
    list_odps_authorizations,
    whoami,
)


class TestIsAvailable:
    def test_is_available_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        assert is_available() is True

    def test_is_available_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert is_available() is False


class TestWhoami:
    def test_whoami_returns_none_when_ncs_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert whoami() is None

    def test_whoami_parses_output(
        self, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")
        output = (fixtures_dir / "ncs_outputs" / "whoami_success.txt").read_text()
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=0, stdout=output, stderr=""
            ),
        )
        w = whoami()
        assert w is not None
        assert w.identity_name == "Test User"
        assert w.employee_id == "100001"

    def test_whoami_returns_none_when_not_logged_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=1, stdout="", stderr="not logged in"
            ),
        )
        assert whoami() is None

    def test_whoami_returns_none_on_subprocess_oserror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")

        def _raise_oserror(*a, **k):
            raise OSError("exec failed")

        monkeypatch.setattr("subprocess.run", _raise_oserror)
        assert whoami() is None

    def test_whoami_returns_none_on_subprocess_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["ncs"], timeout=10)

        monkeypatch.setattr("subprocess.run", _raise_timeout)
        assert whoami() is None


class TestParseWhoami:
    def test_parse_whoami_success(self, fixtures_dir: Path) -> None:
        output = (fixtures_dir / "ncs_outputs" / "whoami_success.txt").read_text()
        w = _parse_whoami(output)
        assert w is not None
        assert w.identity_name == "Test User"
        assert w.employee_id == "100001"

    def test_parse_whoami_missing_identity_key(self) -> None:
        output = "====== Identity ======\nidentity_name    Someone\n"
        w = _parse_whoami(output)
        assert w is None

    def test_parse_whoami_key_without_digits(self) -> None:
        """identity_key with no trailing digits → _parse_whoami returns None."""
        output = "identity_name    Someone\nidentity_key    no_digits_here\n"
        w = _parse_whoami(output)
        assert w is None


class TestExtractField:
    def test_extract_field_found(self) -> None:
        assert _extract_field("identity_name    foo bar", "identity_name") == "foo bar"

    def test_extract_field_missing(self) -> None:
        assert _extract_field("other_key    val", "identity_name") == ""


class TestListAuthorizations:
    def test_list_odps_authorizations_parses(
        self, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")
        output = (fixtures_dir / "ncs_outputs" / "list_authorizations_multi.txt").read_text()
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=0, stdout=output, stderr=""
            ),
        )
        auths = list_odps_authorizations()
        assert len(auths) == 2
        assert auths[0] == NcsAuth(
            buc_user_id="100001", buc_user_type="employee", buc_account_name="testuser"
        )
        assert auths[1] == NcsAuth(
            buc_user_id="WORKER_1725969166792",
            buc_user_type="department",
            buc_account_name="acni_odps_test_account_z",
        )

    def test_list_returns_empty_when_ncs_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert list_odps_authorizations() == []

    def test_list_returns_empty_on_subprocess_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=1, stdout="", stderr="error"
            ),
        )
        assert list_odps_authorizations() == []

    def test_list_returns_empty_on_subprocess_oserror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")

        def _raise_oserror(*a, **k):
            raise OSError("exec failed")

        monkeypatch.setattr("subprocess.run", _raise_oserror)
        assert list_odps_authorizations() == []

    def test_list_returns_empty_on_subprocess_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["ncs"], timeout=30)

        monkeypatch.setattr("subprocess.run", _raise_timeout)
        assert list_odps_authorizations() == []


class TestParseAuthorizations:
    def test_parse_authorizations_multi(self, fixtures_dir: Path) -> None:
        output = (fixtures_dir / "ncs_outputs" / "list_authorizations_multi.txt").read_text()
        auths = _parse_authorizations(output)
        assert len(auths) == 2

    def test_parse_authorizations_empty(self) -> None:
        assert _parse_authorizations("") == []

    def test_parse_authorizations_only_header(self) -> None:
        assert _parse_authorizations("BUC_USER_ID  TYPE  NAME\n---  ---  ---\n") == []

    def test_parse_authorizations_empty_lines_skipped(self) -> None:
        """Empty lines in authorization output are skipped."""
        output = "\nBUC_USER_ID  TYPE  NAME\n---  ---  ---\n100001  employee  testuser\n\n"
        auths = _parse_authorizations(output)
        assert len(auths) == 1

    def test_parse_authorizations_short_lines_skipped(self) -> None:
        """Lines with fewer than 3 parts are skipped."""
        output = "100001  employee\n"
        auths = _parse_authorizations(output)
        assert len(auths) == 0


class TestInstallHint:
    def test_url_constant_matches_canonical(self) -> None:
        """NCS_INSTALL_DOC_URL is the Akless CLI documentation URL."""
        from maxcompute_semantic.auth.ncs import NCS_INSTALL_DOC_URL

        assert NCS_INSTALL_DOC_URL == (
            "https://authx.io.alibaba-inc.com"
        )

    def test_install_hint_contains_url_and_binary_name(self) -> None:
        """install_hint() multi-line output names `ncs` and the docs URL."""
        from maxcompute_semantic.auth.ncs import NCS_INSTALL_DOC_URL, install_hint

        hint = install_hint()
        assert "ncs" in hint
        assert "not found on PATH" in hint
        assert NCS_INSTALL_DOC_URL in hint
        # Multi-line so it renders distinctly in the wizard / doctor output.
        assert "\n" in hint
