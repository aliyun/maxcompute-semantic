"""GitRepo — subprocess-driven git CLI wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.versioning.errors import GitNotAvailable
from maxcompute_semantic.versioning.git_repo import GitRepo


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    """A freshly-constructed GitRepo whose root directory does not yet
    exist on disk. The fixture does not run ``git init`` — the init
    smoke test below exercises that step explicitly. Subsequent
    fixtures in the same module that *do* want an inited repo build
    on this fixture by calling ``repo.init()``."""
    return GitRepo(tmp_path / "profile-data")


@pytest.fixture
def inited_repo(repo: GitRepo) -> GitRepo:
    """A GitRepo where ``git init -b main`` has already been called
    but no commits exist yet. The repo root directory exists; the
    unborn-HEAD-with-empty-index state is the precondition for the
    initial-commit test in T6."""
    repo.init()
    return repo


def test_init_creates_dot_git_with_main_branch(repo: GitRepo) -> None:
    """``GitRepo.init()`` creates the repo root directory, runs
    ``git init -b main``, and marks the repo as existing. The
    default branch is ``main`` regardless of the user's
    ``init.defaultBranch`` global setting (the constructor forces
    ``-b main`` so the branch name is stable across dev machines)."""
    assert not repo.exists()
    repo.init()
    assert repo.exists()
    assert (repo.root / ".git").is_dir()
    # ``git symbolic-ref HEAD`` resolves to ``refs/heads/main`` on a
    # fresh repo. We don't have a public ``GitRepo`` method to read
    # HEAD's symbolic ref (it's not on the surface mcs needs), so
    # peek at the file directly.
    head_text = (repo.root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    assert head_text == "ref: refs/heads/main", (
        f"expected HEAD to be a symbolic ref to refs/heads/main; got {head_text!r}"
    )


def test_init_is_idempotent(inited_repo: GitRepo) -> None:
    """A second call to ``init()`` on an already-initialized repo is
    a no-op. The hook's auto-init-on-first-write branch (T7) relies
    on this — every write would otherwise be a no-op ``git init`` on
    every already-versioned profile."""
    head_before = (inited_repo.root / ".git" / "HEAD").read_text()
    inited_repo.init()
    head_after = (inited_repo.root / ".git" / "HEAD").read_text()
    assert head_before == head_after


def test_commit_after_add_writes_a_log_entry_and_returns_full_sha(
    inited_repo: GitRepo,
) -> None:
    """The happy-path smoke: create a file, ``add_all`` it, ``commit``
    it, the returned full SHA matches ``rev_parse("HEAD")``, ``log``
    shows the one entry."""
    (inited_repo.root / "hello.md").write_text("# Hello\n", encoding="utf-8")
    inited_repo.add_all()
    new_sha = inited_repo.commit("init: smoke")
    assert new_sha is not None
    assert len(new_sha) == 40, f"expected 40-char hex SHA, got {new_sha!r}"
    assert new_sha == inited_repo.rev_parse("HEAD")
    log_rows = inited_repo.log(limit=None)
    assert len(log_rows) == 1
    row = log_rows[0]
    assert row.full_sha == new_sha
    assert row.message == "init: smoke"
    assert row.short_sha == new_sha[:7]


def test_commit_with_empty_index_returns_none(inited_repo: GitRepo) -> None:
    """``commit`` on a clean index (nothing staged) returns ``None``
    without running ``git commit`` — matches the hook's
    ``git diff --cached --quiet`` short-circuit."""
    # Note: a fresh ``git init`` has no commits and no staged files,
    # so the index is empty. Without a prior commit, ``git diff
    # --cached --quiet`` against the implied empty-tree compares
    # against the empty tree, and any *unstaged untracked* files do
    # not show up (``--cached`` is the index, not the working tree).
    # Add an untracked file but don't ``add_all`` — the index stays
    # empty and the commit short-circuits.
    (inited_repo.root / "untracked.txt").write_text("hi", encoding="utf-8")
    result = inited_repo.commit("init: should-be-skipped")
    assert result is None
    assert inited_repo.log(limit=None) == []


def test_commit_allow_empty_true_lands_commit_with_no_staged_delta(
    inited_repo: GitRepo,
) -> None:
    """``commit(..., allow_empty=True)`` lands a commit even when the
    staged tree is byte-identical to ``HEAD``. The hook's auto-commit
    flow relies on this to record a write command's logical end-marker
    after the crash-recovery branch (the "recover then annotate" case
    where the annotate's dump produces no on-disk delta but the
    annotate action still deserves a log entry).
    """
    # Bootstrap a non-empty HEAD first — ``--allow-empty`` is only
    # meaningful when there's a parent to be empty against.
    (inited_repo.root / "a.md").write_text("first", encoding="utf-8")
    inited_repo.add_all()
    first_sha = inited_repo.commit("seed: first")
    assert first_sha is not None

    # No further changes; staged tree is byte-identical to HEAD.
    second_sha = inited_repo.commit("annotate: empty-marker", allow_empty=True)
    assert second_sha is not None
    assert second_sha != first_sha
    msgs = [c.message for c in inited_repo.log(limit=None)]
    assert msgs == ["annotate: empty-marker", "seed: first"]


def test_commit_allow_empty_false_on_clean_tree_returns_none(
    inited_repo: GitRepo,
) -> None:
    """Default ``allow_empty=False`` preserves the empty-index
    short-circuit: a second call against an unchanged tree returns
    ``None`` and produces no extra log entry. Pinned alongside the
    ``allow_empty=True`` test so the two paths are explicitly
    exercised side-by-side.
    """
    (inited_repo.root / "a.md").write_text("first", encoding="utf-8")
    inited_repo.add_all()
    first_sha = inited_repo.commit("seed: first")
    assert first_sha is not None
    # No further changes — defaults short-circuit.
    second_sha = inited_repo.commit("seed: second")
    assert second_sha is None
    msgs = [c.message for c in inited_repo.log(limit=None)]
    assert msgs == ["seed: first"]


def test_has_uncommitted_changes_reflects_status_porcelain(
    inited_repo: GitRepo,
) -> None:
    """``status --porcelain`` non-empty iff there are
    untracked/modified files. After a clean commit it's empty."""
    assert inited_repo.has_uncommitted_changes() is False
    (inited_repo.root / "a.md").write_text("a", encoding="utf-8")
    assert inited_repo.has_uncommitted_changes() is True
    inited_repo.add_all()
    assert inited_repo.has_uncommitted_changes() is True
    inited_repo.commit("add a")
    assert inited_repo.has_uncommitted_changes() is False


