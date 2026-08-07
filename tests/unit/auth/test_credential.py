# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for auth/credential.py — resolve_credentials (process + ak)."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from maxcompute_semantic.auth.credential import (
    _looks_like_identity_not_authorized,
    _looks_like_login_required,
    _parse_payload,
    resolve_credentials,
)
from maxcompute_semantic.auth.errors import (
    AuthBinaryMissingError,
    AuthFailedError,
    ConfigEnvNotSetError,
)
from maxcompute_semantic.auth.schema import AkAuth, ProcessAuth
from maxcompute_semantic.mc_client.errors import IdentityNotAuthorizedError


def _example_process_auth() -> ProcessAuth:
    return ProcessAuth(
        command="ncs create credential odpsuser --employee-id 100001 -o template -t odpscmd",
        timeout=60,
    )


def _example_ak_auth_literal() -> AkAuth:
    return AkAuth(access_key_id="FakeAKID0005", access_key_secret="BarSecret123")


def _example_ak_auth_env() -> AkAuth:
    return AkAuth(access_key_id="${env:MC_AK_ID}", access_key_secret="${env:MC_AK_SECRET}")


# --- AK auth tests ---


class TestResolveAkAuth:
    def test_ak_literal_returns_credentials(self) -> None:
        creds = resolve_credentials(_example_ak_auth_literal())
        assert creds is not None
        assert creds.access_key_id == "FakeAKID0005"
        assert creds.access_key_secret == "BarSecret123"
        assert creds.security_token == ""

    def test_ak_env_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MC_AK_ID", "env_ak_id")
        monkeypatch.setenv("MC_AK_SECRET", "env_ak_secret")
        creds = resolve_credentials(_example_ak_auth_env())
        assert creds is not None
        assert creds.access_key_id == "env_ak_id"
        assert creds.access_key_secret == "env_ak_secret"

    def test_ak_env_not_set_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MC_AK_ID", raising=False)
        monkeypatch.delenv("MC_AK_SECRET", raising=False)
        with pytest.raises(ConfigEnvNotSetError):
            resolve_credentials(_example_ak_auth_env())


# --- Process auth tests ---


class TestResolveProcessAuth:
    def test_process_auth_success(
        self, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
    ) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        payload = json.loads((fixtures_dir / "ncs_outputs" / "credential_success.json").read_text())
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=0, stdout=json.dumps(payload), stderr=""
            ),
        )
        creds = resolve_credentials(_example_process_auth())
        assert creds is not None
        assert creds.access_key_id == "STS.NWnW8ZyHhQyzVYuz1W9gBXzBX"
        assert creds.access_key_secret == "4oUgRnFDQooueZgrjvSBrUZSRHkrCRNWNNKbLdVG7io6"
        assert creds.security_token == "CAISxwN1q6Ft5B2yfSj=="
        assert creds.expiration is not None

    def test_process_auth_binary_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        with pytest.raises(AuthBinaryMissingError):
            resolve_credentials(_example_process_auth())

    def test_process_auth_empty_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # empty command should fail even with ncs available
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        empty_auth = ProcessAuth(command="", timeout=60)
        # resolve_credentials should handle empty command gracefully
        # (schema.py validation catches this, but credential.py should also handle it)
        with pytest.raises(AuthBinaryMissingError):
            resolve_credentials(empty_auth)

    def test_process_auth_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: _raise_timeout(),
        )
        with pytest.raises(AuthFailedError, match="timed out"):
            resolve_credentials(_example_process_auth())

    def test_process_auth_login_required(
        self, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
    ) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        output = (fixtures_dir / "ncs_outputs" / "not_json.txt").read_text()
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=1, stdout=output, stderr=output
            ),
        )
        with pytest.raises(AuthFailedError, match="login"):
            resolve_credentials(_example_process_auth())

    def test_process_auth_failure_does_not_echo_credential_stdout(
        self, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
    ) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        payload = json.loads((fixtures_dir / "ncs_outputs" / "credential_success.json").read_text())
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0],
                returncode=1,
                stdout=json.dumps(payload),
                stderr="",
            ),
        )

        with pytest.raises(AuthFailedError) as exc_info:
            resolve_credentials(_example_process_auth())

        message = str(exc_info.value)
        assert "AccessKeySecret" not in message
        assert "SecurityToken" not in message
        assert "STS.NWnW8ZyHhQyzVYuz1W9gBXzBX" not in message
        assert "no stderr" in message

    def test_process_auth_identity_not_authorized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        stderr = "identity not authorized: ODPS user not found"
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=1, stdout="", stderr=stderr
            ),
        )
        with pytest.raises(IdentityNotAuthorizedError):
            resolve_credentials(_example_process_auth())


