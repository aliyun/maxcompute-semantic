# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``commands/profile.py`` profile-lifecycle verbs.

This file covers ``list``, ``show``, ``whoami``, ``remove``,
``create`` and ``update`` (which have their own test files), plus
the agent-discovery verbs ``import-creds`` and ``spec-template``.
The graduating verbs ``list-projects`` and ``list-schemas`` moved
to the top-level ``mcs meta`` group; the test classes for them
still live here but drive the verbs through their new click argv
paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from maxcompute_semantic.auth.profile_store import load_all, upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, ProcessAuth, Profile
from maxcompute_semantic.commands._import_creds import (
    ImportedCreds,
    McsProfileCandidate,
)
from maxcompute_semantic.commands.meta import meta_group
from maxcompute_semantic.commands.profile import profile_group

# ``auth.link_store.set_link`` and ``os.getcwd`` are imported
# function-scoped in the one test below that drives a cwd-link
# binding directly (``test_bare_invocation_uses_cwd_link``),
# matching the local-import convention the rest of this file
# uses for auth-side helpers.


def _process_profile(name: str = "meta-dev") -> Profile:
    return Profile(
        name=name,
        compute_project="meta_dev",
        endpoint="http://service-corp.odps.aliyun-inc.com/api",
        auth=ProcessAuth(
            command="ncs create credential odpsuser --employee-id 1 -o template -t odpscmd"
        ),
        sources=(DataSource(project="meta_dev", schema="default", tables="*"),),
    )


def _ak_profile(name: str = "ak-prod") -> Profile:
    return Profile(
        name=name,
        compute_project="ak_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="ak_project", schema="default", tables="*"),),
    )


def _invoke(
    isolated_config: Path, args: list[str], obj: dict | None = None, input: str | None = None
) -> object:
    runner = CliRunner()
    return runner.invoke(profile_group, args, obj=obj, input=input)


def _envelope(result) -> dict:
    """Parse the JSON envelope from a CliRunner result; assert success."""
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success", payload
    return payload["data"]


def _invoke_meta(
    isolated_config: Path,
    args: list[str],
    obj: dict | None = None,
    input: str | None = None,
) -> object:
    """Click-runner shim for the top-level ``mcs meta`` verb
    group. The two graduating profile-side verbs
    (``mcs profile list-projects`` and ``mcs profile
    list-schemas``) moved into the freestanding ``meta_group``
    in the post-v0.4 CLI cleanup. Their tests still live in
    this file (the ``TestListProjectsCmd`` /
    ``TestListSchemasCmd`` classes), but they now run against
    the meta-group runner-target rather than the profile-
    group one. The bare verb name is the first argv element
    (no leading ``"meta"`` element — the group itself is the
    runner-target, and click sees the verb directly).

    Mirrors ``_invoke`` above (which still runs against the
    ``profile_group`` for the profile-lifecycle verbs:
    list / show / whoami / create / update / remove / import-
    creds / spec-template / export / import).
    """
    runner = CliRunner()
    return runner.invoke(meta_group, args, obj=obj, input=input)


def test_list_empty(isolated_config: Path) -> None:
    result = _invoke(isolated_config, ["list"])
    assert result.exit_code == 0
    assert "no profiles configured" in result.output


def test_list_with_entries(isolated_config: Path) -> None:
    upsert(_process_profile())
    upsert(_ak_profile())
    result = _invoke(isolated_config, ["list"])
    assert result.exit_code == 0
    assert "meta-dev" in result.output
    assert "ak-prod" in result.output


def test_list_with_tier_cache(isolated_config: Path) -> None:
    """Profile list shows tier label when the per-(profile, project)
    cache exists for the AK's compute_project."""
    from maxcompute_semantic._internal.paths import tier_cache_path

    profile = _process_profile()
    upsert(profile)
    # Write the v0.4 per-(profile, project) tier cache for the AK's
    # compute_project.
    cache_path = tier_cache_path(profile, profile.compute_project)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("2", encoding="utf-8")

    result = _invoke(isolated_config, ["list"])
    assert result.exit_code == 0
    assert "2-level" in result.output


def test_list_json_format(isolated_config: Path) -> None:
    upsert(_process_profile())
    result = _invoke(isolated_config, ["list"], obj={"format": "json"})
    assert result.exit_code == 0
    import json

    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    assert len(envelope["data"]["rows"]) == 1


def test_show_existing(isolated_config: Path) -> None:
    upsert(_process_profile())
    result = _invoke(isolated_config, ["show", "meta-dev"])
    assert result.exit_code == 0
    assert "meta-dev" in result.output
    assert "meta_dev" in result.output


def test_show_missing_exits_3(isolated_config: Path) -> None:
    result = _invoke(isolated_config, ["show", "nope"])
    assert result.exit_code == 3


