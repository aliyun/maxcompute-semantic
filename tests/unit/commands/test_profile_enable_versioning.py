"""mcs profile enable-versioning — the explicit upgrade entry point (T7).

The auto-commit hook (T5) auto-initializes a per-profile ``.git/``
the first time any write verb (``mcs build`` / the proposal workflow /
``mcs memory verify``) touches a legacy data directory that
predates the git-versioning feature. The ``mcs profile enable-
versioning`` subcommand wired here is the explicit user-facing
form of the same upgrade: it lets the user perform the migration
as a named step visible in the shell history rather than as the
side-effect of the next incidental write.

The subcommand's contract is documented in the docstring on
``commands.profile.enable_versioning_cmd``. These tests pin the
five contract surfaces — legacy upgrade happy path, idempotent
re-run, fork-kind no-op-with-hint, env-knob short-circuit, and
the ``ProfileNotFoundError`` propagation for a missing
``--profile`` argument — plus five additional cases (resolver-
chain fallback, help-text registration in two places, empty
data dir, and the missing-``git``-binary failure mode).

The fixture is the project-wide ``isolated_config`` shared with
the T6 ``test_profile_create_versioning.py`` sibling so the
two T-stack tasks share the same XDG-isolation contract; tests
that also need ``MCS_NO_VERSIONING`` cleared do that explicitly
via ``monkeypatch.delenv`` at the head of the test, matching the
T6 file's existing convention.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import (
    profile_data_dir,
    profile_git_dir,
    profile_gitignore_path,
)
from maxcompute_semantic.auth.profile_store import get as get_profile
from maxcompute_semantic.auth.profile_store import upsert as upsert_profile
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.commands.profile import profile_group
from maxcompute_semantic.versioning.errors import GitNotAvailable
from maxcompute_semantic.versioning.git_repo import GitRepo

# Module-level skip when the ``git`` binary isn't on PATH — the
# happy-path tests assert on the on-disk repo shape, which only
# exists when ``git`` is available. The deliberate-PATH-strip
# additional case at the bottom of the file is the exception and
# sets ``PATH=""`` itself via ``monkeypatch``.
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile git versioning requires the ``git`` binary on PATH",
)


# --- helpers ----------------------------------------------------------------


def _seed_legacy_profile(name: str, *, with_files: bool = True) -> Profile:
    """Construct a pre-versioning ("legacy") profile: write the
    ``profiles.yaml`` entry via ``profile_store.upsert``, then
    optionally drop two example data files into the per-profile
    data dir so the inaugural commit's tree captures something
    other than the canonical ``.gitignore``. No ``.git/`` is
    created at seed time — the upgrade verb's job is to land
    that.

    The two example files (``_overview.md`` and ``_state.json``)
    are chosen to match the path-whitelist that ``GitRepo.show``
    applies to its diff output (``*.md`` and ``*.json`` are the
    documented include-list along with ``package.sql`` and
    ``.gitignore``), so the happy-path test's "the seeded files
    appear in the inaugural commit's diff" assertion via
    ``repo.show(head_sha)`` sees them rather than the path-
    whitelist hiding them.

    The ``with_files=False`` mode exercises the empty-data-dir
    corner case where the auto-init branch's only committed entry
    is the canonical ``.gitignore`` (the
    ``test_enable_versioning_on_empty_data_dir_still_uses_the_
    canonical_inaugural_subject`` additional case below pins this
    branch).

    The profile shape mirrors the spec's verbatim ``_seed_legacy_
    profile`` helper at plan lines 4601-4624: an AkAuth pair with
    placeholder literal AK strings (which the upsert path's auth-
    test step skips on the no-test default), a single ``default``-
    schema DataSource over the ``compute_project``, no
    ``package_path`` override so the standard XDG slot under
    ``MCS_DATA_DIR`` applies.
    """
    profile = Profile(
        name=name,
        compute_project="proj_legacy",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=AkAuth(access_key_id="x", access_key_secret="y"),
        sources=(DataSource(project="proj_legacy", schema="default", tables="*"),),
    )
    upsert_profile(profile)
    if with_files:
        pdir = profile_data_dir(profile)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "_overview.md").write_text("# proj_legacy (legacy data)\n", encoding="utf-8")
        (pdir / "_state.json").write_text(
            '{"build_at": "2026-04-01T00:00:00Z"}\n', encoding="utf-8"
        )
    return profile


def _invoke(args: list[str], *, catch_exceptions: bool = False) -> object:
    """Drive the ``mcs profile enable-versioning`` verb through the
    top-level ``mcs_cli`` group (so ``ctx.obj`` is populated with
    the standard ``format`` and ``quiet`` slots the outer ``cli``
    callback sets — the ``_renderer`` helper handles the bare-
    profile-group invocation case too, but the top-level path is
    the canonical one the spec's verbatim assertions target).

    ``catch_exceptions=False`` is the default because four of the
    five primary tests assert on the runner's exit code and merged
    ``result.output`` (which in click 8.3 carries both stdout and
    stderr); the explicit-``True`` opt-in is the failure-path
    pattern that the missing-``git``-binary additional case uses
    to inspect ``result.exception`` directly, mirroring the T6
    file's ``test_profile_create_failure_does_not_leave_half_
    versioned_state`` shape.
    """
    runner = CliRunner()
    return runner.invoke(mcs_cli, args, catch_exceptions=catch_exceptions)


# --- primary tests (spec lines 4627-4779) -----------------------------------


def test_enable_versioning_on_legacy_profile_creates_inaugural_commit(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end legacy upgrade. The seeded profile has its yaml
    entry plus two example files in the data directory but no
    ``.git/``. After ``mcs profile enable-versioning`` the
    on-disk state is: a single inaugural commit with the
    canonical ``init: import existing data`` subject, the
    canonical ``.gitignore`` with its three required substrings,
    and the two seeded files appearing in the inaugural commit's
    diff via ``GitRepo.show(head_sha)``.

    Matches the spec's verbatim test at plan lines 4627-4652.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("legacy-acme")
    # Pre-state: data dir exists (seeded by the upsert+touch dance
    # in ``_seed_legacy_profile``), but no ``.git/`` because the
    # T6-T8 wiring is the only thing that creates one and we're
    # the first write-touching invocation against this name.
    assert profile_data_dir(profile).is_dir()
    assert not profile_git_dir(profile).exists(), (
        f"seeded legacy profile {profile.name!r} unexpectedly has a "
        f".git/ at {profile_git_dir(profile)} — the seeding helper "
        f"isn't supposed to invoke the hook."
    )

    result = _invoke(["profile", "enable-versioning", "--profile", "legacy-acme"])
    assert result.exit_code == 0, (
        f"enable-versioning on a legacy profile should exit 0; got "
        f"exit_code={result.exit_code!r}, output={result.output!r}, "
        f"exception={getattr(result, 'exception', None)!r}."
    )
    # The success message announces the upgrade in the spec's
    # canonical phrasing — the "is now versioned" substring is the
    # load-bearing assertion the spec's verbatim form pins.
    assert "is now versioned" in result.output, (
        f"the upgrade message should announce the new versioned "
        f"state via the ``is now versioned`` phrase from the spec's "
        f"verbatim wording at plan line 4549; got result.output="
        f"{result.output!r}."
    )

    # Post-state: ``.git/`` and canonical ``.gitignore`` are both
    # in place.
    assert profile_git_dir(profile).is_dir(), (
        f"after enable-versioning the per-profile ``.git/`` "
        f"directory should exist at {profile_git_dir(profile)}; "
        f"the auto-init branch of the hook is responsible for "
        f"creating it."
    )
    gi_path = profile_gitignore_path(profile)
    assert gi_path.is_file(), (
        f"after enable-versioning the canonical ``.gitignore`` "
        f"should exist at {gi_path}; the auto-init branch of the "
        f"hook writes it from the ``PROFILE_GITIGNORE`` constant."
    )
    gi_body = gi_path.read_text(encoding="utf-8")
    # The three required substrings are documented in the spec's
    # "Files committed vs ignored" table and pinned identically in
    # the T6 sibling test (``test_profile_create_initializes_per_
    # profile_git_repo``).
    assert "package.db" in gi_body
    assert ".mcs-lock" in gi_body
    assert "tier_cache/" in gi_body

    # The log has exactly one commit and the subject is the
    # auto-init's hardcoded ``init: import existing data``. The
    # ``summary="import existing data"`` argument the verb passes
    # to ``commit_after_command`` matches the auto-init branch's
    # ``_INAUGURAL_COMMIT_SUMMARY`` literal so the would-be second
    # action-marker commit (the hook's step that writes a
    # follow-up commit when the action and summary differ from
    # the existing HEAD) byte-deterministic-short-circuits and
    # the log stays at exactly one entry.
    repo = GitRepo(profile_data_dir(profile))
    rows = repo.log(limit=None)
    assert len(rows) == 1, (
        f"the inaugural-commit-only invariant requires exactly one "
        f"commit in the log after a legacy upgrade; the hook's "
        f"byte-deterministic-dump short-circuit on the action-"
        f"marker step ensures the auto-init's ``init: import "
        f"existing data`` is the only entry. Got {len(rows)} "
        f"commits with messages {[c.message for c in rows]!r}."
    )
    head = rows[0]
    assert head.message == "init: import existing data", (
        f"the inaugural commit's subject should be the canonical "
        f"``init: import existing data`` string the auto-init "
        f"branch hardcodes; got {head.message!r}."
    )

    # The two seeded files appear in the inaugural commit's diff.
    # ``GitRepo.show`` applies a path-whitelist (``*.md``,
    # ``*.json``, ``package.sql``, ``.gitignore``) so the two
    # example files we wrote pass the filter and turn up in the
    # output.
    show_out = repo.show(head.full_sha)
    assert "_overview.md" in show_out, (
        f"the inaugural commit's diff (via ``GitRepo.show``) "
        f"should mention the seeded ``_overview.md`` file. The "
        f"first 500 chars of the output were: {show_out[:500]!r}."
    )
    assert "_state.json" in show_out, (
        f"the inaugural commit's diff (via ``GitRepo.show``) "
        f"should mention the seeded ``_state.json`` file. The "
        f"first 500 chars of the output were: {show_out[:500]!r}."
    )
    # The yaml-side profile entry is unchanged by the upgrade —
    # the verb's only on-disk side effect is the data-dir-rooted
    # ``.git/`` plus the canonical ``.gitignore``.
    saved = get_profile("legacy-acme")
    assert saved.name == "legacy-acme"
    assert saved.compute_project == "proj_legacy"


def test_enable_versioning_on_already_versioned_profile_is_idempotent(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive ``enable-versioning`` invocations against
    the same profile produce exactly one commit. The first lands
    the inaugural; the second sees the existing ``.git/`` and the
    matching action-prefix HEAD, so the hook's byte-deterministic
    short-circuit fires and ``commit_after_command`` returns
    ``None``. The terminal message on the second call names the
    existing HEAD's short SHA via the "already versioned at" /
    "no changes to commit" wording.

    Matches the spec's verbatim test at plan lines 4655-4695.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("idempotent-acme")

    first = _invoke(["profile", "enable-versioning", "--profile", "idempotent-acme"])
    assert first.exit_code == 0, (
        f"first enable-versioning call should succeed; got "
        f"exit_code={first.exit_code!r}, output={first.output!r}, "
        f"exception={getattr(first, 'exception', None)!r}."
    )
    assert "is now versioned" in first.output, (
        f"first call's terminal message should be the upgrade announcement; got {first.output!r}."
    )

    # The log has the single inaugural after the first call.
    repo = GitRepo(profile_data_dir(profile))
    rows_after_first = repo.log(limit=None)
    assert len(rows_after_first) == 1
    inaugural_short = rows_after_first[0].short_sha

    second = _invoke(["profile", "enable-versioning", "--profile", "idempotent-acme"])
    assert second.exit_code == 0, (
        f"second enable-versioning call (idempotent re-run) "
        f"should exit 0; got exit_code={second.exit_code!r}, "
        f"output={second.output!r}, exception="
        f"{getattr(second, 'exception', None)!r}."
    )
    # The terminal message on the second call is in the no-op
    # family. The spec's verbatim assertion at plan lines 4684-4687
    # accepts either of the two wording variants the implementer
    # might land on — "already versioned" (the named-the-HEAD form)
    # or "no changes to commit" (the generic form). The current
    # implementation emits the "already versioned at <sha>" variant
    # in the standard branch where the pre-existing-HEAD lookup
    # succeeded, so both substrings are typically present; the
    # ``or`` keeps the test robust against future wording
    # adjustments that drop one or the other.
    assert "already versioned" in second.output or "no changes to commit" in second.output, (
        f"the idempotent-re-run terminal message must mention "
        f"either ``already versioned`` or ``no changes to "
        f"commit``; got {second.output!r}."
    )
    # The HEAD's short SHA from the first call should appear in
    # the second call's "already versioned at" wording so the
    # user can verify the existing state explicitly.
    assert inaugural_short in second.output, (
        f"the second call's terminal message should name the "
        f"current HEAD's short SHA ({inaugural_short!r}) from "
        f"the first call. Got result.output={second.output!r}."
    )

    # Crucially, no second commit landed. The log is still the
    # one inaugural that the first call wrote.
    rows_after_second = repo.log(limit=None)
    assert len(rows_after_second) == 1, (
        f"a second enable-versioning call must not produce a "
        f"second commit; the log has {len(rows_after_second)} "
        f"commits with messages "
        f"{[c.message for c in rows_after_second]!r}. The byte-"
        f"deterministic-dump short-circuit in "
        f"``commit_after_command`` should compare the staged tree "
        f"against HEAD's tree (identical, since nothing changed "
        f"on disk between the two calls) and the action prefix "
        f"against HEAD's action prefix (both ``init``) and bail "
        f"without writing a new commit."
    )
    # The HEAD object is the same one from the first call.
    assert rows_after_second[0].full_sha == rows_after_first[0].full_sha, (
        f"the HEAD SHA shouldn't change across an idempotent "
        f"re-run. After the first call HEAD was "
        f"{rows_after_first[0].full_sha!r}; after the second "
        f"call HEAD is {rows_after_second[0].full_sha!r}."
    )


def test_enable_versioning_on_fork_kind_profile_points_at_parent(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``kind="fork"`` profile shares its parent's ``.git/`` via
    a detached worktree. Running ``enable-versioning`` against
    the fork's name prints a hint naming the parent (so the user
    knows where the real history is anchored) and exits 0
    without creating a separate ``.git/`` under the fork's
    would-be worktree path.

    The parent has to be upgraded first so its HEAD SHA is a
    real 40-hex string that the fork's ``git_sha`` field
    validator accepts. The fork yaml entry is hand-written via
    ``upsert`` because T14's ``mcs profile fork`` verb (which
    would normally do the worktree-creation side effect) is
    sequenced after T7 in the spec's task list.

    Matches the spec's verbatim test at plan lines 4698-4744.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)

    # Upgrade the parent first so it has an actual inaugural
    # commit whose SHA the fork's ``git_sha`` field can name.
    parent = _seed_legacy_profile("parent-acme")
    parent_result = _invoke(["profile", "enable-versioning", "--profile", "parent-acme"])
    assert parent_result.exit_code == 0, (
        f"the parent's own upgrade should succeed before we "
        f"hand-craft the fork's yaml entry. The parent's exit "
        f"code was {parent_result.exit_code!r}, output "
        f"{parent_result.output!r}, exception "
        f"{getattr(parent_result, 'exception', None)!r}."
    )
    parent_pdir = profile_data_dir(parent)
    parent_repo = GitRepo(parent_pdir)
    parent_head_sha = parent_repo.rev_parse("HEAD")
    # ``rev_parse`` returns the 40-hex full SHA which the
    # Profile.validate() ``_GIT_SHA_RE.fullmatch`` check
    # requires for ``kind="fork"`` entries.
    assert len(parent_head_sha) == 40, (
        f"the parent repo's HEAD rev-parse should return a "
        f"40-char hex SHA; got {parent_head_sha!r}."
    )

    # Hand-craft the fork's yaml entry. The fork's worktree-path
    # lives outside the parent's data dir to mirror the layout
    # T14 will write — a peer directory under the data root.
    # The fork's name uses the ``parent@fork`` convention the
    # ``_NAME_RE`` regex admits (``@`` is in the body-character
    # set).
    fork_wt = isolated_config / "fork-worktree"
    fork_wt.mkdir(parents=True, exist_ok=True)
    fork = Profile(
        name="parent-acme@fork",
        compute_project=parent.compute_project,
        endpoint=parent.endpoint,
        auth=parent.auth,
        sources=parent.sources,
        package_path=fork_wt,
        kind="fork",
        parent_profile=parent.name,
        git_sha=parent_head_sha,
    )
    upsert_profile(fork)

    # Run the verb against the fork's name.
    result = _invoke(["profile", "enable-versioning", "--profile", "parent-acme@fork"])
    assert result.exit_code == 0, (
        f"the fork-kind no-op-with-hint branch should exit 0 "
        f"cleanly; got exit_code={result.exit_code!r}, output "
        f"{result.output!r}, exception "
        f"{getattr(result, 'exception', None)!r}."
    )
    # The hint mentions the "fork" word (the spec's verbatim
    # case-insensitive substring check at plan line 4740 is the
    # canonical form) and names the parent profile's bare name
    # so the user can paste it into a follow-up
    # ``--profile <parent>`` invocation.
    assert "fork" in result.output.lower(), (
        f"the fork-kind hint message should contain the word "
        f"``fork`` (case-insensitive); got result.output="
        f"{result.output!r}."
    )
    assert "parent-acme" in result.output, (
        f"the fork-kind hint should name the parent profile "
        f"(``parent-acme``) so the user can target the parent's "
        f"real ``.git/`` via a follow-up invocation. Got "
        f"result.output={result.output!r}."
    )

    # The fork's worktree path has *no* ``.git/`` of its own —
    # the verb's fork-branch exits before reaching the auto-init
    # step. (The fork would normally see ``<wt>/.git`` as a
    # ``gitdir: ...`` regular file pointing into the parent's
    # ``.git/worktrees/<fork-name>/`` admin directory once T14
    # has wired the ``git worktree add`` step, but T14 hasn't
    # shipped yet and T7's fork branch correctly does nothing.)
    assert not (fork_wt / ".git").exists(), (
        f"the fork-kind hint branch must not create a "
        f"``.git/`` under the fork's worktree path "
        f"({fork_wt / '.git'}). The parent's repo at "
        f"{parent_pdir / '.git'} is the actual versioned root."
    )
    # The parent's commit count is still 1 — the fork-branch
    # shouldn't have touched the parent either.
    assert len(parent_repo.log(limit=None)) == 1


def test_enable_versioning_with_env_disabled_exits_zero_without_init(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``MCS_NO_VERSIONING=1`` set, the env-knob short-
    circuit at the top of the verb body fires before the hook
    is reached. The terminal message names the env var so the
    user knows why nothing happened. No ``.git/`` is created
    under the profile's data dir.

    Matches the spec's verbatim test at plan lines 4747-4762.
    """
    profile = _seed_legacy_profile("eval-mode-legacy")
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")

    result = _invoke(["profile", "enable-versioning", "--profile", "eval-mode-legacy"])
    assert result.exit_code == 0, (
        f"the env-disabled branch should exit 0 cleanly; got "
        f"exit_code={result.exit_code!r}, output={result.output!r}, "
        f"exception={getattr(result, 'exception', None)!r}."
    )
    assert "MCS_NO_VERSIONING" in result.output, (
        f"the env-disabled message should name the offending "
        f"env var so the user knows what to unset; got "
        f"result.output={result.output!r}."
    )
    # No ``.git/`` was created — the env-knob branch returns
    # before any disk-touching step. The data dir itself may
    # exist (the ``_seed_legacy_profile`` helper created it),
    # but the ``.git/`` subdirectory is not there.
    assert not profile_git_dir(profile).exists(), (
        f"the env-knob short-circuit must not create a ``.git/`` "
        f"under {profile_data_dir(profile)}. Found one at "
        f"{profile_git_dir(profile)} — the env check ordering "
        f"may have regressed."
    )
    # The canonical ``.gitignore`` is also not written — the
    # auto-init branch's gitignore-write step is gated by the
    # same env check (the hook's step 1 in ``versioning/hook.py``
    # fires before the gitignore touch).
    assert not profile_gitignore_path(profile).exists(), (
        f"the env-knob short-circuit must not write a "
        f"``.gitignore``. Found one at "
        f"{profile_gitignore_path(profile)} which means the env "
        f"check was skipped or the gitignore step ran before "
        f"the env check."
    )


def test_enable_versioning_for_nonexistent_profile_errors_with_remediation(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``--profile NAME`` where ``NAME`` is not in
    ``profiles.yaml`` raises the existing
    ``ProfileNotFoundError`` (from ``auth.profile_store.get``),
    which the verb's resolver-error wrapper renders to stderr
    via ``Renderer.error`` and exits with the McsError's
    canonical ``exit_code=3``. The remediation text the error's
    constructor carries ("run ``mcs profile list`` to see
    available profiles") shows up in the runner's merged
    ``result.output`` because click 8.3's ``CliRunner.invoke``
    presents ``output`` as the combined stdout+stderr stream.

    Matches the spec's verbatim test at plan lines 4765-4779.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    monkeypatch.delenv("MCS_PROFILE", raising=False)
    # No seed — the named profile does not exist.

    result = _invoke(["profile", "enable-versioning", "--profile", "ghost-profile"])
    assert result.exit_code != 0, (
        f"a nonexistent ``--profile`` should produce a non-zero "
        f"exit code. Got exit_code=0 with output "
        f"{result.output!r}."
    )
    # ``ProfileNotFoundError`` carries ``exit_code=3`` per the
    # auth/errors.py ClassVar, which the wrap-and-sys.exit
    # shape in the verb body propagates verbatim.
    assert result.exit_code == 3, (
        f"``ProfileNotFoundError`` should map to the canonical "
        f"``exit_code=3`` (the McsError ClassVar in "
        f"``auth/errors.py``). Got exit_code={result.exit_code!r}."
    )
    # The offending name appears in the error's message string
    # ("profile 'ghost-profile' not found" — see the raise site
    # in ``auth/profile_store.py:get``).
    assert "ghost-profile" in result.output, (
        f"the error message should name the offending profile "
        f"identifier (``ghost-profile``). Got result.output="
        f"{result.output!r}."
    )
    # The remediation text from the existing error class points
    # at the discovery verb the user runs to see what's actually
    # configured.
    assert "mcs profile list" in result.output, (
        f"the ``ProfileNotFoundError``'s baked-in remediation "
        f"text should mention the canonical ``mcs profile list`` "
        f"discovery verb. Got result.output={result.output!r}."
    )


# --- additional cases (spec lines 4782-4788) --------------------------------


def test_enable_versioning_without_profile_flag_uses_resolver_chain(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standard ``--profile`` / ``MCS_PROFILE`` / cwd-link /
    env-vars resolution chain in
    ``_resolve_profile_for_project`` applies when the
    ``--profile`` flag is omitted. With ``MCS_PROFILE=<name>``
    set in the environment, the chain picks that named profile
    and the upgrade lands on it.

    The documented chain is in the project root ``CLAUDE.md``
    under "Profile auto-resolution chain"; the helper itself is
    at ``commands/profile.py:_resolve_profile_for_project``.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("env-resolved")
    # Point the resolver at the seeded profile via the env-var
    # slot of the resolution chain.
    monkeypatch.setenv("MCS_PROFILE", "env-resolved")

    # No ``--profile`` flag — the chain has to pick the profile.
    result = _invoke(["profile", "enable-versioning"])
    assert result.exit_code == 0, (
        f"the chain-resolved invocation should succeed when "
        f"``MCS_PROFILE`` names a real profile; got exit_code="
        f"{result.exit_code!r}, output={result.output!r}, "
        f"exception={getattr(result, 'exception', None)!r}."
    )
    # The terminal message names the resolved profile by name —
    # the verb pulls the name off the resolved Profile object,
    # so the user sees which profile got upgraded even when
    # the selection was implicit.
    assert "env-resolved" in result.output, (
        f"the terminal message should name the resolved "
        f"profile (``env-resolved``) so the implicit selection "
        f"is visible. Got result.output={result.output!r}."
    )
    # And the on-disk effect lands on that profile specifically.
    assert profile_git_dir(profile).is_dir(), (
        f"the chain-resolved profile's data dir should have "
        f"the new ``.git/`` after the upgrade. Looked at "
        f"{profile_git_dir(profile)}."
    )
    repo = GitRepo(profile_data_dir(profile))
    assert len(repo.log(limit=None)) == 1


def test_enable_versioning_appears_in_profile_group_help(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcs profile --help`` should list the new
    ``enable-versioning`` subcommand. Regression check on the
    click-group command registration: the
    ``@profile_group.command("enable-versioning")`` decorator
    is what plumbs the verb into the group's listing, and if
    the decorator is forgotten or mis-named the verb is
    invisible to the user.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    # Drive the bare ``profile_group`` directly (rather than
    # ``mcs_cli`` then ``profile``) so the help text we read
    # is the group's own listing without the outer top-level
    # ``mcs`` framing. Either entry point would surface the
    # subcommand name; the group-direct form keeps the
    # assertion focused.
    runner = CliRunner()
    result = runner.invoke(profile_group, ["--help"], catch_exceptions=False)
    assert result.exit_code == 0, (
        f"``mcs profile --help`` should exit 0; got exit_code="
        f"{result.exit_code!r}, output={result.output!r}."
    )
    assert "enable-versioning" in result.output, (
        f"the ``mcs profile`` group's help output should list "
        f"the new ``enable-versioning`` subcommand in the "
        f"commands section. Got result.output={result.output!r}."
    )


def test_enable_versioning_visible_via_top_level_mcs_help_two_step(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcs --help`` lists the ``profile`` subgroup (one of
    the entries in ``cli.py``'s ``_COMMAND_ORDER`` list) and
    ``mcs profile --help`` lists ``enable-versioning``. The
    top-level ``mcs --help`` itself doesn't enumerate every
    subgroup's verbs — that's click's standard two-level
    discovery convention — so the test asserts the group is
    visible from the top and the verb is visible from inside
    the group.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    runner = CliRunner()
    top = runner.invoke(mcs_cli, ["--help"], catch_exceptions=False)
    assert top.exit_code == 0
    assert "profile" in top.output, (
        f"the top-level ``mcs --help`` should list the "
        f"``profile`` subgroup. Got top.output={top.output!r}."
    )
    # The top-level help does not enumerate the group's
    # subcommands — clicking down one level is the canonical
    # discovery path for the new verb.
    group = runner.invoke(mcs_cli, ["profile", "--help"], catch_exceptions=False)
    assert group.exit_code == 0
    assert "enable-versioning" in group.output, (
        f"the ``mcs profile --help`` listing reached via the "
        f"top-level CLI dispatch should include "
        f"``enable-versioning``. Got group.output={group.output!r}."
    )


def test_enable_versioning_on_empty_data_dir_still_uses_canonical_subject(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty-data-dir corner case: the profile's yaml entry
    exists but no example files have been written. The
    auto-init branch creates the dir-plus-``.git/`` and the
    canonical ``.gitignore`` and commits that single file as
    the inaugural tree. The subject line is still the canonical
    ``init: import existing data`` regardless of whether the
    "existing data" was zero bytes or two files — the
    "existing data" phrasing is the auto-init branch's
    deliberate constant per the spec's plan-line-4787 design
    note.

    Verifies the in-code ``_INAUGURAL_COMMIT_SUMMARY`` from
    ``versioning/hook.py`` is the load-bearing source of the
    subject string and that the no-file branch doesn't take a
    different code path.

    Matches the spec's verbatim additional case at plan line
    4787.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("empty-dir-acme", with_files=False)
    # Sanity-check the no-files seeding: the data dir was not
    # created by the upsert (the ``profile_data_dir`` slot is
    # the XDG-layout name, but the actual directory is materialized
    # only when something writes into it). The auto-init branch
    # of the hook handles the mkdir itself before ``git init``.
    pdir = profile_data_dir(profile)
    if pdir.exists():
        # The shared ``isolated_config`` fixture's
        # ``MCS_DATA_DIR=tmp_path`` setup may have caused the
        # path to spring into existence as a result of the
        # ``upsert``'s yaml-side write (which doesn't itself
        # mkdir the data slot, but the ``profile_data_dir(name)``
        # call may have any number of caching side effects in
        # the resolver). What we care about is that no ``.git/``
        # exists yet.
        assert not (pdir / ".git").exists(), (
            f"seeded empty-dir profile should not have a "
            f".git/ before the verb runs; found {pdir / '.git'}."
        )

    result = _invoke(["profile", "enable-versioning", "--profile", "empty-dir-acme"])
    assert result.exit_code == 0, (
        f"empty-data-dir upgrade should still succeed; got "
        f"exit_code={result.exit_code!r}, output={result.output!r}, "
        f"exception={getattr(result, 'exception', None)!r}."
    )
    # The inaugural commit's subject is the canonical literal
    # even when the data dir was empty pre-upgrade.
    repo = GitRepo(profile_data_dir(profile))
    rows = repo.log(limit=None)
    assert len(rows) == 1, (
        f"empty-data-dir upgrade should still produce exactly "
        f"one inaugural commit. Got {len(rows)}: "
        f"{[c.message for c in rows]!r}."
    )
    assert rows[0].message == "init: import existing data", (
        f"the empty-data-dir branch should land the same "
        f"canonical ``init: import existing data`` subject as "
        f"the populated-dir case — the auto-init branch's "
        f"hardcoded ``_INAUGURAL_COMMIT_SUMMARY`` covers both "
        f"shapes. Got subject={rows[0].message!r}."
    )
    # The committed tree contains the canonical ``.gitignore``
    # (the only file the auto-init branch is contractually
    # required to write into an otherwise-empty data dir). We
    # inspect the index rather than ``git show`` because
    # ``show``'s pathspec whitelist (``*.md``, ``*.json``,
    # ``package.sql``, ``.gitignore``) admits the gitignore so
    # either view would catch it; ``ls-tree`` is the most
    # direct evidence and matches the standard
    # "the committed tree contains exactly the gitignore"
    # shape.
    ls_tree = subprocess.run(
        ["git", "-C", str(profile_data_dir(profile)), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    assert ".gitignore" in ls_tree, (
        f"the empty-data-dir inaugural tree should contain the "
        f"canonical ``.gitignore`` the auto-init branch writes. "
        f"``git ls-tree --name-only HEAD`` returned: {ls_tree!r}."
    )


def test_enable_versioning_when_git_binary_missing_surfaces_gitnotavailable(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``git`` isn't on PATH the wrapper's first subprocess
    call raises ``FileNotFoundError`` from the OS layer, which
    the wrapper at ``versioning/git_repo.py``'s ``_raw`` catches
    and re-raises as ``GitNotAvailable`` (an ``McsError``
    subclass) carrying the spec's two-pronged remediation hint:
    install ``git`` via the platform package manager OR set
    ``MCS_NO_VERSIONING=1`` to opt out of versioning entirely.

    Because the verb's resolver-error wrap doesn't cover the
    hook's exceptions (the hook is called outside the try/except
    block on the resolver, per the implementation), the
    ``GitNotAvailable`` propagates out of the click body and is
    stuffed into ``result.exception`` by the runner's standard
    ``catch_exceptions=True`` machinery. That's the same shape
    the T6 sibling test
    ``test_profile_create_failure_does_not_leave_half_versioned_state``
    pins for the create-time analogue.

    Matches the spec's verbatim additional case at plan line
    4788.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("no-git-acme")
    # The empty PATH ensures that ``shutil.which("git")`` returns
    # ``None`` (which is what the module-level skip predicate on
    # this file consults) *and* that the wrapper's
    # ``subprocess.run(["git", ...], ...)`` invocation raises
    # ``FileNotFoundError`` (which the wrapper's ``_raw`` catches
    # and converts to ``GitNotAvailable``). The module-level
    # ``pytestmark`` skip predicate is evaluated at collection
    # time against the real environment's PATH, so this
    # in-test ``monkeypatch.setenv("PATH", "")`` is a no-op for
    # the collection-time skip decision but the test-runtime
    # subprocess sees the empty PATH and produces the
    # ``GitNotAvailable``.
    monkeypatch.setenv("PATH", "")

    # ``catch_exceptions=True`` (the runner's default) so the
    # propagated ``GitNotAvailable`` lands in ``result.exception``
    # rather than escaping the runner. We can't use
    # ``catch_exceptions=False`` here — the verb's
    # ``commit_after_command`` call is *outside* the
    # try/except-McsError block that wraps the resolver, by
    # design (so the standard mcs console-script wrapper's
    # outer envelope at ``cli.py:main`` is the canonical
    # renderer for hook-side errors, mirroring the T6 file's
    # pattern).
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["profile", "enable-versioning", "--profile", "no-git-acme"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0, (
        f"the missing-``git``-binary path should exit non-zero; "
        f"got exit_code=0 with output={result.output!r}."
    )
    exc = result.exception
    assert isinstance(exc, GitNotAvailable), (
        f"the propagated exception should be an instance of "
        f"``versioning.errors.GitNotAvailable``; got "
        f"{type(exc).__name__}: {exc!r}."
    )
    rem = (exc.remediation or "").lower()
    assert "mcs_no_versioning" in rem, (
        f"the ``GitNotAvailable`` remediation should mention "
        f"the ``MCS_NO_VERSIONING`` env-knob opt-out. Got "
        f"remediation={exc.remediation!r}."
    )
    assert "git" in rem, (
        f"the ``GitNotAvailable`` remediation should mention "
        f"installing the ``git`` binary. Got remediation="
        f"{exc.remediation!r}."
    )
    # No partial on-disk state: the wrapper's
    # ``FileNotFoundError`` happens on the very first subprocess
    # call (``git --version`` / ``git init`` depending on the
    # call site's first command), so the ``.git/`` directory is
    # not created and no canonical ``.gitignore`` is written.
    assert not profile_git_dir(profile).exists(), (
        f"a hook failure on the missing-``git``-binary path "
        f"should not leave a half-initialized ``.git/`` "
        f"behind. Found one at {profile_git_dir(profile)}."
    )
    assert not profile_gitignore_path(profile).exists(), (
        f"a hook failure on the missing-``git``-binary path "
        f"should not leave a ``.gitignore`` behind either. "
        f"Found one at {profile_gitignore_path(profile)}."
    )
    # The yaml-side row is the seeded one — the verb's only
    # yaml interaction is the read-side ``get(name)`` in the
    # resolver, which succeeded. The on-disk profile entry
    # is intact so the user can re-run the verb after
    # installing ``git``.
    saved = get_profile("no-git-acme")
    assert saved.name == "no-git-acme"


# --- state-machine coverage (Cell 3 and Cell 4 of the verb's --------------
# --- four-cell post-hook decision table) ----------------------------------


def test_enable_versioning_recovers_from_interrupted_prior_init(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cell 3 of the post-hook decision table: pre-call state has
    a ``.git/`` directory but its log is empty (an interrupted
    prior ``git init`` that never landed an inaugural commit).
    The verb's fall-through-to-the-hook path closes the gap by
    landing the missing inaugural on top of the empty repository.

    The defensive arm exists because a hand-aborted prior run —
    or a crashed third-party tool that called ``git init`` in
    the data dir — could leave the repository in this half-
    initialized state, and the verb should heal it rather than
    silently no-op or refuse.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("interrupted-acme")
    pdir = profile_data_dir(profile)
    # Fabricate the interrupted state: hand-run ``git init`` in
    # the data dir without ever committing. The resulting
    # ``.git/`` directory has the standard layout (HEAD,
    # objects/, refs/) but no commits, so ``GitRepo.log()``
    # returns an empty list. This matches the shape a crashed
    # external tool or hand-aborted prior verb run would leave.
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=pdir,
        check=True,
        capture_output=True,
    )
    pre_repo = GitRepo(pdir)
    assert pre_repo.exists(), (
        f"after the hand-run ``git init``, ``GitRepo.exists()`` "
        f"should return True; ``.git/`` is at {pdir / '.git'}."
    )
    assert pre_repo.log(limit=1) == [], (
        f"the hand-run ``git init`` should leave the log empty; "
        f"got {pre_repo.log(limit=1)!r}. The Cell 3 state-machine "
        f"arm requires a populated ``.git/`` with no commits."
    )

    result = _invoke(["profile", "enable-versioning", "--profile", "interrupted-acme"])
    assert result.exit_code == 0, (
        f"the recovery arm should exit 0 cleanly; got exit_code="
        f"{result.exit_code!r}, output={result.output!r}, "
        f"exception={getattr(result, 'exception', None)!r}."
    )
    # The terminal message uses the "interrupted prior" wording
    # the Cell 3 branch of the verb body emits — distinct from
    # the canonical "is now versioned" of Cell 1 so the user
    # knows the verb closed a gap rather than performed a clean
    # initial upgrade.
    assert "interrupted prior" in result.output, (
        f"the recovery arm's terminal message should announce "
        f"the closed-gap state via the ``interrupted prior`` "
        f"phrase the Cell 3 branch emits; got result.output="
        f"{result.output!r}."
    )
    # The log is no longer empty — the fall-through-to-the-hook
    # path landed at least the inaugural and possibly an
    # action-marker commit on top.
    post_repo = GitRepo(pdir)
    rows_post = post_repo.log(limit=None)
    assert len(rows_post) >= 1, (
        f"after the recovery, the log should have at least one "
        f"commit; got {len(rows_post)} commits with messages "
        f"{[c.message for c in rows_post]!r}."
    )


def test_enable_versioning_advances_head_on_uncommitted_state(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cell 4 of the post-hook decision table: pre-call state has
    a populated ``.git/`` with a real HEAD, but the working tree
    has uncommitted changes since the last commit. The hook's
    recovery-snapshot branch writes a ``recover: pre-existing
    changes`` commit, advancing HEAD; the action-marker step
    then lands an ``init: import existing data`` marker on top
    (with ``allow_empty=True`` since the previous HEAD's action
    prefix is ``recover``, not ``init``). The verb sees
    ``head_changed=True`` post-hook and renders the
    recovery-snapshot announcement.

    Setup: seed legacy, run ``enable-versioning`` once to land
    the inaugural, then mutate a tracked file directly on disk
    to introduce uncommitted state, then re-run the verb.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    profile = _seed_legacy_profile("advancing-acme")

    first = _invoke(["profile", "enable-versioning", "--profile", "advancing-acme"])
    assert first.exit_code == 0, (
        f"the first call (canonical inaugural) should succeed; "
        f"got exit_code={first.exit_code!r}, output={first.output!r}, "
        f"exception={getattr(first, 'exception', None)!r}."
    )
    pdir = profile_data_dir(profile)
    repo = GitRepo(pdir)
    rows_after_first = repo.log(limit=None)
    assert len(rows_after_first) == 1, (
        f"after the first call the log should have exactly the "
        f"inaugural; got {len(rows_after_first)} commits."
    )
    pre_head_sha = rows_after_first[0].full_sha

    # Mutate a tracked file directly. This introduces an
    # uncommitted working-tree change against HEAD's tree, which
    # is what the hook's recovery-snapshot branch
    # (``commit_if_uncommitted_on_entry``) keys on.
    (pdir / "_overview.md").write_text(
        "# proj_legacy (mutated mid-flight)\n",
        encoding="utf-8",
    )

    second = _invoke(["profile", "enable-versioning", "--profile", "advancing-acme"])
    assert second.exit_code == 0, (
        f"the second call (recovery-snapshot arm) should exit 0 "
        f"cleanly; got exit_code={second.exit_code!r}, output="
        f"{second.output!r}, exception="
        f"{getattr(second, 'exception', None)!r}."
    )
    # The Cell 4 announcement names the recovery-snapshot
    # branch explicitly so the user understands the new commit's
    # provenance.
    assert "recovery-snapshot" in second.output, (
        f"the advanced arm's terminal message should name the "
        f"``recovery-snapshot`` branch the Cell 4 branch emits; "
        f"got result.output={second.output!r}."
    )
    # HEAD has advanced — the post-state's HEAD SHA is different
    # from the pre-state's, and the log grew.
    rows_after_second = repo.log(limit=None)
    assert len(rows_after_second) > len(rows_after_first), (
        f"the recovery-snapshot branch should grow the log; "
        f"pre={len(rows_after_first)}, post={len(rows_after_second)}, "
        f"messages={[c.message for c in rows_after_second]!r}."
    )
    post_head_sha = rows_after_second[0].full_sha
    assert post_head_sha != pre_head_sha, (
        f"HEAD should advance across the recovery-snapshot call; "
        f"pre={pre_head_sha!r}, post={post_head_sha!r}."
    )


# ``profile_group`` is imported above as the bare-group runner
# target for the ``mcs profile --help`` listing test. The
# top-level ``mcs_cli`` is the canonical dispatch entry point
# all the other primary tests drive through. Both surface the
# same ``enable_versioning_cmd`` body — the layered click-group
# composition in ``cli.py`` adds the ``profile`` subgroup to
# the root cli, and the standard click ``Group`` dispatcher
# routes the trailing argv. The two-step ``mcs --help`` /
# ``mcs profile --help`` test above is the regression check on
# that two-layer dispatch.
_ = profile_group
