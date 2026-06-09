# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``ProfileContext`` — the value object every click verb that
touches a profile receives.

Bundles the four fields verbs always thread through their bodies
(``profile``, ``--project`` override, ``--schema`` override,
``Renderer``) into one immutable value, and exposes the operations
they always do (``open_db``, ``reject_if_fork``, ``success``,
``error``).

Used together with :func:`commands._profile_command.profile_command`,
which is the click decorator that injects the standard
``--project / --profile / --schema`` flag triple, calls
:meth:`ProfileContext.resolve`, runs ``reject_if_fork`` on the
write-verb path, and wraps the body in the
``McsError → renderer.error → sys.exit`` ladder.

Spec: ``docs/superpowers/specs/2026-05-25-mcs-profile-context-design.md``.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.errors import (
    NoProfilesConfiguredError,
    ProfileNotFoundError,
)
from maxcompute_semantic.auth.profile_store import get
from maxcompute_semantic.auth.resolver import resolve_profile
from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    Profile,
)
from maxcompute_semantic.errors.base import McsError

if TYPE_CHECKING:
    from maxcompute_semantic.build.storage import PackageDB


@dataclass(frozen=True)
class ProfileContext:
    """Single value object every click verb that touches a profile
    receives.

    ``profile`` is the resolved :class:`Profile` with the
    ``--project`` override (if any) already applied to
    ``compute_project`` via ``dataclasses.replace``. ``project_override``
    preserves the raw flag value (``None`` when the user didn't pass
    ``--project``) so call sites that distinguish "explicit override"
    from "no override" — like ``commands.sql._route_project`` —
    don't have to compare against ``profile.compute_project``.
    ``schema_override`` is held separately because schema is per-source
    and a single value can't replace it on a multi-source profile.

    The frozen-dataclass shape rules out mid-body mutation; rebind by
    re-resolving via :meth:`resolve`.
    """

    profile: Profile
    project_override: str | None
    schema_override: str | None
    renderer: Renderer

    # Internal mutable holder for the @profile_command decorator's
    # commit-summary stash. None outside the decorator (read verbs,
    # tests). Verb bodies must not touch this — only
    # ``success(commit_summary=...)`` does, indirectly.
    _commit_holder: dict[str, str] | None = field(default=None, repr=False)

    @property
    def target_project(self) -> str:
        """The compute_project the verb should hit. Equals
        ``profile.compute_project`` after the override rewrite."""
        return self.profile.compute_project

    @classmethod
    def resolve(
        cls,
        *,
        profile_name: str | None = None,
        project: str | None = None,
        schema: str | None = None,
        renderer: Renderer | None = None,
    ) -> ProfileContext:
        """Walk the resolver chain and apply CLI overrides.

        Mirrors the pre-existing
        ``commands.profile._resolve_profile_for_project`` semantics:
        explicit ``--profile NAME`` wins, otherwise the inner three-slot
        chain (``MCS_PROFILE`` env var → cwd-link binding) selects a
        saved profile, otherwise we fall back to a synthetic Profile
        constructed from the standard ``ALIBABA_CLOUD_*`` /
        ``MAXCOMPUTE_*`` env vars. ``project``, when set, rewrites
        ``profile.compute_project`` via ``dataclasses.replace``.
        ``schema`` is held separately on :attr:`schema_override`.
        ``renderer=None`` produces a plain-mode default — useful for
        tests; the click decorator always passes a ctx-bound one.
        """
        resolved = _resolve_profile(profile_name, project)
        return cls(
            profile=resolved,
            project_override=project,
            schema_override=schema,
            renderer=renderer or Renderer(),
        )

    def open_db(self) -> PackageDB:
        """Open this profile's PackageDB. Caller is responsible for
        closing it (typical shape: ``db = pctx.open_db(); try: ...
        finally: db.close()``)."""
        from maxcompute_semantic.build.storage import PackageDB

        db_path = profile_data_dir(self.profile) / "package.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return PackageDB(db_path)

    def reject_if_fork(self) -> None:
        """Raise :class:`ProfileReadOnly` if this profile is a forked
        read-only snapshot. Called automatically by
        ``@profile_command(writeable=True)``; callers that need the
        check before resolution-decorator integration is complete can
        invoke it directly."""
        from maxcompute_semantic.versioning import reject_if_fork as _reject

        _reject(self.profile)

    def success(
        self,
        data: dict[str, Any],
        *,
        commit_summary: str | None = None,
    ) -> None:
        """Emit a success envelope and stash the commit summary for
        the decorator's commit hook.

        ``commit_summary`` is recorded only if the decorator wired in
        the holder dict (i.e. the verb is decorated with an
        ``action=`` argument). Outside the decorator the kwarg is a
        no-op, which keeps the same call site compatible with both
        directly-invoked-from-tests and click-driven flows.
        """
        self.renderer.success(data)
        if commit_summary is not None and self._commit_holder is not None:
            self._commit_holder["summary"] = commit_summary

    def error(self, exc: McsError) -> NoReturn:
        """Emit a classified error envelope on the renderer's error
        channel and exit with the McsError's ``exit_code``.

        Equivalent to letting the body raise and the decorator catch —
        provided as a method so verbs that want to handle and rewrite
        an error mid-body can do so without re-raising."""
        self.renderer.error(exc)
        sys.exit(exc.exit_code)

    def _with_commit_holder(self, holder: dict[str, str]) -> ProfileContext:
        """Decorator-only helper: returns a copy threaded with the
        decorator's mutable holder dict. Verb bodies should never
        call this directly."""
        return dataclasses.replace(self, _commit_holder=holder)


