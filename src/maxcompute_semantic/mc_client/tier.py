# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tier detection (2-level vs 3-level) with per-project on-disk cache.

The tier cache is keyed per-(profile, MaxCompute project) so a
multi-source profile — where the AK's compute_project and each
DataSource.project may be different MaxCompute projects, each with
its own 2-level-or-3-level state — gets one cache file per project
under the profile's data directory's ``tier_cache/`` subdir. The
file's single-character content (``"2"`` or ``"3"``) is the cached
probe result.

The probe itself is a pyodps ``list_schemas(project=<name>)`` call
against the named project (passed explicitly so a single MaxCompute
client connection — bound to the AK's compute_project — can
metadata-query any of the profile's source projects via the standard
pyodps cross-project metadata API). A ``NotSupportedError`` or
``InternalServerError`` with "not 3-tier" maps to ``"2"``;
``NoPermission`` defaults to ``"3"`` with a warning (assume-3-level
errs on the operationally-safe side because the SQL session's
``odps.namespace.schema`` hint is harmless on a 2-level project but
required on a 3-level project).

The ``MCS_TIER_OVERRIDE`` env-var is the highest-priority pin and
applies as a global override across all projects the helper is asked
about — useful for the eval CI where the tier-of-record is fixed
across the matrix arms regardless of the live MaxCompute state.

The first argument's ``Profile | str`` discriminant matches the
``profile_data_dir`` helper's signature: the dataclass form honors
``Profile.package_path`` for the imported / NFS-mounted /
custom-data-dir case; the bare-string form is the cleanup path where
the Profile object isn't live.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

# Dual silence: ``import-untyped`` is needed for package-local mypy
# (pyodps ships no ``py.typed`` marker), ``unused-ignore`` is needed
# for workspace-root mypy (the root ``pyproject.toml`` carries an
# ``ignore_missing_imports = true`` override for ``odps.*``, which
# pre-silences the import and makes the first code redundant).
from odps import errors as odps_errors  # type: ignore[import-untyped, unused-ignore]

from maxcompute_semantic._internal.paths import tier_cache_path
from maxcompute_semantic.auth.schema import Profile

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient

logger = logging.getLogger("maxcompute_semantic")


def get_tier(
    profile: Profile,
    project: str,
    *,
    client: MaxComputeClient | None = None,
    allow_live_probe: bool = True,
) -> str:
    """Return ``"2"`` or ``"3"`` for the named MaxCompute project's
    tier, with the per-(profile, project) on-disk cache.

    Priority chain:

    1. The ``MCS_TIER_OVERRIDE`` env-var. When set to ``"2"`` or
       ``"3"`` it's the unconditional global pin and the function
       returns it without touching the cache file or the network.
       The pin applies to every project the helper is asked about,
       which is the eval CI's "make the test arms see a fixed tier
       regardless of the live MaxCompute" trick.
    2. The on-disk cache at
       ``<profile_data_dir(profile)>/tier_cache/<project>``. The
       file's content is one character — ``"2"`` or ``"3"`` — that
       a previous probe wrote. The cache has no TTL; it's
       invalidated only by file removal (the ``mcs profile remove``
       cleanup wipes the whole profile_data_dir and the cache
       within it, the operator can manually delete a single
       ``tier_cache/<project>`` file to force one project's next
       probe to re-run).
    3. A live probe via ``client.list_schemas(project=<name>)`` —
       the spec §3 vocabulary entry's "live tier probe". The
       pyodps ``list_schemas`` keyword's name is the standard
       MaxCompute-cross-project metadata API. A 3-level project
       returns its schema list; a 2-level project raises
       ``NotSupportedError`` or ``InternalServerError`` with the
       "Project ... is not 3-tier model project" message. The
       ``NoPermission`` case (the AK doesn't have list-schemas
       privilege on the named project) defaults to ``"3"`` with a
       warning log because the SQL-session's
       ``odps.namespace.schema`` hint is harmless on a 2-level
       project that the agent's later qualified-name SQL queries
       hit, but the absence of the hint on a 3-level project
       breaks every bare-name reference — assume-3-level errs on
       the operationally-safe side.

    The probe result is written to the cache for the next
    invocation. Cache-write failures (filesystem permissions,
    disk-full) are warned-and-skipped per the v0.3.x convention,
    so the helper degrades gracefully to "always probe live" when
    the cache can't be persisted.

    The ``project`` argument is the explicit name of the
    MaxCompute project whose tier is being determined. For the
    AK-identity-side calls (the live ``mcs profile whoami`` /
    create-or-update profile probes, the meta-subcommands'
    SQL-session-hint decision in ``commands/sql.py``, the
    ``mcs memory verify`` tier-of-record field) the argument is the
    profile's ``compute_project`` since the SQL session's
    ``odps.namespace.schema`` hint is governed by the connection's
    bound project's tier. For the data-side per-source probes (the
    build pipeline's outer-loop in spec §15 phase 6 that
    determines each source's project's
    ``detect_info_schema_source`` target, the
    ``mcs profile show`` per-source-tier column, and the
    profile-create/update validation probe) the argument is the corresponding
    ``DataSource.project``. The cache file's per-project keying
    keeps the two sides of the AK's project graph separate.

    The ``client`` keyword is the optional pyodps wrapper. When
    ``None``, the helper builds a fresh ``MaxComputeClient(profile)``
    which establishes the pyodps connection on the profile's
    ``compute_project`` — that's the AK's home project where the
    connection's instance-creation privilege lives. The ``list_schemas``
    call against a different ``project`` name still goes through the
    same connection, because the AK's cross-project metadata
    privileges (the spec §11 ACL boundary's table-level Describe
    GRANT) are what authorizes the metadata read regardless of the
    connection's bound project. Callers that already have a
    ``MaxComputeClient`` (the wizard's auth-test, the build
    pipeline's per-source loop) pass it through so the same
    connection's HTTP keepalive pool is reused.

    The ``Profile`` first argument is propagated through to
    ``tier_cache_path`` which calls ``profile_data_dir(profile)``
    which honors ``Profile.package_path`` for the imported /
    NFS-mounted / custom-data-dir profile case from the
    2026-05-14 vocabulary-cleanup spec. The first argument's
    ``Profile | str`` discriminant matches the
    ``profile_data_dir`` helper's signature — the bare-string form
    is the post-``mcs profile remove`` cleanup path where the
    profile config is already gone and the helper just produces
    the cache-file path string for the ``shutil.rmtree`` call.
    """
    override = os.environ.get("MCS_TIER_OVERRIDE")
    if override in {"2", "3"}:
        return override

    cache_path = tier_cache_path(profile, project)
    if cache_path.exists():
        try:
            content = cache_path.read_text(encoding="utf-8").strip()
            if content in {"2", "3"}:
                return content
            # Cache file exists but has an unexpected content — warn
            # and fall through to the live probe. The corrupted-cache
            # path overwrites with the fresh probe result below.
            logger.warning(
                "tier cache at %s has unexpected content %r (expected '2' or '3'); "
                "ignoring and re-probing",
                cache_path,
                content,
            )
        except OSError as e:
            logger.warning("failed to read tier cache at %s: %s; will re-probe", cache_path, e)

    if not allow_live_probe:
        # Callers that promised "no MaxCompute round-trip" (e.g.
        # ``mcs sql review``) opt out of the live probe entirely.
        # When the cache misses and the override is unset, fall back
        # to ``"3"`` — the operationally-safe assumption that matches
        # the ``NoPermission`` branch in ``_probe`` below: the
        # ``odps.namespace.schema=true`` session hint is harmless on
        # a 2-level project (ignored) but required on a 3-level one,
        # so guessing "3" never breaks SQL that would otherwise run.
        return "3"
    if client is None:
        from maxcompute_semantic.mc_client.client import MaxComputeClient

        client = MaxComputeClient(profile)
    tier = _probe(client, project)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(tier, encoding="utf-8")
    except OSError as e:
        logger.warning(
            "failed to write tier cache at %s: %s; will probe again next time",
            cache_path,
            e,
        )
    return tier