def test_show_ak_env_ref_preserved(isolated_config: Path) -> None:
    """Env-var references in AK auth pass through unchanged — they're
    not secrets, just resolver lookups. Only literal AK values get
    redacted to ``***REDACTED***``."""
    upsert(_ak_profile())  # uses ${env:MY_AK_ID} / ${env:MY_AK_SECRET}
    result = _invoke(isolated_config, ["show", "ak-prod"])
    assert result.exit_code == 0
    # Env-refs preserved (not redacted)
    assert "${env:MY_AK_ID}" in result.output
    assert "${env:MY_AK_SECRET}" in result.output


# Synthetic AK-shaped identifier used only in tests — never a live
# credential. Built from word-separated parts so it's obviously
# fake and no real AK prefix (e.g. ``LTAI``) appears anywhere in
# the source tree.
_FAKE_AK_ID = "FAKE_AK_" + "ID_FOR_" + "TESTS_ONLY"
_FAKE_AK_SECRET = "FAKE_AK_" + "SECRET_FOR_" + "TESTS_ONLY"


def test_show_ak_literal_redacts_secret(isolated_config: Path) -> None:
    """Literal hardcoded AK secret gets fully redacted; AK id is
    masked in ``FIRST4***LAST4`` style (matching ``maxc auth
    whoami``'s ``principal_masked``).
    """
    p = Profile(
        name="lit-ak",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth(_FAKE_AK_ID, _FAKE_AK_SECRET),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
    )
    upsert(p)
    result = _invoke(isolated_config, ["show", "lit-ak"])
    assert result.exit_code == 0
    # AK secret fully redacted in the rich-text section.
    assert "***REDACTED***" in result.output
    assert _FAKE_AK_SECRET not in result.output
    # AK id masked to first-4 + *** + last-4 — never appears literally.
    expected_mask = f"{_FAKE_AK_ID[:4]}***{_FAKE_AK_ID[-4:]}"
    assert _FAKE_AK_ID not in result.output
    assert expected_mask in result.output


def test_remove_with_yes(isolated_config: Path) -> None:
    upsert(_process_profile())
    result = _invoke(isolated_config, ["remove", "meta-dev", "--yes"])
    assert result.exit_code == 0
    assert load_all() == {}


def test_remove_aborts_without_confirmation(isolated_config: Path) -> None:
    upsert(_process_profile())
    # Simulate user pressing 'n' at confirmation
    result = _invoke(isolated_config, ["remove", "meta-dev"], input="n\n")
    assert "aborted" in result.output
    assert "meta-dev" in load_all()


def test_remove_interactive_yes(isolated_config: Path) -> None:
    upsert(_process_profile())
    result = _invoke(isolated_config, ["remove", "meta-dev"], input="y\n")
    assert result.exit_code == 0
    assert load_all() == {}


def test_remove_nonexistent_idempotent(isolated_config: Path) -> None:
    result = _invoke(isolated_config, ["remove", "ghost", "--yes"])
    assert result.exit_code == 0


def test_list_json_format_empty(isolated_config: Path) -> None:
    """JSON format list with no profiles → success envelope with empty list."""
    import json

    result = _invoke(isolated_config, ["list"], obj={"format": "json"})
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"


def test_show_json_format(isolated_config: Path) -> None:
    """JSON format show for existing profile."""
    import json

    upsert(_process_profile())
    result = _invoke(isolated_config, ["show", "meta-dev"], obj={"format": "json"})
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["status"] == "success"
    assert envelope["data"]["name"] == "meta-dev"


def test_show_yaml_format(isolated_config: Path) -> None:
    """``-f yaml`` show emits an envelope mirroring ``-f json``."""
    from ruamel.yaml import YAML

    upsert(_process_profile())
    result = _invoke(isolated_config, ["show", "meta-dev"], obj={"format": "yaml"})
    assert result.exit_code == 0
    envelope = YAML(typ="safe").load(result.output)
    assert envelope["status"] == "success"
    data = envelope["data"]
    assert data["name"] == "meta-dev"
    assert data["compute_project"] == "meta_dev"
    assert "auth" in data
    assert "sources" in data


def test_show_text_has_no_identity_row(isolated_config: Path) -> None:
    """``mcs profile show`` is pure-static-config — the runtime
    identity is handled by the separate ``mcs profile whoami``
    verb, so the show output contains no Identity / whoami line
    even when the agent might have captured one elsewhere. The
    sample profile relies on env-refs so the existing env-status
    annotation appears (sanity-check we didn't drop that wiring
    along with the identity row)."""
    upsert(_ak_profile())  # uses ${env:MY_AK_ID} / ${env:MY_AK_SECRET}
    result = _invoke(isolated_config, ["show", "ak-prod"])
    assert result.exit_code == 0
    # The retired identity row's leading emoji must not appear,
    # and neither should the "whoami" label that the d115314-era
    # show used.
    assert "🪪" not in result.output
    assert "Identity" not in result.output
    assert "whoami" not in result.output
    # Env-ref annotation is unaffected.
    assert "${env:MY_AK_ID}" in result.output


