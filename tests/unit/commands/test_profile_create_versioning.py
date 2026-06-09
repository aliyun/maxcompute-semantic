# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs profile create's versioning side effect (T6).

The end of every successful ``mcs profile create`` runs the auto-
commit hook with ``action=ACTION_INIT, summary=<profile-name>``.
The hook's auto-init-on-legacy branch (T5) handles the actual
``git init`` + ``.gitignore`` write + inaugural ``init: import
existing data`` commit. The byte-deterministic short-circuit then
collapses what would otherwise be a second ``init: <name>`` commit
into a no-op (see ``test_init_action_on_brand_new_profile_collapses_
to_inaugural_message`` in ``tests/unit/versioning/test_hook.py``
for the unit-level proof). Net effect: a brand-new profile's data
dir is a git repo whose log has exactly one commit
``init: import existing data``.

These tests drive the non-interactive ``--from-spec`` entry point
to avoid the interactive wizard's prompt-order fragility. The wizard
mode shares the same call-site tail (``upsert`` → ``r.success`` →
``commit_after_command``), so the contract is identical; the
``tests/integration/test_wizard_e2e.py`` tail-assertions cover the
wizard path end-to-end.

The fixture pinning that the spec talks about (the wizard's
``probe_and_capture_identity`` / ``get_tier`` / ``list_tables``
monkeypatches) is unneeded here because the ``--no-test`` flag on
``mcs profile create`` short-circuits the auth probe *and* the
auto-discovery of the compute project — the spec's stand-in
fixture for the wizard's network-touching prompts simply isn't
reached on this entry point.
"""

from __future__ import annotations

import json
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
from maxcompute_semantic.versioning.git_repo import GitRepo

# Skip the whole module if ``git`` isn't on PATH — the hook propagates
# ``GitNotAvailable`` in that case and the contract these tests pin
# is the on-disk shape of the per-profile repo, which only exists
# when ``git`` is available. The deliberate-PATH-strip test below
# (``test_profile_create_failure_does_not_leave_half_versioned_state``)
# is the exception — it asserts the *error* path and so it is
# parametrized to set PATH itself.
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="per-profile git versioning requires the ``git`` binary on PATH",
)


# --- helpers ----------------------------------------------------------------


def _canonical_spec(name: str, compute_project: str = "acme_proj") -> str:
    """The minimal valid full-profile spec, matching the shape that
    ``test_create_from_spec_inline_json`` uses in the sibling test
    file. We thread the name through so multiple tests can each ask
    for a uniquely-named profile without colliding."""
    return json.dumps(
        {
            "name": name,
            "compute_project": compute_project,
            "endpoint": "http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
            "auth": {
                "type": "ak",
                "access_key_id": "${env:MY_AK_ID}",
                "access_key_secret": "${env:MY_AK_SEC}",
            },
            "sources": [
                {"project": compute_project, "schema": "default", "tables": "*"},
            ],
        }
    )


def _invoke_create(spec: str, *, extra_args: tuple[str, ...] = ()) -> object:
    """Drive ``mcs profile create --from-spec <spec> --no-test`` via
    the CliRunner the way the existing ``test_create_from_spec_*``
    tests do."""
    runner = CliRunner()
    args = ["create", "--from-spec", spec, "--no-test", *extra_args]
    # ``catch_exceptions=False`` propagates an unhandled exception
    # (e.g. the wrapper's ``GitNotAvailable``) so the assertions can
    # see the exception type — without it the click harness swallows
    # the exception and only the ``result.exception`` attribute
    # carries it.
    return runner.invoke(profile_group, args, catch_exceptions=False)


# --- primary tests (per spec lines 4131-4375) -------------------------------


def test_profile_create_initializes_per_profile_git_repo(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a successful ``mcs profile create`` lands the
    profile in ``profiles.yaml``, materializes the data directory
    as a fresh git repo, writes the canonical ``.gitignore``, and
    the log has exactly one commit whose subject is ``init: import
    existing data``."""
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    name = "acme"
    spec = _canonical_spec(name)

    result = _invoke_create(spec)
    assert result.exit_code == 0, (
        f"create failed; stdout was:\n{result.output!r}\n"
        f"exception: {getattr(result, 'exception', None)!r}"
    )

    # Profile is persisted in profiles.yaml.
    saved = get_profile(name)
    assert saved.name == name
    assert saved.compute_project == "acme_proj"

    # The data dir is a git repo with the inaugural commit.
    pdir = profile_data_dir(saved)
    assert pdir.is_dir(), f"profile data dir not created at {pdir}"
    assert profile_git_dir(saved).is_dir(), (
        f"per-profile .git directory not created at {profile_git_dir(saved)}; "
        f"the auto-init branch of commit_after_command should have created it."
    )
    gi_path = profile_gitignore_path(saved)
    assert gi_path.is_file(), f"canonical .gitignore not written at {gi_path}"
    gi = gi_path.read_text(encoding="utf-8")
    # The canonical PROFILE_GITIGNORE body in
    # ``versioning/gitignore_default.py`` carries these three
    # untracked-pattern lines per the spec's "Files committed vs
    # ignored" table.
    assert "package.db" in gi
    assert ".mcs-lock" in gi
    assert "tier_cache/" in gi

    # The git log has exactly one commit and its subject is the
    # auto-init's hardcoded ``init: import existing data``. The
    # action=ACTION_INIT-with-summary=<name>-style commit that the
    # call site passes is short-circuited by the hook's byte-
    # deterministic-dump check on the second commit attempt — see
    # the analogue ``test_init_action_on_brand_new_profile_collapses_
    # to_inaugural_message`` in ``tests/unit/versioning/test_hook.py``
    # for the unit-level pinning of this collapse behavior.
    repo = GitRepo(pdir)
    rows = repo.log(limit=None)
    assert len(rows) == 1, (
        f"expected exactly one inaugural commit on a freshly-created "
        f"profile; got {len(rows)}: {[c.message for c in rows]!r}. "
        f"If a second ``init: {name}`` commit appeared, the hook's "
        f"byte-deterministic short-circuit isn't firing — the auto-"
        f"init branch in ``versioning/hook.py`` should land a single "
        f"``init: import existing data`` and then the explicit "
        f"action=init action-commit step should be a no-op because "
        f"the staged tree matches HEAD's tree and the action prefix "
        f"matches HEAD's action prefix (both ``init``)."
    )
    assert rows[0].message == "init: import existing data", (
        f"the inaugural commit's subject must be the literal "
        f"``init: import existing data`` string from the spec's "
        f"auto-init message convention; got {rows[0].message!r}."
    )