class TestParsePayload:
    def test_parse_success(self, fixtures_dir: Path) -> None:
        payload = json.loads((fixtures_dir / "ncs_outputs" / "credential_success.json").read_text())
        creds = _parse_payload(payload)
        assert creds is not None
        assert creds.access_key_id.startswith("STS.")

    def test_parse_missing_fields_raises(self, fixtures_dir: Path) -> None:
        """Payload with only AccessKeyId (missing secret + token) -> AuthFailedError."""
        payload = json.loads(
            (fixtures_dir / "ncs_outputs" / "credential_missing_fields.json").read_text()
        )
        with pytest.raises(AuthFailedError, match="missing required fields"):
            _parse_payload(payload)

    def test_parse_non_json_output(self, fixtures_dir: Path) -> None:
        text = (fixtures_dir / "ncs_outputs" / "not_json.txt").read_text()
        creds = _parse_payload(text)
        assert creds is None

    def test_parse_unparseable_expiration(self) -> None:
        payload = {
            "AccessKeyId": "STS.abc",
            "AccessKeySecret": "secret",
            "Expiration": "not-a-date",
            "SecurityToken": "tok",
        }
        creds = _parse_payload(payload)
        assert creds is not None
        assert creds.expiration is None  # unparseable → None

    def test_parse_bad_expiration_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Bad expiration format triggers a warning log."""
        payload = {
            "AccessKeyId": "STS.abc",
            "AccessKeySecret": "secret",
            "Expiration": "totally-not-a-date",
            "SecurityToken": "tok",
        }
        with caplog.at_level(logging.WARNING, logger="maxcompute_semantic"):
            creds = _parse_payload(payload)
        assert creds is not None
        assert creds.expiration is None
        assert any("Expiration parse failure" in rec.message for rec in caplog.records)

    def test_parse_underscore_keys(self) -> None:
        payload = {
            "access_key_id": "STS.abc",
            "access_key_secret": "secret",
            "expiration": "2026-05-11T22:00:00Z",
            "security_token": "tok",
        }
        creds = _parse_payload(payload)
        assert creds is not None
        assert creds.access_key_id == "STS.abc"


class TestHeuristicHelpers:
    def test_looks_like_login_required(self) -> None:
        assert _looks_like_login_required("authentication required: run 'ncs auth login'") is True
        assert _looks_like_login_required("please login first") is True
        assert _looks_like_login_required("normal error") is False

    def test_looks_like_identity_not_authorized(self) -> None:
        assert _looks_like_identity_not_authorized("identity not authorized") is True
        assert _looks_like_identity_not_authorized("ODPS user not found") is True
        assert _looks_like_identity_not_authorized("normal error") is False


def _raise_timeout() -> subprocess.CompletedProcess:
    raise subprocess.TimeoutExpired(cmd=["ncs"], timeout=60)


def _raise_oserror() -> subprocess.CompletedProcess:
    raise OSError("command not found")


class TestProcessAuthEdgeCases:
    def test_process_auth_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError from subprocess.run → AuthFailedError."""
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **k: _raise_oserror())
        with pytest.raises(AuthFailedError, match="failed to execute"):
            resolve_credentials(_example_process_auth())

    def test_process_auth_generic_exit_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-zero exit without login/identity patterns → AuthFailedError."""
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=1, stdout="some error", stderr=""
            ),
        )
        with pytest.raises(AuthFailedError, match="exited 1"):
            resolve_credentials(_example_process_auth())

    def test_process_auth_unparseable_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Process returns rc=0 but output cannot be parsed → AuthFailedError."""
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/local/bin/ncs" if cmd == "ncs" else None
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=0, stdout="not json at all", stderr=""
            ),
        )
        with pytest.raises(AuthFailedError, match="could not be parsed"):
            resolve_credentials(_example_process_auth())


