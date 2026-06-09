# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Fork verbs: ``fork`` (create), ``fork-list`` (inspect),
``fork-remove`` (destroy). T14 lands ``cmd_profile_fork`` here;
T15 and T16 add the other two verbs to the same module.

A fork is a read-only alias of a specific commit of the parent
profile's history. The fork's data directory is a detached-HEAD
``git worktree`` of the parent's repo, lazy-materialized on
creation: the textual ``package.sql`` at the anchor is restored
into a fresh ``package.db`` inside the worktree so the fork is
queryable via ``mcs sql execute --profile <fork-name>`` without
the user having to run the matching ``sqlite3 .read`` themselves.

Forks share the parent's git object database, so the disk
overhead of a fork is the working-tree's checked-out content
(the markdown / json files at the anchor) plus the freshly-
materialized ``package.db``. There's no per-fork copy of the
git history's pack files.

Write verbs (``mcs build``, the proposal workflow, ``mcs memory
verify``, ``mcs udf``) against a fork raise ``ProfileReadOnly``
at the verb's entry via the ``reject_if_fork`` guard wired in T9.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import (
    data_root,
    profile_data_dir,
)
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.errors import ProfileNotFoundError
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
from maxcompute_semantic.commands.profile_history import _resolve_ref
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning import (
    GitNotAvailable,
    GitRepo,
    PackageSqlCorrupt,
    is_versioning_disabled,
    register_fork,
    restore_sql_to_db,
    unregister_fork,
)
from maxcompute_semantic.versioning import (
    parent_repo as _get_parent_repo,
)


def _stderr(msg: str) -> None:
    """Stderr banner helper — matches the pattern used in
    profile_history.py (the Renderer doesn't expose warn/info)."""
    click.echo(msg, err=True)


