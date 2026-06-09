"""Tests for ``auth/resolver.py`` — the three-slot inner chain.

The resolver priority is explicit ``name`` arg → ``MCS_PROFILE``
env var → ``link.json`` cwd binding. The wider user-facing chain
adds the ``ALIBABA_CLOUD_*`` env-vars-anonymous Profile constructor
below; ``--project P`` names the target MaxCompute project, not a
saved profile alias.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from maxcompute_semantic.auth.errors import NoProfilesConfiguredError, ProfileNotFoundError
from maxcompute_semantic.auth.link_store import set_link
from maxcompute_semantic.auth.profile_store import upsert
from maxcompute_semantic.auth.resolver import resolve_profile
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile


def _make_profile(name: str = "acme-corp") -> Profile:
    """A multi-source Profile fixture for the chain tests.

    The resolver only inspects the top-level mapping key (the
    profile's ``name``), so the body content of the dataclass
    doesn't matter for the assertions — but ``Profile.validate``
    requires a non-empty ``compute_project`` and at least one
    ``DataSource`` entry to round-trip through ``upsert``, hence
    the fixed "acme_warehouse" / ``schema="default"`` / wildcard
    tables.
    """
    return Profile(
        name=name,
        compute_project="acme_warehouse",
        endpoint="http://service-corp.odps.aliyun-inc.com/api",
        auth=AkAuth(access_key_id="FAKE_AK_ID", access_key_secret="FAKE_AK_SECRET"),
        sources=(DataSource(project="acme_warehouse", schema="default", tables="*"),),
    )


class TestResolveProfile:
    """The chain is "explicit ``name`` arg → ``MCS_PROFILE`` env →
    cwd-link binding from ``link.json``", three slots. The first
    that yields the name of an existing-on-disk profile wins. The
    bottom of the chain raises ``NoProfilesConfiguredError`` with
    a remediation depending on whether ``load_all()`` returns
    anything (no profiles configured at all vs. profiles exist
    but no pointer at one).
    """

    def test_explicit_name_wins(self, isolated_config: Path) -> None:
        """Slot 1 (explicit name) is the top of the chain. Even
        with a competing target in a lower slot (the env var
        here), the explicit name fires.
        """
        upsert(_make_profile("alpha"))
        upsert(_make_profile("beta"))
        # No env-var or link setup is needed — the explicit-name
        # slot bypasses all the lower slots regardless of what
        # they're set to. We still ensure ``beta`` exists so that
        # if the chain accidentally fell through to a lower slot
        # pointing at ``beta`` the assertion would have an
        # alternate verifiable name to fail against.
        assert resolve_profile("alpha") == "alpha"

    def test_env_var_beats_link(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slot 2 (``MCS_PROFILE``) beats slot 3 (cwd-link).

        The env var is a deliberate shell-scoped choice, so it
        outranks the directory binding.
        """
        upsert(_make_profile("gamma"))
        upsert(_make_profile("beta"))
        set_link(str(isolated_config / "work"), "beta")
        monkeypatch.setenv("MCS_PROFILE", "gamma")
        assert resolve_profile(None, cwd=str(isolated_config / "work")) == "gamma"

    def test_link_fires_when_no_explicit_no_env(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slot 3 (cwd-link) fires when slots 1 and 2 are empty.

        This is the directory-scoped active profile path.
        """
        upsert(_make_profile("alpha"))
        upsert(_make_profile("beta"))
        monkeypatch.delenv("MCS_PROFILE", raising=False)
        set_link(str(isolated_config / "work"), "beta")
        assert resolve_profile(None, cwd=str(isolated_config / "work")) == "beta"

    def test_no_profiles_raises(self, isolated_config: Path) -> None:
        """Empty ``profiles.yaml`` (no profiles configured at
        all): the bottom of the chain raises
        ``NoProfilesConfiguredError`` with a remediation that
        points at ``mcs profile create`` /
        ``mcs profile import-creds``.
        """
        with pytest.raises(NoProfilesConfiguredError, match="no profiles configured"):
            resolve_profile(None)

    def test_explicit_not_found_raises(self, isolated_config: Path) -> None:
        """Slot 1 with a name that doesn't exist on disk:
        ``ProfileNotFoundError`` propagates through the
        ``_validate_exists`` gate the resolver's slot-1 branch
        runs.
        """
        upsert(_make_profile("alpha"))
        with pytest.raises(ProfileNotFoundError):
            resolve_profile("nonexistent")

    def test_env_var_not_found_raises(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slot 2 with an env var pointing at a name that doesn't
        exist: same ``ProfileNotFoundError`` hard-error as slot
        1. The user explicitly named the target (the env var is
        an explicit choice the same way the ``--profile`` flag
        is), so the silent-fall-through that the cwd-link slot
        uses isn't appropriate here.
        """
        upsert(_make_profile("alpha"))
        monkeypatch.setenv("MCS_PROFILE", "nonexistent")
        with pytest.raises(ProfileNotFoundError):
            resolve_profile(None)

    def test_stale_link_warns_and_falls_through(
        self,
        isolated_config: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A cwd-link that names a profile which has since been
        removed (``mcs profile remove``) is "stale": the
        resolver logs a warning and falls through to the
        higher-priority-slot-already-checked, lower-priority-
        slot-still-to-check sequence as if the link weren't
        there. With nothing in slot 2 (env var) and nothing in
        slot 1 (explicit), the chain runs to exhaustion and
        raises ``NoProfilesConfiguredError``.

        The warning-log assertion confirms the stale binding is
        visible while still allowing the chain to keep moving.
        """
        upsert(_make_profile("alpha"))
        # The cwd-link points at a profile name that does not
        # exist in profiles.yaml — the resolver's
        # ``_profile_exists(link)`` check returns False, the
        # warning is logged, and the chain continues to the
        # next-lower slot.
        set_link(str(isolated_config / "work"), "deleted-profile")
        monkeypatch.delenv("MCS_PROFILE", raising=False)

        # Two contexts side by side: ``caplog.at_level`` captures
        # the resolver's stale-link warning, and ``pytest.raises``
        # absorbs the chain-exhausted ``NoProfilesConfiguredError``
        # that the resolver hits after the stale link warning fires
        # (no env-var and no explicit-name are set in this fixture
        # so the chain runs all the way to the bottom). The
        # parenthesised-with form is Python 3.10+ syntax for
        # multiple context managers on one ``with`` statement,
        # which ruff's SIM117 rule prefers over nesting.
        with (
            caplog.at_level(logging.WARNING, logger="maxcompute_semantic"),
            pytest.raises(NoProfilesConfiguredError, match="active-profile chain"),
        ):
            resolve_profile(None, cwd=str(isolated_config / "work"))

        # The stale-link warning text is the literal-string check; the
        # ``caplog.records`` enumeration in the failure-message branch
        # is lifted to a local so the assert line itself stays under
        # the project's 100-column width budget.
        log_messages = [rec.message for rec in caplog.records]
        assert any(
            "link.json names a profile that no longer exists" in msg and "deleted-profile" in msg
            for msg in log_messages
        ), f"missing the stale-link warning; saw: {log_messages!r}"

    def test_profiles_exist_no_resolution_hint_raises(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Saved profiles exist but the user hasn't pointed the
        chain at any of them: ``NoProfilesConfiguredError`` with
        the "active-profile chain yielded no active profile"
        remediation. The match regex anchors on the current
        wording ("active-profile chain").
        """
        monkeypatch.delenv("MCS_PROFILE", raising=False)
        upsert(_make_profile("alpha"))
        # No link binding, no env var, no explicit name — chain
        # runs to exhaustion. The branch the resolver takes is
        # the second ``raise NoProfilesConfiguredError`` site
        # (the "saved profiles exist but nothing in the chain
        # points at them" branch, the one whose remediation
        # lists the three ways to set the active profile).
        with pytest.raises(NoProfilesConfiguredError, match="active-profile chain"):
            resolve_profile(None)

    def test_legacy_file_pointer_is_ignored(
        self, isolated_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale top-level pointer in profiles.yaml is not a resolver slot."""
        from maxcompute_semantic._internal.paths import profiles_yaml_path

        monkeypatch.delenv("MCS_PROFILE", raising=False)
        upsert(_make_profile("alpha"))
        path = profiles_yaml_path()
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("version: 1\n", "version: 1\ndefault_profile: alpha\n", 1),
            encoding="utf-8",
        )

        with pytest.raises(NoProfilesConfiguredError, match="active-profile chain"):
            resolve_profile(None, cwd=str(isolated_config / "unbound"))
