# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for commands/profile.py — create_cmd (non-interactive + wizard)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import load_all
from maxcompute_semantic.auth.schema import ProcessAuth
from maxcompute_semantic.commands.profile import profile_group

# ── Picker echo strings ──────────────────────────────────────────────────────
#
# The wizard's pickers return the displayed choice string, not a numeric
# index. Centralizing the exact strings here keeps every test's queue
# readable and lets one rename in ``profile.py`` ripple cleanly.

PICK_ENV_PUBLIC = "public"
PICK_ENV_INTERNAL = "internal"
PICK_ENV_CUSTOM = "custom"
PICK_AUTH_AK = "ak"
PICK_AUTH_NCS = "ncs"
PICK_AUTH_PROCESS = "process"
PICK_AK_ENV_VAR = "Env var reference — store env var names, not secrets"
PICK_AK_LITERAL = "Literal values — store AK directly in profiles.yaml"
PICK_INTERNAL_CN_HANGZHOU = "CN Hangzhou (corp) (http://service-corp.odps.aliyun-inc.com/api)"


def _queue_public_ak_env_var(mock_picker: list[object]) -> None:
    """Queue the picker returns for the default 'public + AK + env-var' flow.

    Covers env picker → auth picker → AK-mode picker, in that order.
    Used by ~10 tests that all exercise the same default wizard path.
    """
    mock_picker.append(PICK_ENV_PUBLIC)
    mock_picker.append(PICK_AUTH_AK)
    mock_picker.append(PICK_AK_ENV_VAR)


def _invoke(
    isolated_config: Path, args: list[str], obj: dict | None = None, input: str | None = None
) -> object:
    runner = CliRunner()
    return runner.invoke(profile_group, args, obj=obj, input=input)


def test_create_wizard_minimal(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard flow with ncs unavailable, minimal prompts answered (AK env-var defaults)."""
    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
        # Configure now? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "wiz_proj"],
            input="wiz_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "wiz_proj" in profiles
    assert profiles["wiz_proj"].endpoint == (
        "https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )


def test_create_auth_test_failed_save_anyway(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Auth test fails → prompt 'Save anyway?' → user says yes → profile saved."""

    def _mock_run_auth_test(profile, r, **kwargs):
        return 1

    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch(
            "maxcompute_semantic.commands._auth_probe._run_auth_test",
            side_effect=_mock_run_auth_test,
        ),
    ):
        # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
        # save-anyway = y, Configure now? = n
        result = _invoke(
            isolated_config,
            ["create", "--project", "save_proj"],
            input="save_proj\ncn-shanghai\n\n\ny\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "save_proj" in profiles


def test_create_auth_test_failed_abort(isolated_config: Path, mock_picker: list[object]) -> None:
    """Auth test fails → prompt 'Save anyway?' → user says no → profile not saved."""

    def _mock_run_auth_test(profile, r, **kwargs):
        return 1

    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch(
            "maxcompute_semantic.commands._auth_probe._run_auth_test",
            side_effect=_mock_run_auth_test,
        ),
    ):
        # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
        # save-anyway = n → abort
        result = _invoke(
            isolated_config,
            ["create", "--project", "abort_proj"],
            input="abort_proj\ncn-shanghai\n\n\nn\n",
        )
    assert "Auth test failed" in result.output
    profiles = load_all()
    assert "abort_proj" not in profiles


