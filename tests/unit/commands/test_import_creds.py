# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/_import_creds.py + the import-creds CLI verb."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profiles_yaml_path
from maxcompute_semantic._internal.yaml_io import dump_yaml
from maxcompute_semantic.auth.profile_store import get, load_all, upsert
from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    ProcessAuth,
    Profile,
)
from maxcompute_semantic.commands._import_creds import (
    ImportedCreds,
    _classify_auth_kind,
    _default_locations,
    _maxc_default_config_path,
    _odpscmd_default_config_path,
    discover_mcs_profiles,
    discover_creds,
    is_canonical_ncs_process_auth,
    parse_creds_at,
    parse_maxc_config,
    parse_odpscmd_config,
)
from maxcompute_semantic.commands.profile import profile_group

# Canonical ncs command used in fixtures
_NCS_CMD = "ncs create credential odpsuser --employee-id 123456 -o template -t odpscmd"
_NCS_CMD_EID1 = "ncs create credential odpsuser --employee-id 1 -o template -t odpscmd"
_CUSTOM_PROCESS_CMD = "python -m company_sts_helper --profile odps"

_VALID_MAXC_YAML = """\
auth:
  provider: access_key
  access_id: FakeAKID0002
  secret_access_key: SecretMaxcVal
  project: maxc_proj
  endpoint: https://service.cn-shanghai.maxcompute.aliyun.com/api
default_project: maxc_proj
"""

_VALID_MAXC_PROCESS_YAML = (
    "auth:\n"
    "  provider: external\n"
    "  project: maxc_process_proj\n"
    "  endpoint: https://service.cn-shanghai.maxcompute.aliyun.com/api\n"
    "  external:\n"
    f"    process_command: {_NCS_CMD}\n"
    "    process_timeout: 30\n"
)

_VALID_ODPSCMD_INI = """\
project_name=odpscmd_proj
access_id=FakeAKID0003
access_key=SecretOdpscmdVal
end_point=https://service.cn-hangzhou.maxcompute.aliyun.com/api
log_view_host=http://logview.odps.aliyun.com
"""

_VALID_ODPSCMD_PROCESS_INI = (
    "account_provider=external\n"
    f"processCommand={_NCS_CMD}\n"
    "processCommandTimeout=20\n"
    "project_name=odpscmd_process_proj\n"
    "end_point=https://service.cn-hangzhou.maxcompute.aliyun.com/api\n"
)

_CUSTOM_ODPSCMD_PROCESS_INI = (
    "account_provider=external\n"
    f"processCommand={_CUSTOM_PROCESS_CMD}\n"
    "processCommandTimeout=20\n"
    "project_name=odpscmd_custom_process_proj\n"
    "end_point=https://service.cn-hangzhou.maxcompute.aliyun.com/api\n"
)

# Process config with no explicit timeout (defaults to 60)
_VALID_ODPSCMD_PROCESS_NO_TIMEOUT_INI = (
    "account_provider=external\n"
    f"processCommand={_NCS_CMD}\n"
    "project_name=odpscmd_no_timeout_proj\n"
    "end_point=https://service.cn-hangzhou.maxcompute.aliyun.com/api\n"
)

# Process config with both external provider AND AK fields — process wins
_ODPSCMD_PROCESS_PLUS_AK_INI = (
    "account_provider=external\n"
    f"processCommand={_NCS_CMD}\n"
    "processCommandTimeout=20\n"
    "project_name=mixed_proj\n"
    "access_id=FakeAKID0004\n"
    "access_key=SecretMixed\n"
    "end_point=https://service.cn-hangzhou.maxcompute.aliyun.com/api\n"
)


# ── parse_maxc_config ────────────────────────────────────────────────