def test_profile_create_with_mcs_no_versioning_skips_git_init(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the eval harness sets ``MCS_NO_VERSIONING=1``, the
    create flow still writes the profile to ``profiles.yaml`` and
    creates the data directory's parent path, but the auto-init
    branch of the hook is short-circuited so no ``.git/`` is
    created and the lock-file step doesn't run either. This is the
    contract the eval-isolation layer in T20 depends on: every
    per-case sandbox profile under the eval-harness HOME is non-
    versioned, so the EX measurement matches the pre-feature
    baseline numbers in ``eval/baselines/``."""
    monkeypatch.setenv("MCS_NO_VERSIONING", "1")
    name = "evalcase"
    spec = _canonical_spec(name, compute_project="evalproj_0001")

    result = _invoke_create(spec)
    assert result.exit_code == 0, (
        f"create-under-MCS_NO_VERSIONING failed; output: {result.output!r}, "
        f"exception: {getattr(result, 'exception', None)!r}"
    )

    saved = get_profile(name)
    assert saved.name == name

    pdir = profile_data_dir(saved)
    # The .git directory was *not* created because the env short-
    # circuit at step 1 of ``commit_after_command`` fires before
    # the auto-init step.
    assert not profile_git_dir(saved).exists(), (
        f"MCS_NO_VERSIONING=1 should have prevented the auto-init "
        f"branch from running; .git/ exists at {profile_git_dir(saved)}."
    )
    # The lockfile is also absent — the lock-acquire step is gated
    # by the same env check (the env check fires *before* the lock
    # acquisition; see ``versioning/hook.py`` step 1 ordering).
    if pdir.exists():
        assert not (pdir / ".mcs-lock").exists(), (
            f"unexpected .mcs-lock under {pdir} — the env-disabled "
            f"branch should return before WriteLock is entered."
        )
    # ``.gitignore`` is the one file that *could* leak into a non-
    # versioned profile dir if the hook wrote it unconditionally
    # before checking the env knob. Confirm it doesn't.
    assert not profile_gitignore_path(saved).exists(), (
        f"unexpected .gitignore under {pdir} — the env-disabled "
        f"branch should never reach the gitignore-write step."
    )


def test_profile_create_succeeds_silently_when_git_missing(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When git is missing, ``mcs profile create`` succeeds with the
    auto-commit hook silently skipped — versioning is degraded but
    the profile is fully usable. The on-disk state is the profile
    yaml row (the upsert happened first) plus the absence of any
    ``.git/`` directory; a one-shot warning surfaces naming the
    install path and the ``MCS_NO_VERSIONING=1`` opt-out.

    The earlier contract was "loud-failure-with-MCS_NO_VERSIONING-
    hint" — but a hard fail here punished users for an absent
    optional dependency. Versioning being a soft-add (the user can
    install git later and run ``mcs profile enable-versioning
    <name>`` to retro-init) means the create-time hook's right
    behavior is silent skip + warn, matching the per-write hook
    in ``commit_after_command``.
    """
    name = "broken"
    spec = _canonical_spec(name, compute_project="ghost_proj")

    # Force the env probe to report git missing without breaking
    # subprocess resolution for other commands invoked along the way
    # (some indirect paths still call subprocess.run with a non-empty
    # PATH for unrelated tooling).
    monkeypatch.setattr(
        "maxcompute_semantic.versioning.env.shutil.which",
        lambda binary_name: None,
    )
    # Reset the warn-once latch so any in-test warning is observable.
    from maxcompute_semantic.versioning import env as env_mod

    monkeypatch.setattr(env_mod, "_git_missing_warned", False, raising=False)

    runner = CliRunner()
    result = runner.invoke(
        profile_group,
        ["create", "--from-spec", spec, "--no-test"],
        catch_exceptions=True,
    )

    # Silent tolerance — exit clean, no exception bubbling out.
    assert result.exit_code == 0, (
        f"expected clean exit when git is missing (soft-skip), got "
        f"exit_code={result.exit_code}; output: {result.output!r} "
        f"exception: {result.exception!r}"
    )
    # Profile was written via the upsert step.
    saved = get_profile(name)
    assert saved.name == name
    # No ``.git/`` because the hook short-circuited at the env probe.
    assert not profile_git_dir(saved).exists(), (
        f"git was missing, so no .git/ should have been created — "
        f"but found one at {profile_git_dir(saved)}."
    )


# --- additional cases (per spec lines 4377-4382) ----------------------------


def test_existing_named_profile_create_aborts_before_hook(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the spec's ``name`` collides with an already-existing
    profile in ``profiles.yaml``, ``cmd_profile_create``'s
    ``UsageError("already exists")`` path fires before any of the
    upsert / hook plumbing runs. The pre-existing profile's data
    dir state (whatever it is — possibly already a versioned repo
    from a prior create) is untouched.

    This is the negative case for "the hook fires on every
    successful create"; the failed create's side-effect surface is
    a click usage-error exit and nothing on the filesystem.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    # Pre-seed an existing profile with the colliding name. We use
    # the in-process ``upsert`` helper so the seeding doesn't itself
    # go through the create-time hook, which would also create a
    # ``.git/`` and complicate the "the existing profile's state is
    # untouched" assertion.
    existing = Profile(
        name="dup",
        compute_project="existing_proj",
        endpoint="http://service.cn-shanghai.maxcompute.aliyun-inc.com/api",
        auth=AkAuth(
            access_key_id="${env:OLD_AK}",
            access_key_secret="${env:OLD_SEC}",
        ),
        sources=(DataSource(project="existing_proj", schema="default", tables="*"),),
    )
    upsert_profile(existing)
    # The seeded profile's data dir is whatever's-on-disk-default —
    # ``upsert`` doesn't touch the data tree, only the yaml. The
    # pre-existing data dir has no ``.git/`` because the hook
    # didn't run for the seed. Confirm the baseline state.
    pre_existing_git_present = profile_git_dir(existing).exists()
    # Now attempt the colliding create.
    colliding_spec = _canonical_spec("dup", compute_project="ghost_corp")
    runner = CliRunner()
    result = runner.invoke(
        profile_group,
        ["create", "--from-spec", colliding_spec, "--no-test"],
        catch_exceptions=True,
    )
    # The click usage-error path exits non-zero (click's
    # ``UsageError`` maps to exit code 2 by default).
    assert result.exit_code != 0, (
        f"colliding create should have failed; got exit_code=0 with output {result.output!r}"
    )
    output_lower = result.output.lower()
    assert "already exists" in output_lower, (
        f"expected the click UsageError's 'already exists' wording; got output {result.output!r}"
    )
    # The hook did not run, so the existing profile's git-dir state
    # is unchanged from the pre-collision baseline. (If the hook
    # had silently fired despite the rejection, an unwanted ``.git/``
    # would have appeared.)
    post_state = profile_git_dir(existing).exists()
    assert post_state == pre_existing_git_present, (
        f"the existing profile's .git/ state changed across the "
        f"failed colliding create call — was {pre_existing_git_present}, "
        f"now {post_state}. The collision rejection should short-circuit "
        f"before the hook is reached so the pre-existing state is "
        f"untouched."
    )


def test_create_then_immediate_build_produces_expected_log(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per the spec's worked-example flow, the canonical sequence
    is ``mcs profile create`` followed by ``mcs build``, and the
    resulting log should read::

        init: import existing data
        build: <name> @ <ISO timestamp>

    in oldest-first order. T8's task is to wire the hook into the
    ``mcs build`` command itself — until that lands, this test
    pins the *post-create* half of the assertion, with a follow-up
    note that T8 will extend it to include the build commit on
    top.

    Keeping the assertion narrow to the create-side of the chain
    means a green-here-but-no-T8-yet repository state stays clean
    on this gate, and a later T8-or-revert that breaks the
    ``init:`` line lights this test red without dragging in the
    full build-side fixture machinery."""
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    name = "chained_test"
    spec = _canonical_spec(name)

    result = _invoke_create(spec)
    assert result.exit_code == 0, (
        f"create step of the chained sequence failed: {result.output!r}, "
        f"exception: {getattr(result, 'exception', None)!r}"
    )

    saved = get_profile(name)
    pdir = profile_data_dir(saved)
    repo = GitRepo(pdir)
    msgs = [c.message for c in reversed(repo.log(limit=None))]
    # T8 will append a second entry ``build: <name> @ <ISO>`` after
    # ``mcs build`` is invoked in this test. Until then, the create-
    # half of the chain is the only commit in the log.
    assert msgs == ["init: import existing data"], (
        f"expected the single inaugural commit after the create-half "
        f"of the chained sequence; got {msgs!r}. T8 will extend the "
        f"assertion to include the build commit on top once the "
        f"``mcs build`` write-path hook wiring lands."
    )


def test_profile_data_dir_is_the_git_working_tree_root(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-profile git repository's working tree root *is* the
    profile's data directory. The wrapper exposes the path via the
    ``GitRepo.root`` property, and the convention from T2 onwards
    is that ``GitRepo(profile_data_dir(p)).root == profile_data_dir(p)``
    bit-for-bit (no symlink resolution, no ``Path.resolve()`` —
    the wrapper just stores the path it was given).

    This is the layout invariant T2's wrapper documents in its
    constructor docstring and that T13's ``mcs profile reset`` and
    T14's ``mcs profile fork`` rely on to scope ``git -C`` and
    ``git worktree`` invocations to the right tree.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    name = "tree_root_check"
    spec = _canonical_spec(name)

    result = _invoke_create(spec)
    assert result.exit_code == 0, (
        f"create failed: {result.output!r}, exception {getattr(result, 'exception', None)!r}"
    )

    saved = get_profile(name)
    pdir = profile_data_dir(saved)
    repo = GitRepo(pdir)

    # The wrapper's stored root is exactly the path the call site
    # passed it. We compare via ``Path`` equality (which is byte-
    # for-byte string comparison after normalization) rather than
    # ``samefile`` because the spec is asking about path-string
    # equality, not filesystem-inode equality.
    assert repo.root == pdir, (
        f"GitRepo.root ({repo.root!r}) is not the profile_data_dir "
        f"({pdir!r}). The wrapper's constructor stores the path it "
        f"was given; if these diverge, every ``git -C`` subprocess "
        f"would target the wrong directory."
    )
    # Belt-and-braces: ``git rev-parse --show-toplevel`` against the
    # repo should return the same path. We bypass the wrapper for
    # the toplevel query so the assertion isn't a tautology of the
    # wrapper's own ``-C <self._root>`` argument. The env match
    # the wrapper would have used is preserved (so the test stays
    # hermetic on a CI box with ``GIT_DIR`` lying around in the
    # parent env).
    toplevel = subprocess.run(
        ["git", "-C", str(pdir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert Path(toplevel).resolve() == pdir.resolve(), (
        f"``git rev-parse --show-toplevel`` against the per-profile "
        f"repo returned {toplevel!r}; expected the profile_data_dir "
        f"{str(pdir)!r}. The two must agree because the wrapper's "
        f"``git init`` was invoked with the data dir as both ``-C`` "
        f"argument and ``init``'s working-directory cwd."
    )


def test_committer_identity_is_mcs_not_user_global(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper pins author and committer to ``mcs<mcs@local>``
    via env vars (``GIT_AUTHOR_NAME`` / ``EMAIL`` and the matching
    ``GIT_COMMITTER_*`` pair), and zeroes out the global config
    via ``GIT_CONFIG_GLOBAL=/dev/null``. The end-to-end version
    that goes through ``mcs profile create`` is here; the unit-
    level version that pokes ``GitRepo`` directly lives at
    ``tests/unit/versioning/test_git_repo.py::test_env_blocks_user_gitconfig``
    and is the canonical pattern this test mirrors.

    Setup: write a fake ``~/.gitconfig`` carrying a non-``mcs``
    ``[user]`` block, point ``GIT_CONFIG_GLOBAL`` at it (so a
    hypothetical wrapper that *didn't* override the env-var would
    pick up the fake identity), then run the create. The
    wrapper's ``_env()`` should win over the fake config because
    of the ``GIT_CONFIG_GLOBAL=/dev/null`` override layered on
    top of the inherited environment.

    Verification: read the inaugural commit's metadata via a
    fresh ``git log`` subprocess in an env that *does* respect
    ``GIT_CONFIG_GLOBAL`` (so the fake config would leak in if
    the wrapper hadn't pinned the identity at write time). The
    committed author / committer fields should be exactly the
    wrapper's ``mcs|mcs@local|mcs|mcs@local`` literal because
    the identity is baked into the commit object at write time.
    """
    monkeypatch.delenv("MCS_NO_VERSIONING", raising=False)
    # Fake gitconfig with a non-``mcs`` [user] block.
    fake_gitconfig = tmp_path / "fake-gitconfig"
    fake_gitconfig.write_text(
        "[user]\n    name = NotMcs\n    email = wrong@example.com\n[commit]\n    gpgsign = true\n",
        encoding="utf-8",
    )
    # Point the parent env's ``GIT_CONFIG_GLOBAL`` at the fake. The
    # wrapper's ``_env()`` overrides this to ``/dev/null``
    # specifically for the per-profile git subprocesses, so the
    # commit identity stays ``mcs``. The override is the contract
    # we're verifying.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_gitconfig))
    # Also clear any ambient ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*``
    # env vars from the parent. The wrapper *sets* these at
    # subprocess-spawn time via ``_FIXED_ENV_OVERRIDES``; we don't
    # want a stray ambient one in the parent shell to make the
    # assertion vacuous.
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)

    name = "ident_check"
    spec = _canonical_spec(name)
    result = _invoke_create(spec)
    assert result.exit_code == 0, (
        f"create failed during the identity-pinning test: "
        f"{result.output!r}, exception "
        f"{getattr(result, 'exception', None)!r}"
    )

    saved = get_profile(name)
    pdir = profile_data_dir(saved)

    # Read the metadata of the single committed commit. The env we
    # pass to the verification subprocess is the *parent* env
    # (which has ``GIT_CONFIG_GLOBAL`` pointed at the fake config)
    # minus the ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` overrides
    # — so if the wrapper *hadn't* pinned the identity at commit
    # time, the fake config's ``NotMcs`` would be what the
    # subprocess sees. The fact that the verification subprocess
    # is reading a *committed* commit means the identity was
    # already baked in at write time, and the fake config has no
    # effect on a read of an existing commit object. Both layers
    # of the wrapper's defense — ``GIT_*_NAME``/``EMAIL`` env-var
    # overrides plus ``GIT_CONFIG_GLOBAL=/dev/null`` — are
    # exercised: the env vars set the identity, the GLOBAL=null
    # ensures the fake's ``commit.gpgsign = true`` doesn't break
    # the commit by demanding a GPG signature.
    env_for_check = {k: v for k, v in __import__("os").environ.items()}
    env_for_check.pop("GIT_AUTHOR_NAME", None)
    env_for_check.pop("GIT_AUTHOR_EMAIL", None)
    env_for_check.pop("GIT_COMMITTER_NAME", None)
    env_for_check.pop("GIT_COMMITTER_EMAIL", None)
    out = subprocess.run(
        [
            "git",
            "-C",
            str(pdir),
            "log",
            "-1",
            "--no-patch",
            "--format=%an|%ae|%cn|%ce",
        ],
        capture_output=True,
        check=True,
        env=env_for_check,
        text=True,
    ).stdout.strip()
    assert out == "mcs|mcs@local|mcs|mcs@local", (
        f"the committed commit's author/committer identity should be "
        f"the wrapper's hardcoded ``mcs|mcs@local|mcs|mcs@local`` "
        f"regardless of the fake ``~/.gitconfig`` at "
        f"{fake_gitconfig!r}; got {out!r}. If the fake's ``NotMcs`` "
        f"appears in the output, the wrapper's ``_FIXED_ENV_OVERRIDES`` "
        f"isn't getting set on the per-profile git subprocesses, and "
        f"the global-config blockade in ``GIT_CONFIG_GLOBAL=/dev/null`` "
        f"is the only layer left."
    )
    # And as a sanity probe: the fake gitconfig is what the *parent*
    # env points at, but the wrapper's ``_env()`` rewrites
    # ``GIT_CONFIG_GLOBAL`` to ``/dev/null`` for its own subprocesses.
    # We can't directly observe the wrapper's subprocess env at this
    # point (it's gone), but the unit-level test in
    # ``test_git_repo.py`` pins that contract verbatim.
    assert fake_gitconfig.exists(), (
        f"the fake gitconfig at {fake_gitconfig!r} disappeared — "
        f"the wrapper isn't supposed to delete it, only ignore it."
    )


# The ``cli`` import is wired in for any future test that wants to
# drive the top-level CLI dispatch path (the ``mcs profile create``
# subcommand under the ``mcs`` root group). The current tests go
# directly through the ``profile_group`` subcommand for the same
# reason ``tests/unit/commands/test_profile_create.py`` does — it's
# the same entry point click would dispatch to, but it skips the
# banner-render and update-check noise of the outer ``main()``
# wrapper in ``cli.py``.
_ = mcs_cli  # quiet the "unused import" linter on this stable handle
