"""T9 — read-only fork guard at write-command entry.

Every write verb wired in T8 calls ``reject_if_fork(profile)``
*before* it touches the on-disk package state. This file pins the
contract for both halves of the surface:

* **Write verbs** — invoking any of them against a ``kind="fork"``
  profile must fail with ``ProfileReadOnly`` (exit code 2), and
  the per-profile package data on disk must be untouched. The
  remediation string must name both the parent profile and the
  fork's anchor SHA so the user can copy-paste the recovery
  command (``mcs profile reset --to <sha> --profile <parent>`` or
  ``mcs profile fork <new> --from <sha> --profile <parent>``).

* **Read verbs** — invoking a non-mutating verb against a fork
  must succeed (or fail for unrelated reasons like the table not
  existing — never with ``ProfileReadOnly``). Reads are the entire
  point of having forks around for retrospective inspection /
  side-by-side eval comparisons.

The ``fork_profile`` fixture registers a real fork in the test
``profiles.yaml`` so the CLI's auto-resolution chain can find it
by name via ``--profile``. The parent profile reuses the standard
``versioned_profile`` fixture from the per-directory conftest.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.profile_store import upsert as upsert_profile
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.cli import cli as mcs_cli

_SK = "acme_proj__default"
_FAKE_SHA = "a" * 40


@pytest.fixture
def fork_profile(versioned_profile: Profile) -> Profile:
    """Register a fork alias of ``versioned_profile`` and return it.

    The fork's ``package_path`` is materialized as an empty directory
    under the parent's data root so commands that open ``PackageDB``
    (e.g. ``mcs udf create``) don't blow up before reaching the
    ``reject_if_fork`` guard. The fork's git_sha is a deterministic
    fake (40×``a``) — the guard only inspects ``profile.kind``, so
    the SHA's authenticity doesn't matter for these tests.
    """
    parent_pdir = profile_data_dir(versioned_profile)
    fork_path = parent_pdir.parent / "acme-fork"
    fork_path.mkdir(parents=True, exist_ok=True)
    # Seed a PackageDB so PackageDB-opening write verbs (memory,
    # udf, annotate) reach the guard rather than failing on a
    # missing schema. The hook never gets to dump this — the guard
    # fires before any write — so the bare schema is enough.
    PackageDB(fork_path / "package.db").close()

    fork = Profile(
        name="acme-fork",
        compute_project=versioned_profile.compute_project,
        endpoint=versioned_profile.endpoint,
        auth=versioned_profile.auth,
        sources=versioned_profile.sources,
        package_path=fork_path,
        kind="fork",
        parent_profile=versioned_profile.name,
        git_sha=_FAKE_SHA,
    )
    upsert_profile(fork)
    return fork


def _seed_parent_db(profile: Profile) -> None:
    """Seed the parent's package.db with one table + column so
    annotate verbs targeting the parent (anti-cases that flip the
    profile name to the parent) have a real target to work on. The
    fork tests don't need the data because the guard fires first,
    but the test file's anti-cases for reads (which DO read from
    the fork's db) want at least an opened schema in place — that
    happens via PackageDB(...) in the fixture itself."""
    db_path = profile_data_dir(profile) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PackageDB(db_path)
    tid = db.upsert_table(_SK, "orders", "h1")
    db.upsert_columns(
        tid,
        [{"name": "status", "type": "STRING", "comment": "", "is_partition": 0}],
    )
    db.close()


def _assert_fork_rejected(result, fork: Profile) -> None:
    """Assert the CLI invocation failed with ``ProfileReadOnly``
    and the remediation names both the parent profile and the
    anchor SHA. Exit code is 2 (spec'd on ``ProfileReadOnly``)."""
    assert result.exit_code == 2, (
        f"expected fork-write rejection (exit 2), got exit "
        f"{result.exit_code}; output: {result.output!r}"
    )
    # The plain-format renderer puts the error text + remediation on
    # stderr; CliRunner merges stderr into ``result.output`` unless
    # ``mix_stderr=False``. Both the SHA and parent name are part of
    # the remediation string the spec pins.
    out = result.output
    assert fork.parent_profile in out, (
        f"remediation must name parent profile {fork.parent_profile!r}; got {out!r}"
    )
    assert _FAKE_SHA in out, f"remediation must name anchor SHA; got {out!r}"


# ── write verbs: reject ─────────────────────────────────────────────────────


def test_build_rejected_on_fork(fork_profile: Profile, fake_maxcompute) -> None:
    """``mcs build`` against a fork: exit 2, no client/credentials
    work attempted, no dump written."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["build", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_verify_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "memory",
            "verify",
            "--question",
            "q",
            "--sql",
            "SELECT 1",
            "--tables",
            "orders",
            "--profile",
            fork_profile.name,
        ],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_fail_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "memory",
            "fail",
            "--question",
            "q",
            "--sql",
            "SELEC 1",
            "--error-msg",
            "syntax error",
            "--profile",
            fork_profile.name,
        ],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_note_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "note", "a note", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_remove_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "remove", "1", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_clear_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "clear", "--yes", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_memory_reindex_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "reindex", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_udf_create_rejected_on_fork(fork_profile: Profile, tmp_path) -> None:
    script = tmp_path / "u.py"
    script.write_text("class MyUDF:\n    pass\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "udf",
            "create",
            "my_udf",
            "--inline-python",
            str(script),
            "--profile",
            fork_profile.name,
        ],
    )
    _assert_fork_rejected(result, fork_profile)


def test_udf_remove_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["udf", "remove", "my_udf", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


def test_udf_resource_remove_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["udf", "resource", "remove", "my_res", "--profile", fork_profile.name],
    )
    _assert_fork_rejected(result, fork_profile)


# ── anti-cases: reads succeed (or fail for unrelated reasons) ───────────────


def test_memory_recall_not_rejected_on_fork(fork_profile: Profile) -> None:
    """``mcs memory recall`` is a read verb — must NOT raise
    ProfileReadOnly. An empty memory store yields zero results
    (exit 0)."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "recall", "anything", "--profile", fork_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "ProfileReadOnly" not in result.output


def test_memory_list_not_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["memory", "list", "--profile", fork_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "ProfileReadOnly" not in result.output


def test_udf_list_not_rejected_on_fork(fork_profile: Profile) -> None:
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["udf", "list", "--profile", fork_profile.name],
    )
    assert result.exit_code == 0, result.output
    assert "ProfileReadOnly" not in result.output


def test_status_not_rejected_on_fork(fork_profile: Profile) -> None:
    """``mcs status`` reads ``_state.json`` and PackageDB metadata
    — a pure-read verb that must work against a fork."""
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        ["status", "--profile", fork_profile.name],
    )
    # status reports "not built" against a brand-new package.db with
    # no _state.json; the relevant assertion is "not rejected as a
    # fork", which is what ProfileReadOnly absence checks.
    assert "ProfileReadOnly" not in result.output


