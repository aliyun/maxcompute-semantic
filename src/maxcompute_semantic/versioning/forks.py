# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Helpers for the fork-vs-main metadata layer.

A fork is a ``Profile`` with ``kind="fork"`` whose
``package_path`` points at a detached ``git worktree`` of the
parent's per-profile git repository. The parent's repository is
the *only* source of truth for the shared history; the fork's
working directory is a checkout of one specific commit (its
``git_sha`` anchor) on that history, with the standard git
worktree-admin-dir under the parent's
``.git/worktrees/<fork-short-name>/``.

The functions here are the yaml-side bookkeeping that goes
hand-in-hand with the git-side ``git worktree add`` /
``git worktree remove`` calls in
``versioning.git_repo.GitRepo``. The full create / remove flow
happens in ``commands/profile_fork.py``, which composes a
wrapper call with a ``register_fork`` / ``unregister_fork``
call so the on-disk worktree and the yaml entry stay in
lockstep.

The ``parent_repo(fork)`` helper resolves the parent profile's
data directory and returns a ``GitRepo`` rooted there — every
fork-related git operation goes through the parent's repo,
because the fork's worktree shares the parent's object
database and the wrapper's ``log`` / ``rev-parse`` /
``merge-base`` commands need to be scoped to the parent's
object store.
"""

from __future__ import annotations

from pathlib import Path

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.errors import ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import (
    get as get_profile,
)
from maxcompute_semantic.auth.profile_store import (
    remove as remove_profile_yaml_entry,
)
from maxcompute_semantic.auth.profile_store import (
    upsert as upsert_profile,
)
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning.git_repo import GitRepo


def register_fork(
    parent: Profile,
    fork_name: str,
    sha: str,
    worktree_path: Path,
) -> Profile:
    """Construct the fork ``Profile`` and persist it to
    profiles.yaml via the standard ``profile_store.upsert``.

    The wrapper's ``rev_parse`` is called first to normalize a
    short SHA into the full 40-hex form so the yaml's
    ``git_sha`` field is always the canonical full form.

    The fork inherits the parent's ``compute_project``,
    ``endpoint``, ``auth``, ``sources``, ``cost_thresholds``,
    and ``tags`` fields. The ``package_path`` is set to the
    explicit ``worktree_path``.

    Raises ``McsError`` if a profile named ``fork_name``
    already exists with a different shape; idempotent when an
    existing fork-of-parent at the same anchor SHA is
    re-registered (returns the existing entry).
    """
    parent_data_dir = profile_data_dir(parent)
    parent_git = GitRepo(parent_data_dir)
    full_sha = parent_git.rev_parse(sha)

    try:
        existing = get_profile(fork_name)
    except ProfileNotFoundError:
        existing = None

    if existing is not None:
        if (
            existing.kind == "fork"
            and existing.parent_profile == parent.name
            and existing.git_sha == full_sha
        ):
            return existing
        raise McsError(
            f"a profile named {fork_name!r} already exists "
            f"(kind={existing.kind!r}). Pick a different name "
            f"for the fork, or remove the existing entry first "
            f"with ``mcs profile remove`` (for a main-kind "
            f"profile) or ``mcs profile fork-remove`` (for a "
            f"fork-kind alias).",
            remediation=(
                "fork names follow the same regex as profile "
                "names but allow ``@`` and ``:`` as delimiters "
                "— canonical conventions are "
                f"``{parent.name}@<short-sha>`` for "
                "anchor-named forks and "
                f"``{parent.name}:<label>`` for human-named "
                "forks."
            ),
        )

    fork = Profile(
        name=fork_name,
        compute_project=parent.compute_project,
        endpoint=parent.endpoint,
        auth=parent.auth,
        sources=parent.sources,
        cost_thresholds=parent.cost_thresholds,
        tags=parent.tags,
        package_path=str(worktree_path),
        kind="fork",
        parent_profile=parent.name,
        git_sha=full_sha,
    )
    upsert_profile(fork)
    return fork


def unregister_fork(fork_name: str) -> None:
    """Inverse of ``register_fork``. Removes the yaml entry but
    does *not* delete any on-disk files — the caller's
    ``GitRepo.worktree_remove`` from ``cmd_profile_fork_remove``
    is what reclaims the worktree directory and the parent's
    ``.git/worktrees/<short>/`` admin dir.

    Idempotent: removing a fork name that's already gone is a
    no-op. Raises ``McsError`` if the named profile exists but
    isn't a fork (refuse to remove via the fork-removal path).
    """
    try:
        existing = get_profile(fork_name)
    except ProfileNotFoundError:
        return

    if existing.kind != "fork":
        raise McsError(
            f"profile {fork_name!r} is not a fork (kind="
            f"{existing.kind!r}); refusing to remove via the "
            f"fork-removal path. Use ``mcs profile remove "
            f"{fork_name}`` for main-kind profiles.",
            remediation=(
                "the fork-removal path is reserved for the "
                "``kind=fork`` aliases — main-kind profiles "
                "have a per-profile git repository whose "
                "removal should go through the standard "
                "``mcs profile remove`` flow that the existing "
                "yaml-side cleanup and the data-directory "
                "rmtree handle."
            ),
        )

    remove_profile_yaml_entry(fork_name, delete_data_dir=False)


def parent_repo(fork: Profile) -> GitRepo:
    """Return a ``GitRepo`` rooted at the fork's parent's data
    directory. The fork itself shares the parent's
    ``.git/object-database/`` so the wrapper rooted at the
    parent reads the canonical history.

    Raises ``ValueError`` if called on a non-fork profile;
    raises ``ProfileNotFoundError`` if the parent's yaml entry
    has been removed (an orphaned-fork-after-parent-rm case
    which the spec's ``forks_healthy`` doctor check flags).
    """
    if fork.kind != "fork":
        raise ValueError(
            f"parent_repo() requires a kind=fork profile; got {fork.kind!r} (profile {fork.name!r})"
        )
    if fork.parent_profile is None:
        raise ValueError(
            f"fork {fork.name!r} has kind=fork but no parent_profile field set — yaml is malformed"
        )
    parent = get_profile(fork.parent_profile)
    return GitRepo(profile_data_dir(parent))