def test_show_process_auth_no_identity_row(isolated_config: Path) -> None:
    """Same shape on the ProcessAuth side — the auth row is the
    only auth-related output and there is no Identity line."""
    upsert(_process_profile())
    result = _invoke(isolated_config, ["show", "meta-dev"])
    assert result.exit_code == 0
    assert "Process" in result.output
    assert "Identity" not in result.output
    assert "🪪" not in result.output
    assert "access_key_id" not in result.output


def test_show_yaml_has_no_identity_key(isolated_config: Path) -> None:
    """The yaml round-trip carries only the stored config fields
    (compute_project, endpoint, auth, sources, cost_thresholds,
    tags, package_path). No ``identity`` key inside ``data`` — the
    field doesn't exist on the Profile dataclass any more."""
    from ruamel.yaml import YAML

    upsert(_ak_profile())
    result = _invoke(isolated_config, ["show", "ak-prod"], obj={"format": "yaml"})
    assert result.exit_code == 0
    envelope = YAML(typ="safe").load(result.output)
    data = envelope["data"]
    assert "identity" not in data
    # And the standard top-level keys are still there.
    assert data["name"] == "ak-prod"
    assert data["compute_project"] == "ak_project"
    assert data["auth"]["type"] == "ak"


def test_show_yaml_drops_legacy_identity_key(isolated_config: Path) -> None:
    """A yaml file on disk written by the d115314-era code may carry
    a top-level ``identity:`` key (the briefly-existing captured
    field). The loader ignores unknown keys, and the next save
    rewrites the file without the key — verify the show-then-load
    side ignores it without erroring."""
    import os
    from pathlib import Path as _Path

    from ruamel.yaml import YAML

    upsert(_ak_profile())
    # Splice a stray ``identity:`` line into the on-disk yaml the
    # way the prior commit would have written it, then read it
    # back through ``mcs profile show``.
    cfg_dir = _Path(os.environ["MCS_CONFIG_DIR"])
    yaml_path = cfg_dir / "profiles.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    yaml = YAML()
    data = yaml.load(text)
    data["profiles"]["ak-prod"]["identity"] = "RAM$legacy:placeholder"
    with yaml_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)

    result = _invoke(isolated_config, ["show", "ak-prod"])
    assert result.exit_code == 0
    # The stray legacy field is silently ignored — nothing in the
    # output references the placeholder string.
    assert "RAM$legacy:placeholder" not in result.output


def test_show_env_ref_auth_annotates_status(isolated_config: Path, monkeypatch) -> None:
    """For env-ref AK fields, the rendered auth row appends a small
    status tag stating whether the named env var is currently
    exported in the shell. The literal pointer (``${env:NAME}``)
    never gets the literal-AK mask — it's a name, not a secret —
    so the ref string passes through verbatim with the annotation
    tacked on.

    Drives one variable as set and the other as unset and asserts
    both annotation forms appear.
    """
    profile = Profile(
        name="env-ak",
        compute_project="acme",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MCS_TEST_AK_ID}", "${env:MCS_TEST_AK_SECRET}"),
        sources=(DataSource(project="acme", schema="default", tables="*"),),
    )
    upsert(profile)
    monkeypatch.setenv("MCS_TEST_AK_ID", "anything-nonempty")
    monkeypatch.delenv("MCS_TEST_AK_SECRET", raising=False)

    result = _invoke(isolated_config, ["show", "env-ak"])
    assert result.exit_code == 0
    # The ref strings pass through unchanged.
    assert "${env:MCS_TEST_AK_ID}" in result.output
    assert "${env:MCS_TEST_AK_SECRET}" in result.output
    # The id-side annotation reports the env var as set.
    assert "MCS_TEST_AK_ID set in current shell" in result.output
    # The secret-side reports the env var as not set.
    assert "MCS_TEST_AK_SECRET NOT set in current shell" in result.output


def test_show_env_ref_helper_distinguishes_literal(monkeypatch) -> None:
    """Unit-level check on ``env_ref_status``: literals return None,
    env-refs return a ``(label, is_set)`` pair, and the boolean
    matches whether the env var is currently set with a non-empty
    value (an empty string env var counts as unset, since an empty
    AK fails downstream anyway).
    """
    from maxcompute_semantic.commands._identity import env_ref_name, env_ref_status

    assert env_ref_status("LITERAL_AK_VALUE_XYZ") is None
    assert env_ref_name("LITERAL_AK_VALUE_XYZ") is None
    assert env_ref_name("${env:MY_VAR}") == "MY_VAR"

    monkeypatch.setenv("MCS_TEST_REF_SET", "value")
    monkeypatch.setenv("MCS_TEST_REF_EMPTY", "")
    monkeypatch.delenv("MCS_TEST_REF_UNSET", raising=False)

    set_status = env_ref_status("${env:MCS_TEST_REF_SET}")
    assert set_status is not None and set_status[1] is True
    assert "MCS_TEST_REF_SET set" in set_status[0]

    empty_status = env_ref_status("${env:MCS_TEST_REF_EMPTY}")
    assert empty_status is not None and empty_status[1] is False
    assert "MCS_TEST_REF_EMPTY NOT set" in empty_status[0]

    unset_status = env_ref_status("${env:MCS_TEST_REF_UNSET}")
    assert unset_status is not None and unset_status[1] is False
    assert "MCS_TEST_REF_UNSET NOT set" in unset_status[0]

    # An "${env:}" with empty name is treated as a literal (None),
    # not as a malformed env-ref with empty annotation.
    assert env_ref_name("${env:}") is None
    assert env_ref_status("${env:}") is None