@click.command("fork")
@click.argument("fork_name")
@click.option(
    "--from",
    "from_ref",
    required=True,
    help=(
        "the commit ref the fork's worktree is anchored at. "
        "Same forms as ``mcs profile reset --to <ref>``: short "
        "SHA, full 40-hex SHA, ``HEAD`` / ``HEAD~N``, or one of "
        "the ``last-build`` / ``last-refresh`` keywords."
    ),
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help=(
        "the parent profile whose history the fork is taken "
        "from. Defaults to the resolver-chain pick. Must be a "
        "main-kind profile (forks-of-forks aren't supported — "
        "the workaround is to ``fork`` from the same anchor sha "
        "against the parent profile twice)."
    ),
)
@click.option(
    "--worktree-path",
    "explicit_worktree_path",
    default=None,
    type=click.Path(),
    help=(
        "override the default worktree-directory location at "
        "``<XDG_DATA_HOME>/maxcompute-semantic/data/<fork-name>/``. "
        "Don't override unless you have a specific reason — the "
        "default matches the standard ``profile_data_dir(fork)`` "
        "resolution so ``mcs sql execute --profile <fork-name>`` "
        "auto-resolves the package_path."
    ),
)
@click.pass_context
def cmd_profile_fork(
    ctx: click.Context,
    fork_name: str,
    from_ref: str,
    profile_name: str | None,
    explicit_worktree_path: str | None,
) -> None:
    """Create a read-only fork of the parent profile anchored at a
    specific commit. The fork is added to ``profiles.yaml`` as a
    ``kind=fork`` entry and the matching git worktree is created
    via ``git worktree add --detach``. The worktree's
    ``package.sql`` (when present) is restored into a fresh
    ``package.db`` so the fork is queryable immediately.
    """
    obj = ctx.obj or {}
    renderer = Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))

    if is_versioning_disabled():
        renderer.error(
            McsError(
                "mcs profile fork requires the per-profile git "
                "history that ``MCS_NO_VERSIONING=1`` opts out of.",
                remediation="unset ``MCS_NO_VERSIONING`` and re-run.",
            )
        )
        ctx.exit(2)
        return

    # Validate the fork name early via the schema's name regex
    # so the user sees the error before any side effect.
    from maxcompute_semantic.auth.schema import _NAME_RE

    if not _NAME_RE.fullmatch(fork_name):
        renderer.error(
            McsError(
                f"fork name {fork_name!r} doesn't match the profile "
                f"name regex ``^[a-zA-Z0-9][a-zA-Z0-9_\\-@:.]{{2,63}}$``.",
                remediation=(
                    "names start with an alphanumeric character and "
                    "contain only alphanumerics plus ``_-@:.`` after "
                    "that. Canonical fork-name conventions are "
                    "``<parent>@<short-sha>`` (e.g. ``acme@9abcdef``) "
                    "and ``<parent>:<label>`` (e.g. ``acme:baseline``)."
                ),
            )
        )
        ctx.exit(2)
        return

    try:
        pctx = ProfileContext.resolve(profile_name=profile_name, renderer=renderer)
        parent = pctx.profile
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    if parent.kind == "fork":
        renderer.error(
            McsError(
                f"the parent of a fork must be a main-kind profile; "
                f"the resolved profile {parent.name!r} is itself a "
                f"fork of {parent.parent_profile!r} at anchor "
                f"{(parent.git_sha or '')[:12]}.",
                remediation=(
                    f"to fork at the same anchor as {parent.name!r}, "
                    f"use ``--from {parent.git_sha} --profile "
                    f"{parent.parent_profile}`` to point at the "
                    f"*parent of the existing fork* rather than the "
                    f"fork itself."
                ),
            )
        )
        ctx.exit(2)
        return

    parent_dir = profile_data_dir(parent)
    parent_git = GitRepo(parent_dir)
    if not parent_git.exists():
        renderer.error(
            McsError(
                f"the parent profile {parent.name!r}'s data "
                f"directory at {parent_dir} is not a git repository "
                f"— there's no history to fork from.",
                remediation=(
                    f"run ``mcs profile enable-versioning --profile {parent.name}`` first."
                ),
            )
        )
        ctx.exit(2)
        return

    anchor_sha = _resolve_ref(parent_git, from_ref)
    if anchor_sha is None:
        renderer.error(
            McsError(
                f"no commit in the history of profile {parent.name!r} matches the ref {from_ref!r}",
                remediation=(
                    "run ``mcs profile log --all -n 0 --profile "
                    f"{parent.name}`` to inspect the full "
                    "unfiltered history."
                ),
            )
        )
        sys.exit(1)

    if explicit_worktree_path is not None:
        worktree_path = Path(explicit_worktree_path)
    else:
        worktree_path = data_root() / fork_name

    if worktree_path.exists():
        renderer.error(
            McsError(
                f"the worktree path {worktree_path} already exists; "
                f"``git worktree add`` refuses to overwrite it.",
                remediation=(
                    "if the existing directory is a stale fork from "
                    "a prior session, ``mcs profile fork-list`` "
                    "flags it as a GHOST and the housekeeping path "
                    "is ``mcs profile fork-remove <existing-fork-"
                    "name>`` followed by a fresh ``mcs profile fork``."
                ),
            )
        )
        ctx.exit(2)
        return

    parent_git.worktree_add(worktree_path, anchor_sha, detach=True)

    worktree_sql = worktree_path / "package.sql"
    worktree_db = worktree_path / "package.db"
    if worktree_sql.exists():
        try:
            restore_sql_to_db(worktree_sql, worktree_db)
            # Open + close the freshly-materialized DB so the FTS5
            # and vec0 virtual tables get re-created as empty
            # shells (they were intentionally not in the dump).
            from maxcompute_semantic.build.storage import PackageDB
            from maxcompute_semantic.commands.memory import run_reindex

            pdb = PackageDB(worktree_db)
            pdb.close()
            run_reindex(worktree_db, vectors=False)
        except PackageSqlCorrupt as e:
            _stderr(
                f"warning: anchor commit's ``package.sql`` is "
                f"unparseable: {e}. The worktree has been created "
                f"on disk but ``package.db`` materialization "
                f"failed; the fork's read path will surface the "
                f"missing-DB error until you re-run with a "
                f"different anchor or remove the fork."
            )
    else:
        _stderr(
            f"warning: anchor commit {anchor_sha[:12]} has no "
            f"``package.sql`` in its tree (it predates the first "
            f"``mcs build`` of the parent profile). The fork's "
            f"read-side commands will see an empty data directory "
            f"until a build happens against the parent at a later "
            f"commit and you re-fork at that commit."
        )

    try:
        fork = register_fork(parent, fork_name, anchor_sha, worktree_path)
    except McsError as e:
        # Yaml-side register failed (e.g. name collision raced
        # with the worktree-existence check). The worktree was
        # successfully created above; reuse it via
        # ``mcs profile fork-remove`` once that verb lands, or
        # ``git -C <parent> worktree remove <path>`` manually.
        renderer.error(e)
        sys.exit(e.exit_code)

    short = anchor_sha[:12]
    renderer.success(
        {
            "fork": fork.name,
            "parent": parent.name,
            "anchor_sha": anchor_sha,
            "anchor_short": short,
            "worktree_path": str(worktree_path),
        }
    )
    _stderr(
        f"created fork {fork.name!r} of {parent.name!r} anchored "
        f"at {short}. Worktree directory: {worktree_path}.\n"
        f"To compare side-by-side, run the same query against "
        f"both profiles, e.g. ``mcs sql execute --profile "
        f"{parent.name} 'SELECT ...'`` and ``mcs sql execute "
        f"--profile {fork.name} 'SELECT ...'``.\n"
        f"Write verbs against the fork raise ``ProfileReadOnly``; "
        f"to adopt the fork's anchor as the parent's HEAD use "
        f"``mcs profile reset --to {short} --profile "
        f"{parent.name}``. To clean up the fork, use "
        f"``mcs profile fork-remove {fork.name}``."
    )


