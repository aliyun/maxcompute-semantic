# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for `mcs profile update` (v0.4.0a3 new verb shape).

The update_cmd has two paths:
1. Non-interactive: ``--from-file PATH`` or ``--from-spec '<inline>'`` —
   full-replace via complete-profile yaml/json. Tested here with both
   yaml and inline-json forms, name-mismatch + both-flags errors,
   parse errors, validation errors, and auth-test integration.
2. Interactive: opens ``edit_profile`` (from chain α). Mocked here so
   the tests don't drive questionary; the editor itself is tested in
   ``test_profile_editor.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from maxcompute_semantic.auth.profile_store import get, upsert
from maxcompute_semantic.auth.schema import (
    AkAuth,
    DataSource,
    Profile,
)
from maxcompute_semantic.commands.profile import profile_group


def _invoke(
    isolated_config: Path,
    args: list[str],
    *,
    input: str | None = None,
) -> object:
    runner = CliRunner()
    return runner.invoke(profile_group, args, input=input, obj={"format": "plain"})


def _seed_profile(name: str = "myprofile") -> Profile:
    p = Profile(
        name=name,
        compute_project="acme",
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("${env:OLD_ID}", "${env:OLD_SECRET}"),
        sources=(DataSource("acme", "default", tables="*"),),
    )
    upsert(p)
    return p


# ── --from-spec / --from-file ─────────────────────────────────────────


def test_update_from_spec_inline_json_replaces(isolated_config: Path) -> None:
    """`update foo --from-spec '<json>'` replaces the profile with the spec."""
    _seed_profile("foo")
    spec = json.dumps(
        {
            "name": "foo",
            "compute_project": "newcorp",
            "endpoint": "https://new.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:NEW_ID}",
                "access_key_secret": "${env:NEW_SECRET}",
            },
            "sources": [
                {"project": "newcorp", "schema": "ns1", "tables": "*"},
            ],
        }
    )
    with patch(
        "maxcompute_semantic.commands._auth_probe._run_auth_test",
        return_value=0,
    ):
        result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec])
    assert result.exit_code == 0, result.output
    p = get("foo")
    assert p.compute_project == "newcorp"
    assert p.endpoint == "https://new.aliyun.com/api"
    assert isinstance(p.auth, AkAuth)
    assert p.auth.access_key_id == "${env:NEW_ID}"
    assert len(p.sources) == 1
    assert p.sources[0].project == "newcorp"


def test_spec_template_round_trips_through_create(isolated_config: Path) -> None:
    """`mcs profile spec-template` output should be a working create input.

    Generates the template, fills in the placeholder values via simple
    string substitution, and feeds it back through ``mcs profile create
    --from-spec``. Verifies the template's schema is in sync with what
    the loader actually accepts.
    """
    runner = CliRunner()

    # Get the template
    template_result = runner.invoke(profile_group, ["spec-template"])
    assert template_result.exit_code == 0, template_result.output
    template = template_result.output

    # Replace placeholders with test values
    spec = template.replace("my-profile", "test_template_profile").replace(
        "my_project", "test_template_project"
    )

    result = runner.invoke(
        profile_group,
        ["create", "--from-spec", spec, "--no-test"],
        obj={"format": "plain"},
    )
    assert result.exit_code == 0, result.output

    p = get("test_template_profile")
    assert p.compute_project == "test_template_project"
    assert isinstance(p.auth, AkAuth)
    # Env-ref form preserved through the round-trip
    assert p.auth.access_key_id == "${env:ALIBABA_CLOUD_ACCESS_KEY_ID}"


def test_update_from_spec_inline_yaml_works_too(isolated_config: Path) -> None:
    """``--from-spec`` accepts inline YAML (not just JSON) — the loader
    is ruamel.yaml.YAML(typ='safe'), which handles both."""
    _seed_profile("yaml_foo")
    yaml_spec = """
name: yaml_foo
compute_project: yaml_compute
endpoint: https://x.aliyun.com/api
auth:
  type: ak
  access_key_id: ${env:OLD_ID}
  access_key_secret: ${env:OLD_SECRET}
sources:
  - project: yaml_compute
    schema: default
    tables: '*'
"""
    result = _invoke(isolated_config, ["update", "yaml_foo", "--from-spec", yaml_spec, "--no-test"])
    assert result.exit_code == 0, result.output
    p = get("yaml_foo")
    assert p.compute_project == "yaml_compute"
    assert len(p.sources) == 1


