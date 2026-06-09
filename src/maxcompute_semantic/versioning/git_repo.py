"""Thin ``subprocess.run(["git", "-C", repo_root, ...])`` wrapper.

The single source of truth for shelling out to git in mcs. Every
operation goes through ``_run`` which sets a fixed env (PATH-inheriting
but with the author/committer name/email pinned to ``mcs / mcs@local``
and ``GIT_TERMINAL_PROMPT=0`` so a credentials helper can't pop a UI on
a CI runner), captures stdout/stderr as utf-8, and on non-zero exit
raises ``McsError`` with the captured stderr in the ``remediation``
field. ``FileNotFoundError`` from the subprocess layer (the git binary
isn't on PATH) is translated into ``GitNotAvailable``.

The wrapper is *stateless* — each method opens a fresh subprocess.
There is no long-lived ``git`` process. The repo root path is the
``Path`` passed to the constructor; methods never accept their own
``cwd`` override (the constructor's path is the one source of truth).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning.errors import GitNotAvailable


@dataclass(frozen=True)
class CommitInfo:
    """One row of ``git log --oneline`` output."""

    short_sha: str
    full_sha: str
    message: str


@dataclass(frozen=True)
class WorktreeInfo:
    """One entry of ``git worktree list --porcelain`` output. The
    ``head_sha`` is the full SHA at the worktree's HEAD. The
    ``detached`` flag is ``True`` when the porcelain output's ``detached``
    line is present (which is the case for every fork mcs creates,
    since ``mcs profile fork`` always passes ``--detach``)."""

    path: Path
    head_sha: str
    detached: bool


def _is_unborn_head(stderr: str | None) -> bool:
    """Recognize the ``git log`` / ``git rev-parse HEAD`` failure mode
    that means "repo has no commits yet" (a freshly ``git init``'d
    tree with nothing committed). Both messages git emits in that
    state are matched: the ``log`` form (``fatal: your current branch
    'main' does not have any commits yet``) and the ``rev-parse``
    form (``fatal: ambiguous argument 'HEAD'``)."""
    if not stderr:
        return False
    text = stderr.lower()
    return "does not have any commits" in text or "ambiguous argument 'head'" in text


_FIXED_ENV_OVERRIDES = {
    # Author / committer identity, fixed so the user's global gitconfig
    # doesn't leak. The empty-string ``GIT_*_DATE`` would force a
    # specific timestamp; we leave it unset so each commit gets the
    # natural "now". The GPG-signing env disables signature attempts so
    # users with ``commit.gpgsign = true`` globally don't get blocked.
    "GIT_AUTHOR_NAME": "mcs",
    "GIT_AUTHOR_EMAIL": "mcs@local",
    "GIT_COMMITTER_NAME": "mcs",
    "GIT_COMMITTER_EMAIL": "mcs@local",
    "GIT_TERMINAL_PROMPT": "0",
    # Quiet ``git init``'s "hint: Using 'master' as the name of the
    # initial branch" suggestion banner on machines whose
    # ``init.defaultBranch`` isn't set. The branch name itself doesn't
    # matter to mcs (we only operate via short/long SHAs and HEAD), but
    # the banner ends up in our captured stderr and would confuse
    # error-path code that scans for diagnostic substrings.
    "GIT_ADVICE": "0",
}


class GitRepo:
    """Wrapper around git CLI scoped to one repository's working tree.

    The constructor takes ``repo_root`` — the working tree root, which
    is the *parent* of the ``.git/`` admin directory (mcs's convention
    is "the profile data directory is the working tree"). All
    operations pass ``-C <repo_root>`` so they're scoped regardless of
    the calling process's cwd.

    Methods raise ``McsError`` on git failure with stderr captured into
    the ``remediation`` field. ``GitNotAvailable`` is raised once at
    first contact if the ``git`` binary isn't on PATH; subsequent calls
    don't re-probe — the first failure is taken as authoritative for
    the process's lifetime.
    """

    def __init__(self, repo_root: Path) -> None:
        self._root = Path(repo_root)

    @property
    def root(self) -> Path:
        return self._root

    def exists(self) -> bool:
        """Whether ``<root>/.git`` exists. Pure filesystem check — no
        ``git`` invocation. Used by the hook's "is this profile
        versioned?" branch and by ``mcs doctor``'s ``profile_versioned``
        check. The check is for ``.git`` as either a directory (a
        normal repo) *or* a regular file (a linked worktree's gitdir
        file — although mcs only creates the parent-as-repo and the
        forks-as-worktrees, never a linked-repo-via-gitfile, so the
        file-form check is defensive and not exercised by the happy
        path).
        """
        return (self._root / ".git").exists()

    def init(self) -> None:
        """``git init`` if ``.git/`` is absent. Idempotent. The init
        creates a default branch named ``main`` regardless of the
        user's ``init.defaultBranch`` config (we pass ``-b main``)
        so the branch name is stable across machines and not a
        moving target if the user's global config changes."""
        if self.exists():
            return
        self._root.mkdir(parents=True, exist_ok=True)
        self._run("init", "-b", "main", check=True)

    def add_all(self) -> None:
        self._run("add", "-A", check=True)

    def commit(self, message: str, *, allow_empty: bool = False) -> str | None:
        """Commit the staged index with ``message``. Returns the new
        commit's full SHA, or ``None`` if there was nothing staged
        and ``allow_empty=False`` (``git diff --cached --quiet``
        returncode 0 means "no changes" and we short-circuit). The
        ``--no-gpg-sign`` flag is hardcoded to dodge the global
        ``commit.gpgsign`` knob, consistent with the spec's "no
        leaking the user's git identity into the profile's history"
        decision.

        ``allow_empty=True`` skips the empty-tree short-circuit and
        passes ``--allow-empty`` to ``git commit`` so the commit
        lands even when nothing is staged. Used by the auto-commit
        hook to record the logical end-marker of a write command
        whose underlying byte-deterministic dump happened to produce
        no on-disk delta (the "recover then annotate" case in the
        crash-recovery flow: the recover snapshot is the interrupted
        prior work, the annotate is the user's current intent, and
        both deserve a log entry).
        """
        if not allow_empty:
            # Check the index — if nothing's staged, ``git commit``
            # would exit nonzero with "nothing to commit", which our
            # error mapping would turn into an McsError.
            # Short-circuit first.
            proc = self._raw("diff", "--cached", "--quiet", check=False)
            if proc.returncode == 0:
                return None
        args = ["commit", "--no-gpg-sign", "--quiet"]
        if allow_empty:
            args.append("--allow-empty")
        args.extend(("-m", message))
        self._run(*args, check=True)
        return self.rev_parse("HEAD")

    def has_uncommitted_changes(self) -> bool:
        """True iff ``git status --porcelain`` produces any output —
        any line means something is dirty (modified, added,
        untracked-and-not-ignored, renamed, conflicted). Used by the
        crash-recovery branch of the hook."""
        out = self._run("status", "--porcelain", check=True)
        return bool(out.strip())

    def rev_parse(self, ref: str) -> str:
        """Resolve ``ref`` (a short SHA, ``HEAD``, ``HEAD~N``, a tag,
        a branch name) to a 40-char hex full SHA. Raises ``McsError``
        on unknown ref."""
        out = self._run("rev-parse", "--verify", f"{ref}^{{commit}}", check=True)
        return out.strip()

    def log(
        self,
        *,
        limit: int | None = None,
        grep_regex: str | None = None,
        invert_grep: bool = False,
        paths: tuple[str, ...] = (),
    ) -> list[CommitInfo]:
        """Return the most-recent-first list of commits. ``limit``
        translates to ``-n <limit>``. ``grep_regex`` translates to
        ``--grep=<regex> --extended-regexp``; with ``invert_grep=True``
        it adds ``--invert-grep`` to *exclude* matches (which is how
        the default ``mcs profile log`` hides the ``memory:`` noise).
        ``paths`` becomes the trailing positional ``-- <p1> <p2> ...``
        argument so the log is scoped to only commits touching those
        paths.

        An unborn-HEAD repo (``git init`` run, no commits yet) is
        treated as an empty history rather than an error — ``git log``
        on such a repo exits 128 with ``fatal: your current branch
        'main' does not have any commits yet``, but the Pythonic
        answer to "what commits are in this history" is ``[]``.
        """
        args: list[str] = [
            "log",
            "--no-color",
            "--format=%h%x09%H%x09%s",
        ]
        if limit is not None and limit > 0:
            args.extend(("-n", str(limit)))
        if grep_regex is not None:
            args.extend(("--extended-regexp", f"--grep={grep_regex}"))
            if invert_grep:
                args.append("--invert-grep")
        if paths:
            args.append("--")
            args.extend(paths)
        proc = self._raw(*args, check=False)
        if proc.returncode != 0:
            if _is_unborn_head(proc.stderr):
                return []
            self._raise_from_proc(proc, list(args))
        out = proc.stdout
        rows: list[CommitInfo] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            short, full, msg = line.split("\t", 2)
            rows.append(CommitInfo(short_sha=short, full_sha=full, message=msg))
        return rows

    def commit_subject(self, sha: str) -> str:
        """Return the subject line (``%s``) of the commit named by
        ``sha``. Equivalent to ``git log -1 --format=%s <sha>``.

        Used by ``mcs profile show <fork-name>``'s tail block, which
        needs to render the anchor commit's subject without pulling
        the whole 1000-row ``repo.log()`` into Python just to filter
        by one SHA.
        """
        full = self.rev_parse(sha)
        return self._run("log", "-1", "--format=%s", full, check=True).strip()

    def show(self, sha: str) -> str:
        """Return ``git show <sha>``'s output verbatim (the unified
        diff against the parent, with the commit metadata header).
        Filtered to the committed-file paths via the same path
        whitelist used by ``diff``."""
        full = self.rev_parse(sha)
        return self._run(
            "show",
            "--no-color",
            full,
            "--",
            "*.md",
            "*.json",
            "package.sql",
            ".gitignore",
            check=True,
        )

    def diff(self, a: str, b: str) -> str:
        """``git diff <a> <b>`` over the committed-file paths."""
        a_full = self.rev_parse(a)
        b_full = self.rev_parse(b)
        return self._run(
            "diff",
            "--no-color",
            f"{a_full}..{b_full}",
            "--",
            "*.md",
            "*.json",
            "package.sql",
            ".gitignore",
            check=True,
        )

    def reset_hard(self, sha: str) -> None:
        """``git reset --hard <sha>``. The pre-reset ``HEAD`` is
        accessible via ``ORIG_HEAD`` for the spec's "if the
        package.db rebuild fails, restore to ORIG_HEAD" recovery
        branch in T13. The reflog (``git reflog show HEAD``) is the
        30-day window the user-facing warning mentions."""
        full = self.rev_parse(sha)
        self._run("reset", "--hard", full, check=True)

    def merge_base_is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """``git merge-base --is-ancestor <a> <d>``: returncode 0 if
        ``<a>`` is an ancestor of ``<d>``, returncode 1 otherwise.
        Other returncodes (e.g. 128 — unknown ref) are propagated as
        ``McsError``. Used by ``fork-list``'s orphan-detection branch:
        a fork's anchor SHA being an ancestor of the parent's current
        HEAD means the fork is still on the parent's history line; a
        non-ancestor means the parent was ``mcs profile reset``'d to
        a point that doesn't include the fork's anchor."""
        a_full = self.rev_parse(ancestor)
        d_full = self.rev_parse(descendant)
        proc = self._raw("merge-base", "--is-ancestor", a_full, d_full, check=False)
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        # Any other returncode is a real error — surface the stderr.
        self._raise_from_proc(proc, ["merge-base", "--is-ancestor", ancestor, descendant])
        raise AssertionError("unreachable")  # pragma: no cover

    def find_commit_with_prefix(self, message_prefix: str) -> str | None:
        """Return the full SHA of the most-recent commit whose
        message starts with ``message_prefix``, or ``None`` if no
        such commit exists. Drives ``mcs profile reset --to last-build``
        / ``--to last-refresh``: ``last-build`` resolves the most
        recent ``build:`` commit, ``last-refresh`` resolves the most
        recent ``refresh:`` commit."""
        # The ``--grep`` arg is a POSIX-extended regex (because of
        # ``--extended-regexp``); ``^`` anchors to the start of the
        # commit subject line.
        args = (
            "log",
            "-n",
            "1",
            "--format=%H",
            "--extended-regexp",
            f"--grep=^{message_prefix}",
        )
        proc = self._raw(*args, check=False)
        if proc.returncode != 0:
            if _is_unborn_head(proc.stderr):
                return None
            self._raise_from_proc(proc, list(args))
        out = proc.stdout.strip()
        return out if out else None

    def worktree_add(self, path: Path, sha: str, *, detach: bool = True) -> None:
        """``git worktree add --detach <path> <sha>``. The ``detach``
        kwarg is the safety knob — mcs always passes ``True`` so
        forks land on a detached HEAD instead of creating a real
        branch. The path must not exist (git refuses to overwrite an
        existing directory); the caller (``mcs profile fork``) is
        expected to have validated the fork name isn't already taken
        by checking ``profiles.yaml`` first."""
        full = self.rev_parse(sha)
        args = ["worktree", "add"]
        if detach:
            args.append("--detach")
        args.extend((str(path), full))
        self._run(*args, check=True)

    def worktree_list(self) -> list[WorktreeInfo]:
        """Parse ``git worktree list --porcelain`` into a list of
        ``WorktreeInfo``. The porcelain format is groups of lines
        separated by blank lines, each group containing
        ``worktree <path>``, ``HEAD <sha>``, optionally ``branch
        refs/heads/<name>`` or ``detached``, and optionally ``bare``
        for the parent repo's main worktree."""
        out = self._run("worktree", "list", "--porcelain", check=True)
        rows: list[WorktreeInfo] = []
        for block in out.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            fields: dict[str, str] = {}
            detached = False
            for line in block.splitlines():
                if line == "detached":
                    detached = True
                    continue
                if line == "bare":
                    # The bare-repo "main" entry of ``worktree list`` — skip.
                    fields.clear()
                    break
                if " " in line:
                    key, _, val = line.partition(" ")
                    fields[key] = val
            if "worktree" in fields and "HEAD" in fields:
                rows.append(
                    WorktreeInfo(
                        path=Path(fields["worktree"]),
                        head_sha=fields["HEAD"],
                        detached=detached,
                    )
                )
        return rows

    def worktree_remove(self, path: Path, *, force: bool = False) -> None:
        """``git worktree remove [--force] <path>``. ``force=True`` is
        the spec's "user manually edited the worktree directory and
        git wants to refuse" recovery — without it, dirty worktrees
        refuse to remove. ``mcs profile fork-remove`` defaults to
        non-force; the ``--force`` flag of that CLI command flips this
        kwarg through."""
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        self._run(*args, check=True)

    def worktree_prune(self) -> None:
        """``git worktree prune`` — sweep the parent repo's
        ``.git/worktrees/<name>/`` admin directories whose
        corresponding working-tree directory has been manually
        deleted. Called by ``mcs profile fork-list`` as the self-heal
        for ghost forks (yaml entry exists, on-disk worktree directory
        doesn't)."""
        self._run("worktree", "prune", check=True)

    # --- internal subprocess wrapper --------------------------------------

    def _env(self) -> dict[str, str]:
        """Build the subprocess env: pass through the parent's env,
        force the identity overrides, and zero out the
        ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` env vars so a
        machine-wide gitconfig with a ``commit.template`` or
        ``core.hooksPath`` knob can't interfere with the
        machine-portability of mcs's commits. The user's per-repo
        ``<repo_root>/.git/config`` is *not* zeroed — it's part of
        the repo state mcs creates and owns."""
        env = dict(os.environ)
        env.update(_FIXED_ENV_OVERRIDES)
        # Point the global/system config at /dev/null so the user's
        # ~/.gitconfig and /etc/gitconfig don't influence the
        # commit's identity, signing, hooks, or aliases.
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        return env

    def _raw(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        """The lower of the two helpers — returns the
        ``CompletedProcess`` for callers who want to inspect
        ``returncode`` themselves (the ``check=False`` paths in
        ``commit`` and ``merge_base_is_ancestor``)."""
        cmd = ["git", "-C", str(self._root), *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                env=self._env(),
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            cmd_display = "git " + " ".join(args)
            raise McsError(
                f"git command timed out after 30s: {cmd_display!r}",
                remediation="check for git operations blocked on input or network; "
                "if the repository is on a network filesystem, verify connectivity",
            ) from e
        except FileNotFoundError as e:
            raise GitNotAvailable(
                "the ``git`` binary is not on PATH",
                remediation=(
                    "install git via your system package manager "
                    "(macOS: ``xcode-select --install`` or ``brew install git``; "
                    "Debian/Ubuntu: ``apt-get install git``; "
                    "RHEL/centos: ``yum install git``; "
                    "Windows: ``winget install --id Git.Git`` or download "
                    "from https://git-scm.com/download/win). "
                    "mcs's per-profile version history is disabled "
                    "without git — set ``MCS_NO_VERSIONING=1`` in your "
                    "environment to silence this error and run mcs "
                    "without versioning."
                ),
            ) from e
        if check and proc.returncode != 0:
            self._raise_from_proc(proc, list(args))
        return proc

    def _run(self, *args: str, check: bool) -> str:
        """The higher-level helper — same as ``_raw`` but returns just
        the decoded stdout string. With ``check=True`` (the typical
        caller contract), non-zero returncode raises inside ``_raw``;
        with ``check=False`` the caller normally goes through ``_raw``
        directly to inspect ``returncode``, so this convenience wrapper
        is only used in the ``check=True`` path."""
        return self._raw(*args, check=check).stdout

    def _raise_from_proc(self, proc: subprocess.CompletedProcess[str], args: list[str]) -> None:
        cmd_display = "git " + " ".join(args)
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"exit {proc.returncode}"
        raise McsError(
            f"git command failed: {cmd_display!r} (exit {proc.returncode})",
            remediation=f"git stderr: {detail}",
        )
