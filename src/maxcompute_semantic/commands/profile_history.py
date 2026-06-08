# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile log`` / ``log-show`` / ``diff`` / ``reset`` — the
history-inspection read verbs and the rollback verb of the per-profile
git layer.

Each verb is a thin click wrapper over the ``GitRepo`` methods from
``versioning.git_repo`` (T2). The wrapper has all the subprocess
plumbing and porcelain parsing; these verbs do CLI argument shape,
keyword resolution (``last-build`` / ``last-refresh``), and
render-time formatting only.

Forks (``profile.kind == "fork"``) transparently redirect to the
parent's git repo — the fork is a detached worktree sharing the
parent's ``.git/``, so the parent is the history-of-record. An
informational stderr banner names the parent and the anchor SHA so
the redirect is visible. ``reset`` rejects fork targets outright
(the fork's anchor SHA is the immutable identity of the alias).

The ``MCS_NO_VERSIONING`` env knob doesn't gate reads — the env
disables the auto-commit hook on writes, but an already-versioned
profile reads back fine. The renderer emits a one-line warning so
the user notices the asymmetry. ``reset`` is the exception: it
hard-errors when the env is set, since the rollback story *is* the
git layer the env opts out of.

Mounted on the existing ``profile`` click group by
``commands/profile.py``'s tail import block; see the comment there
for the naming choice (``log-show`` instead of ``show`` to avoid
the collision with the existing ``mcs profile show <name>``
config-dump verb).
"""

from __future__ import annotations

import shutil
import sys

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_lock_path,
    profile_package_sql_path,
)
from maxcompute_semantic.auth.context import resolve_profile_for_project
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning import (
    GitRepo,
    PackageSqlCorrupt,
    WriteLock,
    commit_if_uncommitted_on_entry,
    is_versioning_disabled,
    restore_sql_to_db,
)

# Anchored on the start of the commit subject so partial matches
# don't slip through. POSIX-extended regex (``git log --extended-
# regexp --grep``).
_DEFAULT_NOISE_FILTER = r"^memory:"

# Special keywords accepted by log-show / diff in place of a SHA.
# Each resolves to the most-recent commit whose subject starts with
# the matching prefix (via ``GitRepo.find_commit_with_prefix``).
_KEYWORD_PREFIXES: dict[str, str] = {
    "last-build": "build",
    "last-refresh": "refresh",
}


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))


def _stderr(msg: str) -> None:
    """Stderr banner helper — Renderer has no warn/info method, so
    the established codebase pattern is ``click.echo(..., err=True)``
    (see e.g. ``commands/build.py:209``)."""
    click.echo(msg, err=True)


def _repo_root_for(profile: Profile) -> tuple[GitRepo, Profile | None]:
    """Return the ``GitRepo`` rooted at the profile's history-of-
    record dir, plus the parent profile when the input is a fork.

    For ``kind="main"`` the repo root is the profile's own data dir.
    For ``kind="fork"`` the repo root is the parent's data dir
    (forks are detached worktrees sharing the parent's ``.git/``);
    the parent is also returned so the caller can render the
    fork-redirect banner.
    """
    from maxcompute_semantic.auth.profile_store import get as get_profile

    pdir = profile_data_dir(profile)
    if profile.kind == "fork" and profile.parent_profile:
        parent = get_profile(profile.parent_profile)
        return GitRepo(profile_data_dir(parent)), parent
    return GitRepo(pdir), None


def _emit_fork_banner(profile: Profile, parent: Profile) -> None:
    """One-line stderr banner for a fork-redirect — names the
    parent and the fork's anchor SHA so the user sees the
    redirect happen."""
    anchor = (profile.git_sha or "")[:12]
    _stderr(
        f"profile {profile.name!r} is a fork of {parent.name!r} "
        f"anchored at {anchor}; showing the parent's history."
    )


def _resolve_ref(repo: GitRepo, ref: str) -> str | None:
    """Resolve a CLI-supplied ref to a full SHA.

    Special-cases the ``last-*`` keywords (which dispatch to
    ``find_commit_with_prefix``) and otherwise delegates to
    ``rev_parse`` for short-SHA / full-SHA / ``HEAD`` / ``HEAD~N``.

    Returns ``None`` when nothing matches.
    """
    prefix = _KEYWORD_PREFIXES.get(ref)
    if prefix is not None:
        return repo.find_commit_with_prefix(prefix)
    try:
        return repo.rev_parse(ref)
    except McsError:
        return None


# ── mcs profile log ─────────────────────────────────────────────────────────


@click.command("log")
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="profile to inspect; defaults to the resolver-chain pick "
    "(--profile / MCS_PROFILE / cwd-link / env-vars).",
)
@click.option(
    "-n",
    "--limit",
    "limit",
    default=20,
    type=int,
    show_default=True,
    help="cap the number of returned commits; 0 (or negative) means unlimited.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="don't filter out ``memory:`` prefix noise commits.",
)
@click.option(
    "--grep",
    "grep_regex",
    default=None,
    help="filter the log to subjects matching the given POSIX-extended "
    "regex (same syntax as ``git log --grep --extended-regexp``). An "
    "explicit --grep supersedes the default ^memory: filter since git "
    "log can't AND two --grep regexes.",
)
@click.pass_context
def cmd_profile_log(
    ctx: click.Context,
    profile_name: str | None,
    limit: int,
    show_all: bool,
    grep_regex: str | None,
) -> None:
    """Show the per-profile commit history.

    The default invocation hides ``memory:`` prefix noise commits and
    caps the output at 20 commits. ``--all`` removes the noise filter;
    ``-n 0`` (or negative) removes the cap.

    Plain output is a ``<short-sha>  <subject>`` line per commit.
    Structured output (``-f json`` / ``-f yaml``) uses the standard
    success envelope with ``data.commits[]`` rows carrying
    ``short_sha`` / ``full_sha`` / ``message`` — matching the
    ``CommitInfo`` dataclass.
    """
    renderer = _renderer(ctx)

    if is_versioning_disabled():
        _stderr(
            "warning: MCS_NO_VERSIONING is set — the env knob disables "
            "the auto-commit hook for writes, but reads against an "
            "existing git history are unaffected."
        )

    try:
        profile = resolve_profile_for_project(None, profile_name=profile_name)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    repo, parent = _repo_root_for(profile)
    if parent is not None:
        _emit_fork_banner(profile, parent)

    if not repo.exists():
        _stderr(
            f"profile {profile.name!r} is not versioned — its data "
            f"directory {repo.root} has no .git/ subdirectory. Run "
            f"`mcs profile enable-versioning --profile {profile.name}` "
            f"to upgrade it."
        )
        ctx.exit(0)

    if show_all:
        effective_grep = grep_regex
        invert = False
    elif grep_regex is None:
        effective_grep = _DEFAULT_NOISE_FILTER
        invert = True
    else:
        # Both --grep and the implicit ^memory: filter are active;
        # git log can't AND two --grep options (multiple --grep are
        # OR'd). Drop the implicit filter and warn the user.
        _stderr(
            "note: the default ^memory: noise filter is bypassed "
            "because an explicit --grep is set; pass --all to make "
            "it explicit, or drop --grep to restore the default."
        )
        effective_grep = grep_regex
        invert = False

    effective_limit = None if limit <= 0 else limit
    try:
        rows = repo.log(limit=effective_limit, grep_regex=effective_grep, invert_grep=invert)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    if not rows:
        if show_all:
            _stderr("no commits in this profile's history.")
        else:
            _stderr(
                "no non-memory commits in the filtered window. Pass "
                "--all to include the memory: prefix family."
            )
        ctx.exit(0)

    if renderer.is_envelope:
        payload = [
            {"short_sha": r.short_sha, "full_sha": r.full_sha, "message": r.message} for r in rows
        ]
        renderer.success({"commits": payload})
        return

    for r in rows:
        click.echo(f"{r.short_sha}  {r.message}")


# ── mcs profile log-show <sha> ──────────────────────────────────────────────


@click.command("log-show")
@click.argument("sha")
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="the target profile (defaults to the resolver-chain pick).",
)
@click.pass_context
def cmd_profile_show_sha(ctx: click.Context, sha: str, profile_name: str | None) -> None:
    """Show a single commit's metadata + diff over the tracked-file globs.

    ``sha`` may be a short SHA, a full 40-hex SHA, ``HEAD`` / ``HEAD~N``,
    or one of the keywords ``last-build`` / ``last-refresh``
    (most-recent commit whose subject starts with the matching prefix).

    The output is the raw text of ``git show <sha>`` filtered to the
    committed-file globs (``*.md`` ``*.json`` ``package.sql``
    ``.gitignore``). Structured output wraps the diff body in the
    standard success envelope as
    ``data.{short_sha, full_sha, message, diff_text}``.

    Mounted as ``mcs profile log-show`` (not ``show``) because the
    existing ``mcs profile show <name>`` config-dump verb already
    owns the bare ``show`` name.
    """
    renderer = _renderer(ctx)

    try:
        profile = resolve_profile_for_project(None, profile_name=profile_name)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    repo, parent = _repo_root_for(profile)
    if parent is not None:
        _emit_fork_banner(profile, parent)

    if not repo.exists():
        _stderr(
            f"profile {profile.name!r} is not versioned. Run "
            f"`mcs profile enable-versioning --profile {profile.name}` "
            f"first; then this verb can target the inaugural commit."
        )
        ctx.exit(0)

    full = _resolve_ref(repo, sha)
    if full is None:
        renderer.error(
            McsError(
                f"no commit in the history of profile {profile.name!r} matches the ref {sha!r}",
                remediation="run `mcs profile log --all -n 0` to inspect "
                "the full unfiltered history.",
            )
        )
        sys.exit(1)

    try:
        rendered = repo.show(full)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    if renderer.is_envelope:
        head_part, sep, diff_part = rendered.partition("diff --git")
        diff_text = (sep + diff_part) if sep else ""
        # Parse the subject out of ``git show``'s metadata header.
        # The layout is::
        #
        #     commit <full_sha>
        #     [Merge: ...]
        #     Author: ...
        #     Date:   ...
        #
        #         <subject>
        #
        # The subject is the first 4-space-indented non-blank line
        # after the blank that follows ``Date:``. Short SHA is the
        # 7-hex prefix of the full SHA (matching ``git log
        # --format=%h``'s default abbrev length).
        message = ""
        lines = head_part.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Date:"):
                for follow in lines[i + 1 :]:
                    if follow.startswith("    "):
                        message = follow[4:].rstrip()
                        break
                break
        payload = {
            "short_sha": full[:7],
            "full_sha": full,
            "message": message,
            "diff_text": diff_text,
        }
        renderer.success(payload)
        return

    click.echo(rendered, nl=False)


# ── mcs profile diff <ref_a> <ref_b> ────────────────────────────────────────


@click.command("diff")
@click.argument("ref_a")
@click.argument("ref_b")
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="the target profile (defaults to the resolver-chain pick).",
)
@click.pass_context
def cmd_profile_diff(
    ctx: click.Context,
    ref_a: str,
    ref_b: str,
    profile_name: str | None,
) -> None:
    """Show the unified diff between two commits' trees.

    Both ref arguments accept the same forms as ``mcs profile log-show
    <ref>``: short SHA, full SHA, ``HEAD`` / ``HEAD~N``, or one of
    the ``last-*`` keywords. The comparison is ``ref_a..ref_b`` —
    "what changed from a to b". For the reverse direction, swap the
    argument order.

    The diff is filtered to the four committed-file globs
    (``*.md``, ``*.json``, ``package.sql``, ``.gitignore``).
    Structured output wraps the diff body in the standard success
    envelope as ``data.{ref_a, ref_b, diff_text}``.
    """
    renderer = _renderer(ctx)

    try:
        profile = resolve_profile_for_project(None, profile_name=profile_name)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    repo, parent = _repo_root_for(profile)
    if parent is not None:
        _emit_fork_banner(profile, parent)

    if not repo.exists():
        _stderr(
            f"profile {profile.name!r} is not versioned. Run "
            f"`mcs profile enable-versioning --profile {profile.name}` "
            f"first."
        )
        ctx.exit(0)

    a = _resolve_ref(repo, ref_a)
    if a is None:
        renderer.error(
            McsError(
                f"no commit in the profile's history matches {ref_a!r}",
                remediation="run `mcs profile log --all -n 0` to inspect "
                "the full unfiltered history.",
            )
        )
        sys.exit(1)
    b = _resolve_ref(repo, ref_b)
    if b is None:
        renderer.error(
            McsError(
                f"no commit in the profile's history matches {ref_b!r}",
                remediation="run `mcs profile log --all -n 0` to inspect "
                "the full unfiltered history.",
            )
        )
        sys.exit(1)

    try:
        diff_text = repo.diff(a, b)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    if renderer.is_envelope:
        renderer.success({"ref_a": a[:12], "ref_b": b[:12], "diff_text": diff_text})
        return

    if not diff_text.strip():
        _stderr(
            f"no tracked-file changes between {a[:7]} and {b[:7]} "
            f"(the two commits' trees are identical for the four "
            f"committed globs *.md / *.json / package.sql / .gitignore)."
        )
        return

    click.echo(diff_text, nl=False)


# ── mcs profile reset --to <sha> ────────────────────────────────────────────


def _discarded_commits(repo: GitRepo, target_sha: str, head_sha: str) -> list[tuple[str, str]]:
    """Return ``(short_sha, subject)`` for each commit on the current
    HEAD's ancestry line that the reset will drop — the range
    ``<target>..<head>`` in git's "commits in head but not in
    target" semantics, newest first.

    Returns ``[]`` when the target is on a side branch (no commits in
    the linear range); the caller surfaces that case via the
    ``merge_base_is_ancestor`` warn-banner branch.
    """
    range_spec = f"{target_sha}..{head_sha}"
    raw = repo._run("log", "--format=%h%x09%s", range_spec, check=True).strip()
    if not raw:
        return []
    rows: list[tuple[str, str]] = []
    for line in raw.splitlines():
        short, _, subject = line.partition("\t")
        rows.append((short, subject))
    return rows


@click.command("reset")
@click.option(
    "--to",
    "target_ref",
    required=True,
    help="commit ref to reset HEAD to. Same forms as ``mcs profile "
    "log-show <ref>``: short / full SHA, ``HEAD`` / ``HEAD~N``, or "
    "one of the keywords ``last-build`` / ``last-refresh``.",
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="target profile; must be a main-kind profile. Forks have a "
    "fixed anchor SHA and can't be reset (see ``mcs profile "
    "fork-remove`` to drop a fork).",
)
@click.option(
    "--yes",
    "-y",
    "skip_confirmation",
    is_flag=True,
    default=False,
    help="skip the [y/N] confirmation prompt; the default no answer "
    "aborts the reset with exit 0 and no state change.",
)
@click.pass_context
def cmd_profile_reset(
    ctx: click.Context,
    target_ref: str,
    profile_name: str | None,
    skip_confirmation: bool,
) -> None:
    """Move the profile's HEAD back to an earlier commit and rebuild
    the on-disk ``package.db`` from the target's ``package.sql``
    dump.

    Discarded commits remain reachable via ``git reflog show HEAD``
    for the standard 30-day window. Any uncommitted state in the
    working tree at the moment the reset runs is packaged as a
    ``recover: pre-existing changes`` commit *before* the reset
    moves HEAD past it, so a user who reset accidentally has the
    just-before-the-reset state as a named log row rather than only
    in the reflog.

    The fcntl write lock is held across the rebuild sequence, so a
    concurrent ``mcs build`` against the same profile fails with
    the standard lock-contention error. A rebuild failure after the
    git reset bounces back to ``ORIG_HEAD`` (the pre-reset commit
    that git saves on ``reset --hard``) and re-runs the rebuild
    against the bounced-back ``package.sql`` so the on-disk
    ``package.db`` is left consistent with the tree. The twice-
    failing case surfaces the original error with the manual
    ``sqlite3 package.db < package.sql`` Unix-tooling fallback in
    the remediation field.
    """
    renderer = _renderer(ctx)

    if is_versioning_disabled():
        renderer.error(
            McsError(
                "mcs profile reset requires the per-profile git "
                "history that MCS_NO_VERSIONING=1 opts out of.",
                remediation="unset MCS_NO_VERSIONING and re-run, or "
                "use ``git reset --hard <sha>`` manually inside the "
                "profile's data directory to bypass mcs.",
            )
        )
        ctx.exit(2)
        return

    try:
        profile = resolve_profile_for_project(None, profile_name=profile_name)
    except McsError as e:
        renderer.error(e)
        sys.exit(e.exit_code)

    if profile.kind == "fork":
        renderer.error(
            McsError(
                f"profile {profile.name!r} is a fork of "
                f"{profile.parent_profile!r} anchored at "
                f"{(profile.git_sha or '')[:12]}; forks have a fixed "
                f"anchor SHA and can't be reset.",
                remediation=(
                    f"options:\n"
                    f"  (a) ``mcs profile reset --to <sha> "
                    f"--profile {profile.parent_profile}`` to move "
                    f"the parent's HEAD (the fork stays anchored at "
                    f"its current SHA, becoming an orphan if the "
                    f"parent moves past the anchor — see ``mcs "
                    f"profile fork-list``); or\n"
                    f"  (b) ``mcs profile fork-remove {profile.name}`` "
                    f"and ``mcs profile fork <new-name> --from "
                    f"<new-sha> --profile {profile.parent_profile}`` "
                    f"to drop the current fork and create a new one "
                    f"at the desired anchor."
                ),
            )
        )
        ctx.exit(2)
        return

    pdir = profile_data_dir(profile)
    repo = GitRepo(pdir)
    if not repo.exists():
        renderer.error(
            McsError(
                f"profile {profile.name!r} is not versioned — its "
                f"data directory {pdir} has no .git/ subdirectory.",
                remediation=(
                    f"run ``mcs profile enable-versioning --profile "
                    f"{profile.name}`` first; then this verb can "
                    f"target the inaugural commit."
                ),
            )
        )
        ctx.exit(2)
        return

    target_sha = _resolve_ref(repo, target_ref)
    if target_sha is None:
        renderer.error(
            McsError(
                f"no commit in the history of profile "
                f"{profile.name!r} matches the ref {target_ref!r}",
                remediation="run ``mcs profile log --all -n 0`` to "
                "inspect the full unfiltered history.",
            )
        )
        sys.exit(1)

    pre_reset_sha = repo.rev_parse("HEAD")
    if target_sha == pre_reset_sha:
        _stderr(f"profile {profile.name!r} is already at {target_sha[:12]}; nothing to do.")
        return

    is_ancestor = repo.merge_base_is_ancestor(target_sha, pre_reset_sha)
    if not is_ancestor:
        _stderr(
            f"warning: target {target_sha[:12]} is not an ancestor "
            f"of the current HEAD {pre_reset_sha[:12]}. The reset "
            f"will move HEAD across history lines rather than "
            f"linearly backward; the discarded-commits listing "
            f"below covers only the linear-range commits."
        )

    discarded = _discarded_commits(repo, target_sha, pre_reset_sha)
    if discarded:
        _stderr(
            f"resetting profile {profile.name!r} from "
            f"{pre_reset_sha[:12]} to {target_sha[:12]} will "
            f"discard {len(discarded)} commit(s) (newest first):"
        )
        for short, subject in discarded[:10]:
            _stderr(f"  {short}  {subject}")
        if len(discarded) > 10:
            _stderr(f"  ... and {len(discarded) - 10} more.")
        _stderr(
            f"git's reflog (``cd {pdir} && git reflog show HEAD``) "
            f"keeps the pre-reset HEAD reachable for ~30 days, so "
            f"the discarded commits are recoverable until then."
        )
    else:
        _stderr(
            f"reset to {target_sha[:12]} — no commits on the "
            f"current HEAD's ancestry line are discarded."
        )

    if not skip_confirmation and not click.confirm("proceed with the reset?", default=False):
        _stderr("aborted; no state change.")
        return

    with WriteLock(profile_lock_path(profile)):
        recovery_sha = commit_if_uncommitted_on_entry(profile)
        if recovery_sha is not None:
            _stderr(f"captured pre-reset uncommitted state as recovery commit {recovery_sha[:12]}.")
            pre_reset_sha = repo.rev_parse("HEAD")

        # Snapshot the pre-reset ``package.sql`` so the bounce-back
        # path has a known-good dump even if the working-tree state
        # is itself inconsistent after a partial rebuild.
        sidecar_dir = pdir / ".mcs-reset-backup"
        sidecar_dir.mkdir(exist_ok=True)
        sidecar_path = sidecar_dir / f"{pre_reset_sha}.sql"
        sql_path = profile_package_sql_path(profile)
        if sql_path.exists():
            shutil.copy2(sql_path, sidecar_path)

        repo.reset_hard(target_sha)

        db_path = pdir / "package.db"
        rebuilt_from_target = False
        try:
            if sql_path.exists():
                restore_sql_to_db(sql_path, db_path)
                # Lazy import — keeps the reindex's optional vec
                # dependency out of the import-time graph for the
                # other verbs in this module.
                from maxcompute_semantic.commands.memory import run_reindex

                run_reindex(db_path, vectors=True)
                rebuilt_from_target = True
            else:
                _stderr(
                    f"warning: the target commit {target_sha[:12]} "
                    f"has no ``package.sql`` in its tree (the bare "
                    f"inaugural commit). The on-disk ``package.db`` "
                    f"is the pre-reset binary and is now decoupled "
                    f"from the git working tree's metadata — run "
                    f"``mcs build`` to rebuild it from the upstream "
                    f"MaxCompute catalog."
                )
        except PackageSqlCorrupt as e:
            _stderr(
                f"error: rebuild of ``package.db`` from the target's "
                f"``package.sql`` failed: {e}. Bouncing back to the "
                f"pre-reset HEAD ({pre_reset_sha[:12]})."
            )
            try:
                repo.reset_hard(pre_reset_sha)
                if sidecar_path.exists():
                    shutil.copy2(sidecar_path, sql_path)
                if sql_path.exists():
                    restore_sql_to_db(sql_path, db_path)
                    from maxcompute_semantic.commands.memory import run_reindex

                    run_reindex(db_path, vectors=True)
            except Exception as bounce_e:
                renderer.error(
                    McsError(
                        f"bounce-back to the pre-reset state also "
                        f"failed: {bounce_e}. The on-disk state is "
                        f"inconsistent — git tree at "
                        f"{repo.rev_parse('HEAD')[:12]} and "
                        f"``package.db`` content uncertain.",
                        remediation=(
                            f"manual recovery: ``cd {pdir} && git "
                            f"reset --hard {pre_reset_sha} && "
                            f"sqlite3 package.db '.read "
                            f"{sidecar_path}'``."
                        ),
                    )
                )
                raise
            renderer.error(e)
            ctx.exit(3)
            return

        # Happy path — drop the sidecar backup. The reflog is the
        # authoritative recovery handle going forward.
        try:
            sidecar_path.unlink(missing_ok=True)
            sidecar_dir.rmdir()
        except OSError:
            pass

        head_log = repo.log(limit=1)
        head_subject = head_log[0].message if head_log else ""
        renderer.success(
            {
                "profile": profile.name,
                "from": pre_reset_sha[:12],
                "to": target_sha[:12],
                "head_subject": head_subject,
                "rebuilt_package_db": rebuilt_from_target,
            }
        )
        _stderr(
            f"discarded commits remain reachable via ``cd {pdir} "
            f"&& git reflog show HEAD`` for ~30 days. To recover, "
            f"look up the SHA in the reflog and run ``mcs profile "
            f"reset --to <that-sha>``."
        )
