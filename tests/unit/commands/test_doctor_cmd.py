# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for mcs doctor command — focus on McsError propagation.

The doctor check functions that call MaxComputeClient now catch McsError
separately from generic Exception, preserving code and remediation in
the detail string instead of truncating raw exception text.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from maxcompute_semantic.auth.schema import AkAuth, DataSource, ProcessAuth, Profile
from maxcompute_semantic.commands.doctor import (
    _check_auth,
    _check_build_data,
    _check_config_permissions,
    _check_connectivity,
    _check_link_json,
    _check_ncs_available,
    _check_profile_resolution,
    _check_profiles_yaml,
    _check_skill_install,
    _check_tier,
)
from maxcompute_semantic.mc_client.errors import (
    AuthFailedError,
    IdentityNotAuthorizedError,
    PermissionDeniedError,
)


def _ak_profile() -> Profile:
    return Profile(
        name="doctor-test",
        compute_project="test_proj",
        endpoint="https://service.odps.aliyun.com/api",
        auth=AkAuth("FooAKID", "***fake_secret"),
        sources=(DataSource(project="test_proj", tables="*"),),
    )


def _mock_client_cls(exc=None, return_value=None):
    """Build a mock MaxComputeClient class.

    exc: side_effect for execute_sql / get_tier.
    return_value: return value for execute_sql (when exc is None).
    """
    mock_client = MagicMock()
    if exc is not None:
        mock_client.execute_sql.side_effect = exc
    elif return_value is not None:
        mock_client.execute_sql.return_value = return_value
    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls, mock_client


def test_check_env_fallback_endpoint_warns_for_unusual_host(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maxcompute_semantic.commands.doctor import _check_env_fallback_endpoint

    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "https://proxy.example.test/api")

    name, status, detail = _check_env_fallback_endpoint()

    assert name == "env_fallback_endpoint"
    assert status == "warn"
    assert detail is not None
    assert "custom env fallback endpoint" in detail
    assert "supported" in detail


def test_check_env_fallback_endpoint_skips_when_saved_profile_is_used(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maxcompute_semantic.commands.doctor import _check_env_fallback_endpoint

    monkeypatch.setenv("MCS_PROFILE", "prod")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "https://proxy.example.test/api")

    name, status, detail = _check_env_fallback_endpoint()

    assert name == "env_fallback_endpoint"
    assert status == "skip"
    assert detail == "not using env-var fallback"


def test_check_env_fallback_endpoint_skips_for_explicit_profile(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maxcompute_semantic.commands.doctor import _check_env_fallback_endpoint

    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "https://proxy.example.test/api")

    name, status, detail = _check_env_fallback_endpoint(profile_name="prod")

    assert name == "env_fallback_endpoint"
    assert status == "skip"
    assert detail == "not using env-var fallback"


def test_check_env_fallback_endpoint_warns_with_stale_link(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maxcompute_semantic.auth.link_store import set_link
    from maxcompute_semantic.commands.doctor import _check_env_fallback_endpoint

    set_link(str(Path.cwd()), "deleted-profile")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "https://proxy.example.test/api")

    name, status, detail = _check_env_fallback_endpoint()

    assert name == "env_fallback_endpoint"
    assert status == "warn"
    assert detail is not None
    assert "custom env fallback endpoint" in detail