def _probe(client: MaxComputeClient, project: str) -> str:
    """Live tier probe for one named MaxCompute project.

    Runs ``odps.list_schemas(project=<name>)`` through the given
    client's pyodps connection. The connection is bound to the AK's
    compute_project, but pyodps's ``list_schemas`` accepts a
    ``project=`` keyword that routes the metadata query to any
    project the AK has Describe-level cross-project access to (the
    standard MaxCompute cross-project metadata pattern). The
    return-value-shape contract:

    - ``NotSupportedError`` from pyodps (the project doesn't have
      the schema-enable flag set on its project-config) → ``"2"``.
    - ``InternalServerError`` with a message containing "not
      3-tier" or "not 3 tier" → ``"2"``. This is the historical
      MaxCompute error format for the "the named project is in the
      flat-namespace mode" case; the message text varies across
      MaxCompute service-side versions, hence the two-pattern
      check.
    - ``NoPermission`` (the AK doesn't have list-schemas on the
      named project) → ``"3"`` with a warning. The
      assume-3-level-on-ambiguity choice means the SQL session's
      ``odps.namespace.schema=true`` hint gets injected, which is
      harmless on a 2-level project (the hint is ignored) but
      required on a 3-level project (without the hint, the
      session's bare-table-name references fail to resolve). The
      operator can manually pin via the ``MCS_TIER_OVERRIDE`` env
      to override the auto-fallback if the project is actually
      2-level.
    - Any other ``InternalServerError`` is re-raised so the caller
      surfaces it as a real probe failure.
    - The success case (the AK can list_schemas and the call
      returns) is "3" if the returned schema list is non-empty,
      "2" if it's empty (which is the corner case of a 3-level
      project whose only schemas the AK can see are zero — empty
      means the AK has list-schemas privilege but the AK's
      visible-schema-set within the project is empty, which is
      indistinguishable from the 2-level case where the schema
      concept doesn't exist, so the conservative default is "2"
      because the SQL session-hint wouldn't help in either case).

    The function returns the single-character tier identifier.
    """
    odps = client._ensure_odps()
    try:
        schemas = list(odps.list_schemas(project=project))
    except odps_errors.NotSupportedError:
        return "2"
    except odps_errors.InternalServerError as e:
        msg = str(e).lower()
        if "not 3-tier" in msg or "not 3 tier" in msg:
            return "2"
        raise
    except odps_errors.NoPermission:
        logger.warning(
            "no list-schemas permission on project %r; assuming 3-level "
            "(set MCS_TIER_OVERRIDE=2 to pin to 2-level if the project is "
            "actually flat-namespace and the AK should fall back).",
            project,
        )
        return "3"
    return "3" if schemas else "2"