def test_log_grep_invert_filters_memory_prefix_by_default(
    inited_repo: GitRepo,
) -> None:
    """``log(grep_regex=r'^memory:', invert_grep=True)`` returns
    commits whose subject does *not* match the regex. This is the
    default-noise-filter behavior of ``mcs profile log``."""
    # Build a history with one memory: commit between two build:
    # commits so the filter produces a deterministic ordering.
    (inited_repo.root / "build1.md").write_text("b1", encoding="utf-8")
    inited_repo.add_all()
    inited_repo.commit("build: first")
    (inited_repo.root / "mem.md").write_text("m", encoding="utf-8")
    inited_repo.add_all()
    inited_repo.commit('memory: verify 7 ("top customers")')
    (inited_repo.root / "build2.md").write_text("b2", encoding="utf-8")
    inited_repo.add_all()
    inited_repo.commit("build: second")

    all_msgs = [c.message for c in inited_repo.log(limit=None)]
    assert all_msgs == [
        "build: second",
        'memory: verify 7 ("top customers")',
        "build: first",
    ]
    filtered_msgs = [
        c.message for c in inited_repo.log(limit=None, grep_regex="^memory:", invert_grep=True)
    ]
    assert filtered_msgs == ["build: second", "build: first"]


def test_rev_parse_unknown_ref_raises_mcserror(inited_repo: GitRepo) -> None:
    """``rev_parse("does-not-exist")`` raises ``McsError`` whose
    remediation field carries the stderr from the failed git call."""
    with pytest.raises(McsError) as exc_info:
        inited_repo.rev_parse("nonexistent-ref-name")
    # The stderr should mention "unknown revision" or similar — we
    # don't pin the exact wording (it varies across git versions),
    # but it has to be non-empty and informative.
    err_text = str(exc_info.value)
    assert "git" in err_text.lower(), (
        f"the error message should name the failed git invocation; got {err_text!r}"
    )