class TestParseMaxc:
    def test_valid_ak(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(_VALID_MAXC_YAML, encoding="utf-8")
        creds = parse_maxc_config(p)
        assert creds is not None
        assert isinstance(creds.auth, AkAuth)
        assert creds.auth.access_key_id == "FakeAKID0002"
        assert creds.auth.access_key_secret == "SecretMaxcVal"
        assert creds.compute_project == "maxc_proj"
        assert creds.endpoint == "https://service.cn-shanghai.maxcompute.aliyun.com/api"
        assert creds.source_label == "maxc"

    def test_valid_process(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(_VALID_MAXC_PROCESS_YAML, encoding="utf-8")
        creds = parse_maxc_config(p)
        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.auth.command == _NCS_CMD
        assert creds.auth.timeout == 30
        assert creds.compute_project == "maxc_process_proj"
        assert creds.source_label == "maxc"

    def test_falls_back_to_default_project(self, tmp_path: Path) -> None:
        """When auth.project is missing, fall back to top-level default_project."""
        yaml = """\
auth:
  provider: access_key
  access_id: X
  secret_access_key: Y
  endpoint: https://x/api
default_project: top_level_proj
"""
        p = tmp_path / "config.yaml"
        p.write_text(yaml, encoding="utf-8")
        creds = parse_maxc_config(p)
        assert creds is not None
        assert creds.compute_project == "top_level_proj"

    def test_rejects_non_ak_provider(self, tmp_path: Path) -> None:
        yaml = """\
auth:
  provider: ram_role
  role_arn: acs:ram:::role/foo
  endpoint: https://x/api
"""
        p = tmp_path / "config.yaml"
        p.write_text(yaml, encoding="utf-8")
        assert parse_maxc_config(p) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert parse_maxc_config(tmp_path / "nonexistent.yaml") is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(":\n  - [garbage", encoding="utf-8")
        assert parse_maxc_config(p) is None

    def test_non_mapping_yaml_is_not_importable(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

        assert parse_maxc_config(p) is None

    def test_external_provider_rejects_missing_external_block(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
auth:
  provider: external
  project: p
  endpoint: https://x/api
""",
            encoding="utf-8",
        )

        assert parse_maxc_config(p) is None

    def test_external_provider_rejects_empty_process_command(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
auth:
  provider: external
  project: p
  endpoint: https://x/api
  external:
    process_command: ""
""",
            encoding="utf-8",
        )

        assert parse_maxc_config(p) is None

    def test_external_provider_bad_timeout_defaults_to_60(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            f"""\
auth:
  provider: external
  project: p
  endpoint: https://x/api
  external:
    process_command: {_NCS_CMD}
    process_timeout: not-a-number
""",
            encoding="utf-8",
        )

        creds = parse_maxc_config(p)

        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.auth.timeout == 60

    def test_external_provider_requires_project_and_endpoint(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            f"""\
auth:
  provider: external
  external:
    process_command: {_NCS_CMD}
""",
            encoding="utf-8",
        )

        assert parse_maxc_config(p) is None

    def test_access_key_provider_requires_all_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
auth:
  provider: access_key
  access_id: X
  endpoint: https://x/api
default_project: p
""",
            encoding="utf-8",
        )

        assert parse_maxc_config(p) is None


# ── parse_odpscmd_config ─────────────────────────────────────────────


class TestParseOdpscmd:
    def test_valid_ak(self, tmp_path: Path) -> None:
        p = tmp_path / "odps_config.ini"
        p.write_text(_VALID_ODPSCMD_INI, encoding="utf-8")
        creds = parse_odpscmd_config(p)
        assert creds is not None
        assert isinstance(creds.auth, AkAuth)
        assert creds.auth.access_key_id == "FakeAKID0003"
        assert creds.auth.access_key_secret == "SecretOdpscmdVal"
        assert creds.compute_project == "odpscmd_proj"
        assert creds.endpoint == "https://service.cn-hangzhou.maxcompute.aliyun.com/api"
        assert creds.source_label == "odpscmd"

    def test_valid_process(self, tmp_path: Path) -> None:
        p = tmp_path / "odps_config.ini"
        p.write_text(_VALID_ODPSCMD_PROCESS_INI, encoding="utf-8")
        creds = parse_odpscmd_config(p)
        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.auth.command == _NCS_CMD
        assert creds.auth.timeout == 20
        assert creds.compute_project == "odpscmd_process_proj"
        assert creds.endpoint == "https://service.cn-hangzhou.maxcompute.aliyun.com/api"
        assert creds.source_label == "odpscmd"

    def test_process_no_timeout_defaults_to_60(self, tmp_path: Path) -> None:
        p = tmp_path / "odps_config.ini"
        p.write_text(_VALID_ODPSCMD_PROCESS_NO_TIMEOUT_INI, encoding="utf-8")
        creds = parse_odpscmd_config(p)
        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.auth.timeout == 60

    def test_process_wins_over_ak_when_provider_external(self, tmp_path: Path) -> None:
        """Both account_provider=external and access_id present →
        process auth takes priority (AK fields are irrelevant with
        external provider)."""
        p = tmp_path / "odps_config.ini"
        p.write_text(_ODPSCMD_PROCESS_PLUS_AK_INI, encoding="utf-8")
        creds = parse_odpscmd_config(p)
        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.compute_project == "mixed_proj"

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """access_key empty → reject."""
        ini = """\
project_name=p
access_id=X
end_point=https://x/api
"""
        p = tmp_path / "odps_config.ini"
        p.write_text(ini, encoding="utf-8")
        assert parse_odpscmd_config(p) is None

    def test_process_missing_project(self, tmp_path: Path) -> None:
        """Process auth but no project_name → reject."""
        ini = """\
account_provider=external
processCommand=ncs create credential odpsuser --employee-id 1 -o template -t odpscmd
end_point=https://x/api
"""
        p = tmp_path / "odps_config.ini"
        p.write_text(ini, encoding="utf-8")
        assert parse_odpscmd_config(p) is None

    def test_process_missing_endpoint(self, tmp_path: Path) -> None:
        """Process auth but no end_point → reject."""
        ini = """\
account_provider=external
processCommand=ncs create credential odpsuser --employee-id 1 -o template -t odpscmd
project_name=p
"""
        p = tmp_path / "odps_config.ini"
        p.write_text(ini, encoding="utf-8")
        assert parse_odpscmd_config(p) is None

    def test_process_empty_command(self, tmp_path: Path) -> None:
        """account_provider=external but processCommand is empty →
        fall through to AK path (which also fails if no AK fields)."""
        ini = """\
account_provider=external
processCommand=
project_name=p
end_point=https://x/api
"""
        p = tmp_path / "odps_config.ini"
        p.write_text(ini, encoding="utf-8")
        assert parse_odpscmd_config(p) is None

    def test_with_comments(self, tmp_path: Path) -> None:
        ini = """\
# this is a comment
project_name=p
# another comment
access_id=A
access_key=B
end_point=https://x/api
"""
        p = tmp_path / "odps_config.ini"
        p.write_text(ini, encoding="utf-8")
        creds = parse_odpscmd_config(p)
        assert creds is not None
        assert isinstance(creds.auth, AkAuth)
        assert creds.compute_project == "p"

    def test_missing_file(self, tmp_path: Path) -> None:
        assert parse_odpscmd_config(tmp_path / "nonexistent.ini") is None

    def test_malformed_properties_file_is_not_importable(self, tmp_path: Path) -> None:
        p = tmp_path / "odps_config.ini"
        p.write_text("[unterminated\n", encoding="utf-8")

        assert parse_odpscmd_config(p) is None

    def test_process_bad_timeout_defaults_to_60(self, tmp_path: Path) -> None:
        p = tmp_path / "odps_config.ini"
        p.write_text(
            """\
account_provider=external
processCommand=ncs create credential odpsuser --employee-id 1 -o template -t odpscmd
processCommandTimeout=not-a-number
project_name=p
end_point=https://x/api
""",
            encoding="utf-8",
        )

        creds = parse_odpscmd_config(p)

        assert creds is not None
        assert isinstance(creds.auth, ProcessAuth)
        assert creds.auth.timeout == 60


# ── default discovery locations ──────────────────────────────────────


class TestDefaultCredentialLocations:
    def test_odpscmd_default_config_path_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "maxcompute_semantic.commands._import_creds.shutil.which",
            lambda _name: None,
        )

        assert _odpscmd_default_config_path() is None

    def test_odpscmd_default_config_path_resolves_sibling_conf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_root = tmp_path / "odpscmd_public"
        binary = install_root / "bin" / "odpscmd"
        config = install_root / "conf" / "odps_config.ini"
        binary.parent.mkdir(parents=True)
        config.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        config.write_text(_VALID_ODPSCMD_INI, encoding="utf-8")
        monkeypatch.setattr(
            "maxcompute_semantic.commands._import_creds.shutil.which",
            lambda _name: str(binary),
        )

        assert _odpscmd_default_config_path() == config

    def test_maxc_default_config_path_uses_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        path = home / ".maxc" / "config.yaml"
        path.parent.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))

        assert _maxc_default_config_path() is None

        path.write_text(_VALID_MAXC_YAML, encoding="utf-8")
        assert _maxc_default_config_path() == path

    def test_default_locations_preserve_maxc_then_odpscmd_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        maxc = tmp_path / "maxc.yaml"
        odpscmd = tmp_path / "odps_config.ini"
        monkeypatch.setattr(
            "maxcompute_semantic.commands._import_creds._maxc_default_config_path",
            lambda: maxc,
        )
        monkeypatch.setattr(
            "maxcompute_semantic.commands._import_creds._odpscmd_default_config_path",
            lambda: odpscmd,
        )

        assert _default_locations() == [("maxc", maxc), ("odpscmd", odpscmd)]

    def test_parse_creds_at_unknown_label_returns_none(self, tmp_path: Path) -> None:
        assert parse_creds_at("unknown", tmp_path / "creds") is None

    def test_discover_creds_keeps_only_parseable_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        maxc = tmp_path / "config.yaml"
        odpscmd = tmp_path / "odps_config.ini"
        maxc.write_text("auth: {provider: ram_role}\n", encoding="utf-8")
        odpscmd.write_text(_VALID_ODPSCMD_INI, encoding="utf-8")
        monkeypatch.setattr(
            "maxcompute_semantic.commands._import_creds._default_locations",
            lambda: [("maxc", maxc), ("odpscmd", odpscmd)],
        )

        found = discover_creds()

        assert len(found) == 1
        assert found[0].source_label == "odpscmd"
        assert found[0].compute_project == "odpscmd_proj"


# ── ImportedCreds.display() ──────────────────────────────────────────


class TestImportedCredsDisplay:
    def test_ak_display(self, tmp_path: Path) -> None:
        creds = ImportedCreds(
            source_label="maxc",
            source_path=tmp_path / "config.yaml",
            auth=AkAuth(access_key_id="X", access_key_secret="Y"),
            compute_project="proj",
            endpoint="https://x/api",
        )
        label = creds.display()
        assert "[AK]" in label
        assert "[process]" not in label

    def test_process_display(self, tmp_path: Path) -> None:
        creds = ImportedCreds(
            source_label="odpscmd",
            source_path=tmp_path / "odps_config.ini",
            auth=ProcessAuth(command=_NCS_CMD_EID1),
            compute_project="proj",
            endpoint="https://x/api",
        )
        label = creds.display()
        assert "[process]" in label
        assert "[AK]" not in label


# ── mcs profile import-creds ──────────────────────────────────────────


def _runner_invoke(args: list[str], **kwargs):
    runner = CliRunner()
    return runner.invoke(profile_group, args, obj={"format": "plain"}, **kwargs)


def test_import_creds_from_maxc_explicit_path(isolated_config: Path, tmp_path: Path) -> None:
    """`mcs profile import-creds --source maxc --config-path PATH` round-trip."""
    config = tmp_path / "maxc_config.yaml"
    config.write_text(_VALID_MAXC_YAML, encoding="utf-8")

    result = _runner_invoke(
        ["import-creds", "--source", "maxc", "--config-path", str(config), "--no-test"]
    )
    assert result.exit_code == 0, result.output
    p = get("maxc_proj")
    assert p.compute_project == "maxc_proj"
    assert isinstance(p.auth, AkAuth)
    assert p.auth.access_key_id == "FakeAKID0002"
    assert p.auth.access_key_secret == "SecretMaxcVal"
    assert "imported maxc credentials" in result.output


def test_import_creds_from_odpscmd_process(isolated_config: Path, tmp_path: Path) -> None:
    """`mcs profile import-creds --source odpscmd` with process auth."""
    config = tmp_path / "odps_config.ini"
    config.write_text(_VALID_ODPSCMD_PROCESS_INI, encoding="utf-8")

    result = _runner_invoke(
        ["import-creds", "--source", "odpscmd", "--config-path", str(config), "--no-test"],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    p = get("odpscmd_process_proj")
    assert p.compute_project == "odpscmd_process_proj"
    assert isinstance(p.auth, ProcessAuth)
    assert p.auth.command == _NCS_CMD
    assert p.auth.timeout == 20
    assert "imported odpscmd credentials" in result.output


def test_import_creds_custom_process_requires_trust_flag(
    isolated_config: Path, tmp_path: Path
) -> None:
    config = tmp_path / "odps_config.ini"
    config.write_text(_CUSTOM_ODPSCMD_PROCESS_INI, encoding="utf-8")

    result = _runner_invoke(
        ["import-creds", "--source", "odpscmd", "--config-path", str(config), "--no-test"],
        input="y\n",
    )

    assert result.exit_code != 0
    assert "--trust-process-command" in result.output
    assert _CUSTOM_PROCESS_CMD in result.output
    assert "odpscmd_custom_process_proj" not in load_all()


def test_import_creds_custom_process_trust_flag_imports(
    isolated_config: Path, tmp_path: Path
) -> None:
    config = tmp_path / "odps_config.ini"
    config.write_text(_CUSTOM_ODPSCMD_PROCESS_INI, encoding="utf-8")

    result = _runner_invoke(
        [
            "import-creds",
            "--source",
            "odpscmd",
            "--config-path",
            str(config),
            "--no-test",
            "--trust-process-command",
        ]
    )

    assert result.exit_code == 0, result.output
    p = get("odpscmd_custom_process_proj")
    assert isinstance(p.auth, ProcessAuth)
    assert p.auth.command == _CUSTOM_PROCESS_CMD


def test_import_creds_alias_override(isolated_config: Path, tmp_path: Path) -> None:
    config = tmp_path / "maxc_config.yaml"
    config.write_text(_VALID_MAXC_YAML, encoding="utf-8")
    result = _runner_invoke(
        [
            "import-creds",
            "--source",
            "maxc",
            "--config-path",
            str(config),
            "--alias",
            "custom_alias",
            "--no-test",
        ]
    )
    assert result.exit_code == 0, result.output
    p = get("custom_alias")
    assert p.name == "custom_alias"
    assert p.compute_project == "maxc_proj"


def test_import_creds_auto_picks_only_candidate(isolated_config: Path, tmp_path: Path) -> None:
    """`--source auto` with one match: auto-picks it."""
    fake_creds = ImportedCreds(
        source_label="maxc",
        source_path=tmp_path / "config.yaml",
        auth=AkAuth(access_key_id="X", access_key_secret="Y"),
        compute_project="auto_proj",
        endpoint="https://x/api",
    )
    with patch(
        "maxcompute_semantic.commands._import_creds.discover_creds",
        return_value=[fake_creds],
    ):
        result = _runner_invoke(["import-creds", "--source", "auto", "--no-test"])
    assert result.exit_code == 0, result.output
    p = get("auto_proj")
    assert p.compute_project == "auto_proj"


def test_import_creds_auto_picks_process_candidate(isolated_config: Path, tmp_path: Path) -> None:
    """`--source auto` with a process-auth match: auto-picks it."""
    fake_creds = ImportedCreds(
        source_label="odpscmd",
        source_path=tmp_path / "odps_config.ini",
        auth=ProcessAuth(command=_NCS_CMD_EID1),
        compute_project="auto_process_proj",
        endpoint="https://x/api",
    )
    with patch(
        "maxcompute_semantic.commands._import_creds.discover_creds",
        return_value=[fake_creds],
    ):
        result = _runner_invoke(["import-creds", "--source", "auto", "--no-test"], input="y\n")
    assert result.exit_code == 0, result.output
    p = get("auto_process_proj")
    assert isinstance(p.auth, ProcessAuth)


def test_import_creds_auto_no_candidates_errors(isolated_config: Path) -> None:
    with patch(
        "maxcompute_semantic.commands._import_creds.discover_creds",
        return_value=[],
    ):
        result = _runner_invoke(["import-creds", "--source", "auto", "--no-test"])
    assert result.exit_code == 4
    assert "no credentials discovered" in result.output.lower()


def test_import_creds_collision_decline(isolated_config: Path, tmp_path: Path) -> None:
    """Same-name profile already exists + user declines overwrite → no-op."""
    from maxcompute_semantic.auth.profile_store import upsert
    from maxcompute_semantic.auth.schema import DataSource, Profile

    upsert(
        Profile(
            name="maxc_proj",
            compute_project="existing_proj",
            endpoint="https://existing/api",
            auth=AkAuth("EX_ID", "EX_SECRET"),
            sources=(DataSource("existing_proj", "default", tables="*"),),
        )
    )
    config = tmp_path / "maxc_config.yaml"
    config.write_text(_VALID_MAXC_YAML, encoding="utf-8")

    result = _runner_invoke(
        ["import-creds", "--source", "maxc", "--config-path", str(config), "--no-test"],
        input="n\n",  # decline overwrite
    )
    assert result.exit_code == 0
    # Profile unchanged
    p = get("maxc_proj")
    assert p.compute_project == "existing_proj"


def test_import_creds_malformed_file_errors(isolated_config: Path, tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text(":\n  garbage", encoding="utf-8")
    result = _runner_invoke(
        ["import-creds", "--source", "maxc", "--config-path", str(config), "--no-test"]
    )
    assert result.exit_code == 4
    assert "could not parse" in result.output.lower()


# ── wizard import-discovery hook ──────────────────────────────────────


# Picker echo strings used by the wizard import-discovery hook.
_PICK_SKIP_IMPORT = "➡️  skip — configure manually"
_PICK_ENV_PUBLIC = "public"
_PICK_AUTH_AK = "ak"
_PICK_AK_ENV_VAR = "Env var reference — store env var names, not secrets"


def test_wizard_import_discovery_hook_imports_ak_when_user_picks(
    isolated_config: Path, tmp_path: Path, mock_picker: list[object]
) -> None:
    """In the wizard, after the alias prompt, if AK creds are detected
    the user can pick one — the wizard skips the endpoint / auth
    prompts and uses the imported AK auth."""
    fake_creds = ImportedCreds(
        source_label="odpscmd",
        source_path=tmp_path / "odps_config.ini",
        auth=AkAuth(access_key_id="WizardImported", access_key_secret="SecretImported"),
        compute_project="wizard_imported_proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
    )

    runner = CliRunner()
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_creds],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        # Picker returns the imported-creds display string → wizard
        # short-circuits and skips endpoint / auth prompts.
        mock_picker.append(f"🔑 {fake_creds.display()}")
        # Wizard stdin: alias='wiz', "Configure now?" = n
        result = runner.invoke(
            profile_group,
            ["create", "--no-test"],
            input="wiz\nn\n",
        )
    assert result.exit_code == 0, result.output
    p = get("wiz")
    assert p.compute_project == "wizard_imported_proj"
    assert isinstance(p.auth, AkAuth)
    assert p.auth.access_key_id == "WizardImported"
    assert p.endpoint == "https://service.cn-shanghai.maxcompute.aliyun.com/api"


def test_wizard_import_discovery_hook_imports_process_when_user_picks(
    isolated_config: Path, tmp_path: Path, mock_picker: list[object]
) -> None:
    """In the wizard, process-auth creds are detected and picked —
    the wizard skips endpoint / auth prompts and uses ProcessAuth."""
    fake_creds = ImportedCreds(
        source_label="odpscmd",
        source_path=tmp_path / "odps_config.ini",
        auth=ProcessAuth(command=_NCS_CMD, timeout=20),
        compute_project="wizard_process_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
    )

    runner = CliRunner()
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_creds],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        mock_picker.append(f"🔑 {fake_creds.display()}")
        result = runner.invoke(
            profile_group,
            ["create", "--no-test"],
            input="wiz_proc\ny\nn\n",
        )
    assert result.exit_code == 0, result.output
    p = get("wiz_proc")
    assert p.compute_project == "wizard_process_proj"
    assert isinstance(p.auth, ProcessAuth)
    assert p.auth.command == _NCS_CMD
    assert p.auth.timeout == 20


def test_wizard_import_discovery_hook_custom_process_decline_falls_back_to_manual(
    isolated_config: Path, tmp_path: Path, mock_picker: list[object]
) -> None:
    """Custom ProcessAuth helpers are visible and default to not adopted."""
    fake_creds = ImportedCreds(
        source_label="odpscmd",
        source_path=tmp_path / "odps_config.ini",
        auth=ProcessAuth(command=_CUSTOM_PROCESS_CMD, timeout=20),
        compute_project="wizard_custom_process_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
    )

    runner = CliRunner()
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_creds],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        mock_picker.append(f"🔑 {fake_creds.display()}")
        mock_picker.append(_PICK_ENV_PUBLIC)
        mock_picker.append(_PICK_AUTH_AK)
        mock_picker.append(_PICK_AK_ENV_VAR)
        result = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "manual_after_decline"],
            input="wiz_custom_decline\nn\ncn-shanghai\n\n\nn\n",
        )

    assert result.exit_code == 0, result.output
    assert _CUSTOM_PROCESS_CMD in result.output
    assert "Skipped" in result.output
    p = get("wiz_custom_decline")
    assert p.compute_project == "manual_after_decline"
    assert isinstance(p.auth, AkAuth)


def test_wizard_import_discovery_hook_custom_process_accept_imports(
    isolated_config: Path, tmp_path: Path, mock_picker: list[object]
) -> None:
    fake_creds = ImportedCreds(
        source_label="odpscmd",
        source_path=tmp_path / "odps_config.ini",
        auth=ProcessAuth(command=_CUSTOM_PROCESS_CMD, timeout=20),
        compute_project="wizard_custom_process_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
    )

    runner = CliRunner()
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_creds],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        mock_picker.append(f"🔑 {fake_creds.display()}")
        result = runner.invoke(
            profile_group,
            ["create", "--no-test"],
            input="wiz_custom_accept\ny\nn\n",
        )

    assert result.exit_code == 0, result.output
    assert _CUSTOM_PROCESS_CMD in result.output
    p = get("wiz_custom_accept")
    assert p.compute_project == "wizard_custom_process_proj"
    assert isinstance(p.auth, ProcessAuth)
    assert p.auth.command == _CUSTOM_PROCESS_CMD


def test_wizard_import_discovery_hook_skipped_when_user_picks_skip(
    isolated_config: Path, tmp_path: Path, mock_picker: list[object]
) -> None:
    """User picks the "skip — configure manually" choice → wizard
    proceeds with the normal endpoint / auth / etc. prompts."""
    fake_creds = ImportedCreds(
        source_label="maxc",
        source_path=tmp_path / "x.yaml",
        auth=AkAuth(access_key_id="X", access_key_secret="Y"),
        compute_project="skipped_proj",
        endpoint="https://x/api",
    )
    runner = CliRunner()
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_creds],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        # Picker queue: skip the import → env=public → auth=ak → AK
        # mode=env var reference.
        mock_picker.append(_PICK_SKIP_IMPORT)
        mock_picker.append(_PICK_ENV_PUBLIC)
        mock_picker.append(_PICK_AUTH_AK)
        mock_picker.append(_PICK_AK_ENV_VAR)
        # Wizard stdin: alias='manual', region='cn-shanghai',
        # AK_ID env (default), AK_SECRET env (default), Configure now? = n
        result = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "manual_proj"],
            input="manual\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    p = get("manual")
    # Did NOT use the imported creds — used the wizard prompts.
    assert p.compute_project == "manual_proj"
    assert p.endpoint.startswith("https://service.cn-shanghai")


def test_wizard_import_discovery_hook_silent_when_no_creds(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """No creds detected → no prompt fires; wizard goes straight to
    endpoint."""
    runner = CliRunner()
    # The default isolated_config fixture already mocks
    # discover_creds to return [], so no extra patch needed.
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # No import picker fires (no candidates) — picker queue starts
        # at env → auth → AK mode.
        mock_picker.append(_PICK_ENV_PUBLIC)
        mock_picker.append(_PICK_AUTH_AK)
        mock_picker.append(_PICK_AK_ENV_VAR)
        result = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "no_creds_proj"],
            input="silent\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    p = get("silent")
    # No "Detected existing credentials" banner in output
    assert "Detected existing credentials" not in result.output
    assert p.compute_project == "no_creds_proj"


# ── discover_mcs_profiles ────────────────────────────────────────────────────


def _seed_profile(name: str, **overrides) -> None:
    """Seed a minimal AK profile so discover_mcs_profiles can find it.

    Goes through ``upsert`` for fully-valid profiles, but falls back to
    writing yaml directly for build-in-progress shells (empty
    ``compute_project``) so we can exercise ``discover_mcs_profiles``'s
    robustness against that state without ``Profile.validate`` rejecting
    it on the way in.
    """
    defaults = {
        "compute_project": f"{name}_proj",
        "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
        "auth": AkAuth(
            access_key_id="${env:ALIBABA_CLOUD_ACCESS_KEY_ID}",
            access_key_secret="${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}",
        ),
        "sources": (DataSource(project=f"{name}_proj", schema="default", tables="*"),),
        "cost_thresholds": CostThresholds(),
        "tags": (),
    }
    defaults.update(overrides)
    profile = Profile(name=name, **defaults)
    if profile.compute_project:
        upsert(profile)
        return
    # Shell profile (empty compute_project) — bypass upsert's validate
    # via profile_store internals; only this branch needs the private
    # symbols, hence the local-only import.
    from maxcompute_semantic.auth.profile_store import _profile_to_dict, _read_raw

    raw = _read_raw()
    raw["profiles"][profile.name] = _profile_to_dict(profile)
    dump_yaml(raw, profiles_yaml_path())


def test_discover_mcs_profiles_empty(isolated_config: Path) -> None:
    assert discover_mcs_profiles() == []


def test_discover_mcs_profiles_returns_all(isolated_config: Path) -> None:
    _seed_profile("dev")
    _seed_profile("staging")
    out = discover_mcs_profiles()
    names = {c.name for c in out}
    assert names == {"dev", "staging"}


def test_discover_mcs_profiles_excludes_name(isolated_config: Path) -> None:
    _seed_profile("dev")
    _seed_profile("staging")
    out = discover_mcs_profiles(exclude_name="dev")
    names = [c.name for c in out]
    assert names == ["staging"]


def test_discover_mcs_profiles_includes_shell_with_empty_project(
    isolated_config: Path,
) -> None:
    """Profile with empty compute_project (build-in-progress shell) is still listed."""
    _seed_profile("shell", compute_project="", sources=())
    out = discover_mcs_profiles()
    assert len(out) == 1
    assert out[0].compute_project == ""
    assert out[0].sources == ()


def test_discover_mcs_profiles_includes_process_auth(isolated_config: Path) -> None:
    _seed_profile("proc", auth=ProcessAuth(command=_NCS_CMD, timeout=60))
    out = discover_mcs_profiles()
    assert len(out) == 1
    assert isinstance(out[0].auth, ProcessAuth)
    assert out[0].auth.command == _NCS_CMD


def test_mcs_profile_candidate_display_ak(isolated_config: Path) -> None:
    _seed_profile("dev")
    [c] = discover_mcs_profiles()
    assert c.display() == (
        "mcs:dev — [AK] project=dev_proj, "
        "endpoint=https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )


def test_mcs_profile_candidate_display_process(isolated_config: Path) -> None:
    _seed_profile("proc", auth=ProcessAuth(command=_NCS_CMD, timeout=60))
    [c] = discover_mcs_profiles()
    assert c.display() == (
        "mcs:proc — [process] project=proc_proj, "
        "endpoint=https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )


def test_mcs_profile_candidate_display_no_project(isolated_config: Path) -> None:
    _seed_profile("shell", compute_project="", sources=())
    [c] = discover_mcs_profiles()
    assert "(no project yet)" in c.display()


class TestClassifyAuthKind:
    def test_ak_auth_classifies_as_ak(self) -> None:
        assert _classify_auth_kind(AkAuth("id", "secret")) == "ak"

    def test_ak_auth_with_env_refs_classifies_as_ak(self) -> None:
        assert _classify_auth_kind(AkAuth("${env:AK_ID}", "${env:AK_SECRET}")) == "ak"

    @pytest.mark.parametrize(
        "command",
        [
            "ncs create credential odpsuser --employee-id 12345 -o template -t odpscmd",
            "ncs create credential odpsuser --buc-user-id 67890 -o template -t odpscmd",
            # leading whitespace
            "  ncs create credential odpsuser --employee-id 1 -o template -t odpscmd",
        ],
    )
    def test_ncs_command_shapes_classify_as_ncs(self, command: str) -> None:
        assert _classify_auth_kind(ProcessAuth(command=command)) == "ncs"
        assert is_canonical_ncs_process_auth(ProcessAuth(command=command))

    @pytest.mark.parametrize(
        "command",
        [
            "/path/to/sts.sh",
            "aliyun sts AssumeRole --role-arn ...",
            "ncs whoami",  # ncs binary but not the credential subcommand
            "ncs create credential odpsuser-extra --employee-id 1 -o template -t odpscmd",
            "python -m my_sts_helper",
        ],
    )
    def test_other_process_commands_classify_as_process(self, command: str) -> None:
        assert _classify_auth_kind(ProcessAuth(command=command)) == "process"
        assert not is_canonical_ncs_process_auth(ProcessAuth(command=command))