def test_update_from_file_yaml_replaces(isolated_config: Path, tmp_path: Path) -> None:
    """`update foo --from-file @path` reads yaml and replaces."""
    _seed_profile("foo")
    yaml_path = tmp_path / "spec.yaml"
    yaml_path.write_text(
        """
name: foo
compute_project: yamlcorp
endpoint: https://y.aliyun.com/api
auth:
  type: ak
  access_key_id: ${env:Y_ID}
  access_key_secret: ${env:Y_SECRET}
sources:
  - project: yamlcorp
    schema: ys
    tables:
      - orders
      - users
""",
        encoding="utf-8",
    )
    with patch(
        "maxcompute_semantic.commands._auth_probe._run_auth_test",
        return_value=0,
    ):
        result = _invoke(isolated_config, ["update", "foo", "--from-file", str(yaml_path)])
    assert result.exit_code == 0, result.output
    p = get("foo")
    assert p.compute_project == "yamlcorp"
    assert isinstance(p.sources[0].tables, tuple)
    names = tuple(ts.name for ts in p.sources[0].tables)
    assert names == ("orders", "users")


def test_update_from_file_at_prefix_stripped(isolated_config: Path, tmp_path: Path) -> None:
    """Leading ``@`` in --from-file path is stripped (curl-style convention)."""
    _seed_profile("foo")
    yaml_path = tmp_path / "spec.yaml"
    yaml_path.write_text(
        """
name: foo
compute_project: ats
endpoint: https://x.aliyun.com/api
auth:
  type: ak
  access_key_id: ${env:ID}
  access_key_secret: ${env:SECRET}
sources: []
""",
        encoding="utf-8",
    )
    with patch(
        "maxcompute_semantic.commands._auth_probe._run_auth_test",
        return_value=0,
    ):
        result = _invoke(isolated_config, ["update", "foo", "--from-file", "@" + str(yaml_path)])
    assert result.exit_code == 0, result.output


# ── error paths ────────────────────────────────────────────────────────


def test_update_both_flags_errors(isolated_config: Path) -> None:
    _seed_profile("foo")
    result = _invoke(
        isolated_config,
        ["update", "foo", "--from-spec", "{}", "--from-file", "/x"],
    )
    assert result.exit_code == 2


def test_update_name_mismatch_errors(isolated_config: Path) -> None:
    """Spec's `name` field must equal the PROFILE arg."""
    _seed_profile("foo")
    spec = json.dumps(
        {
            "name": "bar",  # mismatch
            "compute_project": "x",
            "endpoint": "https://x/api",
            "auth": {"type": "ak", "access_key_id": "X", "access_key_secret": "Y"},
            "sources": [],
        }
    )
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec])
    assert result.exit_code == 2
    assert "does not match" in result.output.lower()


def test_update_missing_name_errors(isolated_config: Path) -> None:
    _seed_profile("foo")
    spec = json.dumps(
        {
            "compute_project": "x",
            "endpoint": "https://x/api",
            "auth": {"type": "ak", "access_key_id": "X", "access_key_secret": "Y"},
            "sources": [],
        }
    )
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec])
    assert result.exit_code == 2
    assert "missing required 'name'" in result.output.lower()


def test_update_invalid_yaml_errors(isolated_config: Path) -> None:
    _seed_profile("foo")
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", ":\n  - [garbage"])
    assert result.exit_code == 2


def test_update_non_dict_spec_errors(isolated_config: Path) -> None:
    _seed_profile("foo")
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", "[1, 2, 3]"])
    assert result.exit_code == 2
    assert "mapping" in result.output.lower()


def test_update_validation_rejects_duplicate_sources(isolated_config: Path) -> None:
    """Profile.validate() rejects duplicate (project, schema) pairs."""
    _seed_profile("foo")
    spec = json.dumps(
        {
            "name": "foo",
            "compute_project": "acme",
            "endpoint": "https://x.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:ID}",
                "access_key_secret": "${env:SECRET}",
            },
            "sources": [
                {"project": "p", "schema": "s", "tables": "*"},
                {"project": "p", "schema": "s", "tables": "*"},  # duplicate key
            ],
        }
    )
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec])
    assert result.exit_code != 0
    assert "duplicate" in result.output.lower()


def test_update_nonexistent_profile_errors(isolated_config: Path) -> None:
    spec = json.dumps(
        {
            "name": "ghost",
            "compute_project": "x",
            "endpoint": "https://x/api",
            "auth": {"type": "ak", "access_key_id": "X", "access_key_secret": "Y"},
            "sources": [],
        }
    )
    result = _invoke(isolated_config, ["update", "ghost", "--from-spec", spec])
    assert result.exit_code != 0


# ── auth-test gate ─────────────────────────────────────────────────────


