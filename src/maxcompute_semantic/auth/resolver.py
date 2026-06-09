# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""The "which profile is active right now?" decision tree.

Three slots in descending priority. The first that yields the
name of a saved profile that actually exists in ``profiles.yaml``
wins; later slots aren't consulted. The chain raises
``NoProfilesConfiguredError`` only when every slot is empty.

  1. Explicit ``name`` argument — the CLI's ``--profile NAME``
     flag and the outer wrappers in
     ``commands.profile._make_client_for_project`` and
     ``commands.profile._resolve_profile_for_project`` are the
     upstream paths that feed this slot. The named profile must
     exist on disk (``ProfileNotFoundError`` is propagated
     otherwise — the user named a non-existent target and silent
     fall-through would mask the typo).

  2. The ``MCS_PROFILE`` environment variable, stripped, when
     non-empty. Names a saved profile in ``profiles.yaml``.
     Same hard-error-on-missing contract as slot 1. This is the
     shell-scoped active-profile pointer — the
     ``export MCS_PROFILE=foo`` form sets it for the current
     shell, the ``~/.zshrc`` form sets it persistently per-user,
     and a CI yaml's job-level ``env:`` block sets it
     per-job-invocation.

  3. The cwd-link binding written by ``mcs link bind <NAME>``.
     The link store (``auth.link_store``) maps the current
     working directory (or an explicit ``cwd=`` arg) to a
     profile name via ``link.json`` in the config dir. A "stale"
     binding — the linked name no longer corresponds to a saved
     profile because someone ran ``mcs profile remove`` after
     the link was written — logs a warning and falls through to
     the chain-exhausted case rather than failing hard, since
     the ``link.json`` artifact outliving the profile it pointed
     at is a known wedged state we'd rather warn-then-recover
     from than abort the whole CLI on.

The outer wrapper ``commands.profile._resolve_profile_for_project``
adds the standard ``ALIBABA_CLOUD_*`` env-vars-anonymous Profile
constructor below this resolver for the "there's no on-disk profile
at all, the user's just got the env-vars set the canonical pyodps
way" case. ``commands.link.status_cmd`` / ``unlink_cmd`` and the
wizard's ``_bind_action`` consult this resolver directly for the
"what would the next command pick if I ran one right now?"
introspection question.
"""

from __future__ import annotations

import logging
import os

from maxcompute_semantic.auth.errors import NoProfilesConfiguredError, ProfileNotFoundError
from maxcompute_semantic.auth.link_store import get_link
from maxcompute_semantic.auth.profile_store import get, load_all

logger = logging.getLogger("maxcompute_semantic")


def resolve_profile(name: str | None = None, cwd: str | None = None) -> str:
    """Return the active-profile name per the three-slot inner chain.

    Priority and semantics are documented at module level. The
    return value is always the bare name string of a profile that
    exists on disk at call time — never a Profile object, never
    a sentinel or alias outside the saved profile names.

    Exceptions:

      ``ProfileNotFoundError`` — slot 1 (explicit name) or slot 2
      (``MCS_PROFILE`` env var) named a profile that isn't on
      disk. The error's message identifies which slot the bad
      pointer came from. Stale slot-3 (cwd-link) bindings don't
      raise; they log a warning and fall through.

      ``NoProfilesConfiguredError`` — the inner chain ran to
      exhaustion (no explicit name, no env var, no live cwd-link
      binding). Two flavours of remediation depending on whether
      ``load_all()`` finds anything: when there are zero saved
      profiles, the remediation suggests ``mcs profile create``
      / ``mcs profile import-creds``; when there are saved
      profiles but the user hasn't pointed the chain at any of
      them, the remediation lists the three ways to point the
      chain (``--profile`` flag, ``mcs link bind``, ``export
      MCS_PROFILE=…``) plus ``mcs profile list`` to see the
      candidate names.
    """
    # Slot 1 — explicit name.
    if name is not None:
        _validate_exists(name)
        return name

    # Slot 2 — the ``MCS_PROFILE`` env var. The ``.strip()`` lets
    # whitespace-only values fall through to slot 3 (treating an
    # accidentally-empty export as "not set"), and ``""`` from
    # ``os.environ.get`` collapses to the same fall-through path.
    env_profile = os.environ.get("MCS_PROFILE", "").strip()
    if env_profile:
        _validate_exists(env_profile)
        return env_profile

    # Slot 3 — the cwd-link binding in ``link.json``. Stale
    # bindings (the linked name no longer corresponds to a saved
    # profile) log a warning and continue; the no-binding case
    # silently falls through.
    link = get_link(cwd)
    if link is not None:
        if _profile_exists(link):
            return link
        logger.warning(
            "link.json names a profile that no longer exists: %r — "
            "ignoring the stale binding and treating the cwd as "
            "unbound. Re-run `mcs link bind <name>` against an "
            "existing profile, or `mcs link unlink` to clear the "
            "binding.",
            link,
        )

    # Chain exhausted. The two remediation forms differ on
    # whether there's anything saved to point at at all.
    if not load_all():
        raise NoProfilesConfiguredError(
            "no profiles configured",
            remediation=(
                "set up a profile with one of the three create "
                "paths: ``mcs profile create`` (interactive "
                "wizard), ``mcs profile create --from-file "
                "@<path>`` (non-interactive, takes a fillable "
                "yaml — ``mcs profile spec-template`` prints the "
                "shape), or ``mcs profile import-creds`` (scans "
                "the existing ``~/.maxc/config.yaml`` and the "
                "odpscmd ``odps_config.ini`` and pulls the AK "
                "pair from whichever it finds)."
            ),
        )

    raise NoProfilesConfiguredError(
        "the active-profile chain (explicit ``--profile`` flag, "
        "the ``MCS_PROFILE`` environment variable, and the "
        "cwd-link binding from ``mcs link bind``) yielded no "
        "active profile, even though there are saved profiles "
        "on disk.",
        remediation=(
            "pick one of the saved profiles by passing "
            "``--profile <NAME>`` on each command line, by "
            "binding the current directory to one via ``mcs "
            "link bind <NAME>`` (writes a per-cwd entry to "
            "``link.json`` in the config dir), or by setting "
            "the shell-scoped ``MCS_PROFILE`` environment "
            "variable (the typical form is ``export "
            "MCS_PROFILE=<NAME>`` in your shell's rc file). "
            "``mcs profile list`` shows the names of the saved "
            "profiles to pick from."
        ),
    )


def _validate_exists(name: str) -> None:
    """Existence gate. The ``profile_store.get(name)`` call is the
    canonical "raise ``ProfileNotFoundError`` if missing"
    primitive; we just call it for its side effect.
    """
    get(name)


def _profile_exists(name: str) -> bool:
    """Existence-as-boolean. Used by the slot-3 stale-link branch
    above, which wants the "is this name still valid?" question
    answered without propagating the not-found error.
    """
    try:
        get(name)
        return True
    except ProfileNotFoundError:
        return False