class TestParsePayloadEdgeCases:
    def test_parse_non_dict_data(self) -> None:
        """_parse_payload with non-dict JSON → returns None."""
        creds = _parse_payload(json.dumps([1, 2, 3]))
        assert creds is None

    def test_parse_missing_ak_id_raises(self) -> None:
        """Payload missing AccessKeyId → AuthFailedError."""
        with pytest.raises(AuthFailedError, match="AccessKeyId/access_key_id") as exc_info:
            _parse_payload({"AccessKeySecret": "secret"})
        assert "found keys" in str(exc_info.value)

    def test_parse_missing_ak_secret_raises(self) -> None:
        """Payload missing AccessKeySecret → AuthFailedError."""
        with pytest.raises(AuthFailedError, match="AccessKeySecret/access_key_secret") as exc_info:
            _parse_payload({"AccessKeyId": "STS.abc", "SecurityToken": "tok"})
        assert "found keys" in str(exc_info.value)

    def test_parse_missing_security_token_raises(self) -> None:
        """Payload missing SecurityToken → AuthFailedError, NOT silently empty token."""
        with pytest.raises(AuthFailedError, match="SecurityToken/security_token") as exc_info:
            _parse_payload({"AccessKeyId": "STS.abc", "AccessKeySecret": "secret"})
        assert "found keys" in str(exc_info.value)

    def test_parse_missing_all_three_raises(self) -> None:
        """Payload missing all three required fields → AuthFailedError listing found keys."""
        with pytest.raises(AuthFailedError, match="missing required fields") as exc_info:
            _parse_payload({"Expiration": "2026-05-11T22:00:00Z"})
        msg = str(exc_info.value)
        assert "AccessKeyId/access_key_id" in msg
        assert "AccessKeySecret/access_key_secret" in msg
        assert "SecurityToken/security_token" in msg
        assert "Expiration" in msg  # found keys should include Expiration


class TestAuthBinaryMissingRemediation:
    """The AuthBinaryMissingError remediation gets the Akless CLI
    install-docs URL when the missing binary is `ncs`. Other
    binaries keep the generic remediation."""

    def test_ncs_remediation_includes_install_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When `ncs` is the missing binary, remediation contains the
        install-docs URL so the user can act on the error directly."""
        from maxcompute_semantic.auth.credential import _resolve_process
        from maxcompute_semantic.auth.errors import AuthBinaryMissingError
        from maxcompute_semantic.auth.ncs import NCS_INSTALL_DOC_URL
        from maxcompute_semantic.auth.schema import ProcessAuth

        monkeypatch.setattr("shutil.which", lambda cmd: None)
        auth = ProcessAuth(
            command="ncs create credential odpsuser --employee-id 12345 -o template -t odpscmd"
        )

        with pytest.raises(AuthBinaryMissingError) as excinfo:
            _resolve_process(auth)

        assert NCS_INSTALL_DOC_URL in excinfo.value.remediation
        assert "ncs" in excinfo.value.remediation

    def test_other_binary_keeps_generic_remediation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-ncs binaries keep the existing generic remediation —
        regression guard against an over-broad rewrite."""
        from maxcompute_semantic.auth.credential import _resolve_process
        from maxcompute_semantic.auth.errors import AuthBinaryMissingError
        from maxcompute_semantic.auth.schema import ProcessAuth

        monkeypatch.setattr("shutil.which", lambda cmd: None)
        auth = ProcessAuth(command="my-helper get-creds")

        with pytest.raises(AuthBinaryMissingError) as excinfo:
            _resolve_process(auth)

        assert excinfo.value.remediation == ("install my-helper or switch to auth.type=ak")
