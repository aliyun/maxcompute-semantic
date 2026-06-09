"""Anti-test for T8: verbs that don't mutate package data must NOT
land a new commit in the per-profile git repo.

The T8 hook is wired only into commands that mutate ``package.db``
(or its on-disk siblings: per-table markdown, ``_state.json``,
``_overview.md``). Two families deliberately stay out of the
versioning timeline:

  * **YAML-only mutations.** ``mcs profile update`` rewrites
    ``profiles.yaml`` (auth keys, endpoint, tags, sources, cost
    thresholds) which lives in ``~/.config/maxcompute-semantic/``,
    *not* under the per-profile data dir. No commit. The next build
    or annotate run picks the latest yaml up automatically.
  * **Read-only verbs.** ``mcs profile show`` / ``mcs profile list``
    / ``mcs memory list`` / ``mcs status --tables`` / ``mcs udf list``
    don't write anything to disk. No commit.

If any of these regressions and starts producing a commit, the per-
profile git log fills with noise — users grep ``git log --oneline``
for build / annotate / memory milestones and stale yaml/read-only
commits would drown the signal.

The test pattern is uniform: snapshot the inaugural commit's SHA,
run the verb, assert ``HEAD`` still points at the same SHA.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.cli import cli as mcs_cli
from maxcompute_semantic.versioning.git_repo import GitRepo


def _head_sha(profile: Profile) -> str:
    """Return the full SHA of the per-profile repo's HEAD commit."""
    repo = GitRepo(profile_data_dir(profile))
    rows = repo.log(limit=None)
    assert rows, "expected at least one commit in the per-profile repo"
    return rows[0].full_sha


def test_profile_update_does_not_create_commit(
    versioned_profile: Profile,
) -> None:
    """``mcs profile update --from-spec`` rewrites ``profiles.yaml``
    but leaves the per-profile data-dir untouched — no new commit.

    We pass the same spec back through ``--from-spec`` so the yaml's
    serialized bytes change (the round-trip can reorder fields /
    normalize whitespace) but ``package.db`` and the markdown side
    files are byte-identical."""
    sha_before = _head_sha(versioned_profile)

    # Round-trip the canonical spec; same shape the conftest fixture
    # built the profile with, plus a fresh tag so the yaml changes.
    spec = {
        "name": versioned_profile.name,
        "compute_project": versioned_profile.compute_project,
        "endpoint": versioned_profile.endpoint,
        "auth": {
            "type": "ak",
            "access_key_id": "${env:MY_AK_ID}",
            "access_key_secret": "${env:MY_AK_SEC}",
        },
        "sources": [
            {
                "project": versioned_profile.compute_project,
                "schema": "default",
                "tables": "*",
            }
        ],
        "tags": ["t8-anti-test"],
    }
    runner = CliRunner()
    result = runner.invoke(
        mcs_cli,
        [
            "profile",
            "update",
            versioned_profile.name,
            "--from-spec",
            json.dumps(spec),
            "--no-test",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    sha_after = _head_sha(versioned_profile)
    assert sha_after == sha_before, (
        f"`mcs profile update` should NOT produce a commit; HEAD moved "
        f"{sha_before[:7]} → {sha_after[:7]}. The verb only mutates "
        f"profiles.yaml outside the data-dir."
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "show"],
        ["profile", "list"],
        ["memory", "list"],
        ["annotate", "list"],
        ["udf", "list"],
    ],
    ids=["profile-show", "profile-list", "memory-list", "annotate-list", "udf-list"],
)
def test_read_only_verbs_do_not_create_commit(versioned_profile: Profile, argv: list[str]) -> None:
    """Read-only verbs don't write to package.db so they must not
    leave a commit behind. The parametrization covers one verb per
    command group that has a write-counterpart in the same group —
    if a copy-paste regression accidentally wires the hook into a
    sibling list/show verb, this test catches it."""
    sha_before = _head_sha(versioned_profile)

    runner = CliRunner()
    extra = ["--profile", versioned_profile.name] if argv[0] != "profile" else []
    result = runner.invoke(mcs_cli, [*argv, *extra])
    # Some read-only verbs may surface non-zero exit codes when the
    # underlying state is empty (e.g. no annotations on a freshly
    # seeded profile). The exit-code path doesn't matter for this
    # test — what matters is that no commit lands either way.

    sha_after = _head_sha(versioned_profile)
    assert sha_after == sha_before, (
        f"read-only verb `mcs {' '.join(argv)}` should NOT produce a "
        f"commit; HEAD moved {sha_before[:7]} → {sha_after[:7]}. "
        f"Verb output (exit={result.exit_code}):\n{result.output[:400]}"
    )