def test_update_auth_test_failure_with_decline_no_save(isolated_config: Path) -> None:
    """Auth-test fails + user declines override → profile NOT updated."""
    _seed_profile("foo")
    original = get("foo")
    spec = json.dumps(
        {
            "name": "foo",
            "compute_project": "newcorp",  # changed
            "endpoint": "https://new.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:NEW_ID}",  # different auth → triggers test
                "access_key_secret": "${env:NEW_SECRET}",
            },
            "sources": [],
        }
    )
    with patch(
        "maxcompute_semantic.commands._auth_probe._run_auth_test",
        return_value=1,
    ):
        # Decline the "save anyway?" prompt by typing "n\n"
        result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec], input="n\n")
    assert result.exit_code == 0  # graceful return, no exception
    p = get("foo")
    # Original kept (compute_project still acme)
    assert p.compute_project == original.compute_project


def test_update_no_test_skips_auth_check(isolated_config: Path) -> None:
    _seed_profile("foo")
    spec = json.dumps(
        {
            "name": "foo",
            "compute_project": "newcorp",
            "endpoint": "https://new.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:DIFFERENT_ID}",
                "access_key_secret": "${env:DIFFERENT_SECRET}",
            },
            "sources": [],
        }
    )
    # No _run_auth_test patch → real implementation would error;
    # --no-test flag prevents the call entirely.
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec, "--no-test"])
    assert result.exit_code == 0, result.output


def test_update_no_auth_change_skips_auth_test(isolated_config: Path) -> None:
    """When auth is unchanged, --no-test is implicit (no auth-test runs)."""
    _seed_profile("foo")
    spec = json.dumps(
        {
            "name": "foo",
            "compute_project": "differentcorp",  # changed
            "endpoint": "https://x.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:OLD_ID}",  # SAME as seed
                "access_key_secret": "${env:OLD_SECRET}",  # SAME as seed
            },
            "sources": [],
        }
    )
    # No _run_auth_test patch — and update should not call it because auth is unchanged.
    result = _invoke(isolated_config, ["update", "foo", "--from-spec", spec])
    assert result.exit_code == 0, result.output
    p = get("foo")
    assert p.compute_project == "differentcorp"


# ── interactive path (mocked editor) ──────────────────────────────────


def test_update_interactive_calls_editor_and_upserts(isolated_config: Path) -> None:
    _seed_profile("foo")
    expected = Profile(
        name="foo",
        compute_project="editedcorp",
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("${env:OLD_ID}", "${env:OLD_SECRET}"),
        sources=(),
        tags=("edited",),
    )
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=expected,
        ),
        patch("maxcompute_semantic.mc_client.client.MaxComputeClient.__init__", return_value=None),
    ):
        result = _invoke(isolated_config, ["update", "foo"])
    assert result.exit_code == 0, result.output
    p = get("foo")
    assert p.compute_project == "editedcorp"
    assert p.tags == ("edited",)


def test_update_interactive_cancel_no_upsert(isolated_config: Path) -> None:
    """Editor returning None → "aborted" + no profile change."""
    _seed_profile("foo")
    original = get("foo")
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=None,
        ),
        patch("maxcompute_semantic.mc_client.client.MaxComputeClient.__init__", return_value=None),
    ):
        result = _invoke(isolated_config, ["update", "foo"])
    assert result.exit_code == 0
    assert "aborted" in result.output.lower()
    p = get("foo")
    assert p == original  # unchanged


def test_update_interactive_auth_change_runs_test(isolated_config: Path) -> None:
    """Editor returns profile with different auth → auth-test invoked."""
    _seed_profile("foo")
    edited = Profile(
        name="foo",
        compute_project="acme",
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("${env:NEW_ID}", "${env:NEW_SECRET}"),  # changed
        sources=(),
    )
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=edited,
        ),
        patch("maxcompute_semantic.mc_client.client.MaxComputeClient.__init__", return_value=None),
        patch(
            "maxcompute_semantic.commands._auth_probe._run_auth_test",
            return_value=0,
        ) as mock_test,
    ):
        result = _invoke(isolated_config, ["update", "foo"])
    assert result.exit_code == 0
    mock_test.assert_called_once()


def test_update_interactive_no_auth_change_skips_test(isolated_config: Path) -> None:
    """Editor returns profile with same auth → no auth-test."""
    _seed_profile("foo")
    edited = Profile(
        name="foo",
        compute_project="newcorp",  # changed
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("${env:OLD_ID}", "${env:OLD_SECRET}"),  # unchanged
        sources=(),
    )
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=edited,
        ),
        patch("maxcompute_semantic.mc_client.client.MaxComputeClient.__init__", return_value=None),
        patch(
            "maxcompute_semantic.commands._auth_probe._run_auth_test",
            return_value=0,
        ) as mock_test,
    ):
        result = _invoke(isolated_config, ["update", "foo"])
    assert result.exit_code == 0
    mock_test.assert_not_called()