def test_show_rejects_local_format_flag(isolated_config: Path) -> None:
    """The legacy ``mcs profile show --format yaml|json|text``
    sub-option was removed in 0.8.0 — format is the global ``-f``
    flag only. The Click parser must reject the unknown option."""
    upsert(_process_profile())
    result = _invoke(isolated_config, ["show", "meta-dev", "--format", "yaml"])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


def test_remove_mcs_error(isolated_config: Path) -> None:
    """Remove profile that raises McsError during deletion → exits with error code."""
    from maxcompute_semantic.mc_client.errors import McsError

    upsert(_process_profile())
    with patch(
        "maxcompute_semantic.commands.profile.remove",
        side_effect=McsError("delete failed", remediation="try again"),
    ):
        result = _invoke(isolated_config, ["remove", "meta-dev", "--yes"])
    assert result.exit_code != 0


class TestProfileQuiet:
    def test_list_quiet_outputs_profile_names(self, isolated_config: Path) -> None:
        """profile list -q: profile names one per line, no table headers."""
        upsert(_process_profile())
        upsert(_ak_profile())
        result = _invoke(isolated_config, ["list"], obj={"format": "plain", "quiet": True})
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        # Should have exactly 2 lines (one per profile), sorted
        assert len(lines) == 2
        assert "ak-prod" in lines
        assert "meta-dev" in lines

    def test_list_quiet_empty_no_output(self, isolated_config: Path) -> None:
        """profile list -q with no profiles: no output."""
        result = _invoke(isolated_config, ["list"], obj={"format": "plain", "quiet": True})
        assert result.exit_code == 0
        assert result.output == ""

    def test_show_quiet_outputs_profile_name(self, isolated_config: Path) -> None:
        """profile show -q: just the profile name."""
        upsert(_process_profile())
        result = _invoke(
            isolated_config, ["show", "meta-dev"], obj={"format": "plain", "quiet": True}
        )
        assert result.exit_code == 0
        assert result.output.strip() == "meta-dev"

    def test_list_quiet_json_mode_emits_envelope(self, isolated_config: Path) -> None:
        """profile list -q -f json: envelope emitted (quiet ignored in json mode)."""
        import json

        upsert(_process_profile())
        result = _invoke(isolated_config, ["list"], obj={"format": "json", "quiet": True})
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["status"] == "success"


# ── v0.4.0a2: list-projects / list-schemas data API verbs ──────────────────


class TestListProjectsCmd:
    def test_outputs_json_envelope(self, isolated_config: Path) -> None:
        """profile list-projects: envelope with data.projects = [...]."""
        import json

        upsert(_ak_profile())
        mock_client = MagicMock()
        with patch(
            "maxcompute_semantic.commands.meta.make_client_for_project",
            return_value=mock_client,
        ):
            mock_client.list_projects.return_value = ["proj_a", "proj_b", "proj_c"]
            result = _invoke_meta(isolated_config, ["list-projects", "--profile", "ak-prod"])
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["status"] == "success"
        assert env["data"]["projects"] == ["proj_a", "proj_b", "proj_c"]

    def test_failure_emits_error_envelope(self, isolated_config: Path) -> None:
        """list-projects with auth failure emits failure envelope, exit 1."""
        import json

        from maxcompute_semantic.mc_client.errors import AuthFailedError

        upsert(_ak_profile())
        mock_client = MagicMock()
        with patch(
            "maxcompute_semantic.commands.meta.make_client_for_project",
            return_value=mock_client,
        ):
            mock_client.list_projects.side_effect = AuthFailedError("auth boom")
            result = _invoke_meta(isolated_config, ["list-projects", "--profile", "ak-prod"])
        assert result.exit_code == 4
        env = json.loads(result.output)
        assert env["status"] == "error"
        assert env["error"]["code"] == "AuthFailed"


class TestListSchemasCmd:
    def test_outputs_json_envelope(self, isolated_config: Path) -> None:
        import json

        upsert(_ak_profile())
        mock_client = MagicMock()
        with patch(
            "maxcompute_semantic.commands.meta.make_client_for_project",
            return_value=mock_client,
        ):
            mock_client.list_schemas.return_value = ["default", "sales_dw"]
            result = _invoke_meta(
                isolated_config,
                ["list-schemas", "--project", "ak_project", "--profile", "ak-prod"],
            )
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["data"]["schemas"] == ["default", "sales_dw"]
        # Confirm the project= kwarg was threaded through.
        mock_client.list_schemas.assert_called_once_with(project="ak_project")