def resolve_profile_for_project(
    project: str | None = None,
    profile_name: str | None = None,
) -> Profile:
    """Resolve the active Profile and apply the optional project override.

    This is the public, command-agnostic helper for verbs that need profile
    metadata without constructing a MaxCompute client.
    """
    return _resolve_profile(profile_name, project)


def make_client_for_project(
    project: str | None = None,
    profile_name: str | None = None,
):
    """Create a MaxComputeClient using the standard profile resolution chain."""
    from maxcompute_semantic.mc_client.client import MaxComputeClient

    return MaxComputeClient(resolve_profile_for_project(project, profile_name))


def _validate_env_fallback_profile(profile: Profile) -> None:
    """Validate env-fallback auth/endpoint without requiring a saved-profile name.

    The anonymous fallback is not persisted, and commands like `mcs profile whoami`
    can use it without a compute project. Validate a copy with a legal transient
    name/project so Profile.validate() still checks endpoint/auth/cost/source shape.
    Commands that require a real compute project keep their existing downstream checks.
    """
    validation_project = profile.compute_project.strip() or "env_fallback_project"
    validation_auth = profile.auth
    if isinstance(profile.auth, AkAuth):
        has_ak_id = bool(profile.auth.access_key_id)
        has_ak_secret = bool(profile.auth.access_key_secret)
        if not has_ak_id and not has_ak_secret:
            validation_auth = AkAuth(
                access_key_id="env_fallback_access_key_id",
                access_key_secret="env_fallback_access_key_secret",
            )
    validation_copy = dataclasses.replace(
        profile,
        name="env-vars",
        compute_project=validation_project,
        auth=validation_auth,
        sources=(DataSource(project=validation_project, schema="default", tables="*"),),
    )
    validation_copy.validate()


def _resolve_profile(name: str | None, project: str | None) -> Profile:
    """The four-slot resolution chain.

    Mirrors ``commands.profile._resolve_profile_for_project`` so
    PR1 is purely a relocation, not a behavior change. PR3 will
    delete the old helper after PR2 finishes mass-rewriting call
    sites.
    """
    resolved: Profile | None = None
    if name:
        resolved = get(name)
    else:
        try:
            chained = resolve_profile(name=None)
        except NoProfilesConfiguredError:
            chained = None
        if chained:
            try:
                resolved = get(chained)
            except ProfileNotFoundError:
                resolved = None

    if resolved is not None:
        if project and project != resolved.compute_project:
            resolved = dataclasses.replace(resolved, compute_project=project)
        return resolved

    # Env-var anonymous fallback. Single DataSource pointing at the
    # env's MAXCOMPUTE_PROJECT (or the CLI ``--project`` override).
    # The schema defaults to "default" so 2-level env-var setups
    # work out of the box; for 3-level env-var setups the user
    # passes ``--schema NAME`` on the SQL subcommand.
    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    endpoint = os.environ.get("MAXCOMPUTE_ENDPOINT", "https://service.odps.aliyun.com/api")
    env_project = project or os.environ.get("MAXCOMPUTE_PROJECT", "")
    fallback = Profile(
        name=env_project,
        compute_project=env_project,
        endpoint=endpoint,
        auth=AkAuth(access_key_id=ak_id, access_key_secret=ak_secret),
        sources=(DataSource(project=env_project, schema="default", tables="*"),),
        cost_thresholds=CostThresholds(),
    )
    _validate_env_fallback_profile(fallback)
    return fallback
