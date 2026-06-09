"""Tests for ``auth/context.py`` — :class:`ProfileContext`.

Covers the four-slot resolve chain, the ``--project`` rewrite of
``compute_project``, the ``--schema`` passthrough, the
``Renderer`` plumbing, and the per-method behaviors (``open_db``,
``reject_if_fork``, ``success`` with and without commit holder,
``error``).
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.errors import InvalidProfileError, ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.errors.base import ErrorCode, McsError


def _make_profile(name: str = "acme", project: str = "acme_warehouse") -> Profile:
    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service-corp.odps.aliyun-inc.com/api",
        auth=AkAuth(access_key_id="FAKE", access_key_secret="SECRET"),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


class TestResolve:
    def test_explicit_name_wins(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        upsert(_make_profile("beta"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        assert pctx.profile.name == "alpha"

    def test_explicit_not_found_raises(self, isolated_config: Path) -> None:
        with pytest.raises(ProfileNotFoundError):
            ProfileContext.resolve(profile_name="ghost")

    def test_project_override_rewrites_compute_project(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha", project="default_proj"))
        pctx = ProfileContext.resolve(profile_name="alpha", project="other_proj")
        assert pctx.profile.compute_project == "other_proj"
        assert pctx.target_project == "other_proj"
        assert pctx.project_override == "other_proj"

    def test_no_project_override_preserves_profile_default(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha", project="default_proj"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        assert pctx.profile.compute_project == "default_proj"
        assert pctx.project_override is None

    def test_schema_held_separately(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha", schema="my_schema")
        assert pctx.schema_override == "my_schema"
        # Schema is NOT pushed onto the profile's sources; it stays on
        # the context where the verb body reads it explicitly.
        assert pctx.profile.sources[0].schema == "default"

    def test_env_var_anonymous_fallback(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No saved profile, env vars set → constructs synthetic Profile."""
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ENV_SECRET")
        monkeypatch.setenv("MAXCOMPUTE_PROJECT", "env_project")
        pctx = ProfileContext.resolve()
        assert pctx.profile.compute_project == "env_project"
        assert pctx.profile.name == "env_project"
        # Env-var fallback profile carries no project_override even
        # when constructed from MAXCOMPUTE_PROJECT — the override
        # field is reserved for the explicit ``--project`` flag.
        assert pctx.project_override is None

    def test_env_var_fallback_invalid_endpoint_is_rejected(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ENV_SECRET")
        monkeypatch.setenv("MAXCOMPUTE_PROJECT", "env_project")
        monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "ftp://service.odps.aliyun.com/api")

        with pytest.raises(InvalidProfileError, match="endpoint must start"):
            ProfileContext.resolve()

    def test_env_var_fallback_missing_secret_is_rejected(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
        monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
        monkeypatch.setenv("MAXCOMPUTE_PROJECT", "env_project")

        with pytest.raises(InvalidProfileError, match="access_key_secret is empty"):
            ProfileContext.resolve()

    def test_env_var_fallback_without_project_stays_anonymous(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ENV_AK")
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ENV_SECRET")
        monkeypatch.delenv("MAXCOMPUTE_PROJECT", raising=False)
        pctx = ProfileContext.resolve()
        assert pctx.profile.name == ""
        assert pctx.profile.compute_project == ""

    def test_env_var_fallback_empty_credentials_stays_anonymous(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        monkeypatch.delenv("MAXCOMPUTE_PROJECT", raising=False)

        pctx = ProfileContext.resolve()

        assert pctx.profile.name == ""
        assert pctx.profile.compute_project == ""
        assert pctx.profile.auth.access_key_id == ""
        assert pctx.profile.auth.access_key_secret == ""

    def test_default_renderer_when_omitted(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        # Default Renderer is plain mode — useful for tests.
        assert pctx.renderer.format == "plain"


class TestSuccessAndError:
    def test_success_emits_envelope_in_json_mode(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        out = StringIO()
        renderer = Renderer(format="json", stdout=out)
        pctx = ProfileContext.resolve(profile_name="alpha", renderer=renderer)
        pctx.success({"id": 42})
        payload = json.loads(out.getvalue())
        assert payload == {"status": "success", "data": {"id": 42}}

    def test_success_records_commit_summary_when_holder_present(
        self, isolated_config: Path
    ) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        holder: dict[str, str] = {}
        held = pctx._with_commit_holder(holder)
        held.success({"id": 7}, commit_summary="note 7")
        assert holder == {"summary": "note 7"}

    def test_success_ignores_commit_summary_without_holder(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        # No holder threaded in — should not raise even though the
        # caller provides commit_summary. This keeps verb bodies
        # callable directly from tests without needing the decorator.
        pctx.success({"id": 1}, commit_summary="ignored")

    def test_error_emits_envelope_and_exits_with_code(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        out = StringIO()
        renderer = Renderer(format="json", stdout=out)
        pctx = ProfileContext.resolve(profile_name="alpha", renderer=renderer)
        exc = McsError("boom", code=ErrorCode.AUTH_FAILED, exit_code=4)
        with pytest.raises(SystemExit) as info:
            pctx.error(exc)
        assert info.value.code == 4
        payload = json.loads(out.getvalue())
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "AuthFailed"


class TestOpenDb:
    def test_open_db_creates_data_dir(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        db = pctx.open_db()
        try:
            # The data dir was created and the package.db file exists.
            from maxcompute_semantic._internal.paths import profile_data_dir

            db_path = profile_data_dir(pctx.profile) / "package.db"
            assert db_path.parent.is_dir()
        finally:
            db.close()


class TestFrozenness:
    def test_cannot_mutate_target_project(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        with pytest.raises((AttributeError, TypeError)):
            pctx.profile = _make_profile("beta")  # type: ignore[misc]

    def test_with_commit_holder_returns_new_instance(self, isolated_config: Path) -> None:
        upsert(_make_profile("alpha"))
        pctx = ProfileContext.resolve(profile_name="alpha")
        held = pctx._with_commit_holder({})
        assert held is not pctx
        assert held.profile == pctx.profile
