"""Regression-lint for the maxcompute-semantic skill bundle.

These tests bind the installed skill bundle's documentation to the
CLI surface so neither side can drift without the other failing. If
a verb is renamed or removed, the doc reference becomes a phantom
and these tests catch it; if a wizard step is added without a
matching doc update, that's a separate plan.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).parent.parent.parent.parent
_SKILL_ROOT = _PACKAGE_ROOT / "src" / "maxcompute_semantic" / "_skill"
_SKILL_DATA_ROOT = _SKILL_ROOT.parent / "_skill_data"


@pytest.mark.parametrize(
    "verb_name",
    [
        # NOTE: matched without the leading `mcs ` prefix because the doc
        # invokes each verb with a global format flag in between
        # (`mcs -f json profile suggest-creds`). The verb tail is the
        # load-bearing identifier — if it's renamed in the CLI, the doc
        # reference becomes a phantom and these tests catch it.
        "profile suggest-creds",
        "profile endpoint-presets",
        "profile list-ncs-identities",
    ],
)
def test_onboarding_references_agent_wizard_verbs(verb_name: str) -> None:
    """The Agent Wizard Flow section must call out each new verb by name."""
    onboarding = (_SKILL_DATA_ROOT / "onboarding" / "references" / "onboarding.md").read_text(
        encoding="utf-8"
    )
    assert verb_name in onboarding, (
        f"verb {verb_name!r} not referenced in onboarding.md — "
        f"either the verb was renamed (update the doc) or the doc "
        f"was rewritten without the Agent Wizard Flow section "
        f"(restore Step 1.5 / Step 2 / Step 4 references)."
    )


def test_onboarding_has_agent_wizard_section_header() -> None:
    """The doc must keep an explicit 'Agent Wizard Flow' header so
    `mcs skill install`-ed agents can grep for it."""
    onboarding = (_SKILL_DATA_ROOT / "onboarding" / "references" / "onboarding.md").read_text(
        encoding="utf-8"
    )
    assert "Agent Wizard Flow" in onboarding, (
        "onboarding.md no longer contains the 'Agent Wizard Flow' "
        "section header — agent step-by-step guidance has gone "
        "missing."
    )


def test_installed_skill_is_discovery_stub() -> None:
    text = (_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "mcs skill get query" in text
    assert "mcs skill get build" in text
    assert "never run `mcs build`" in text
    assert len(text.split()) < 900


def test_enrich_runtime_skill_documents_proposal_workflow() -> None:
    text = (_SKILL_DATA_ROOT / "enrich" / "SKILL.md").read_text(encoding="utf-8")
    assert "mcs package propose --from-suggestions" in text
    assert "mcs package apply" in text
    assert "Do not bypass the proposal queue" in text


def test_enrich_runtime_skill_documents_optional_reject_reason() -> None:
    skill = (_SKILL_DATA_ROOT / "enrich" / "SKILL.md").read_text(encoding="utf-8")
    reference = (_SKILL_DATA_ROOT / "enrich" / "references" / "enrich.md").read_text(
        encoding="utf-8"
    )
    assert "mcs package reject <id>" in skill
    assert "omitting `--reason` is valid" in skill
    assert "mcs package reject <id>" in reference
    assert "omitting `--reason` is valid" in reference


def test_enrich_build_docs_are_proposal_first_after_build() -> None:
    build_skill = (_SKILL_DATA_ROOT / "build" / "SKILL.md").read_text(encoding="utf-8")
    build_ref = (_SKILL_DATA_ROOT / "build" / "references" / "build.md").read_text(encoding="utf-8")

    assert "mcs skill get enrich" in build_skill
    assert "mcs package propose --from-suggestions" in build_skill
    assert "mcs skill get enrich" in build_ref
    assert "mcs package propose --from-suggestions" in build_ref
    assert "mcs package apply <id>" in build_ref
    assert "After build, load `mcs skill get annotate`" not in build_skill
    assert "mcs annotate batch --stdin` carrying" not in build_ref


def test_build_runtime_skill_documents_profile_scoped_build() -> None:
    build_skill = (_SKILL_DATA_ROOT / "build" / "SKILL.md").read_text(encoding="utf-8")

    assert "profile/project overrides" not in build_skill
    assert "mcs build --project" not in build_skill
    assert "profile/schema/table overrides" in build_skill


def test_udf_runtime_skill_uses_executable_command_shapes() -> None:
    udf_skill = (_SKILL_DATA_ROOT / "udf" / "SKILL.md").read_text(encoding="utf-8")

    assert "mcs udf create\n" not in udf_skill
    assert "mcs udf test NAME\n" not in udf_skill
    assert "mcs udf create NAME --inline-python script.py" in udf_skill
    assert "mcs udf test NAME --args" in udf_skill



def test_enrich_docs_do_not_use_build_step_two_direct_batch_wording() -> None:
    build_ref = (_SKILL_DATA_ROOT / "build" / "references" / "build.md").read_text(encoding="utf-8")
    assert "Step 2 — agent annotation" not in build_ref


def test_runtime_skills_cover_query_and_build() -> None:
    from maxcompute_semantic.commands.skill_catalog import discover_runtime_skills

    names = {skill.name for skill in discover_runtime_skills([_SKILL_DATA_ROOT])}
    assert {"query", "build", "enrich", "onboarding", "memory", "udf"} <= names


def test_runtime_skill_data_is_importable_package_resource() -> None:
    from importlib import resources

    package_root = resources.files("maxcompute_semantic")
    query_skill = package_root.joinpath("_skill_data", "query", "SKILL.md")
    build_skill = package_root.joinpath("_skill_data", "build", "SKILL.md")
    onboarding_ref = package_root.joinpath(
        "_skill_data", "onboarding", "references", "onboarding.md"
    )

    assert query_skill.is_file()
    assert build_skill.is_file()
    assert onboarding_ref.is_file()
    assert "mcs sql execute" in query_skill.read_text(encoding="utf-8")
    assert "mcs build" in build_skill.read_text(encoding="utf-8")


def test_wheel_contains_runtime_skill_data(tmp_path: Path) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build the package wheel")

    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(_PACKAGE_ROOT)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 0, result.stdout
    wheels = sorted(tmp_path.glob("maxcompute_semantic-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    assert "maxcompute_semantic/_skill_data/query/SKILL.md" in names
    assert "maxcompute_semantic/_skill_data/build/SKILL.md" in names
    assert "maxcompute_semantic/_skill_data/onboarding/references/onboarding.md" in names


def test_every_runtime_skill_directory_has_skill_markdown() -> None:
    names = {path.name for path in _SKILL_DATA_ROOT.iterdir() if path.is_dir()}
    assert {"query", "build", "enrich", "onboarding", "memory", "udf", "report-issue"} <= names
    for name in names:
        assert (_SKILL_DATA_ROOT / name / "SKILL.md").is_file(), (
            f"runtime skill {name!r} is missing SKILL.md"
        )