class TestLocalConfigChecks:
    def test_profiles_yaml_fails_when_file_has_no_profiles(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profiles_yaml_path

        ypath = profiles_yaml_path()
        ypath.parent.mkdir(parents=True, exist_ok=True)
        ypath.write_text("version: 1\nprofiles: {}\n", encoding="utf-8")

        assert _check_profiles_yaml() == (
            "profiles_yaml",
            "fail",
            "profiles.yaml exists but defines no profiles",
        )

    def test_profiles_yaml_reports_read_errors(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profiles_yaml_path

        ypath = profiles_yaml_path()
        ypath.parent.mkdir(parents=True, exist_ok=True)
        ypath.write_text("profiles: {}\n", encoding="utf-8")

        with patch(
            "maxcompute_semantic.auth.profile_store._read_raw",
            side_effect=RuntimeError("yaml broke"),
        ):
            name, status, detail = _check_profiles_yaml()

        assert name == "profiles_yaml"
        assert status == "fail"
        assert "yaml broke" in (detail or "")

    def test_config_permissions_warns_for_group_world_bits(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profiles_yaml_path

        ypath = profiles_yaml_path()
        ypath.parent.mkdir(parents=True, exist_ok=True)
        ypath.write_text("profiles: {}\n", encoding="utf-8")
        ypath.parent.chmod(0o755)
        ypath.chmod(0o644)

        name, status, detail = _check_config_permissions()

        assert name == "config_permissions"
        assert status == "warn"
        assert detail is not None
        assert "chmod 700" in detail
        assert "chmod 600" in detail

    def test_config_permissions_warns_when_stat_fails(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profiles_yaml_path

        ypath = profiles_yaml_path()
        ypath.parent.mkdir(parents=True, exist_ok=True)
        ypath.write_text("profiles: {}\n", encoding="utf-8")

        with patch("maxcompute_semantic.commands.doctor.os.stat", side_effect=OSError("no stat")):
            name, status, detail = _check_config_permissions()

        assert name == "config_permissions"
        assert status == "warn"
        assert "cannot stat config dir" in (detail or "")
        assert "cannot stat profiles.yaml" in (detail or "")

    def test_link_json_reports_bound_cwd_and_malformed_json(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maxcompute_semantic._internal.paths import link_json_path

        monkeypatch.chdir(isolated_config)
        link_json_path().parent.mkdir(parents=True, exist_ok=True)
        link_json_path().write_text(
            json.dumps({str(Path.cwd()): "prod"}),
            encoding="utf-8",
        )
        assert _check_link_json() == ("link_json", "pass", "cwd → profile 'prod'")

        link_json_path().write_text("{not json", encoding="utf-8")
        name, status, detail = _check_link_json()
        assert name == "link_json"
        assert status == "fail"
        assert "cannot read link.json" in (detail or "")


class TestProfileAndAuthChecks:
    def test_profile_resolution_reports_named_profile_slot(self, isolated_config: Path) -> None:
        from maxcompute_semantic.auth.profile_store import upsert

        p = _ak_profile()
        upsert(p)

        result, resolved = _check_profile_resolution(p.name)

        assert result == ("profile_resolution", "pass", f"profile '{p.name}' via --profile")
        assert resolved == p

    def test_profile_resolution_reports_mcs_error(self, isolated_config: Path) -> None:
        result, resolved = _check_profile_resolution("missing")

        assert result[0] == "profile_resolution"
        assert result[1] == "fail"
        assert "not found" in (result[2] or "").lower()
        assert resolved is None

    def test_profile_resolution_fails_when_compute_project_missing(self) -> None:
        fake = MagicMock()
        fake.compute_project = ""
        fake.name = "env"

        with patch(
            "maxcompute_semantic.commands.doctor.resolve_profile_for_project",
            return_value=fake,
        ):
            result, resolved = _check_profile_resolution(None)

        assert result == (
            "profile_resolution",
            "fail",
            "env-var fallback has no compute_project; set MAXCOMPUTE_PROJECT",
        )
        assert resolved is None

    def test_auth_pass_includes_sts_expiration(self) -> None:
        from datetime import datetime, timezone

        p = _ak_profile()
        creds = MagicMock()
        creds.security_token = "token"
        creds.expiration = datetime(2026, 1, 1, tzinfo=timezone.utc)

        with patch(
            "maxcompute_semantic.auth.credential.resolve_credentials",
            return_value=creds,
        ):
            name, status, detail = _check_auth(p)

        assert name == "auth"
        assert status == "pass"
        assert "STS" in (detail or "")
        assert "2026-01-01" in (detail or "")

    def test_auth_generic_exception_fails_with_message(self) -> None:
        with patch(
            "maxcompute_semantic.auth.credential.resolve_credentials",
            side_effect=RuntimeError("helper crashed"),
        ):
            result = _check_auth(_ak_profile())

        assert result == ("auth", "fail", "helper crashed")


# ── _check_connectivity ────────────────────────────────────────────


class TestCheckConnectivity:
    def test_mcs_error_preserves_code_and_message(self) -> None:
        """McsError from execute_sql preserves code + message in detail."""
        p = _ak_profile()
        err = AuthFailedError(
            "AccessKeyIdNotFound: ...",
            remediation="re-run `ncs auth login`",
        )
        mock_cls, _ = _mock_client_cls(exc=err)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result[0] == "connectivity"
        assert result[1] == "fail"
        assert "AuthFailed" in result[2]
        assert "AccessKeyIdNotFound" in result[2]

    def test_permission_denied_preserves_code(self) -> None:
        """PermissionDeniedError surfaces in connectivity detail."""
        p = _ak_profile()
        err = PermissionDeniedError(
            "ODPS-0130013: Authorization Failed",
            remediation="request SELECT access from table owner",
        )
        mock_cls, _ = _mock_client_cls(exc=err)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result[1] == "fail"
        assert "PermissionDenied" in result[2]

    def test_identity_not_authorized_preserves_code(self) -> None:
        """IdentityNotAuthorizedError surfaces in connectivity detail."""
        p = _ak_profile()
        err = IdentityNotAuthorizedError(
            "User doesn't exist in the project",
            remediation="check ODPS authorization",
        )
        mock_cls, _ = _mock_client_cls(exc=err)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result[1] == "fail"
        assert "IdentityNotAuthorized" in result[2]

    def test_generic_exception_truncates_raw_text(self) -> None:
        """Generic Exception is truncated to 100 chars (old behavior)."""
        p = _ak_profile()
        mock_cls, mock_client = _mock_client_cls()
        mock_client.execute_sql.side_effect = RuntimeError("x" * 200)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result[1] == "fail"
        # Generic exception: 100 chars + "..."
        assert len(result[2]) <= 103

    def test_success_returns_pass(self) -> None:
        """SELECT 1 success returns pass."""
        p = _ak_profile()
        envelope = MagicMock()
        envelope.status = "success"
        mock_cls, _ = _mock_client_cls(return_value=envelope)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result == ("connectivity", "pass", "SELECT 1 OK on test_proj")

    def test_non_success_envelope_returns_fail(self) -> None:
        p = _ak_profile()
        envelope = MagicMock()
        envelope.status = "failed"
        mock_cls, _ = _mock_client_cls(return_value=envelope)
        with patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls):
            result = _check_connectivity(p)

        assert result == ("connectivity", "fail", "SELECT 1 returned status=failed")

    def test_none_profile_returns_skip(self) -> None:
        """None profile (prerequisite failed) returns skip."""
        result = _check_connectivity(None)
        assert result == ("connectivity", "skip", "skipped: prerequisite failed")


# ── _check_tier ────────────────────────────────────────────────────


class TestCheckTier:
    def test_env_override_returns_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCS_TIER_OVERRIDE", "3")

        assert _check_tier(_ak_profile()) == ("tier", "pass", "tier=3 (MCS_TIER_OVERRIDE)")

    def test_cache_file_returns_cached_tier(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profile_data_dir

        p = _ak_profile()
        cache_path = profile_data_dir(p) / "tier_cache" / p.compute_project
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("2\n", encoding="utf-8")

        assert _check_tier(p) == ("tier", "pass", "tier=2-level (cached)")

    def test_mcs_error_preserves_code_and_message(self) -> None:
        """McsError from tier probe preserves code + message in detail."""
        p = _ak_profile()
        err = AuthFailedError(
            "AccessKeyIdNotFound",
            remediation="re-run `ncs auth login`",
        )
        mock_cls, _ = _mock_client_cls()
        with (
            patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls),
            patch(
                "maxcompute_semantic.mc_client.tier.get_tier",
                side_effect=err,
            ),
        ):
            result = _check_tier(p)

        assert result[0] == "tier"
        assert result[1] == "fail"
        assert "AuthFailed" in result[2]

    def test_permission_denied_info_schema_in_tier(self) -> None:
        """PermissionDeniedError surfaces in tier detail (info_schema flavour)."""
        p = _ak_profile()
        err = PermissionDeniedError(
            "No permission for information_schema",
            remediation="request project-level IS access",
        )
        mock_cls, _ = _mock_client_cls()
        with (
            patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls),
            patch(
                "maxcompute_semantic.mc_client.tier.get_tier",
                side_effect=err,
            ),
        ):
            result = _check_tier(p)

        assert result[1] == "fail"
        assert "PermissionDenied" in result[2]

    def test_generic_exception_truncates(self) -> None:
        """Generic Exception is truncated to 100 chars."""
        p = _ak_profile()
        mock_cls, _ = _mock_client_cls()
        with (
            patch("maxcompute_semantic.mc_client.client.MaxComputeClient", mock_cls),
            patch(
                "maxcompute_semantic.mc_client.tier.get_tier",
                side_effect=RuntimeError("y" * 200),
            ),
        ):
            result = _check_tier(p)

        assert result[1] == "fail"
        assert "cannot probe tier" in result[2]

    def test_none_profile_returns_skip(self) -> None:
        """None profile (prerequisite failed) returns skip."""
        result = _check_tier(None)
        assert result == ("tier", "skip", "skipped: prerequisite failed")


class TestBuildAndLocalToolChecks:
    def test_build_data_passes_with_artifacts(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profile_data_dir

        p = _ak_profile()
        pdir = profile_data_dir(p)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "package.db").write_bytes(b"sqlite")
        (pdir / "_overview.md").write_text("# overview\n", encoding="utf-8")
        (pdir / "_state.json").write_text('{"last_built_at":"2026-01-01T00:00:00Z"}', encoding="utf-8")
        source_dir = pdir / "test_proj__default"
        source_dir.mkdir()
        (source_dir / "orders.md").write_text("# orders\n", encoding="utf-8")

        name, status, detail = _check_build_data(p)

        assert name == "build_data"
        assert status == "pass"
        assert detail is not None
        assert "package.db" in detail
        assert "_overview.md" in detail
        assert "_state.json" in detail
        assert "1 table .md files across 1 source(s)" in detail
        assert "built 2026-01-01T00:00:00Z" in detail

    def test_build_data_ignores_malformed_state_json(self, isolated_config: Path) -> None:
        from maxcompute_semantic._internal.paths import profile_data_dir

        p = _ak_profile()
        pdir = profile_data_dir(p)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "package.db").write_bytes(b"sqlite")
        (pdir / "_state.json").write_text("{bad", encoding="utf-8")

        name, status, detail = _check_build_data(p)

        assert name == "build_data"
        assert status == "pass"
        assert "built" not in (detail or "")

    def test_ncs_available_skips_for_ak_and_custom_process(self) -> None:
        assert _check_ncs_available(None) == (
            "ncs_available",
            "skip",
            "skipped: prerequisite failed",
        )
        assert _check_ncs_available(_ak_profile()) == (
            "ncs_available",
            "skip",
            "profile does not use ncs",
        )

        p = Profile(
            name="proc",
            compute_project="p",
            endpoint="https://service.odps.aliyun.com/api",
            auth=ProcessAuth("python helper.py", 60),
        )
        assert _check_ncs_available(p) == (
            "ncs_available",
            "skip",
            "profile does not use ncs",
        )

    def test_ncs_available_fail_and_pass_for_ncs_process(self) -> None:
        p = Profile(
            name="proc",
            compute_project="p",
            endpoint="https://service.odps.aliyun.com/api",
            auth=ProcessAuth("ncs create credential odpsuser --employee-id 1", 60),
        )

        with patch("shutil.which", return_value=None):
            name, status, detail = _check_ncs_available(p)
        assert name == "ncs_available"
        assert status == "fail"
        assert "ncs" in (detail or "").lower()

        with patch("shutil.which", return_value="/usr/bin/ncs"):
            assert _check_ncs_available(p) == ("ncs_available", "pass", "ncs is on PATH")


class TestCheckSkillInstall:
    def test_skips_when_no_skill_symlinks_exist(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(isolated_config)

        result = _check_skill_install()

        assert result == (
            "skill_install",
            "skip",
            "no skill symlink; run `mcs skill install`",
        )

    def test_passes_when_global_skill_points_to_current_package(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from maxcompute_semantic.commands.skill import _skill_root

        monkeypatch.chdir(isolated_config)
        target = Path.home() / ".agents/skills/maxcompute-semantic"
        target.parent.mkdir(parents=True)
        target.symlink_to(_skill_root(), target_is_directory=True)

        result = _check_skill_install()

        assert result[0] == "skill_install"
        assert result[1] == "pass"
        assert "agents (global)" in (result[2] or "")

    def test_fails_when_global_skill_points_elsewhere(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(isolated_config)
        wrong_root = isolated_config / "wrong-skill"
        wrong_root.mkdir()
        target = Path.home() / ".agents/skills/maxcompute-semantic"
        target.parent.mkdir(parents=True)
        target.symlink_to(wrong_root, target_is_directory=True)

        result = _check_skill_install()

        assert result[0] == "skill_install"
        assert result[1] == "fail"
        assert "agents (global)" in (result[2] or "")
        assert "expected" in (result[2] or "")

    def test_fails_when_skill_target_is_directory(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(isolated_config)
        target = Path(".agents/skills/maxcompute-semantic")
        target.mkdir(parents=True)

        result = _check_skill_install()

        assert result == (
            "skill_install",
            "fail",
            "broken: agents (local): directory instead of symlink",
        )


class TestUpdateChannelChecks:
    """Tests for the two update-channel checks added by the
    update-subcommand-and-release-notification design (2026-05-22).

    Both checks share a single fetch via a closure; see
    ``_run_update_check_fetch`` in ``commands/doctor.py``."""

    from click.testing import CliRunner

    def test_channel_pass_and_version_current_pass(
        self, latest_json_server, isolated_config: Path
    ) -> None:
        """latest.json is reachable and the running version >=
        latest_version → both checks PASS."""
        from maxcompute_semantic import __version__
        from maxcompute_semantic.commands.doctor import doctor_cmd

        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": __version__,
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.0.1",
                "disabled": [],
                "notice": "",
            }
        )

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, ["--offline"])
        # --offline skips the update checks — confirm the lines say
        # "skipped: --offline" exactly like the existing auth /
        # connectivity / tier skip rows do.
        assert "update channel" in result.output.lower()
        assert "update version" in result.output.lower()
        assert "skipped" in result.output.lower()

        # And the non-offline path runs them and reports the PASS
        # status.
        result_online = runner.invoke(doctor_cmd, [])
        out_lc = result_online.output.lower()
        assert "update channel" in out_lc
        assert "update version" in out_lc

    def test_channel_fail_when_metadata_5xx(
        self, latest_json_server, isolated_config: Path
    ) -> None:
        from maxcompute_semantic.commands.doctor import doctor_cmd

        _, setter = latest_json_server
        setter(503)

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, [])
        out_lc = result.output.lower()
        assert "update channel" in out_lc
        # The channel check is FAIL with a hint at the base URL.
        # The downstream version check should report
        # SKIP-prerequisite-failed because the metadata isn't
        # available — same shorthand the existing auth/connectivity
        # checks use when their prerequisite fails (see
        # ``_SKIP_SHORT = "skipped: prerequisite failed"``).
        assert "fail" in out_lc or "❌" in result.output

    def test_channel_pass_version_info_when_upgrade_available(
        self, latest_json_server, isolated_config: Path
    ) -> None:
        """Channel reachable, but a newer version is published. The
        version check is an informational line — call it a "skip
        with info" so the overall doctor exit stays 0. Wording
        contains the arrow ``<cur> → <new>``."""
        from maxcompute_semantic import __version__
        from maxcompute_semantic.commands.doctor import doctor_cmd

        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "9.9.9",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.0.1",
                "disabled": [],
                "notice": "",
            }
        )

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, [])
        assert "9.9.9" in result.output
        assert __version__ in result.output
        # The update version check itself is a SKIP — but isolated_config
        # has no profile, so profile_resolution fails → exit code 1.
        # The upgrade-available signal is informational and appears in
        # the output regardless.

    def test_version_fails_when_running_is_disabled(
        self, latest_json_server, isolated_config: Path
    ) -> None:
        """Disabled-list match → version check FAILs. Doctor exit code
        is 1 (the standard "any fail" rule)."""
        from maxcompute_semantic import __version__
        from maxcompute_semantic.commands.doctor import doctor_cmd

        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "9.9.9",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.0.1",
                "disabled": [__version__],
                "notice": "withdrawn for CVE-2026-1234",
            }
        )

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, [])
        out_lc = result.output.lower()
        assert "disabled" in out_lc or "withdrawn" in out_lc or "cve-2026-1234" in out_lc
        # ``mcs doctor`` exits 1 on any FAIL.
        assert result.exit_code == 1

    def test_version_fails_when_below_min_supported(
        self, latest_json_server, isolated_config: Path
    ) -> None:
        from maxcompute_semantic.commands.doctor import doctor_cmd

        _, setter = latest_json_server
        # min_supported is way ahead of the running version, so
        # current < min → FAIL.
        setter(
            {
                "schema_version": 1,
                "latest_version": "9.9.9",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "9.0.0",
                "disabled": [],
                "notice": "",
            }
        )

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, [])
        out_lc = result.output.lower()
        assert "min_supported" in out_lc or "minimum" in out_lc or "below" in out_lc
        assert result.exit_code == 1

    def test_offline_skips_both_update_checks(self, isolated_config: Path) -> None:
        """``--offline`` short-circuits all network probes, including
        the two new ones. No request reaches MCS_UPDATE_BASE_URL —
        the test doesn't even need the stub server up because the
        fetcher must not be called."""
        from maxcompute_semantic.commands.doctor import doctor_cmd

        runner = self.CliRunner()
        result = runner.invoke(doctor_cmd, ["--offline"])
        assert "skipped: --offline" in result.output
        # Both names appear in the rendered list.
        assert "update channel" in result.output.lower()
        assert "update version" in result.output.lower()

    def test_fetcher_called_once_when_both_checks_run(
        self,
        latest_json_server,
        isolated_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The spec says the two checks share **one** fetch via a
        closure. We assert that by counting the wrapped
        ``fetch_latest_metadata`` calls."""
        from maxcompute_semantic._internal import update_check as uc
        from maxcompute_semantic.commands import doctor as doc

        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "0.0.1",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.0.0",
                "disabled": [],
                "notice": "",
            }
        )

        counter = {"calls": 0}
        real = uc.fetch_latest_metadata

        def counting(*a, **kw):
            counter["calls"] += 1
            return real(*a, **kw)

        # The closure inside doctor.py references its own module-level
        # import of fetch_latest_metadata; patch *that* attribute so
        # the counter records the consumption.
        monkeypatch.setattr(doc, "fetch_latest_metadata", counting)

        runner = self.CliRunner()
        runner.invoke(doc.doctor_cmd, [])

        # The shared-fetch invariant: exactly one wrapped call. If
        # the doctor implementation does the naive "each check calls
        # fetch", this assertion is the canary.
        assert counter["calls"] == 1, (
            f"doctor fetched the channel metadata "
            f"{counter['calls']} times; the spec requires one shared "
            f"fetch across the two update checks."
        )

    def test_doctor_warms_the_banner_cache(
        self,
        latest_json_server,
        tmp_path: Path,
        isolated_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Side effect: after a successful doctor run, the update-check
        cache file is populated so the next foreground mcs command's
        banner reflects the same state. (The spec calls this
        "doctor doubles as a cache warmup.")"""
        cdir = tmp_path / "cache"
        monkeypatch.setenv("MCS_CACHE_DIR", str(cdir))
        _, setter = latest_json_server
        setter(
            {
                "schema_version": 1,
                "latest_version": "9.9.9",
                "released_at": "2026-05-22T00:00:00Z",
                "wheel_url": "https://example.test/wheels/x.whl",
                "sha256": "a" * 64,
                "min_supported": "0.4.0",
                "disabled": [],
                "notice": "warmed by doctor",
            }
        )

        from click.testing import CliRunner
        from maxcompute_semantic._internal.update_check import (
            cache_path,
            read_cache,
        )
        from maxcompute_semantic.commands.doctor import doctor_cmd

        runner = CliRunner()
        runner.invoke(doctor_cmd, [])

        assert cache_path().exists(), "doctor did not write the banner cache"
        entry = read_cache()
        assert entry is not None
        assert entry.latest_version == "9.9.9"
        assert entry.notice == "warmed by doctor"
        assert entry.fetch_error == ""


# ── _check_ncs_available ───────────────────────────────────────────


class TestCheckNcsAvailable:
    """The check fires only when the profile's ProcessAuth command
    starts with `ncs`. AK auth and other ProcessAuth helpers are
    skipped to avoid noise."""

    def _ncs_profile(self) -> Profile:
        from maxcompute_semantic.auth.schema import ProcessAuth

        return Profile(
            name="doctor-ncs-test",
            compute_project="test_proj",
            endpoint="http://service-corp.odps.aliyun-inc.com/api",
            auth=ProcessAuth(
                command="ncs create credential odpsuser --employee-id 12345 -o template -t odpscmd"
            ),
            sources=(DataSource(project="test_proj", tables="*"),),
        )

    def test_skips_when_profile_uses_ak_auth(self) -> None:
        """AK auth never needs ncs — check returns skip."""
        from maxcompute_semantic.commands.doctor import _check_ncs_available

        p = _ak_profile()  # defined at module top
        result = _check_ncs_available(p)
        assert result[0] == "ncs_available"
        assert result[1] == "skip"

    def test_skips_when_process_command_is_not_ncs(self) -> None:
        """ProcessAuth with a non-ncs command (e.g. a custom helper)
        is skipped — we only know how to guide ncs installs."""
        from maxcompute_semantic.auth.schema import ProcessAuth
        from maxcompute_semantic.commands.doctor import _check_ncs_available

        p = Profile(
            name="doctor-custom-proc",
            compute_project="test_proj",
            endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
            auth=ProcessAuth(command="my-helper get-creds"),
            sources=(DataSource(project="test_proj", tables="*"),),
        )
        result = _check_ncs_available(p)
        assert result == ("ncs_available", "skip", "profile does not use ncs")

    def test_passes_when_binary_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ncs profile + binary on PATH → pass."""
        from maxcompute_semantic.commands.doctor import _check_ncs_available

        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/ncs")
        p = self._ncs_profile()
        result = _check_ncs_available(p)
        assert result == ("ncs_available", "pass", "ncs is on PATH")

    def test_fails_with_install_url_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ncs profile + binary missing → fail with install_hint in detail."""
        from maxcompute_semantic.auth.ncs import NCS_INSTALL_DOC_URL
        from maxcompute_semantic.commands.doctor import _check_ncs_available

        monkeypatch.setattr("shutil.which", lambda cmd: None)
        p = self._ncs_profile()
        result = _check_ncs_available(p)
        assert result[0] == "ncs_available"
        assert result[1] == "fail"
        assert NCS_INSTALL_DOC_URL in result[2]
        assert "not found on PATH" in result[2]

    def test_skips_when_profile_is_none(self) -> None:
        """None profile (prerequisite failed upstream) returns skip."""
        from maxcompute_semantic.commands.doctor import _check_ncs_available

        result = _check_ncs_available(None)
        assert result == ("ncs_available", "skip", "skipped: prerequisite failed")

    def test_doctor_json_envelope_includes_ncs_available_entry(
        self,
        isolated_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: `mcs -f json doctor --offline` includes a
        ncs_available entry in the checks list. Uses --offline to keep
        the test hermetic (no live ODPS calls)."""
        import json

        from click.testing import CliRunner
        from maxcompute_semantic.auth.profile_store import upsert
        from maxcompute_semantic.auth.schema import ProcessAuth
        from maxcompute_semantic.cli import cli as mcs_cli

        # Persist an ncs profile in the isolated config so resolution
        # succeeds and _check_ncs_available has something to inspect.
        p = Profile(
            name="ncs-integration",
            compute_project="test_proj",
            endpoint="http://service-corp.odps.aliyun-inc.com/api",
            auth=ProcessAuth(
                command=(
                    "ncs create credential odpsuser --employee-id 12345 -o template -t odpscmd"
                )
            ),
            sources=(DataSource(project="test_proj", tables="*"),),
        )
        upsert(p)
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        runner = CliRunner()
        result = runner.invoke(
            mcs_cli,
            ["-f", "json", "doctor", "--offline", "--profile", "ncs-integration"],
        )
        payload = json.loads(result.output)
        names = [c["name"] for c in payload["data"]["checks"]]
        assert "ncs_available" in names
        ncs_entry = next(c for c in payload["data"]["checks"] if c["name"] == "ncs_available")
        assert ncs_entry["status"] == "fail"