# ── mcs profile whoami (live identity probe) ───────────────────────────────


class TestProfileWhoamiCmd:
    """The new live-identity verb replaces the deleted
    ``mcs auth whoami`` and ``mcs auth test`` pair. It calls
    ``commands._identity.live_identity`` once per invocation —
    there's no on-disk cache of the result — and exits non-zero
    when the probe can't produce a principal string.
    """

    def test_outputs_identity_text(self, isolated_config: Path) -> None:
        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="RAM$test-role:test-user",
        ) as probe:
            result = _invoke(isolated_config, ["whoami", "ak-prod"])
        assert result.exit_code == 0
        probe.assert_called_once()
        assert "ak-prod" in result.output
        assert "RAM$test-role:test-user" in result.output

    def test_outputs_json_envelope(self, isolated_config: Path) -> None:
        import json

        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="RAM$json-envelope-test:user",
        ):
            result = _invoke(isolated_config, ["whoami", "ak-prod"], obj={"format": "json"})
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["status"] == "success"
        assert env["data"] == {
            "profile": "ak-prod",
            "auth_type": "ak",
            "identity": "RAM$json-envelope-test:user",
        }

    def test_process_auth_branch_marks_auth_type(self, isolated_config: Path) -> None:
        import json

        upsert(_process_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="alice (employee.42)",
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "meta-dev"],
                obj={"format": "json"},
            )
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["data"]["auth_type"] == "process"
        assert env["data"]["identity"] == "alice (employee.42)"

    def test_failure_exits_non_zero(self, isolated_config: Path) -> None:
        """``live_identity`` returning None → error envelope and
        non-zero exit. The CLI's canonical failure envelope uses
        ``status: "error"`` (see ``mc_client/envelope.Envelope``)
        which the ``Renderer.error`` helper emits when handed an
        ``McsError``."""
        import json

        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value=None,
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "ak-prod"],
                obj={"format": "json"},
            )
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["status"] == "error"
        assert env["error"]["code"] == "WhoAmIFailed"
        # The remediation pointer mentions the canonical fallback
        # diagnostic (any real ``mcs sql execute`` against the
        # profile would surface a richer auth/permission error).
        assert "mcs sql execute" in env["error"]["remediation"]

    def test_missing_profile_exits_3(self, isolated_config: Path) -> None:
        """Same exit-code contract as ``mcs profile show <missing>`` —
        a ``ProfileNotFoundError`` carries ``exit_code=3``."""
        result = _invoke(isolated_config, ["whoami", "ghost"])
        assert result.exit_code == 3

    def test_quiet_mode_prints_only_identity(self, isolated_config: Path) -> None:
        """``-q`` flag drops the ``profile <name>: `` framing and
        prints the bare principal string, matching the convention
        the other profile verbs already use."""
        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="RAM$quiet:user",
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "ak-prod"],
                obj={"format": "plain", "quiet": True},
            )
        assert result.exit_code == 0
        assert result.output.strip() == "RAM$quiet:user"

    def test_bare_invocation_uses_cwd_link(self, isolated_config: Path) -> None:
        """Without a positional argument the verb routes through
        the standard active-profile chain. With a cwd-link
        binding registered, that's the target the probe sees.
        ``MCS_PROFILE``
        env var is the per-shell alternative; we use the cwd
        link here because it's the form the wizard-e2e test
        also drives and because the link-store's setup helper
        (``auth.link_store.set_link``) takes the cwd path
        explicitly, which makes the test hermetic against
        whatever the process's actual cwd happens to be at run
        time.

        The verb's flag-surface absence is the same ("no
        ``--project`` / ``--profile`` flags exist; the bare form
        IS the no-target form, and the resolution chain it
        consults is the same one ``mcs sql execute`` uses
        internally").
        """
        import os

        from maxcompute_semantic.auth.link_store import set_link

        upsert(_process_profile())
        cwd = os.getcwd()
        set_link(cwd, "meta-dev")

        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="bob (employee.7)",
        ) as probe:
            result = _invoke(isolated_config, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "meta-dev" in result.output
        assert "bob (employee.7)" in result.output
        probe.assert_called_once()
        ((called_profile,), _) = probe.call_args
        assert called_profile.name == "meta-dev"

    def test_no_flag_surface(self, isolated_config: Path) -> None:
        """The verb's CLI surface is a single optional positional —
        no ``--project`` / ``--profile`` flags exist, since the
        bare form already routes through the standard active-
        profile chain. Click rejects an unknown flag with usage
        exit 2, which pins the absence in a stable place.
        """
        upsert(_ak_profile())
        result = _invoke(isolated_config, ["whoami", "--project", "anything"])
        # ``CliRunner`` collapses stderr into ``output`` by default
        # (``mix_stderr=True``), so the usage-error text appears
        # there regardless of where click wrote it. Exit code 2 is
        # click's canonical "bad usage" code.
        assert result.exit_code == 2
        combined = (result.output or "").lower()
        assert "no such option" in combined

    def test_env_anon_label_when_chain_falls_to_env_vars(
        self, isolated_config: Path, monkeypatch
    ) -> None:
        """If the chain has no on-disk profile and no default, it
        lands on the env-vars-anonymous fallback whose ``name`` is
        the empty string. The output banner labels that as the
        fixed ``(env-vars)`` tag rather than showing an empty
        quote.
        """
        # No upsert: the on-disk profile set is empty. No default
        # is set either. The env-var fallback in the resolver
        # constructs a Profile with name="" because $MAXCOMPUTE_PROJECT
        # is also unset (the isolated_config fixture clears it).
        monkeypatch.delenv("MAXCOMPUTE_PROJECT", raising=False)

        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            return_value="some-identity",
        ):
            result = _invoke(isolated_config, ["whoami"])
        assert result.exit_code == 0
        assert "(env-vars)" in result.output
        assert "some-identity" in result.output

    def test_auth_failed_error_propagates(self, isolated_config: Path) -> None:
        """Classified AuthFailedError from live_identity propagates
        through whoami_cmd with code and remediation, instead of
        being folded into a generic WhoAmIFailedError."""
        import json

        from maxcompute_semantic.mc_client.errors import AuthFailedError

        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            side_effect=AuthFailedError(
                "AccessKeyIdNotFound",
                remediation="re-run `ncs auth login`",
            ),
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "ak-prod"],
                obj={"format": "json"},
            )
        assert result.exit_code == 4
        env = json.loads(result.output)
        assert env["status"] == "error"
        assert env["error"]["code"] == "AuthFailed"
        assert "ncs auth login" in env["error"]["remediation"]

    def test_identity_not_authorized_error_propagates(self, isolated_config: Path) -> None:
        """IdentityNotAuthorizedError from live_identity propagates
        with its specific code and remediation."""
        import json

        from maxcompute_semantic.mc_client.errors import (
            IdentityNotAuthorizedError,
        )

        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            side_effect=IdentityNotAuthorizedError(
                "User doesn't exist in the project",
                remediation="check ODPS authorization",
            ),
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "ak-prod"],
                obj={"format": "json"},
            )
        assert result.exit_code == 4
        env = json.loads(result.output)
        assert env["status"] == "error"
        assert env["error"]["code"] == "IdentityNotAuthorized"

    def test_endpoint_unreachable_error_propagates(self, isolated_config: Path) -> None:
        """EndpointUnreachableError from live_identity propagates."""
        import json

        from maxcompute_semantic.mc_client.errors import (
            EndpointUnreachableError,
        )

        upsert(_ak_profile())
        with patch(
            "maxcompute_semantic.commands.profile.live_identity",
            side_effect=EndpointUnreachableError(
                "Connection refused",
                remediation="check MAXCOMPUTE_ENDPOINT",
            ),
        ):
            result = _invoke(
                isolated_config,
                ["whoami", "ak-prod"],
                obj={"format": "json"},
            )
        assert result.exit_code == 1
        env = json.loads(result.output)
        assert env["status"] == "error"
        assert env["error"]["code"] == "EndpointUnreachable"


