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

The probe is two-stage (see :func:`_probe`): it first asks the
``/projects/<name>/schemas`` endpoint directly whether it answers — a
pure metadata read that submits no SQL instance — and only then falls
back to a pyodps ``list_schemas(project=<name>)`` enumeration, which is
the pre-existing mechanism and handles every case stage 1 cannot settle
definitively. The enumeration is routed through the named project
(passed explicitly so a single MaxCompute client connection — bound to
the AK's compute_project — can metadata-query any of the profile's
source projects via the standard pyodps cross-project metadata API). A
flat-namespace answer at either stage, recognized by
:func:`~maxcompute_semantic.errors.mc.is_two_tier_error` because the
server wording differs by which path it came down, maps to ``"2"``;
``NoPermission`` defaults to ``"3"`` with a warning (assume-3-level
errs on the operationally-safe side because the SQL session's
``odps.namespace.schema`` hint is harmless on a 2-level project but
required on a 3-level project). An unrecognized server error is
re-raised rather than guessed.

The direct-endpoint stage is not an optimization. pyodps's
``list_schemas`` swallows the accurate ``InvalidParameter: Project <p> is
not 3-tier model project`` and substitutes a ``SHOW SCHEMAS`` DDL task
that a two-tier project always rejects, so a probe built on it reports a
worse error and leaves a failed job in the user's instance list on every
cold probe.

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
from typing import TYPE_CHECKING, Any

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

    Two stages, deliberately in this order:

    1. **Metadata-first.** ``GET {endpoint}/projects/<project>/schemas`` —
       the schema-collection endpoint itself, asked once whether it
       answers. This is the question the tier actually is, and it is a
       pure metadata read: no SQL instance, no DDL task, no logview.
    2. **Enumeration** (:func:`_probe_by_enumeration`), reached only when
       the service does not implement the REST schema API at all, or when
       it answered cleanly and the schema set has to be counted.

    Stage 1 exists because pyodps's ``list_schemas`` cannot be used as a
    tier probe on its own. ``Schemas.iterate`` is wrapped by
    ``with_schema_api_fallback``, which catches both ``MethodNotAllowed``
    (service predates the API — the legacy path genuinely works there)
    and ``InvalidParameter`` — and the two-tier answer is the latter:
    ``InvalidParameter: Project <p> is not 3-tier model project``. That is
    not "the service lacks the API", it is "this project has no schema
    layer", yet the decorator treats them alike and falls back to
    ``SHOW SCHEMAS IN <p>``. On a two-tier project that DDL task is
    guaranteed to fail (``ODPS-0110061: Invalid database operations on
    two-tier model``, arriving as the base ``ODPSError`` class), so the
    fallback cannot succeed for the very input that triggers it — it only
    trades an accurate typed signal for an untyped one, plus a failed job
    in the user's instance list, and the ``_check_schema_api`` cache skips
    the REST call on later iterations but not the fallback. Asking the
    endpoint directly keeps the accurate answer and submits nothing.

    The response body is deliberately not parsed. Stage 1 reads only the
    *absence* of a tier-naming error; counting schemas goes through
    pyodps's own deserializer in stage 2 rather than a hand-rolled parse
    of a payload shape this probe has no reason to pin down.

    Stage 1 is strictly additive: it can only short-circuit the two
    answers that are already definitive here (flat-namespace → ``"2"``,
    no-permission → ``"3"``), and every other outcome — API absent, the
    request rejected for an unrelated reason, a transport failure, even an
    ``AttributeError`` from a pyodps too old to expose these accessors —
    defers to stage 2, which is the mechanism that predates this one and
    still works. Adding stage 1 therefore cannot turn a probe that used to
    answer into one that fails.

    The function returns the single-character tier identifier.
    """
    from odps import (  # type: ignore[import-untyped, unused-ignore]
        errors as odps_errors,
    )

    from maxcompute_semantic.errors import is_two_tier_error

    odps = client._ensure_odps()

    # ``get_project`` is lazy and ``resource()`` only builds a URL, so
    # stage 1 costs exactly one HTTP GET. The URL building is inside the
    # guard on purpose: a pyodps too old to expose these accessors raises
    # while assembling the call, and that must defer to stage 2 rather
    # than break a probe that used to work.
    try:
        schemas_url = odps.get_project(project).resource() + "/schemas"
        odps.rest.get(schemas_url, params={"expectmarker": "true"})
    except Exception as exc:  # noqa: BLE001 — every miss defers to stage 2
        if is_two_tier_error(exc):
            return "2"
        if isinstance(exc, odps_errors.NoPermission):
            # Enumeration reads the same URL under the same privilege, so
            # asking it again could only repeat this answer.
            return _assume_three_level(project, stage="schema endpoint")
    return _probe_by_enumeration(odps, project)


def _assume_three_level(project: str, *, stage: str) -> str:
    """Apply the assume-3-level-on-no-permission policy from a probe stage.

    ``odps.namespace.schema=true`` — the session hint a ``"3"`` verdict
    buys — is ignored by a 2-level project but required by a 3-level one,
    so guessing high is the operationally safe direction. ``MCS_TIER_OVERRIDE=2``
    pins the other way when the operator knows better.
    """
    logger.warning(
        "no permission to read schemas on project %r (%s); assuming 3-level "
        "(set MCS_TIER_OVERRIDE=2 to pin to 2-level if the project is "
        "actually flat-namespace and the AK should fall back).",
        project,
        stage,
    )
    return "3"


def _probe_by_enumeration(odps: Any, project: str) -> str:
    """Tier verdict from a schema enumeration: the pre-stage-1 mechanism.

    Runs ``odps.list_schemas(project=<name>)``, which the connection bound
    to the AK's compute_project routes to any project the AK has
    Describe-level cross-project access to (the standard MaxCompute
    cross-project metadata pattern).

    Return-value contract:

    - ``NotSupportedError`` (the project doesn't have the schema-enable
      flag set on its project-config) → ``"2"``.
    - ``NoPermission`` (the AK can't enumerate schemas here) → ``"3"`` via
      :func:`_assume_three_level`.
    - Any other ``ODPSError`` whose message
      :func:`~maxcompute_semantic.errors.mc.is_two_tier_error` recognizes →
      ``"2"``. That predicate is the single source of truth for the
      flat-namespace wording, which it has to be because the wording
      follows whichever metadata path pyodps took — and the DDL-fallback
      shape is a base ``ODPSError``, so an ``InternalServerError``-only
      catch let it escape unclassified and abort the caller.
    - An ``ODPSError`` with no two-tier signature is re-raised so the
      caller surfaces it as a real probe failure — the probe never guesses
      on an unrecognized server error.
    - Success: ``"3"`` if the schema list is non-empty, ``"2"`` if empty
      (a 3-level project whose visible-schema set is empty is
      indistinguishable from the flat-namespace case, and the SQL
      session-hint helps in neither).
    """
    from odps import (  # type: ignore[import-untyped, unused-ignore]
        errors as odps_errors,
    )

    from maxcompute_semantic.errors import is_two_tier_error

    try:
        schemas = list(odps.list_schemas(project=project))
    except odps_errors.NotSupportedError:
        return "2"
    except odps_errors.NoPermission:
        return _assume_three_level(project, stage="enumeration")
    except odps_errors.ODPSError as exc:
        if is_two_tier_error(exc):
            return "2"
        raise
    return "3" if schemas else "2"