@click.command("fork-list")
@click.option(
    "--profile",
    "parent_name",
    default=None,
    help=(
        "restrict the listing to forks whose parent is the named "
        "main-kind profile. Omitting the flag lists every fork "
        "across every parent."
    ),
)
@click.option(
    "--no-self-heal",
    "skip_heal",
    is_flag=True,
    default=False,
    help=(
        "report ghost forks without sweeping their stale "
        "worktree-admin entries. Useful for the doctor-style "
        "read-only audit case where the operator wants to see "
        "the inconsistency without the side effect of fixing it."
    ),
)
@click.pass_context
def cmd_profile_fork_list(ctx: click.Context, parent_name: str | None, skip_heal: bool) -> None:
    """Enumerate registered forks with their health state.

    Three states per row:

    * ``healthy`` — the fork's anchor SHA is reachable from the
      parent's current HEAD (``git merge-base --is-ancestor``).
    * ``ORPHAN`` — the anchor isn't an ancestor of the parent's
      HEAD anymore. The fork's data on disk is still the anchor
      commit's tree and the fork is still queryable; this label
      flags the anchor-on-the-shared-timeline relationship as
      broken. Two sub-cases are also flagged ORPHAN: the parent
      profile is gone from ``profiles.yaml`` ("no parent") and
      the parent's data directory isn't git-initialized.
    * ``GHOST`` — the yaml entry exists but the worktree
      directory has been hand-deleted. The default-on self-heal
      sweeps the parent's ``.git/worktrees/<short>/`` admin
      entry (``git worktree prune``) and drops the yaml row
      (``unregister_fork``). ``--no-self-heal`` reports the row
      without the side effect.
    """
    obj = ctx.obj or {}
    renderer = Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))

    all_profiles = load_all_profiles()
    fork_entries = [p for p in all_profiles.values() if p.kind == "fork"]
    if parent_name is not None:
        fork_entries = [f for f in fork_entries if f.parent_profile == parent_name]

    pruned_parents: set[str] = set()
    rows: list[tuple[str, str, str, str, str]] = []
    counts = {"healthy": 0, "orphan": 0, "ghost": 0, "swept": 0, "unknown": 0}

    for fork in sorted(fork_entries, key=lambda f: (f.parent_profile or "", f.name)):
        parent_label = fork.parent_profile or ""
        anchor_short = (fork.git_sha or "")[:12]
        try:
            parent = get_profile(parent_label)
        except ProfileNotFoundError:
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "ORPHAN",
                    "parent profile is gone from profiles.yaml",
                )
            )
            counts["orphan"] += 1
            continue

        parent_dir = profile_data_dir(parent)
        parent_repo = GitRepo(parent_dir)
        if not parent_repo.exists():
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "ORPHAN",
                    "parent profile's data directory is not git-initialized",
                )
            )
            counts["orphan"] += 1
            continue

        wt_path = Path(fork.package_path) if fork.package_path is not None else None
        if wt_path is None or not wt_path.is_dir():
            if skip_heal:
                rows.append(
                    (
                        fork.name,
                        parent_label,
                        anchor_short,
                        "GHOST",
                        "worktree directory missing on disk",
                    )
                )
                counts["ghost"] += 1
                continue
            # Default-on self-heal. Run ``git worktree prune``
            # once per parent profile so a parent with N ghost
            # forks doesn't trigger N prune subprocesses.
            if parent.name not in pruned_parents:
                try:
                    parent_repo.worktree_prune()
                except GitNotAvailable:
                    _stderr(
                        "warning: git binary not available; "
                        "ghost-fork self-heal is skipped. Install "
                        "git and re-run, or pass ``--no-self-heal`` "
                        "to suppress the self-heal attempt for "
                        "read-only audits."
                    )
                    rows.append(
                        (
                            fork.name,
                            parent_label,
                            anchor_short,
                            "GHOST",
                            "worktree dir is gone; self-heal skipped (no git)",
                        )
                    )
                    counts["ghost"] += 1
                    continue
                pruned_parents.add(parent.name)
            try:
                unregister_fork(fork.name)
            except Exception as exc:
                rows.append(
                    (
                        fork.name,
                        parent_label,
                        anchor_short,
                        "GHOST",
                        f"self-heal of the yaml entry failed: {exc}",
                    )
                )
                counts["ghost"] += 1
                continue
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "GHOST",
                    "worktree dir missing; self-healed (yaml entry removed)",
                )
            )
            counts["swept"] += 1
            continue

        # Worktree exists. Check ancestor relationship.
        try:
            ok = parent_repo.merge_base_is_ancestor(fork.git_sha or "", "HEAD")
        except GitNotAvailable:
            _stderr(
                "warning: git binary not available; "
                "orphan-vs-healthy determination skipped. Run "
                "``mcs doctor`` for the underlying status."
            )
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "?",
                    "git not available; state unknown",
                )
            )
            counts["unknown"] += 1
            continue

        if ok:
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "healthy",
                    f"anchor reachable from {parent.name}'s HEAD",
                )
            )
            counts["healthy"] += 1
        else:
            rows.append(
                (
                    fork.name,
                    parent_label,
                    anchor_short,
                    "ORPHAN",
                    f"anchor SHA not in {parent.name}'s HEAD-walk-back",
                )
            )
            counts["orphan"] += 1

    if renderer.is_envelope:
        payload = [
            {
                "name": r[0],
                "parent": r[1],
                "anchor": r[2],
                "state": r[3],
                "detail": r[4],
            }
            for r in rows
        ]
        renderer.success(
            {
                "forks": payload,
                "totals": {
                    "total": len(rows),
                    "healthy": counts["healthy"],
                    "orphan": counts["orphan"],
                    "ghost": counts["ghost"],
                    "self_healed": counts["swept"],
                },
            }
        )
        return

    if not rows:
        if parent_name is None:
            _stderr("no forks registered.")
        else:
            _stderr(f"no forks registered for parent profile {parent_name!r}.")
        return

    headers = ("NAME", "PARENT", "ANCHOR", "STATE", "DETAIL")
    col_widths = [max(len(r[i]) for r in rows) for i in range(5)]
    for i in range(5):
        col_widths[i] = max(col_widths[i], len(headers[i]))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    click.echo(fmt.format(*headers))
    click.echo(fmt.format(*("-" * w for w in col_widths)))
    for r in rows:
        click.echo(fmt.format(*r))

    summary = (
        f"{len(rows)} fork(s): "
        f"{counts['healthy']} healthy, "
        f"{counts['orphan']} orphan, "
        f"{counts['ghost']} ghost"
        + (
            f", {counts['swept']} self-healed and removed from profiles.yaml"
            if counts["swept"]
            else ""
        )
        + "."
    )
    click.echo("")
    _stderr(summary)


