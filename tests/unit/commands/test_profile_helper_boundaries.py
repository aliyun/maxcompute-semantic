"""Architecture guard for profile-resolution helper ownership."""

from __future__ import annotations

from pathlib import Path


def test_command_modules_do_not_import_profile_private_resolution_helpers() -> None:
    """Profile resolution helpers live outside commands.profile for reuse."""
    commands_dir = Path(__file__).resolve().parents[3] / "src" / "maxcompute_semantic" / "commands"
    offenders: list[str] = []
    forbidden = (
        "from maxcompute_semantic.commands.profile import _make_client_for_project",
        "from maxcompute_semantic.commands.profile import _resolve_profile_for_project",
    )

    for path in commands_dir.rglob("*.py"):
        if path.name == "profile.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in forbidden):
            offenders.append(str(path.relative_to(commands_dir)))

    assert offenders == []
