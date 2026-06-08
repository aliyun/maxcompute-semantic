# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cross-file consistency guards for the per-profile git-versioning
release (T22).

These are cheap, no-fixture-needed text-shape checks that catch the
recurring drift trap of "version bumped in one place, not the other":

* ``test_pyproject_version_matches_changelog`` — the ``version = "..."``
  line in ``pyproject.toml`` must appear as a ``[<version>]`` heading
  in ``CHANGELOG.md``. Catches the case where a release commit bumps
  the pyproject but forgets to land the matching changelog section.
* ``test_onboarding_skill_lists_versioning_recovery_verbs`` — the
  onboarding runtime skill must mention every one of the eight new
  ``mcs profile`` verbs the feature introduced. Catches the case where
  the runtime skill drifts out of sync with the CLI surface so the
  agent forgets to suggest ``mcs profile reset`` when the user reports
  a damaged annotate pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _PACKAGE_ROOT.parents[1]


def test_pyproject_version_matches_changelog() -> None:
    pyproject = tomllib.loads((_PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    changelog = (_PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Match both `## [X.Y.Z] — date` (em-dash) and `## [X.Y.Z] - date`
    # (hyphen) — both shapes appear in the file's history.
    pattern = re.compile(rf"^##\s+\[{re.escape(version)}\]\s+[—-]", re.MULTILINE)
    assert pattern.search(changelog), (
        f"pyproject.toml says version={version!r} but CHANGELOG.md has no "
        f"matching `## [{version}] — <date>` heading; bump the changelog "
        f"alongside the version."
    )


def test_onboarding_skill_lists_versioning_recovery_verbs() -> None:
    skill_root = _PACKAGE_ROOT / "src/maxcompute_semantic/_skill_data/onboarding"
    skill = (
        (skill_root / "SKILL.md").read_text(encoding="utf-8")
        + "\n"
        + (skill_root / "references/profile-history.md").read_text(encoding="utf-8")
    )
    for verb in (
        "mcs profile log",
        "mcs profile log-show",
        "mcs profile diff",
        "mcs profile reset",
        "mcs profile fork",
        "mcs profile fork-list",
        "mcs profile fork-remove",
        "mcs profile enable-versioning",
    ):
        assert verb in skill, (
            f"onboarding runtime skill is missing a mention of {verb!r} — "
            f"the agent won't suggest it when the user needs version recovery."
        )


def test_onboarding_skill_uses_valid_profile_history_command_shapes() -> None:
    skill = (_PACKAGE_ROOT / "src/maxcompute_semantic/_skill_data/onboarding/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "mcs profile log PROFILE" not in skill
    assert "mcs profile diff PROFILE" not in skill
    assert "mcs profile reset PROFILE" not in skill
    assert "mcs profile fork PROFILE" not in skill
    assert "mcs profile log --profile PROFILE" in skill
    assert "mcs profile diff REF_A REF_B --profile PROFILE" in skill
    assert "mcs profile reset --to REF --profile PROFILE" in skill
    assert "mcs profile fork FORK_NAME --from REF --profile PROFILE" in skill


def test_claude_md_documents_no_versioning_env_knob() -> None:
    # CLAUDE.md's eval-mode section is the canonical home for the
    # env-knob family (MCS_NO_HISTORY + MCS_NO_VERSIONING). A new
    # contributor reading the file should learn both knobs from the
    # same place.
    claude_md = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "MCS_NO_VERSIONING" in claude_md, (
        "CLAUDE.md should document MCS_NO_VERSIONING alongside "
        "MCS_NO_HISTORY in the eval-mode section."
    )


def test_agent_docs_document_profile_scoped_build_surface() -> None:
    """Build is profile-scoped; contributor docs must not advertise --project."""
    for rel in ("CLAUDE.md", "AGENTS.md"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "mcs build [--project P]" not in text
        assert "mcs build [--profile X]" in text
        assert "Build is profile-scoped" in text



def test_claude_md_skill_install_table_matches_codex_discovery_dir() -> None:
    """Contributor docs should match the Codex path pinned by install CI."""
    text = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "| OpenAI Codex | `codex` | `.agents/skills/` | `~/.agents/skills/` |" in text
    assert "~/.codex/skills/" not in text


def test_claude_md_eval_isolation_uses_current_package_path_flow() -> None:
    """Eval docs should not point readers at the retired profiles/ data path."""
    text = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<tmphome>/.config/maxcompute-semantic/data/profiles/" not in text
    assert "package_path" in text



def test_public_docs_do_not_advertise_retired_feedback_surface() -> None:
    """Keep public docs and runtime skills on the current memory surface."""
    docs = [
        _REPO_ROOT / "docs/yuque-public-usage.md",
        _PACKAGE_ROOT / "README.md",
        _PACKAGE_ROOT / "src/maxcompute_semantic/_skill/SKILL.md",
        _PACKAGE_ROOT / "src/maxcompute_semantic/_skill_data/memory/SKILL.md",
        _PACKAGE_ROOT / "src/maxcompute_semantic/_skill_data/memory/references/memory.md",
        _REPO_ROOT / "site/docs.html",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)
    assert "mcs feedback record" not in combined
    assert "memory note --text" not in combined
    assert "mcs memory note '<TEXT>'" in combined


def test_package_readme_mentions_metric_surface() -> None:
    """The package short reference should expose the top-level metric CLI."""
    readme = (_PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "mcs metric list" in readme
    assert "mcs metric add" in readme


def test_public_docs_use_current_link_and_batch_commands() -> None:
    """Public docs should not mention retired link verbs or incomplete batch commands."""
    docs = {
        "README.md": (_REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "README.en.md": (_REPO_ROOT / "README.en.md").read_text(encoding="utf-8"),
        "package README.md": (_PACKAGE_ROOT / "README.md").read_text(encoding="utf-8"),
        "site/docs.html": (_REPO_ROOT / "site" / "docs.html").read_text(encoding="utf-8"),
    }
    combined = "\n".join(docs.values())
    assert "mcs link show" not in combined
    assert "mcs link unbind" not in combined
    assert "mcs link status" in combined
    assert "mcs link unlink" in combined
    assert "mcs sql review" in combined
    assert "mcs package propose" in combined


def test_package_readme_uses_current_platform_aliases() -> None:
    readme = (_PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "`gemini-cli`" in readme
    assert "`qwen-code`" in readme
    assert "`gemini`, `qwen`" not in readme


def test_yuque_public_usage_uses_current_platform_aliases() -> None:
    doc = (_REPO_ROOT / "docs/yuque-public-usage.md").read_text(encoding="utf-8")
    assert "``gemini-cli``" in doc
    assert "``qwen-code``" in doc
    assert "gemini-cli、qwen-code" in doc
    assert "| ``gemini`` |" not in doc
    assert "| ``qwen`` |" not in doc
    assert "、gemini、qwen、" not in doc


def test_root_makefile_check_covers_mcs_without_live_tests() -> None:
    """The root quality gate should cover mcs without requiring live MC creds."""
    text = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "check: lint lint-sh spell typecheck coverage test-mcs audit" in text
    assert "test:\n\t$(UV) pytest tests/eval/\n\t$(MAKE) test-mcs" in text
    assert "test-mcs:" in text
    assert "-m 'not live'" in text
    assert "make test-mcs" in text