@click.command("fork-remove")
@click.argument("fork_name")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help=(
        "pass through to ``git worktree remove --force``. "
        "Required when the fork's worktree directory has "
        "uncommitted modifications relative to the anchor "
        "commit (which git interprets as a dirty worktree "
        "and refuses to drop without explicit consent)."
    ),
)
@click.option(
    "--yes",
    "-y",
    "skip_confirmation",
    is_flag=True,
    default=False,
    help=(
        "skip the ``[y/N]`` confirmation prompt. The default "
        "``no`` answer aborts and exits 0 without state change."
    ),
)
@click.pass_context
def cmd_profile_fork_remove(
    ctx: click.Context,
    fork_name: str,
    force: bool,
    skip_confirmation: bool,
) -> None:
    """Remove a fork's yaml entry and its on-disk worktree.

    The two-step ordering is git-side first (because git's
    ``.git/worktrees/<short>/`` admin entry's lifetime is tied
    to the target directory; removing the dir without telling
    git leaves a stale admin entry), then yaml-side.

    Ghost-fork (worktree already deleted), orphan-fork (anchor
    not in parent's HEAD ancestry), and double-orphan (parent
    yaml gone too) all converge on the same exit: the yaml row
    is dropped and whatever git-side cleanup is possible runs.
    Refuses to remove a main-kind profile (delegating to the
    ``unregister_fork`` helper's kind-check); the user gets the
    "use ``mcs profile remove``" remediation.
    """
    obj = ctx.obj or {}
    renderer = Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))

    try:
        fork = get_profile(fork_name)
    except ProfileNotFoundError as exc:
        renderer.error(
            McsError(
                str(exc),
                remediation=(
                    "run ``mcs profile list`` to see the registered "
                    "profiles; fork names typically follow the "
                    "``<parent>@<short-sha>`` or ``<parent>:<label>`` "
                    "convention."
                ),
            )
        )
        sys.exit(2)

    if fork.kind != "fork":
        renderer.error(
            McsError(
                f"profile {fork_name!r} is not a fork (kind="
                f"{fork.kind!r}); refusing to remove via the "
                f"fork-removal path.",
                remediation=(
                    f"main-kind profiles go through ``mcs profile "
                    f"remove {fork_name}`` (which sweeps the per-"
                    f"profile data directory). The fork-removal "
                    f"path is reserved for the ``kind=fork`` aliases."
                ),
            )
        )
        sys.exit(2)

    parent_label = fork.parent_profile or "<unknown-parent>"
    anchor_short = (fork.git_sha or "")[:12]
    worktree_path = Path(fork.package_path) if fork.package_path is not None else None
    wt_label = str(worktree_path) if worktree_path is not None else "<no-path>"

    _stderr(
        f"about to remove fork {fork_name!r} (parent="
        f"{parent_label}, anchor={anchor_short}, "
        f"worktree={wt_label}). The parent profile and its "
        f"history are unaffected; only this alias is gone."
    )
    if not skip_confirmation and not click.confirm("proceed with the removal?", default=False):
        _stderr("aborted; no state change.")
        return

    # Resolve the parent's GitRepo. If the parent's yaml is
    # missing too (double-orphan), fall back to a yaml-only
    # remove since the wrapper has no anchor.
    try:
        parent_git = _get_parent_repo(fork)
    except ProfileNotFoundError:
        _stderr(
            f"warning: the parent profile {parent_label!r} is "
            f"gone from profiles.yaml — only the yaml-side "
            f"cleanup of the fork entry can run. The on-disk "
            f"worktree directory at {wt_label} is orphaned; "
            f"remove it by hand if you want the disk space back "
            f"(``rm -rf <worktree>``)."
        )
        try:
            unregister_fork(fork_name)
        except McsError as e:
            renderer.error(e)
            sys.exit(e.exit_code)
        renderer.success(
            {
                "fork": fork_name,
                "parent": parent_label,
                "anchor_short": anchor_short,
                "state": "double-orphan",
                "yaml_removed": True,
                "worktree_removed": False,
            }
        )
        _stderr(f"fork {fork_name!r}'s yaml entry removed. The worktree directory was not touched.")
        return

    # Git-side remove. Ghost case (worktree dir already gone)
    # takes the prune path; healthy/orphan case takes the
    # ``worktree remove`` path.
    if worktree_path is None or not worktree_path.exists():
        _stderr(
            f"the worktree directory at {wt_label} doesn't exist "
            f"on disk (ghost-fork shape). Running ``git worktree "
            f"prune`` to clean the parent's admin-side entry."
        )
        try:
            parent_git.worktree_prune()
        except GitNotAvailable as e:
            _stderr(str(e))
            _stderr(
                "the git-side cleanup can't run without the git "
                "binary, so the parent's ``.git/worktrees/<short>/`` "
                "admin directory for this fork is left in place. A "
                "future ``mcs profile fork-list`` invocation on a "
                "machine with git installed will run the matching "
                "prune."
            )
    else:
        try:
            parent_git.worktree_remove(worktree_path, force=force)
        except GitNotAvailable as e:
            renderer.error(e)
            sys.exit(e.exit_code)
        except McsError as e:
            renderer.error(e)
            _stderr(
                "if the worktree has uncommitted modifications "
                "relative to the anchor commit, pass ``--force`` "
                "to drop it anyway. The yaml entry has not been "
                "removed; re-run with ``--force`` once you've "
                "confirmed the modifications are expendable."
            )
            sys.exit(e.exit_code)

    # Yaml-side unregister.
    try:
        unregister_fork(fork_name)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    renderer.success(
        {
            "fork": fork_name,
            "parent": parent_label,
            "anchor_short": anchor_short,
            "state": "removed",
            "yaml_removed": True,
            "worktree_removed": worktree_path is not None and not worktree_path.exists(),
        }
    )
    _stderr(
        f"fork {fork_name!r} removed. Parent profile "
        f"{parent_label!r}'s history is unchanged. To re-create "
        f"the fork at a different anchor, use ``mcs profile fork "
        f"<new-name> --from <sha> --profile {parent_label}``."
    )