# ── on-disk side-effect: rejected writes leave no trace ─────────────────────


def test_rejected_write_does_not_dirty_fork_package(
    fork_profile: Profile,
) -> None:
    """A rejected fork write must not create a per-table markdown,
    must not bump ``_state.json``, must not commit anything. The
    fork's data dir starts with just the seeded ``package.db`` and
    nothing else; that's exactly what it should still look like
    after the rejected invocation."""
    fork_pdir = profile_data_dir(fork_profile)
    before = sorted(p.name for p in fork_pdir.iterdir())

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "memory",
            "note",
            "should-not-land",
            "--profile",
            fork_profile.name,
        ],
    )
    _assert_fork_rejected(result, fork_profile)

    after = sorted(p.name for p in fork_pdir.iterdir())
    assert after == before, (
        f"fork data dir changed during a rejected write; before={before!r}, after={after!r}"
    )


# ── profile import: refuse to clobber an existing fork ───────────────────────


def test_profile_import_rejects_clobbering_fork(
    fork_profile: Profile, tmp_path, versioned_profile: Profile
) -> None:
    """``mcs profile import --name <fork-name> <archive>`` against
    an existing fork name must reject with ProfileReadOnly, even
    when ``--name`` would otherwise bypass the PROFILE_EXISTS check.
    The fork's anchor contract can't be silently broken by an
    import overwrite."""
    # Build a real archive from the parent profile. The
    # ``versioned_profile`` fixture only stamps the YAML — there's
    # no package data yet — so materialize a bare PackageDB at the
    # parent's data dir before exporting, otherwise ``export_profile``
    # bails with ``PackageNotBuiltError``.
    from maxcompute_semantic.commands.profile_export import export_profile

    parent_pdir = profile_data_dir(versioned_profile)
    parent_pdir.mkdir(parents=True, exist_ok=True)
    PackageDB(parent_pdir / "package.db").close()

    archive = tmp_path / "parent.tar.gz"
    export_profile(versioned_profile.name, archive)

    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "import",
            str(archive),
            "--name",
            fork_profile.name,
        ],
    )
    _assert_fork_rejected(result, fork_profile)
