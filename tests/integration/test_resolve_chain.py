"""Integration tests for the active-profile resolution chain.

The user-facing chain is exposed by
``commands.profile._resolve_profile_for_project``:

  1. Explicit ``--profile NAME`` flag.
  2. ``MCS_PROFILE`` env-var pointer to a saved profile name.
  3. cwd-link binding from ``mcs link bind``, stored in
     ``link.json``.
  4. ``ALIBABA_CLOUD_*`` env-vars-anonymous in-memory Profile.

The saved-profile slots are ``auth.resolver.resolve_profile``'s job,
which the unit tests under ``tests/unit/auth/test_resolver.py`` cover.
This file is the integration-level companion that drives the full
setup: ``profile_store.upsert`` to register named profiles in
``profiles.yaml``, ``link_store.set_link`` to write a cwd-link in
``link.json``, ``monkeypatch.setenv`` to set ``MCS_PROFILE`` in the
process env, and assertions on which profile wins when the slots are
competing.

The tests here focus on the live slots only: explicit name,
``MCS_PROFILE`` and cwd link.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from maxcompute_semantic.auth.link_store import set_link
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.resolver import resolve_profile
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.commands.profile import _resolve_profile_for_project


def _make(name: str) -> Profile:
    """Minimal ``Profile`` fixture that passes
    ``Profile.validate`` (which ``upsert`` calls transitively as
    its config-shape sanity gate). The chain assertions in this
    file only inspect the name the resolver returns — the
    ``compute_project`` / ``endpoint`` / ``auth`` / ``sources``
    field values exist solely to satisfy the validator.
    """
    return Profile(
        name=name,
        compute_project=f"proj_{name}",
        endpoint="http://x",
        auth=AkAuth("ak", "secret"),
        sources=(DataSource(project=f"proj_{name}", schema="default", tables="*"),),
    )


def test_full_chain_explicit_wins(isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Slot 1 (the explicit ``name`` arg to ``resolve_profile``)
    beats every lower slot. The setup populates the env-var
    slot ("bbb") and the cwd-link slot ("ccc") with competing
    targets, and the assertion is that the explicit name
    ("aaa") wins all the same.

    No other slot should override the explicit name.
    """
    for n in ["aaa", "bbb", "ccc"]:
        upsert(_make(n))
    monkeypatch.chdir(isolated_config)
    set_link(str(isolated_config), "ccc")
    monkeypatch.setenv("MCS_PROFILE", "bbb")
    assert resolve_profile(name="aaa") == "aaa"


def test_full_chain_env_var_beats_link(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slot 4 / resolver-slot-2 (``MCS_PROFILE`` env var) beats
    slot 3 (cwd-link binding from ``mcs link bind``) when both
    are set.
    """
    for n in ["bbb", "ccc"]:
        upsert(_make(n))
    monkeypatch.chdir(isolated_config)
    set_link(str(isolated_config), "ccc")
    monkeypatch.setenv("MCS_PROFILE", "bbb")
    assert resolve_profile() == "bbb"


def test_outer_chain_project_does_not_select_saved_profile(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--project P`` does not select a saved profile named ``P``."""
    for n in ["data_proj", "env_profile", "linked_profile"]:
        upsert(_make(n))
    monkeypatch.chdir(isolated_config)
    set_link(str(isolated_config), "linked_profile")
    monkeypatch.setenv("MCS_PROFILE", "env_profile")

    resolved = _resolve_profile_for_project(project="data_proj")

    assert resolved.name == "env_profile"


def test_outer_chain_cwd_link_beats_project_name_match(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no env pointer, cwd link wins even if project matches an alias."""
    for n in ["data_proj", "linked_profile"]:
        upsert(_make(n))
    monkeypatch.chdir(isolated_config)
    monkeypatch.delenv("MCS_PROFILE", raising=False)
    set_link(str(isolated_config), "linked_profile")

    resolved = _resolve_profile_for_project(project="data_proj")

    assert resolved.name == "linked_profile"


def test_outer_chain_env_fallback_uses_project_arg(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no saved profile is selected, ``--project`` names env fallback."""
    upsert(_make("cli_proj"))
    monkeypatch.chdir(isolated_config)
    monkeypatch.delenv("MCS_PROFILE", raising=False)
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv("MAXCOMPUTE_ENDPOINT", "http://service.odps.aliyun.com/api")
    monkeypatch.setenv("MAXCOMPUTE_PROJECT", "env_proj")

    resolved = _resolve_profile_for_project(project="cli_proj")

    assert resolved.name == "cli_proj"
    assert resolved.compute_project == "cli_proj"
    assert isinstance(resolved.auth, AkAuth)
    assert resolved.auth.access_key_id == "ak"
    assert resolved.endpoint == "http://service.odps.aliyun.com/api"
    assert resolved.sources[0].project == "cli_proj"