class TestResolveProfileForProject:
    """``--project`` overrides ``profile.compute_project`` so commands
    that submit SQL or hit metadata endpoints actually target the
    user's chosen project — previously the flag was silently dropped
    when a saved profile was in play."""

    def test_overrides_compute_project_when_supplied(self, isolated_config: Path) -> None:
        from maxcompute_semantic.commands.profile import _resolve_profile_for_project

        upsert(_ak_profile())
        resolved = _resolve_profile_for_project(project="other_project", profile_name="ak-prod")
        assert resolved.compute_project == "other_project"
        # Other fields stay untouched.
        assert resolved.name == "ak-prod"
        assert resolved.endpoint == "https://odps.aliyun.com/api"

    def test_no_override_when_project_matches_profile(self, isolated_config: Path) -> None:
        from maxcompute_semantic.commands.profile import _resolve_profile_for_project

        upsert(_ak_profile())
        resolved = _resolve_profile_for_project(project="ak_project", profile_name="ak-prod")
        assert resolved.compute_project == "ak_project"

    def test_no_override_when_project_omitted(self, isolated_config: Path) -> None:
        from maxcompute_semantic.commands.profile import _resolve_profile_for_project

        upsert(_ak_profile())
        resolved = _resolve_profile_for_project(profile_name="ak-prod")
        assert resolved.compute_project == "ak_project"