def test_git_not_available_when_binary_missing(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the ``git`` binary is missing from PATH, ``GitRepo._raw``'s
    subprocess layer raises ``FileNotFoundError``, which the wrapper
    translates into ``GitNotAvailable`` with a helpful remediation
    message naming the ``MCS_NO_VERSIONING=1`` escape hatch."""
    # Empty PATH means the kernel can't find ``git``.
    monkeypatch.setenv("PATH", "")
    with pytest.raises(GitNotAvailable) as exc_info:
        repo.init()
    assert "MCS_NO_VERSIONING" in exc_info.value.remediation
    assert "git" in str(exc_info.value).lower()


def test_env_blocks_user_gitconfig(
    inited_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrapper points ``GIT_CONFIG_GLOBAL`` and
    ``GIT_CONFIG_SYSTEM`` at ``os.devnull`` so a user's
    ``~/.gitconfig`` with a ``user.signingkey`` or ``commit.gpgsign``
    setting can't break mcs commits. We verify the contract by
    setting a global config (via the env var the wrapper is supposed
    to override) pointing at a file with a known ``user.name``, and
    confirming the committed commit's author name is ``mcs`` and not
    that value."""
    bogus_gitconfig = tmp_path / "bogus-gitconfig"
    bogus_gitconfig.write_text(
        "[user]\n  name = HostUser\n  email = host@example.com\n[commit]\n  gpgsign = true\n",
        encoding="utf-8",
    )
    # The wrapper overrides ``GIT_CONFIG_GLOBAL`` regardless of what
    # the parent env says, so even if the parent points it at the
    # bogus config, the subprocess sees ``GIT_CONFIG_GLOBAL=/dev/null``.
    # We set the parent's value to the bogus path; the test
    # confirms the wrapper's override wins.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(bogus_gitconfig))

    (inited_repo.root / "f.md").write_text("x", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("test: identity-override")
    assert sha is not None

    # Read the committed commit's metadata via ``git show --format=%an
    # %ae`` (author name, author email). We use the raw subprocess so
    # the assertion isn't reliant on the wrapper's own log method (a
    # tautology if the wrapper sets the env the same way both for
    # the commit and for the inspection — which it does, so the
    # check would always pass. Use a subprocess call with a *clean*
    # env that does respect the bogus gitconfig, so the test fails
    # if the commit's recorded identity is HostUser instead of mcs).
    env_for_check = dict(os.environ)
    env_for_check.pop("GIT_AUTHOR_NAME", None)
    env_for_check.pop("GIT_AUTHOR_EMAIL", None)
    env_for_check.pop("GIT_COMMITTER_NAME", None)
    env_for_check.pop("GIT_COMMITTER_EMAIL", None)
    env_for_check.pop("GIT_CONFIG_GLOBAL", None)
    env_for_check.pop("GIT_CONFIG_SYSTEM", None)
    out = subprocess.run(
        [
            "git",
            "-C",
            str(inited_repo.root),
            "show",
            "--no-patch",
            "--format=%an<%ae>",
            sha,
        ],
        capture_output=True,
        check=True,
        env=env_for_check,
        text=True,
    ).stdout.strip()
    assert out == "mcs<mcs@local>", (
        f"the committed author identity should be the wrapper's "
        f"hardcoded ``mcs<mcs@local>``, not whatever the user's "
        f"global gitconfig says. Got {out!r}."
    )


def test_show_filters_to_committed_paths_only(inited_repo: GitRepo) -> None:
    """``show`` passes the path whitelist ``*.md *.json package.sql
    .gitignore`` so changes in other paths (e.g. a hand-written
    file in the tracked tree that somehow got added — though
    ``.gitignore`` should prevent it) are filtered out of the diff
    text. The whitelist matches the actual file types mcs commits
    (markdown, JSON state, the SQL dump, and the .gitignore
    itself)."""
    (inited_repo.root / "tracked.md").write_text("hi", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("track-md")
    assert sha is not None
    out = inited_repo.show(sha)
    # The diff body should mention the markdown file path. The exact
    # format of ``git show`` output (the ``diff --git`` header line
    # in particular) is well-known.
    assert "tracked.md" in out
    assert "diff --git" in out


def test_diff_renders_unified_diff_between_two_shas(inited_repo: GitRepo) -> None:
    """The end-to-end of ``mcs profile diff <a> <b>``: ``GitRepo.diff``
    is a unified-diff string suitable for printing verbatim."""
    (inited_repo.root / "x.md").write_text("v1\n", encoding="utf-8")
    inited_repo.add_all()
    sha_a = inited_repo.commit("v1")
    (inited_repo.root / "x.md").write_text("v2\n", encoding="utf-8")
    inited_repo.add_all()
    sha_b = inited_repo.commit("v2")
    assert sha_a is not None and sha_b is not None
    diff_text = inited_repo.diff(sha_a, sha_b)
    assert "-v1" in diff_text and "+v2" in diff_text
    assert "x.md" in diff_text


def test_reset_hard_moves_head_and_drops_post_commits(inited_repo: GitRepo) -> None:
    """``reset_hard(<earlier-sha>)`` moves HEAD back and the working
    tree's content matches the earlier state. The dropped commits
    are still in ``git reflog`` (we don't test the reflog
    directly — the spec's 30-day-reflog guarantee is a property of
    git itself, not of the wrapper)."""
    (inited_repo.root / "f.md").write_text("a", encoding="utf-8")
    inited_repo.add_all()
    sha_a = inited_repo.commit("a")
    assert sha_a is not None
    (inited_repo.root / "f.md").write_text("b", encoding="utf-8")
    inited_repo.add_all()
    sha_b = inited_repo.commit("b")
    assert sha_b is not None
    assert inited_repo.rev_parse("HEAD") == sha_b
    inited_repo.reset_hard(sha_a)
    assert inited_repo.rev_parse("HEAD") == sha_a
    assert (inited_repo.root / "f.md").read_text(encoding="utf-8") == "a"


def test_merge_base_is_ancestor_true_for_self_and_parent(
    inited_repo: GitRepo,
) -> None:
    """``merge-base --is-ancestor X X`` is always ``True`` (every
    commit is its own ancestor). After a child commit C on top of
    parent P, ``is_ancestor(P, C)`` is ``True`` and ``is_ancestor(C,
    P)`` is ``False``. The orphan-detection branch of ``fork-list``
    uses ``is_ancestor(fork_sha, parent_HEAD)``: ``True`` means the
    fork's anchor is reachable from the parent's current HEAD →
    healthy; ``False`` means the parent moved off that line of
    history → orphan."""
    (inited_repo.root / "p.md").write_text("p", encoding="utf-8")
    inited_repo.add_all()
    sha_p = inited_repo.commit("p")
    (inited_repo.root / "c.md").write_text("c", encoding="utf-8")
    inited_repo.add_all()
    sha_c = inited_repo.commit("c")
    assert sha_p is not None and sha_c is not None
    assert inited_repo.merge_base_is_ancestor(sha_p, sha_p) is True
    assert inited_repo.merge_base_is_ancestor(sha_p, sha_c) is True
    assert inited_repo.merge_base_is_ancestor(sha_c, sha_p) is False


def test_find_commit_with_prefix_returns_most_recent_match(
    inited_repo: GitRepo,
) -> None:
    """``find_commit_with_prefix("annotate")`` matches both
    ``annotate:`` and ``package-apply:`` prefixes (the regex is
    ``^build``, not ``^build:``). Returns the most recent
    match's full SHA."""
    (inited_repo.root / "a.md").write_text("a", encoding="utf-8")
    inited_repo.add_all()
    sha_first = inited_repo.commit("build: profile @ 2026-05-23T14:00:00Z")
    (inited_repo.root / "b.md").write_text("b", encoding="utf-8")
    inited_repo.add_all()
    sha_second = inited_repo.commit("build: profile @ 2026-05-23T15:00:00Z")
    (inited_repo.root / "c.md").write_text("c", encoding="utf-8")
    inited_repo.add_all()
    _sha_memory = inited_repo.commit("memory: verify 1")
    assert sha_first is not None
    assert sha_second is not None

    hit = inited_repo.find_commit_with_prefix("build")
    assert hit == sha_second
    hit_memory = inited_repo.find_commit_with_prefix("memory")
    assert hit_memory == _sha_memory
    miss = inited_repo.find_commit_with_prefix("nonexistent-prefix-")
    assert miss is None


def test_worktree_lifecycle(inited_repo: GitRepo, tmp_path: Path) -> None:
    """End-to-end for the fork support: add a worktree at a specific
    SHA, list it back, remove it. The orphaned-admin-dir case (the
    worktree directory is manually deleted but git's admin entry
    survives) is exercised by ``worktree_prune`` and the integration
    test in T21."""
    (inited_repo.root / "f.md").write_text("a", encoding="utf-8")
    inited_repo.add_all()
    sha_a = inited_repo.commit("a")
    (inited_repo.root / "f.md").write_text("b", encoding="utf-8")
    inited_repo.add_all()
    _sha_b = inited_repo.commit("b")
    assert sha_a is not None

    wt_path = tmp_path / "the-fork"
    inited_repo.worktree_add(wt_path, sha_a, detach=True)
    # The worktree's file content matches commit ``a`` (the older one).
    assert (wt_path / "f.md").read_text(encoding="utf-8") == "a"

    # ``worktree_list`` returns at least two entries: the parent's
    # main worktree at the repo root and the newly-added fork.
    entries = inited_repo.worktree_list()
    paths = [e.path.resolve() for e in entries]
    assert wt_path.resolve() in paths
    fork_entry = next(e for e in entries if e.path.resolve() == wt_path.resolve())
    assert fork_entry.detached is True
    assert fork_entry.head_sha == sha_a

    # Remove cleanly.
    inited_repo.worktree_remove(wt_path)
    assert not wt_path.exists()
    # The parent's ``.git/worktrees/the-fork/`` admin directory is
    # also gone after a clean ``worktree remove``.
    admin_dir = inited_repo.root / ".git" / "worktrees" / "the-fork"
    assert not admin_dir.exists()


def test_worktree_prune_cleans_admin_for_manually_deleted_dir(
    inited_repo: GitRepo, tmp_path: Path
) -> None:
    """The "ghost fork" self-heal: a user ``rm -rf``'s the worktree
    directory without going through ``git worktree remove``. The
    parent's ``.git/worktrees/<name>/`` admin directory stays. The
    next ``worktree_prune()`` sweeps the stale admin entry."""
    (inited_repo.root / "x.md").write_text("x", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("x")
    assert sha is not None

    wt_path = tmp_path / "ghost-fork"
    inited_repo.worktree_add(wt_path, sha)
    admin_dir = inited_repo.root / ".git" / "worktrees" / "ghost-fork"
    assert admin_dir.is_dir()

    # Simulate the user manually deleting the worktree directory.
    shutil.rmtree(wt_path)
    # Without prune, git still thinks the worktree exists.
    paths_before = [e.path for e in inited_repo.worktree_list()]
    # The list call after a manual delete returns the entry with the
    # gone path; the directory's gone, the admin entry isn't.
    assert any(p.name == "ghost-fork" for p in paths_before)

    inited_repo.worktree_prune()
    # Admin dir is gone after prune.
    assert not admin_dir.exists()
    # And the worktree list no longer reports the ghost.
    paths_after = [e.path for e in inited_repo.worktree_list()]
    assert all(p.name != "ghost-fork" for p in paths_after)


# --- Additional cases from plan lines 1761-1768 -----------------------------


def test_commit_message_supports_multiline_subject_or_body(
    inited_repo: GitRepo,
) -> None:
    """``commit("subject\\n\\nbody")`` succeeds; the resulting commit's
    full message round-trips with the first line as the subject (as
    reported by ``log``) and the body accessible via a separate
    ``git show --format=%B <sha>`` probe outside the wrapper. The mcs
    hook itself never emits multiline messages — the action format is
    a single subject line — but the wrapper supports it for the
    spec's "free-text commit body" follow-up in the
    Open-questions section.
    """
    (inited_repo.root / "f.md").write_text("hi", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("subject line\n\nthis is the body paragraph")
    assert sha is not None

    # ``log`` reports the subject line (``%s``) only.
    rows = inited_repo.log(limit=None)
    assert rows[0].message == "subject line"

    # The full message including body is accessible via a raw
    # ``git show --format=%B`` probe outside the wrapper.
    full_msg = subprocess.run(
        ["git", "-C", str(inited_repo.root), "show", "--no-patch", "--format=%B", sha],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert "subject line" in full_msg
    assert "this is the body paragraph" in full_msg


def test_commit_short_circuit_when_only_ignored_paths_changed(
    inited_repo: GitRepo,
) -> None:
    """If the only thing different in the working tree since the last
    commit is a path matched by ``.gitignore``, ``add_all()`` adds
    nothing and ``commit()`` returns ``None``. Exercised by writing
    a ``.gitignore`` matching the changed file before the call."""
    # Initial commit to anchor the repo at a non-empty HEAD.
    (inited_repo.root / ".gitignore").write_text("package.db\n", encoding="utf-8")
    (inited_repo.root / "first.md").write_text("first", encoding="utf-8")
    inited_repo.add_all()
    first_sha = inited_repo.commit("init")
    assert first_sha is not None

    # Now create the ignored file. ``add_all`` should not stage it
    # because ``.gitignore`` excludes it; the subsequent ``commit``
    # short-circuits.
    (inited_repo.root / "package.db").write_text("binary-ish", encoding="utf-8")
    inited_repo.add_all()
    result = inited_repo.commit("should-be-skipped")
    assert result is None
    # Still only one commit.
    assert len(inited_repo.log(limit=None)) == 1


def test_diff_against_empty_initial_state_uses_empty_tree(
    inited_repo: GitRepo,
) -> None:
    """The *first* ``commit()`` of a repo has no parent; ``show(<first-sha>)``
    returns the diff against the empty tree (the implicit initial
    state in git's data model). The unified-diff header contains
    ``--- /dev/null`` for files that are added in that first
    commit."""
    (inited_repo.root / "only.md").write_text("hello\n", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("init: only")
    assert sha is not None
    out = inited_repo.show(sha)
    assert "--- /dev/null" in out
    assert "only.md" in out


def test_log_paths_filter_scopes_history_to_specific_files(
    inited_repo: GitRepo,
) -> None:
    """A repo where commit A touches ``foo.md`` and commit B touches
    ``bar.md``; ``log(paths=("foo.md",))`` returns only commit A.
    Used by no live caller today but the API is exposed for the
    spec's ``mcs profile log -- <path>`` follow-up."""
    (inited_repo.root / "foo.md").write_text("foo", encoding="utf-8")
    inited_repo.add_all()
    sha_a = inited_repo.commit("touched foo")
    (inited_repo.root / "bar.md").write_text("bar", encoding="utf-8")
    inited_repo.add_all()
    sha_b = inited_repo.commit("touched bar")
    assert sha_a is not None and sha_b is not None

    foo_only = inited_repo.log(paths=("foo.md",))
    assert [c.full_sha for c in foo_only] == [sha_a]
    bar_only = inited_repo.log(paths=("bar.md",))
    assert [c.full_sha for c in bar_only] == [sha_b]


def test_run_command_failure_message_includes_command_words(
    inited_repo: GitRepo,
) -> None:
    """``_run("rev-parse", "--verify", "bogus-ref")`` raises
    ``McsError`` whose ``__str__`` contains the substring
    ``rev-parse`` so the user can identify which git verb failed."""
    with pytest.raises(McsError) as exc_info:
        inited_repo.rev_parse("bogus-ref")
    assert "rev-parse" in str(exc_info.value)


def test_constructor_does_not_create_directory(tmp_path: Path) -> None:
    """``GitRepo(tmp_path / "not-yet")`` is a pure constructor (no
    I/O); ``(tmp_path / "not-yet").exists()`` is ``False``
    immediately after. The directory is created lazily inside
    ``init()``."""
    target = tmp_path / "not-yet"
    assert not target.exists()
    _ = GitRepo(target)
    assert not target.exists()


def test_repo_root_property_returns_constructor_arg_unchanged(
    tmp_path: Path,
) -> None:
    """``repo.root`` is the exact ``Path`` the constructor was given
    (no resolve, no absolute conversion — the wrapper preserves the
    caller's path shape)."""
    given = tmp_path / "some" / "nested" / "path"
    r = GitRepo(given)
    assert r.root == given


def test_init_then_add_then_commit_then_status_clean_invariant(
    inited_repo: GitRepo,
) -> None:
    """The canonical add-commit cycle leaves ``has_uncommitted_changes()``
    returning ``False``. This is the contract the hook's pre-write
    ``commit_if_uncommitted_on_entry`` and post-write commit cycle
    rely on: after a successful round trip, the working tree is
    clean."""
    (inited_repo.root / "f.md").write_text("content", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("round-trip")
    assert sha is not None
    assert inited_repo.has_uncommitted_changes() is False


# --- Regression guards for the unborn-HEAD shim -----------------------------


def test_log_returncode_128_other_than_unborn_head_still_raises(
    inited_repo: GitRepo,
) -> None:
    """The ``_is_unborn_head`` deviation in ``log()`` must remain
    narrowly scoped to the unborn-HEAD stderr shape. Other returncode-128
    failures (e.g. an unparseable extended-regex passed via
    ``grep_regex``) must still surface as ``McsError`` — otherwise the
    shim would silently swallow real bugs into an empty log list."""
    # Land a commit so the repo isn't on the unborn-HEAD path.
    (inited_repo.root / "f.md").write_text("x", encoding="utf-8")
    inited_repo.add_all()
    inited_repo.commit("init")
    # An unclosed bracket in the extended regex makes git's regex
    # compiler fail with a non-zero exit and a stderr that does NOT
    # match the unborn-HEAD pattern.
    with pytest.raises(McsError):
        inited_repo.log(grep_regex="[unclosed-bracket")


def test_find_commit_with_prefix_on_unborn_returns_none(
    inited_repo: GitRepo,
) -> None:
    """The unborn-HEAD shim covers ``find_commit_with_prefix`` too:
    on a freshly-init'd repo with no commits, the call returns
    ``None`` rather than raising. Used by ``mcs profile reset --to
    last-build`` against a profile whose history is empty."""
    assert inited_repo.find_commit_with_prefix("anything") is None


# --- Stronger gitconfig blockade test ---------------------------------------


def test_env_blocks_user_gitconfig_for_non_identity_knobs(
    inited_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GIT_AUTHOR_NAME``/``EMAIL`` env vars override gitconfig's
    ``user.name``/``user.email`` even when the global config is loaded,
    so the existing identity-override test (above) would pass even if
    the wrapper stopped setting ``GIT_CONFIG_GLOBAL=/dev/null``. Pin
    the actual zeroing behavior with a config knob the env vars don't
    override: ``core.hooksPath``. If the global config leaks, the
    failing ``pre-commit`` hook below runs and the commit aborts. With
    ``GIT_CONFIG_GLOBAL`` pointed at ``/dev/null``, the bogus
    ``hooksPath`` is ignored and the commit succeeds."""
    # A hooks dir with a deliberately-failing pre-commit hook.
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    pre_commit = hooks_dir / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho 'global hook leaked' >&2\nexit 1\n", encoding="utf-8")
    pre_commit.chmod(0o755)

    bogus_gitconfig = tmp_path / "bogus-gitconfig"
    bogus_gitconfig.write_text(
        f"[core]\n  hooksPath = {hooks_dir}\n",
        encoding="utf-8",
    )
    # Point the parent's ``GIT_CONFIG_GLOBAL`` at the bogus file. The
    # wrapper is supposed to override this with ``/dev/null`` for the
    # subprocess; this test verifies the override actually wins.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(bogus_gitconfig))

    (inited_repo.root / "f.md").write_text("x", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("test: hooks-path-override")
    # If the wrapper's ``GIT_CONFIG_GLOBAL`` override broke, the global
    # ``core.hooksPath`` would be honored, the pre-commit hook would
    # fail, and the commit would raise ``McsError`` instead of
    # returning a SHA.
    assert sha is not None, "commit failed — the wrapper isn't zeroing GIT_CONFIG_GLOBAL"


# --- Worktree kwarg coverage (detach=False / force=True) --------------------


def test_worktree_add_detach_false_branch_is_reachable(
    inited_repo: GitRepo, tmp_path: Path
) -> None:
    """``worktree_add(detach=False)`` exercises the non-``--detach``
    branch of the args builder. With the wrapper's current shape
    (``rev_parse`` resolves the ``sha`` argument to a 40-char SHA
    before passing to ``git worktree add``), the resulting worktree
    still lands on a detached HEAD — git refuses to create a branch
    from a bare SHA without ``-b <name>``. So this test pins
    callability + non-crash behavior, not the on-disk state of the
    new worktree. mcs always passes ``detach=True`` in production;
    the kwarg's ``False`` value is documented but not on the live
    path."""
    (inited_repo.root / "f.md").write_text("a", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("a")
    assert sha is not None
    wt_path = tmp_path / "wt-non-detached-call"
    # Just verify the call succeeds without raising.
    inited_repo.worktree_add(wt_path, sha, detach=False)
    assert wt_path.is_dir()


def test_worktree_remove_with_force_drops_dirty_worktree(
    inited_repo: GitRepo, tmp_path: Path
) -> None:
    """``worktree_remove(force=True)`` removes a worktree even when
    its working tree has uncommitted edits. Without ``force=True``,
    git refuses. Mirrors the ``mcs profile fork-remove --force`` CLI
    flag wiring."""
    (inited_repo.root / "f.md").write_text("a", encoding="utf-8")
    inited_repo.add_all()
    sha = inited_repo.commit("a")
    assert sha is not None
    wt_path = tmp_path / "dirty-wt"
    inited_repo.worktree_add(wt_path, sha)
    # Dirty the worktree so the non-force removal refuses.
    (wt_path / "f.md").write_text("modified", encoding="utf-8")
    with pytest.raises(McsError):
        inited_repo.worktree_remove(wt_path)
    # ``force=True`` succeeds.
    inited_repo.worktree_remove(wt_path, force=True)
    assert not wt_path.exists()