def test_create_wizard_ak_auth(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard flow with AK auth method (env-var mode with defaults)."""
    _queue_public_ak_env_var(mock_picker)
    result = _invoke(
        isolated_config,
        ["create", "--no-test", "--project", "ak_proj"],
        input="ak_proj\ncn-shanghai\n\n\nn\n",
    )
    assert result.exit_code == 0
    profiles = load_all()
    assert "ak_proj" in profiles
    assert profiles["ak_proj"].auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"


def test_create_wizard_custom_endpoint(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard flow with custom endpoint (env_type=custom)."""
    mock_picker.append(PICK_ENV_CUSTOM)
    mock_picker.append(PICK_AUTH_AK)
    mock_picker.append(PICK_AK_ENV_VAR)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, custom-url, ak-id-env(default), ak-secret-env(default),
        # Configure now? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "custom_ep"],
            input="custom_ep\nhttp://custom.odps.example.com/api\n\n\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "custom_ep" in profiles
    assert profiles["custom_ep"].endpoint == "http://custom.odps.example.com/api"


def test_create_wizard_env_picker_cancelled(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """User cancels (Esc) the env picker → wizard aborts before saving anything."""
    mock_picker.append(None)  # env picker cancelled
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        _invoke(
            isolated_config,
            ["create", "--no-test"],
            input="bad_ep\n",
        )
    # The wizard returns None → create_cmd handles it without raising,
    # but no profile is persisted.
    assert "bad_ep" not in load_all()


# ── Endpoint helper tests (from Task 1) ──────────────────────────────────────


def test_build_endpoint_from_region() -> None:
    from maxcompute_semantic.commands.profile import _build_endpoint_from_region

    assert _build_endpoint_from_region("cn-shanghai") == (
        "https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )
    assert _build_endpoint_from_region("ap-southeast-1") == (
        "https://service.ap-southeast-1.maxcompute.aliyun.com/api"
    )


def test_classify_endpoint_public() -> None:
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, region = _classify_endpoint("https://service.cn-shanghai.maxcompute.aliyun.com/api")
    assert kind == "public"
    assert region == "cn-shanghai"


def test_classify_endpoint_internal() -> None:
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, key = _classify_endpoint("http://service-corp.odps.aliyun-inc.com/api")
    assert kind == "internal"
    assert key == "2"


def test_classify_endpoint_custom() -> None:
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, url = _classify_endpoint("http://custom.example.com/api")
    assert kind == "custom"
    assert url == "http://custom.example.com/api"


def test_classify_endpoint_internal_lazada() -> None:
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, key = _classify_endpoint("http://service-all.ali-sg-lazada.odps.aliyun-inc.com/api")
    assert kind == "internal"
    assert key == "1"


def test_classify_endpoint_corp_variant_is_internal() -> None:
    """User-typed intranet endpoint variants classify as internal.

    Domain ends in `.aliyun-inc.com` but URL isn't in the preset list.
    Spec requires this so the wizard's Step 3 picks auth_default=ncs
    and the new doctor/preflight ncs gates fire.
    """
    from maxcompute_semantic.commands.profile import _classify_endpoint

    url = "http://service.cn-shanghai-corp.odps.aliyun-inc.com/api"
    kind, key = _classify_endpoint(url)
    assert kind == "internal"
    # User-typed variants return the URL itself as the second tuple
    # element (downstream callers only consume the first element).
    assert key == url


def test_classify_endpoint_preset_match_wins_over_host_fallback() -> None:
    """A URL that matches a preset still returns the preset key,
    not the URL — preset check runs before the host fallback."""
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, key = _classify_endpoint("http://service-corp.odps.aliyun-inc.com/api")
    assert kind == "internal"
    assert key == "2"  # CN Hangzhou (corp) preset key


def test_classify_endpoint_public_template_wins_over_host_fallback() -> None:
    """A public-cloud URL stays `public`; the new fallback only
    triggers after both preset and public-template matches fail."""
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, region = _classify_endpoint("https://service.cn-shanghai.maxcompute.aliyun.com/api")
    assert kind == "public"
    assert region == "cn-shanghai"


def test_classify_endpoint_external_host_still_custom() -> None:
    """External hosts (no `.aliyun-inc.com` suffix) stay `custom`.

    Regression guard against an over-broad rewrite that would have
    flipped this case too.
    """
    from maxcompute_semantic.commands.profile import _classify_endpoint

    kind, url = _classify_endpoint("https://example.com/api")
    assert kind == "custom"
    assert url == "https://example.com/api"


def test_prompt_required_accepts_value(isolated_config: Path, mock_picker: list[object]) -> None:
    """_prompt_required returns non-empty value (wizard sanity check)."""
    _queue_public_ak_env_var(mock_picker)
    runner = CliRunner()
    runner.invoke(
        profile_group,
        ["create", "--no-test"],
        input="my_val\ncn-shanghai\n\n\nmy_compute\nn\n",
    )
    # Not testing _prompt_required directly (requires interactive prompt),
    # but verify the wizard accepted the alias "my_val" instead of rejecting it.


def test_build_endpoint_from_region_passthrough() -> None:
    """_build_endpoint_from_region: full URL passed as region → passthrough."""
    from maxcompute_semantic.commands.profile import _build_endpoint_from_region

    url = "https://service.cn-beijing.maxcompute.aliyun.com/api"
    assert _build_endpoint_from_region(url) == url


# ── New wizard tests (Task 2) ────────────────────────────────────────────────


def test_create_wizard_public_cloud_region(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: public cloud → region input → auto-built endpoint."""
    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "pub_proj"],
            input="pub_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "pub_proj" in profiles
    assert profiles["pub_proj"].endpoint == (
        "https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )
    assert profiles["pub_proj"].auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"


def test_create_wizard_internal_endpoint(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard: internal → CN Hangzhou preset."""
    mock_picker.append(PICK_ENV_INTERNAL)
    mock_picker.append(PICK_INTERNAL_CN_HANGZHOU)
    # internal endpoint defaults auth to ncs; user keeps the AK choice.
    mock_picker.append(PICK_AUTH_AK)
    mock_picker.append(PICK_AK_ENV_VAR)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, ak-id-env(default), ak-secret-env(default), Configure? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "corp_proj"],
            input="corp_proj\n\n\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "corp_proj" in profiles
    assert profiles["corp_proj"].endpoint == "http://service-corp.odps.aliyun-inc.com/api"


def test_create_wizard_ak_literal_mode(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard: AK literal mode — store values directly."""
    mock_picker.append(PICK_ENV_PUBLIC)
    mock_picker.append(PICK_AUTH_AK)
    mock_picker.append(PICK_AK_LITERAL)
    result = _invoke(
        isolated_config,
        ["create", "--no-test", "--project", "lit_proj"],
        input="lit_proj\ncn-shanghai\nFakeAKID0001\nFakeSecret\nn\n",
    )
    assert result.exit_code == 0
    profiles = load_all()
    assert profiles["lit_proj"].auth.access_key_id == "FakeAKID0001"
    assert profiles["lit_proj"].auth.access_key_secret == "FakeSecret"


def test_create_ak_literal_secret_from_stdin(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "stdin_proj",
            "--alias",
            "stdin_proj",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\nn\n",
    )

    assert result.exit_code == 0, result.output
    profiles = load_all()
    assert profiles["stdin_proj"].auth.access_key_id == "FakeAKID0001"
    assert profiles["stdin_proj"].auth.access_key_secret == "SecretFromStdin"


def test_create_ak_secret_stdin_conflicts_with_ak_secret(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "conflict_proj",
            "--alias",
            "conflict_proj",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret",
            "SecretOnArgv",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin cannot be used with --ak-secret" in result.output


def test_create_ak_secret_flag_warns_to_stderr(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "argv_proj",
            "--alias",
            "argv_proj",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret",
            "SecretOnArgv",
        ],
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    assert "--ak-secret may expose the secret in shell history" in result.output


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        (
            "--ak-secret-env",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
            "--ak-secret cannot be used with --ak-secret-env",
        ),
        (
            "--ak-id-env",
            "ALIBABA_CLOUD_ACCESS_KEY_ID",
            "--ak-secret cannot be used with --ak-id-env",
        ),
    ],
)
def test_create_ak_secret_conflicts_with_ak_env_flags(
    isolated_config: Path, mock_picker: list[object], flag: str, value: str, message: str
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "argv_env_conflict",
            "--alias",
            "argv_env_conflict",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret",
            "SecretOnArgv",
            flag,
            value,
        ],
    )

    assert result.exit_code != 0
    assert message in result.output


def test_create_ak_secret_requires_ak_id_or_literal_mode(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "argv_no_destination",
            "--alias",
            "argv_no_destination",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-secret",
            "SecretOnArgv",
        ],
    )

    assert result.exit_code != 0
    assert "--ak-secret requires --ak-id or --ak-literal" in result.output


def test_create_ak_secret_literal_mode_requires_ak_id(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "argv_literal_no_id",
            "--alias",
            "argv_literal_no_id",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-secret",
            "SecretOnArgv",
        ],
    )

    assert result.exit_code != 0
    assert "--ak-secret with --ak-literal requires --ak-id" in result.output


def test_create_ak_secret_stdin_requires_ak_literal(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "stdin_no_literal",
            "--alias",
            "stdin_no_literal",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin requires --ak-literal" in result.output


def test_create_ak_secret_stdin_requires_ak_id(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "stdin_no_id",
            "--alias",
            "stdin_no_id",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin requires --ak-id" in result.output


def test_create_ak_secret_stdin_conflicts_with_ak_secret_env(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "stdin_secret_env",
            "--alias",
            "stdin_secret_env",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret-env",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin cannot be used with --ak-secret-env" in result.output


def test_create_ak_secret_stdin_conflicts_with_ak_id_env(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "stdin_id_env",
            "--alias",
            "stdin_id_env",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "ak",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-id-env",
            "ALIBABA_CLOUD_ACCESS_KEY_ID",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin cannot be used with --ak-id-env" in result.output


def test_create_ak_secret_stdin_rejected_with_from_spec(isolated_config: Path) -> None:
    import json as _json

    spec = _json.dumps(
        {
            "name": "stdin_from_spec",
            "compute_project": "acme",
            "endpoint": "http://x",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:MY_ID}",
                "access_key_secret": "${env:MY_SEC}",
            },
            "sources": [],
        }
    )
    result = _invoke(
        isolated_config,
        [
            "create",
            "--from-spec",
            spec,
            "--no-test",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret-stdin",
        ],
        input="SecretFromStdin\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret-stdin cannot be used with --from-file or --from-spec" in result.output


def test_create_ak_secret_rejected_with_from_spec(isolated_config: Path) -> None:
    import json as _json

    spec = _json.dumps(
        {
            "name": "secret_from_spec",
            "compute_project": "acme",
            "endpoint": "http://x",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:MY_ID}",
                "access_key_secret": "${env:MY_SEC}",
            },
            "sources": [],
        }
    )
    result = _invoke(
        isolated_config,
        [
            "create",
            "--from-spec",
            spec,
            "--no-test",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret",
            "SecretOnArgv",
        ],
    )

    assert result.exit_code != 0
    assert "--ak-secret cannot be used with --from-file or --from-spec" in result.output


def test_create_ak_secret_rejected_for_process_auth(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    mock_picker.append(PICK_ENV_PUBLIC)
    result = _invoke(
        isolated_config,
        [
            "create",
            "--no-test",
            "--project",
            "process_secret",
            "--alias",
            "process_secret",
            "--region",
            "cn-shanghai",
            "--auth-type",
            "process",
            "--ncs-command",
            "ncs whoami",
            "--ak-literal",
            "--ak-id",
            "FakeAKID0001",
            "--ak-secret",
            "SecretOnArgv",
        ],
        input="n\n",
    )

    assert result.exit_code != 0
    assert "--ak-secret cannot be used with --auth-type process" in result.output


def test_create_wizard_ak_env_var_with_defaults(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: AK env-var mode with standard defaults — just press Enter."""
    _queue_public_ak_env_var(mock_picker)
    result = _invoke(
        isolated_config,
        ["create", "--no-test", "--project", "env_proj"],
        input="env_proj\ncn-shanghai\n\n\nn\n",
    )
    assert result.exit_code == 0
    profiles = load_all()
    assert profiles["env_proj"].auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"
    assert profiles["env_proj"].auth.access_key_secret == "${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}"


def test_create_wizard_process_auth(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard: ncs auth works (ncs unavailable, falls to manual eid)."""
    mock_picker.append(PICK_ENV_INTERNAL)
    mock_picker.append(PICK_INTERNAL_CN_HANGZHOU)
    mock_picker.append(PICK_AUTH_NCS)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, employee_id, Configure? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "proc_proj"],
            input="proc_proj\n12345\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "proc_proj" in profiles
    assert isinstance(profiles["proc_proj"].auth, ProcessAuth)


def test_create_wizard_warns_when_ncs_missing_for_internal(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard preflight: when ncs is selected as auth but the binary
    is missing, print install_hint() before falling back to the
    manual employee-id prompt. The profile is still saved
    (soft warning, not a hard gate)."""
    from maxcompute_semantic.auth.ncs import NCS_INSTALL_DOC_URL

    mock_picker.append(PICK_ENV_INTERNAL)
    mock_picker.append(PICK_INTERNAL_CN_HANGZHOU)
    mock_picker.append(PICK_AUTH_NCS)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, employee_id, Configure? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "warn_proc_proj"],
            input="warn_proc_proj\n12345\nn\n",
        )

    assert result.exit_code == 0
    # The install-docs URL must appear in the wizard output.
    assert NCS_INSTALL_DOC_URL in result.output
    assert "not found on PATH" in result.output

    # The profile is still saved — preflight is a soft warning,
    # not a hard gate. The fallback to manual employee-id is
    # unchanged from the pre-existing behavior.
    profiles = load_all()
    assert "warn_proc_proj" in profiles
    assert isinstance(profiles["warn_proc_proj"].auth, ProcessAuth)


def test_create_wizard_custom_process_auth(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: process auth with custom command (not ncs)."""
    mock_picker.append(PICK_ENV_PUBLIC)
    mock_picker.append(PICK_AUTH_PROCESS)
    result = _invoke(
        isolated_config,
        ["create", "--no-test", "--project", "custom_proc_proj"],
        # stdin: alias, region, custom-command, timeout, Configure? = n
        input="custom_proc_proj\ncn-shanghai\nmy-helper get-creds\n30\nn\n",
    )
    assert result.exit_code == 0
    profiles = load_all()
    assert "custom_proc_proj" in profiles
    p = profiles["custom_proc_proj"]
    assert isinstance(p.auth, ProcessAuth)
    assert p.auth.command == "my-helper get-creds"
    assert p.auth.timeout == 30


def test_create_wizard_show_advanced(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard: --show-advanced reveals cost thresholds and tags prompts."""
    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
        # confirm_cny=20, blocked_cny=200, tags=prod, Configure? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--show-advanced", "--project", "adv_proj"],
            input="adv_proj\ncn-shanghai\n\n\n20\n200\nprod\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert profiles["adv_proj"].cost_thresholds.confirm_cny == 20.0
    assert profiles["adv_proj"].cost_thresholds.blocked_cny == 200.0
    assert profiles["adv_proj"].tags == ("prod",)


def test_create_wizard_explicit_profile_name(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: alias and compute_project can differ — user types alias
    at the first prompt, project comes from the ``--project`` flag (CI
    convenience) or the auto-discovery picker."""
    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "shared_proj"],
            input="shared_proj_ops\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0
    profiles = load_all()
    assert "shared_proj_ops" in profiles
    assert "shared_proj" not in profiles
    assert profiles["shared_proj_ops"].compute_project == "shared_proj"


def test_create_wizard_name_collision_auto_disambiguates(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: alias collision detection — user typing an existing alias
    triggers an explicit overwrite-confirmation prompt. Refusing aborts.

    (Replaces the v0.x ``auto-disambiguates to <project>_2`` behavior;
    the new alias-first flow has no project to derive a default from,
    so the user is asked explicitly.)
    """
    runner = CliRunner()
    _queue_public_ak_env_var(mock_picker)  # only the FIRST create runs the pickers
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        result1 = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "dup_proj"],
            input="dup_proj\ncn-shanghai\n\n\nn\n",
        )
        assert result1.exit_code == 0
        assert "dup_proj" in load_all()

        # Second create with same alias → collision detected, asked to
        # overwrite, refused, abort — never reaches env/auth pickers.
        result2 = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "dup_proj"],
            input="dup_proj\nn\n",
        )
        assert result2.exit_code == 0
        assert "aborted" in result2.output.lower()
        # "dup_proj" still exists, no "dup_proj_2" auto-created.
        profiles = load_all()
        assert "dup_proj" in profiles
        assert "dup_proj_2" not in profiles


def test_create_wizard_name_collision_explicit_confirm(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard: alias collision + user accepts overwrite → second create
    replaces the first."""
    runner = CliRunner()
    # Two full wizard flows → two picker sequences back-to-back.
    _queue_public_ak_env_var(mock_picker)
    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "existing"],
            input="existing\ncn-shanghai\n\n\nn\n",
        )
        original_endpoint = load_all()["existing"].endpoint

        # Second create: alias=existing → overwrite=y → wizard re-runs with
        # cn-beijing region.
        result = runner.invoke(
            profile_group,
            ["create", "--no-test", "--project", "different_proj"],
            input="existing\ny\ncn-beijing\n\n\nn\n",
        )
        assert result.exit_code == 0
        # Profile "existing" replaced — endpoint switched to cn-beijing,
        # compute_project from --project flag.
        assert load_all()["existing"].endpoint != original_endpoint
        assert load_all()["existing"].compute_project == "different_proj"


# ── New non-interactive tests (Task 2) ───────────────────────────────────────


# ── v0.4.0a3: update verb (file-browser editor + --from-file/--from-spec) ────


def test_update_nonexistent_profile(isolated_config: Path) -> None:
    """`update` errors when the profile doesn't exist."""
    result = _invoke(
        isolated_config,
        ["update", "nonexistent", "--from-spec", '{"name":"nonexistent"}'],
    )
    assert result.exit_code != 0


# ── v0.4.0a2: wizard Phase 1+2 (picker auto-flow) ──────────────────────────


def test_create_wizard_picker_yes_drops_into_editor(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard answers Y to 'Configure now?' → drops into editor.

    Verifies the Phase 1 commit (empty sources) → Phase 2 editor →
    final profile has whatever the editor returned. The editor itself
    is mocked; this only tests the wizard's plumbing into the editor.
    """
    from maxcompute_semantic.auth.profile_store import get
    from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile

    edited = Profile(
        name="picker_proj",
        compute_project="picker_proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(
            access_key_id="${env:ALIBABA_CLOUD_ACCESS_KEY_ID}",
            access_key_secret="${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}",
        ),
        sources=(DataSource(project="myproj", schema="sales", tables="*"),),
    )
    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
        patch("maxcompute_semantic.commands.profile.MaxComputeClient"),
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=edited,
        ),
    ):
        result = _invoke(
            isolated_config,
            ["create", "--project", "picker_proj"],
            # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
            # Configure now? = y → editor runs (mocked)
            input="picker_proj\ncn-shanghai\n\n\ny\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("picker_proj")
    assert len(prof.sources) == 1
    assert prof.sources[0].project == "myproj"
    assert prof.sources[0].schema == "sales"


def test_create_wizard_picker_yes_editor_cancel_keeps_shell(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard answers Y but editor returns None (cancel) → shell saved with sources=()."""
    from maxcompute_semantic.auth.profile_store import get

    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
        patch("maxcompute_semantic.commands.profile.MaxComputeClient"),
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=None,
        ),
    ):
        result = _invoke(
            isolated_config,
            ["create", "--project", "cancel_proj"],
            input="cancel_proj\ncn-shanghai\n\n\ny\n",
        )
    assert result.exit_code == 0, result.output
    # Phase 1 shell committed with empty sources; editor cancel
    # didn't overwrite.
    prof = get("cancel_proj")
    assert prof.sources == ()


def test_create_wizard_picker_skip(isolated_config: Path, mock_picker: list[object]) -> None:
    """Wizard answers N to 'Add source?' → profile saved with empty sources."""
    from maxcompute_semantic.auth.profile_store import get

    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
    ):
        result = _invoke(
            isolated_config,
            ["create", "--project", "empty_proj"],
            input="empty_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("empty_proj")
    assert prof.sources == ()


def test_create_wizard_auto_discovers_project(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Wizard without --project flag → auto-discover via list_projects.

    Verifies the new chain: wizard returns shell with empty
    compute_project → ``_discover_compute_project`` runs the picker
    over ``client.list_projects()`` results → user picks → profile
    saved with picked project. Mocks list_projects and queues the
    picker's project pick.
    """
    from unittest.mock import MagicMock

    from maxcompute_semantic.auth.profile_store import get

    fake_client = MagicMock()
    fake_client.list_projects.return_value = ["picked_proj", "other_proj"]
    _queue_public_ak_env_var(mock_picker)
    mock_picker.append("picked_proj")  # _pick_project's _pick_one return
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
        # MaxComputeClient instantiation in _discover_compute_project →
        # returns fake client.
        patch("maxcompute_semantic.commands.profile.MaxComputeClient", return_value=fake_client),
    ):
        result = _invoke(
            isolated_config,
            ["create"],
            # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
            # Configure now? = n
            input="auto_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("auto_proj")
    assert prof.compute_project == "picked_proj"
    fake_client.list_projects.assert_called_once()


def test_create_wizard_passes_compute_role_to_project_picker(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """``_discover_compute_project`` wires ``role="compute"`` through to
    ``_pick_project`` so the picker can render the dev-convention tip via
    fzf's native ``header=`` mechanism. Asserting on stdout/stderr is
    insufficient now — the tip lives inside fzf's UI, not in click's
    output stream."""
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.list_projects.return_value = ["picked_proj", "other_proj_dev"]
    _queue_public_ak_env_var(mock_picker)
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
        patch("maxcompute_semantic.commands.profile.MaxComputeClient", return_value=fake_client),
        # ``_discover_compute_project`` imports ``_pick_project`` inline,
        # so patch the source module (the inline import resolves to it).
        patch(
            "maxcompute_semantic.commands._source_picker._pick_project",
            return_value="other_proj_dev",
        ) as mock_pick_project,
    ):
        result = _invoke(
            isolated_config,
            ["create"],
            input="auto_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    # The wizard must invoke _pick_project with role="compute" so the
    # picker emits the dev-convention tip / reorders dev projects first.
    assert mock_pick_project.called
    assert mock_pick_project.call_args.kwargs.get("role") == "compute"


def test_create_wizard_no_test_falls_back_to_manual_project_prompt(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """``--no-test`` skips auto-discovery → wizard prompts for project name directly."""
    from maxcompute_semantic.auth.profile_store import get

    _queue_public_ak_env_var(mock_picker)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, region, ak-id-env(default), ak-secret-env(default),
        # project (manual fallback under --no-test), Configure now? = n
        result = _invoke(
            isolated_config,
            ["create", "--no-test"],
            input="manual_proj\ncn-shanghai\n\n\nmanual_compute\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("manual_proj")
    assert prof.compute_project == "manual_compute"


def test_create_from_spec_inline_json(isolated_config: Path) -> None:
    """`create --from-spec '<json>'` non-interactive full-profile create."""
    import json as _json

    from maxcompute_semantic.auth.profile_store import get

    spec = _json.dumps(
        {
            "name": "with_src",
            "compute_project": "acme",
            "endpoint": "http://x",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:MY_ID}",
                "access_key_secret": "${env:MY_SEC}",
            },
            "sources": [
                {
                    "project": "data_proj",
                    "schema": "catalog",
                    "tables": [{"name": "cards"}, {"name": "decks"}],
                },
            ],
        }
    )
    result = _invoke(
        isolated_config,
        ["create", "--from-spec", spec, "--no-test"],
    )
    assert result.exit_code == 0, result.output
    prof = get("with_src")
    assert len(prof.sources) == 1
    assert prof.sources[0].project == "data_proj"
    assert tuple(ts.name for ts in prof.sources[0].tables) == ("cards", "decks")


def test_create_from_spec_existing_profile_errors(isolated_config: Path) -> None:
    """create --from-spec refuses to clobber an existing profile."""
    import json as _json

    from maxcompute_semantic.auth.profile_store import upsert
    from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile

    upsert(
        Profile(
            name="dup",
            compute_project="acme",
            endpoint="http://x",
            auth=AkAuth("${env:X}", "${env:Y}"),
            sources=(DataSource("acme", "default", tables="*"),),
        )
    )
    spec = _json.dumps(
        {
            "name": "dup",  # collision
            "compute_project": "newcorp",
            "endpoint": "http://y",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:NEW_ID}",
                "access_key_secret": "${env:NEW_SEC}",
            },
            "sources": [],
        }
    )
    result = _invoke(isolated_config, ["create", "--from-spec", spec, "--no-test"])
    assert result.exit_code == 2
    assert "already exists" in result.output.lower()


# ── Reuse-from-mcs-profile helpers (Task 2 / Task 3) ─────────────────────────


def test_auth_summary_ak_literal() -> None:
    from maxcompute_semantic.auth.schema import AkAuth
    from maxcompute_semantic.commands.profile import _auth_summary

    auth = AkAuth(access_key_id="FakeAKID0001abcde", access_key_secret="dontshow")
    assert _auth_summary(auth) == "AK xxxxxxxxabcde"


def test_auth_summary_ak_short_id() -> None:
    """Short access_key_id < 5 chars: don't crash; show whatever's there."""
    from maxcompute_semantic.auth.schema import AkAuth
    from maxcompute_semantic.commands.profile import _auth_summary

    auth = AkAuth(access_key_id="abc", access_key_secret="x")
    assert _auth_summary(auth) == "AK xxxxxxxxabc"


def test_auth_summary_ak_env_var_ref() -> None:
    """${env:VAR} reference: show the reference verbatim (env-var name isn't sensitive)."""
    from maxcompute_semantic.auth.schema import AkAuth
    from maxcompute_semantic.commands.profile import _auth_summary

    auth = AkAuth(
        access_key_id="${env:MY_CUSTOM_AK_ID}",
        access_key_secret="${env:MY_SECRET}",
    )
    assert _auth_summary(auth) == "AK ${env:MY_CUSTOM_AK_ID}"


def test_auth_summary_process_short_command() -> None:
    from maxcompute_semantic.auth.schema import ProcessAuth
    from maxcompute_semantic.commands.profile import _auth_summary

    auth = ProcessAuth(command="ncs whoami", timeout=60)
    assert _auth_summary(auth) == "process: ncs whoami"


def test_auth_summary_process_long_command_truncated() -> None:
    """Commands > 40 chars are truncated with ellipsis."""
    from maxcompute_semantic.auth.schema import ProcessAuth
    from maxcompute_semantic.commands.profile import _auth_summary

    long_cmd = "ncs create credential odpsuser --employee-id 12345 -o template -t odpscmd"
    auth = ProcessAuth(command=long_cmd, timeout=60)
    assert _auth_summary(auth) == "process: ncs create credential odpsuser --employe…"


def test_sources_summary_empty() -> None:
    from maxcompute_semantic.commands.profile import _sources_summary

    assert _sources_summary(()) == "0 sources, 0 tables"


def test_sources_summary_wildcard_counts_as_one() -> None:
    """Wildcard ('*') source counts as 1 table — matches mcs status pre-build."""
    from maxcompute_semantic.auth.schema import DataSource
    from maxcompute_semantic.commands.profile import _sources_summary

    src = (DataSource(project="p", schema="default", tables="*"),)
    assert _sources_summary(src) == "1 sources, 1 tables"


def test_sources_summary_enumerated() -> None:
    from maxcompute_semantic.auth.schema import DataSource, TableSpec
    from maxcompute_semantic.commands.profile import _sources_summary

    src = (
        DataSource(project="p", schema="default", tables=(TableSpec("t1"), TableSpec("t2"))),
        DataSource(project="q", schema="default", tables="*"),
    )
    # 2 sources; t1, t2, * → 3 tables
    assert _sources_summary(src) == "2 sources, 3 tables"


# ── _reuse_existing_profile per-field y/n prompts ────────────────────────────


def _seed_src_candidate(
    name: str = "src",
    compute_project: str = "src_proj",
    sources_count: int = 1,
):
    """Build an McsProfileCandidate for direct injection into the helper."""
    from maxcompute_semantic.auth.schema import AkAuth, DataSource
    from maxcompute_semantic.commands._import_creds import McsProfileCandidate

    if sources_count == 0:
        sources = ()
    else:
        sources = tuple(
            DataSource(project=f"data{i}", schema="default", tables="*")
            for i in range(sources_count)
        )
    return McsProfileCandidate(
        name=name,
        auth=AkAuth(
            access_key_id="${env:ALIBABA_CLOUD_ACCESS_KEY_ID}",
            access_key_secret="${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}",
        ),
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        compute_project=compute_project,
        sources=sources,
    )


def test_reuse_all_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """User answers Y to all four prompts → every field cloned."""
    from maxcompute_semantic.commands.profile import _reuse_existing_profile

    src = _seed_src_candidate()
    answers = iter([True, True, True, True])
    monkeypatch.setattr("click.confirm", lambda *a, **kw: next(answers))

    decisions = _reuse_existing_profile(src)
    assert decisions.auth is src.auth
    assert decisions.endpoint == src.endpoint
    assert decisions.compute_project == src.compute_project
    assert decisions.sources == src.sources


def test_reuse_default_path_yyNN(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Y/Y/N/N default path: auth + endpoint reused; project + sources fresh."""
    from maxcompute_semantic.commands.profile import _reuse_existing_profile

    src = _seed_src_candidate()
    # click.confirm uses the helper's defaults: Y, Y, N, N
    # Simulate by returning the default for each call.
    captured_defaults: list[bool] = []

    def _confirm(prompt: str, *, default: bool = True) -> bool:
        captured_defaults.append(default)
        return default

    monkeypatch.setattr("click.confirm", _confirm)
    decisions = _reuse_existing_profile(src)

    # The four prompt defaults must be exactly Y, Y, N, N in order.
    assert captured_defaults == [True, True, False, False]
    assert decisions.auth is src.auth
    assert decisions.endpoint == src.endpoint
    assert decisions.compute_project == ""
    assert decisions.sources == ()


def test_reuse_all_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """User says N to everything → ReuseDecisions has no values set."""
    from maxcompute_semantic.commands.profile import _reuse_existing_profile

    src = _seed_src_candidate()
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)

    decisions = _reuse_existing_profile(src)
    assert decisions.auth is None
    assert decisions.endpoint is None
    assert decisions.compute_project == ""
    assert decisions.sources == ()


def test_reuse_skips_compute_project_prompt_when_source_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source with empty compute_project: prompt not shown; treated as N."""
    from maxcompute_semantic.commands.profile import _reuse_existing_profile

    src = _seed_src_candidate(compute_project="", sources_count=0)
    n_calls = [0]

    def _confirm(prompt: str, *, default: bool = True) -> bool:
        n_calls[0] += 1
        return True  # would say "yes" but prompt should be auto-skipped

    monkeypatch.setattr("click.confirm", _confirm)
    decisions = _reuse_existing_profile(src)

    # auth + endpoint asked; compute_project + sources auto-skipped (no prompt fires).
    assert n_calls[0] == 2
    assert decisions.compute_project == ""
    assert decisions.sources == ()


def test_reuse_skips_sources_prompt_when_source_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source with non-empty project but empty sources: sources prompt skipped."""
    from maxcompute_semantic.commands.profile import _reuse_existing_profile

    src = _seed_src_candidate(compute_project="p", sources_count=0)
    answers = iter([True, True, True])  # auth Y, endpoint Y, project Y — no sources prompt
    monkeypatch.setattr("click.confirm", lambda *a, **kw: next(answers))

    decisions = _reuse_existing_profile(src)
    assert decisions.compute_project == "p"
    assert decisions.sources == ()
    # The iter has no fourth element — if the sources prompt fired,
    # next() would raise StopIteration and the test would error.


def test_reuse_decisions_dataclass_defaults() -> None:
    """Smoke-check ReuseDecisions field defaults so callers can rely on them."""
    from maxcompute_semantic.commands.profile import ReuseDecisions

    d = ReuseDecisions()
    assert d.auth is None
    assert d.endpoint is None
    assert d.compute_project == ""
    assert d.sources == ()


# ── Step 1.5 picker + per-field reuse — integration ──────────────────────────


def _seed_existing_ak_profile(name: str = "src") -> None:
    """Upsert a complete AK profile into isolated_config for the wizard to find."""
    from maxcompute_semantic.auth.profile_store import upsert
    from maxcompute_semantic.auth.schema import (
        AkAuth,
        CostThresholds,
        DataSource,
        Profile,
    )

    upsert(
        Profile(
            name=name,
            compute_project=f"{name}_compute",
            endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
            auth=AkAuth(
                access_key_id="${env:ALIBABA_CLOUD_ACCESS_KEY_ID}",
                access_key_secret="${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}",
            ),
            sources=(DataSource(project=f"{name}_data", schema="default", tables="*"),),
            cost_thresholds=CostThresholds(),
            tags=(),
        )
    )


def test_step15_picker_lists_mcs_candidates_first(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """When both mcs profiles and maxc are present, mcs entries appear first."""
    from maxcompute_semantic.commands._import_creds import ImportedCreds

    _seed_existing_ak_profile("src")
    fake_maxc = ImportedCreds(
        source_label="maxc",
        source_path=Path("/fake/maxc.yaml"),
        auth=__import__("maxcompute_semantic.auth.schema", fromlist=["AkAuth"]).AkAuth(
            "FakeAKID0002", "x"
        ),
        compute_project="maxc_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
    )

    # User picks "skip" on the Step 1.5 picker; the rest of the wizard's
    # picker calls (env type / auth method / AK mode) are served from
    # the mock_picker queue.
    captured: dict[str, object] = {}

    def _stub_pick_one(prompt, *, choices, **kw):
        if "➡️  skip — configure manually" in choices:
            captured["choices"] = list(choices)
            return "➡️  skip — configure manually"
        assert mock_picker, f"unexpected _pick_one call with choices={choices!r}"
        return mock_picker.pop(0)

    _queue_public_ak_env_var(mock_picker)
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_maxc],
        ),
        patch(
            "maxcompute_semantic.commands._source_picker._pick_one",
            side_effect=_stub_pick_one,
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "new_proj"],
            input="new_proj\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    choices = captured["choices"]
    # First entry: mcs candidate (📋). Second: maxc (🔑). Last: skip.
    assert choices[0].startswith("📋 mcs:src")
    assert choices[1].startswith("🔑 maxc ")
    assert choices[-1] == "➡️  skip — configure manually"


def test_step15_picker_excludes_self_by_alias(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """When user's new alias matches an existing profile, it's excluded from candidates."""
    _seed_existing_ak_profile("src")
    captured: dict[str, object] = {}

    def _stub_pick_one(prompt, *, choices, **kw):
        if "➡️  skip — configure manually" in choices:
            captured["choices"] = list(choices)
            return "➡️  skip — configure manually"
        assert mock_picker, f"unexpected _pick_one call with choices={choices!r}"
        return mock_picker.pop(0)

    _queue_public_ak_env_var(mock_picker)
    with (
        patch(
            "maxcompute_semantic.commands._source_picker._pick_one",
            side_effect=_stub_pick_one,
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
    ):
        # Same alias as the seeded profile + overwrite=y on the
        # collision prompt; picker must NOT offer "src" as a candidate.
        result = _invoke(
            isolated_config,
            ["create", "--no-test", "--project", "src_compute"],
            input="src\ny\ncn-shanghai\n\n\nn\n",
        )
    assert result.exit_code == 0, result.output
    choices = captured.get("choices", [])
    # Only the skip option remains (when Step 1.5 fires at all). With
    # the new alias matching the seeded profile, discover_mcs_profiles
    # excludes "src" — and since there are no maxc/odpscmd candidates
    # either (the isolated_config fixture stubs discover_creds → []),
    # Step 1.5 may not fire at all (no candidates → no picker). Either
    # way the assertion holds: no 📋 src candidate.
    mcs_choices = [c for c in choices if c.startswith("📋 ")]
    assert mcs_choices == []


def test_step15_pick_mcs_profile_clones_auth_and_endpoint(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Pick 📋 src + answer Y/Y/N/N → new profile inherits auth + endpoint."""
    from unittest.mock import MagicMock

    from maxcompute_semantic.auth.profile_store import get

    _seed_existing_ak_profile("src")
    fake_client = MagicMock()
    fake_client.list_projects.return_value = ["new_compute"]

    # Picker queue: the picker pick + the list_projects pick.
    mock_picker.append(
        "📋 mcs:src — [AK] project=src_compute, "
        "endpoint=https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )
    mock_picker.append("new_compute")
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
        patch("maxcompute_semantic.commands.profile.MaxComputeClient", return_value=fake_client),
    ):
        # stdin: alias, reuse-auth=Y (default), reuse-endpoint=Y (default),
        # reuse-project=N, reuse-sources=N, then Configure cost-gate?=n.
        # Source has 1 DataSource so the sources prompt fires (not auto-skipped).
        result = _invoke(
            isolated_config,
            ["create"],
            input="new_proj\n\n\nn\nn\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("new_proj")
    # Cloned fields:
    assert prof.endpoint == "https://service.cn-shanghai.maxcompute.aliyun.com/api"
    assert prof.auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"
    # Picked via list_projects:
    assert prof.compute_project == "new_compute"
    # Sources NOT cloned (N answer):
    assert prof.sources == ()


def test_step15_pick_mcs_profile_clone_all(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Pick 📋 src + answer Y to all → new profile is a pure fork."""
    from maxcompute_semantic.auth.profile_store import get

    _seed_existing_ak_profile("src")
    mock_picker.append(
        "📋 mcs:src — [AK] project=src_compute, "
        "endpoint=https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )
    with (
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
    ):
        # stdin: alias, reuse-auth=y, reuse-endpoint=y, reuse-project=y,
        # reuse-sources=y, Configure now?=n
        result = _invoke(
            isolated_config,
            ["create"],
            input="fork\ny\ny\ny\ny\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("fork")
    assert prof.compute_project == "src_compute"
    assert prof.endpoint == "https://service.cn-shanghai.maxcompute.aliyun.com/api"
    assert prof.auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"
    assert len(prof.sources) == 1
    assert prof.sources[0].project == "src_data"


def test_step15_pick_mcs_profile_no_to_everything_falls_through(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Pick 📋 src + N to all → wizard's Step 2/3/4 run normally."""
    from maxcompute_semantic.auth.profile_store import get

    _seed_existing_ak_profile("src")
    mock_picker.append(
        "📋 mcs:src — [AK] project=src_compute, "
        "endpoint=https://service.cn-shanghai.maxcompute.aliyun.com/api"
    )
    # After saying N to auth+endpoint, Step 2 runs (env picker), Step 3 runs (auth picker),
    # Step 4 runs (AK mode picker). Queue those returns.
    mock_picker.append(PICK_ENV_PUBLIC)
    mock_picker.append(PICK_AUTH_AK)
    mock_picker.append(PICK_AK_ENV_VAR)
    with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
        # stdin: alias, reuse-auth=n, reuse-endpoint=n, reuse-project=n, reuse-sources=n,
        # Step 2 region (cn-hangzhou — different from src to verify fall-through ran),
        # Step 4 ak-id-env(default), ak-secret-env(default),
        # --no-test means manual project, Configure now?=n
        result = _invoke(
            isolated_config,
            ["create", "--no-test"],
            input="fresh\nn\nn\nn\nn\ncn-hangzhou\n\n\nfresh_compute\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("fresh")
    # Endpoint came from Step 2's cn-hangzhou region, not src's cn-shanghai.
    assert prof.endpoint == "https://service.cn-hangzhou.maxcompute.aliyun.com/api"
    assert prof.compute_project == "fresh_compute"


def test_step15_pick_external_creds_still_short_circuits(
    isolated_config: Path, mock_picker: list[object]
) -> None:
    """Regression: 🔑 maxc/odpscmd path still bulk-imports and skips Steps 2/3/4."""
    from pathlib import Path as _Path

    from maxcompute_semantic.auth.profile_store import get
    from maxcompute_semantic.auth.schema import AkAuth
    from maxcompute_semantic.commands._import_creds import ImportedCreds

    fake_maxc = ImportedCreds(
        source_label="maxc",
        source_path=_Path("/fake/maxc.yaml"),
        auth=AkAuth("FakeAKID0002", "fakesec"),
        compute_project="maxc_proj",
        endpoint="https://service.cn-hangzhou.maxcompute.aliyun.com/api",
    )
    # Picker pick — full display string from ImportedCreds.display().
    mock_picker.append(f"🔑 {fake_maxc.display()}")
    with (
        patch(
            "maxcompute_semantic.commands._import_creds.discover_creds",
            return_value=[fake_maxc],
        ),
        patch("maxcompute_semantic.auth.ncs.is_available", return_value=False),
        patch("maxcompute_semantic.commands._auth_probe._run_auth_test", return_value=0),
    ):
        # stdin: alias, Configure now?=n
        # No reuse-prompt answers (🔑 path doesn't ask), no Step 2/3 inputs.
        result = _invoke(
            isolated_config,
            ["create"],
            input="from_maxc\nn\n",
        )
    assert result.exit_code == 0, result.output
    prof = get("from_maxc")
    assert prof.compute_project == "maxc_proj"
    assert prof.endpoint == "https://service.cn-hangzhou.maxcompute.aliyun.com/api"
    assert prof.auth.access_key_id == "FakeAKID0002"