class TestSuggestCreds:
    def test_empty_returns_empty_arrays(self, isolated_config: Path) -> None:
        with (
            patch(
                "maxcompute_semantic.commands._import_creds.discover_creds",
                return_value=[],
            ),
            patch(
                "maxcompute_semantic.commands._import_creds.discover_mcs_profiles",
                return_value=[],
            ),
        ):
            result = _invoke(isolated_config, ["suggest-creds"], obj={"format": "json"})
        data = _envelope(result)
        assert data == {"existing_mcs": [], "external": []}

    def test_existing_mcs_shape_and_no_secret_leak(self, isolated_config: Path) -> None:
        ak_candidate = McsProfileCandidate(
            name="prod-ak",
            auth=AkAuth(
                access_key_id="AKID_LITERAL_THAT_MUST_NOT_LEAK",
                access_key_secret="SECRET_THAT_MUST_NOT_LEAK",
            ),
            endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
            compute_project="acme",
            sources=(),
        )
        ncs_candidate = McsProfileCandidate(
            name="dev-ncs",
            auth=ProcessAuth(
                command="ncs create credential odpsuser --employee-id 99 -o template -t odpscmd",
            ),
            endpoint="http://service-corp.odps.aliyun-inc.com/api",
            compute_project="dev_proj",
            sources=(),
        )
        with (
            patch(
                "maxcompute_semantic.commands._import_creds.discover_creds",
                return_value=[],
            ),
            patch(
                "maxcompute_semantic.commands._import_creds.discover_mcs_profiles",
                return_value=[ak_candidate, ncs_candidate],
            ),
        ):
            result = _invoke(isolated_config, ["suggest-creds"], obj={"format": "json"})
        data = _envelope(result)
        assert len(data["existing_mcs"]) == 2
        ak_entry, ncs_entry = data["existing_mcs"]
        assert ak_entry["name"] == "prod-ak"
        assert ak_entry["auth_kind"] == "ak"
        assert ak_entry["compute_project"] == "acme"
        assert ak_entry["sources_count"] == 0
        assert ncs_entry["auth_kind"] == "ncs"
        # Secret-leak guard: nowhere in the serialized output.
        for forbidden in (
            "AKID_LITERAL_THAT_MUST_NOT_LEAK",
            "SECRET_THAT_MUST_NOT_LEAK",
            "--employee-id 99",
        ):
            assert forbidden not in result.output, f"secret leaked: {forbidden!r} found in output"

    def test_external_shape_and_no_secret_leak(self, isolated_config: Path) -> None:
        odpscmd_ak = ImportedCreds(
            source_label="odpscmd",
            source_path=Path("/Users/x/.odpscmd/conf/odps_config.ini"),
            auth=AkAuth(
                access_key_id="AKID_EXT_LITERAL",
                access_key_secret="SECRET_EXT_LITERAL",
            ),
            compute_project="acme",
            endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        )
        maxc_ncs = ImportedCreds(
            source_label="maxc",
            source_path=Path("/Users/x/.maxc/config.yaml"),
            auth=ProcessAuth(
                command="ncs create credential odpsuser --buc-user-id 4242 -o template -t odpscmd",
            ),
            compute_project="dev_proj",
            endpoint="http://service-corp.odps.aliyun-inc.com/api",
        )
        with (
            patch(
                "maxcompute_semantic.commands._import_creds.discover_creds",
                return_value=[odpscmd_ak, maxc_ncs],
            ),
            patch(
                "maxcompute_semantic.commands._import_creds.discover_mcs_profiles",
                return_value=[],
            ),
        ):
            result = _invoke(isolated_config, ["suggest-creds"], obj={"format": "json"})
        data = _envelope(result)
        assert len(data["external"]) == 2
        first, second = data["external"]
        assert first["source"] == "odpscmd"
        assert first["auth_kind"] == "ak"
        assert first["compute_project"] == "acme"
        assert "path" in first
        assert second["source"] == "maxc"
        assert second["auth_kind"] == "ncs"
        for forbidden in (
            "AKID_EXT_LITERAL",
            "SECRET_EXT_LITERAL",
            "--buc-user-id 4242",
        ):
            assert forbidden not in result.output, f"secret leaked: {forbidden!r} found in output"

    def test_exclude_name_forwarded(self, isolated_config: Path) -> None:
        from maxcompute_semantic.commands import _import_creds as _ic

        with (
            patch.object(_ic, "discover_creds", return_value=[]),
            patch.object(_ic, "discover_mcs_profiles", return_value=[]) as mock_disc,
        ):
            _invoke(
                isolated_config,
                ["suggest-creds", "--exclude-name", "myprof"],
                obj={"format": "json"},
            )
        mock_disc.assert_called_once_with(exclude_name="myprof")


class TestEndpointPresets:
    def test_envelope_shape(self, isolated_config: Path) -> None:
        result = _invoke(isolated_config, ["endpoint-presets"], obj={"format": "json"})
        data = _envelope(result)
        assert set(data.keys()) == {
            "public_region_template",
            "common_regions",
            "internal",
        }
        assert (
            data["public_region_template"] == "https://service.<region>.maxcompute.aliyun.com/api"
        )
        assert "cn-shanghai" in data["common_regions"]
        assert "cn-beijing" in data["common_regions"]
        assert "cn-hangzhou" in data["common_regions"]
        # internal is a list of {label, url} dicts; each dict must have
        # exactly those two string keys.
        for entry in data["internal"]:
            assert set(entry.keys()) == {"label", "url"}
            assert isinstance(entry["label"], str) and entry["label"]
            assert isinstance(entry["url"], str) and entry["url"]

    def test_internal_matches_module_constant(self, isolated_config: Path) -> None:
        from maxcompute_semantic.commands.profile import _INTERNAL_ENDPOINTS

        result = _invoke(isolated_config, ["endpoint-presets"], obj={"format": "json"})
        data = _envelope(result)
        # Round-trip equivalence: the envelope's ``internal`` must
        # describe the same (label, url) pairs as _INTERNAL_ENDPOINTS,
        # in the same order.
        envelope_pairs = [(e["label"], e["url"]) for e in data["internal"]]
        constant_pairs = [(label, url) for label, url in _INTERNAL_ENDPOINTS.values()]
        assert envelope_pairs == constant_pairs