def test_update_interactive_validation_failure(isolated_config: Path) -> None:
    """Editor returns invalid Profile (e.g. missing name) → validation error."""
    _seed_profile("foo")
    bad = Profile(
        name="foo",
        compute_project="",  # invalid: empty
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("${env:ID}", "${env:SECRET}"),
        sources=(),
    )
    with (
        patch(
            "maxcompute_semantic.commands._profile_editor.edit_profile",
            return_value=bad,
        ),
        patch("maxcompute_semantic.mc_client.client.MaxComputeClient.__init__", return_value=None),
    ):
        result = _invoke(isolated_config, ["update", "foo"])
    assert result.exit_code != 0


# ── --format json round-trippable + REDACTED marker (chain ε) ─────────


def test_show_json_round_trippable_with_update(isolated_config: Path) -> None:
    """Round-trip: show --format json → update --from-spec preserves auth.

    Agent's GET-mutate-PUT flow: read profile via show JSON (auth secrets
    appear as ``***REDACTED***`` for literal AKs, env-refs unchanged),
    mutate non-auth fields locally, PUT back via update --from-spec.
    The redacted markers should resolve to the existing auth values.
    """
    import json as _json

    from click.testing import CliRunner

    _seed_profile("foo")
    runner = CliRunner()

    # GET via show --format json
    show_result = runner.invoke(profile_group, ["show", "foo"], obj={"format": "json"})
    assert show_result.exit_code == 0, show_result.output
    payload = _json.loads(show_result.output)
    assert payload["status"] == "success"
    spec = payload["data"]
    # Env-ref AK passes through unchanged.
    assert spec["auth"]["access_key_id"] == "${env:OLD_ID}"
    assert spec["auth"]["access_key_secret"] == "${env:OLD_SECRET}"

    # Mutate: change tags
    spec["tags"] = ["edited"]

    # PUT back via update --from-spec
    update_result = runner.invoke(
        profile_group,
        ["update", "foo", "--from-spec", _json.dumps(spec), "--no-test"],
        obj={"format": "plain"},
    )
    assert update_result.exit_code == 0, update_result.output
    p = get("foo")
    assert p.tags == ("edited",)
    # Auth preserved
    assert isinstance(p.auth, AkAuth)
    assert p.auth.access_key_id == "${env:OLD_ID}"


def test_show_json_redacts_literal_ak(isolated_config: Path) -> None:
    """Literal AK secrets get ``***REDACTED***``; env refs pass through."""
    from maxcompute_semantic.auth.profile_store import upsert as _upsert

    p = Profile(
        name="lit",
        compute_project="acme",
        endpoint="https://x.aliyun.com/api",
        auth=AkAuth("LITERAL_ID_VALUE", "LITERAL_SECRET_VALUE"),
        sources=(),
    )
    _upsert(p)

    runner = CliRunner()
    show_result = runner.invoke(profile_group, ["show", "lit"], obj={"format": "json"})
    assert show_result.exit_code == 0
    import json as _json

    spec = _json.loads(show_result.output)["data"]
    assert spec["auth"]["access_key_id"] == "***REDACTED***"
    assert spec["auth"]["access_key_secret"] == "***REDACTED***"


def test_update_redacted_marker_substitutes_existing(isolated_config: Path) -> None:
    """update --from-spec with REDACTED markers preserves existing auth."""
    import json as _json

    from maxcompute_semantic.auth.profile_store import upsert as _upsert

    _upsert(
        Profile(
            name="lit",
            compute_project="acme",
            endpoint="https://x.aliyun.com/api",
            auth=AkAuth("REAL_ID", "REAL_SECRET"),
            sources=(),
        )
    )
    spec = _json.dumps(
        {
            "name": "lit",
            "compute_project": "newcorp",  # changed
            "endpoint": "https://x.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "***REDACTED***",
                "access_key_secret": "***REDACTED***",
            },
            "sources": [],
        }
    )
    runner = CliRunner()
    result = runner.invoke(
        profile_group,
        ["update", "lit", "--from-spec", spec, "--no-test"],
        obj={"format": "plain"},
    )
    assert result.exit_code == 0, result.output
    p = get("lit")
    assert p.compute_project == "newcorp"
    assert isinstance(p.auth, AkAuth)
    assert p.auth.access_key_id == "REAL_ID"
    assert p.auth.access_key_secret == "REAL_SECRET"


def test_create_redacted_marker_rejected(isolated_config: Path) -> None:
    """create --from-spec with REDACTED markers errors (no existing to read from)."""
    import json as _json

    spec = _json.dumps(
        {
            "name": "new_profile",
            "compute_project": "acme",
            "endpoint": "https://x.aliyun.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "***REDACTED***",
                "access_key_secret": "***REDACTED***",
            },
            "sources": [],
        }
    )
    runner = CliRunner()
    result = runner.invoke(
        profile_group,
        ["create", "--from-spec", spec, "--no-test"],
        obj={"format": "plain"},
    )
    assert result.exit_code == 2
    assert "redacted" in result.output.lower()