class TestListNcsIdentities:
    def test_unavailable_when_binary_missing(self, isolated_config: Path) -> None:
        with patch("maxcompute_semantic.auth.ncs.is_available", return_value=False):
            result = _invoke(isolated_config, ["list-ncs-identities"], obj={"format": "json"})
        data = _envelope(result)
        assert data["available"] is False
        assert data["identities"] == []
        assert "reason" in data and data["reason"]
        assert data["reason"] == "ncs binary not found on PATH"

    def test_unavailable_when_zero_identities(self, isolated_config: Path) -> None:
        with (
            patch("maxcompute_semantic.auth.ncs.is_available", return_value=True),
            patch(
                "maxcompute_semantic.auth.ncs.list_odps_authorizations",
                return_value=[],
            ),
        ):
            result = _invoke(isolated_config, ["list-ncs-identities"], obj={"format": "json"})
        data = _envelope(result)
        assert data["available"] is False
        assert data["identities"] == []
        assert "reason" in data
        assert data["reason"] == "ncs returned no ODPS authorizations"

    def test_populated(self, isolated_config: Path) -> None:
        from maxcompute_semantic.auth.ncs import NcsAuth

        identities = [
            NcsAuth(
                buc_user_id="111",
                buc_user_type="EMPLOYEE",
                buc_account_name="alice",
            ),
            NcsAuth(
                buc_user_id="222",
                buc_user_type="EMPLOYEE",
                buc_account_name="bob",
            ),
        ]
        with (
            patch("maxcompute_semantic.auth.ncs.is_available", return_value=True),
            patch(
                "maxcompute_semantic.auth.ncs.list_odps_authorizations",
                return_value=identities,
            ),
        ):
            result = _invoke(isolated_config, ["list-ncs-identities"], obj={"format": "json"})
        data = _envelope(result)
        assert data["available"] is True
        assert len(data["identities"]) == 2
        assert data["identities"][0] == {
            "buc_user_id": "111",
            "buc_user_type": "EMPLOYEE",
            "buc_account_name": "alice",
        }
        assert data["identities"][1]["buc_account_name"] == "bob"
        # No extra fields beyond the three documented.
        for entry in data["identities"]:
            assert set(entry.keys()) == {
                "buc_user_id",
                "buc_user_type",
                "buc_account_name",
            }

    def test_subprocess_failure_caught(self, isolated_config: Path) -> None:
        with (
            patch("maxcompute_semantic.auth.ncs.is_available", return_value=True),
            patch(
                "maxcompute_semantic.auth.ncs.list_odps_authorizations",
                side_effect=RuntimeError("ncs CLI exit 1"),
            ),
        ):
            result = _invoke(isolated_config, ["list-ncs-identities"], obj={"format": "json"})
        data = _envelope(result)
        assert data["available"] is False
        assert data["identities"] == []
        assert "reason" in data
        # The exception message must not leak; the reason is a
        # human-readable summary, not the raw exception repr.
        assert "ncs CLI exit 1" not in data["reason"]
        assert data["reason"] == "ncs probe failed"
        assert result.exit_code == 0


def _ak_profile_with_desc(name: str = "desc-prof") -> Profile:
    return Profile(
        name=name,
        compute_project="ak_project",
        endpoint="https://odps.aliyun.com/api",
        auth=AkAuth("${env:MY_AK_ID}", "${env:MY_AK_SECRET}"),
        sources=(DataSource(project="ak_project", schema="default", tables="*"),),
        description="monthly active user analysis on orders + payments",
    )


def test_show_plain_includes_description(isolated_config: Path) -> None:
    upsert(_ak_profile_with_desc())
    result = _invoke(isolated_config, ["show", "desc-prof"])
    assert result.exit_code == 0, result.output
    assert "monthly active user analysis" in result.output


def test_show_json_includes_description(isolated_config: Path) -> None:
    upsert(_ak_profile_with_desc())
    result = _invoke(isolated_config, ["show", "desc-prof"], obj={"format": "json"})
    data = _envelope(result)
    assert data["description"] == "monthly active user analysis on orders + payments"


def test_spec_template_includes_description(isolated_config: Path) -> None:
    result = _invoke(isolated_config, ["spec-template"])
    assert result.exit_code == 0, result.output
    assert "description:" in result.output
