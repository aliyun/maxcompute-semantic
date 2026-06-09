"""mcs skill — deploy maxcompute-semantic skill via symlink.

Default: local install → ``<cwd>/.agents/skills/maxcompute-semantic``
``-g``: global install → ``~/.agents/skills/maxcompute-semantic``
``--all``: install to every supported agent platform
``--detect``: install only to detected (installed) agent platforms
``--target <path>``: custom location

The _skill/ directory (containing just the discovery-stub SKILL.md) is
symlinked as a single unit. Runtime workflow content lives in _skill_data/
and is served dynamically via ``mcs skill get <phase>``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.commands.skill_catalog import (
    collect_supplementary_files,
    discover_runtime_skills,
)

_SKILL_NAME = "maxcompute-semantic"

_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


@dataclass(frozen=True)
class _Platform:
    """Agent platform skill-directory convention."""

    id: str
    local_dir: str
    global_segments: tuple[str, ...]
    xdg: bool = False


_DEPRECATED_ALIASES: dict[str, str] = {
    "gemini": "gemini-cli",
    "qwen": "qwen-code",
}

_PLATFORM_REGISTRY: list[_Platform] = [
    # ── universal / widely-shared (.agents/skills/) ──
    _Platform("agents", ".agents/skills", (".agents", "skills")),
    # ── agent-specific ──
    _Platform("adal", ".adal/skills", (".adal", "skills")),
    _Platform("aider-desk", ".aider-desk/skills", (".aider-desk", "skills")),
    _Platform("amp", ".agents/skills", ("agents", "skills"), xdg=True),
    _Platform("antigravity", ".agents/skills", (".gemini", "antigravity", "skills")),
    _Platform("augment", ".augment/skills", (".augment", "skills")),
    _Platform("bob", ".bob/skills", (".bob", "skills")),
    _Platform("claude-code", ".claude/skills", (".claude", "skills")),
    _Platform("cline", ".agents/skills", (".agents", "skills")),
    _Platform("codearts-agent", ".codeartsdoer/skills", (".codeartsdoer", "skills")),
    _Platform("codebuddy", ".codebuddy/skills", (".codebuddy", "skills")),
    _Platform("codemaker", ".codemaker/skills", (".codemaker", "skills")),
    _Platform("codestudio", ".codestudio/skills", (".codestudio", "skills")),
    # OpenAI Codex: both local and global land in ``.agents/skills/`` per
    # the CLAUDE.md "Skill installation" table; codex reads the universal
    # ``.agents/skills/`` discovery path rather than a ``.codex/`` namespace.
    # The multi-agent-install CI yaml hard-pins this.
    _Platform("codex", ".agents/skills", (".agents", "skills")),
    _Platform("command-code", ".commandcode/skills", (".commandcode", "skills")),
    _Platform("continue", ".continue/skills", (".continue", "skills")),
    _Platform("cortex", ".cortex/skills", (".snowflake", "cortex", "skills")),
    _Platform("crush", ".crush/skills", (".config", "crush", "skills")),
    _Platform("cursor", ".cursor/skills", (".cursor", "skills")),
    _Platform("deepagents", ".agents/skills", (".deepagents", "agent", "skills")),
    _Platform("devin", ".devin/skills", ("devin", "skills"), xdg=True),
    _Platform("dexto", ".agents/skills", (".agents", "skills")),
    _Platform("droid", ".factory/skills", (".factory", "skills")),
    _Platform("firebender", ".agents/skills", (".firebender", "skills")),
    _Platform("forgecode", ".forge/skills", (".forge", "skills")),
    _Platform("gemini-cli", ".gemini/skills", (".gemini", "skills")),
    _Platform("github-copilot", ".agents/skills", (".copilot", "skills")),
    _Platform("goose", ".goose/skills", ("goose", "skills"), xdg=True),
    _Platform("hermes-agent", ".hermes/skills", (".hermes", "skills")),
    _Platform("iflow-cli", ".iflow/skills", (".iflow", "skills")),
    _Platform("junie", ".junie/skills", (".junie", "skills")),
    _Platform("kilo", ".kilocode/skills", (".kilocode", "skills")),
    _Platform("kimi-cli", ".agents/skills", (".config", "agents", "skills")),
    _Platform("kiro-cli", ".kiro/skills", (".kiro", "skills")),
    _Platform("kode", ".kode/skills", (".kode", "skills")),
    _Platform("mcpjam", ".mcpjam/skills", (".mcpjam", "skills")),
    _Platform("mistral-vibe", ".vibe/skills", (".vibe", "skills")),
    _Platform("mux", ".mux/skills", (".mux", "skills")),
    _Platform("neovate", ".neovate/skills", (".neovate", "skills")),
    _Platform("openclaw", "skills", (".openclaw", "skills")),
    _Platform("opencode", ".opencode/skills", ("opencode", "skills"), xdg=True),
    _Platform("openhands", ".openhands/skills", (".openhands", "skills")),
    _Platform("pi", ".pi/skills", (".pi", "agent", "skills")),
    _Platform("pochi", ".pochi/skills", (".pochi", "skills")),
    _Platform("qoder", ".qoder/skills", (".qoder", "skills")),
    _Platform("qoderwork", ".qoderwork/skills", (".qoderwork", "skills")),
    _Platform("qwen-code", ".qwen/skills", (".qwen", "skills")),
    _Platform("replit", ".agents/skills", ("agents", "skills"), xdg=True),
    _Platform("roo", ".roo/skills", (".roo", "skills")),
    _Platform("rovodev", ".rovodev/skills", (".rovodev", "skills")),
    _Platform("tabnine-cli", ".tabnine/agent/skills", (".tabnine", "agent", "skills")),
    _Platform("trae", ".trae/skills", (".trae", "skills")),
    _Platform("trae-cn", ".trae/skills", (".trae-cn", "skills")),
    _Platform("universal", ".agents/skills", ("agents", "skills"), xdg=True),
    _Platform("warp", ".agents/skills", (".agents", "skills")),
    _Platform("windsurf", ".windsurf/skills", (".codeium", "windsurf", "skills")),
    _Platform("zencoder", ".zencoder/skills", (".zencoder", "skills")),
]

# Computed from _PLATFORM_REGISTRY at module load.
# Dicts are kept for test patching + existing code compatibility.
_GLOBAL_PATHS: dict[str, Path] = {}
_LOCAL_DIRS: dict[str, str] = {}
_PLATFORM_BY_ID: dict[str, _Platform] = {}
for _p in _PLATFORM_REGISTRY:
    _base = _XDG_CONFIG_HOME if _p.xdg else Path.home()
    _GLOBAL_PATHS[_p.id] = _base.joinpath(*_p.global_segments) / _SKILL_NAME
    _LOCAL_DIRS[_p.id] = _p.local_dir
    _PLATFORM_BY_ID[_p.id] = _p

_PLATFORM_IDS = frozenset(_GLOBAL_PATHS)
_PLATFORM_IDS_WITH_ALIASES = frozenset(_PLATFORM_IDS | set(_DEPRECATED_ALIASES))


def _resolve_platform(raw: str) -> str:
    """Canonicalize a platform name, warning on deprecated aliases."""
    if raw in _DEPRECATED_ALIASES:
        canonical = _DEPRECATED_ALIASES[raw]
        click.echo(
            f"warning: '{raw}' is deprecated, use '{canonical}' instead",
            err=True,
        )
        return canonical
    if raw in _PLATFORM_IDS:
        return raw
    raise ValueError(f"unknown platform: '{raw}'")


class _PlatformParam(click.ParamType):
    """click ParamType for platform IDs with deprecation-aware validation."""

    name = "PLATFORM"

    def convert(self, value: str, param: object, ctx: object) -> str:
        try:
            return _resolve_platform(value)
        except ValueError:
            self.fail(
                f"'{value}' is not a known platform. "
                f"Run 'mcs skill list' to see available platforms.",
            )

    def shell_complete(self, ctx: object, param: object, incomplete: str):
        """Tab-complete all known platform IDs including deprecated aliases."""
        from click.shell_completion import CompletionItem

        return [
            CompletionItem(n)
            for n in sorted(_PLATFORM_IDS_WITH_ALIASES)
            if n.startswith(incomplete)
        ]


PLATFORM_TYPE = _PlatformParam()


def _skill_root() -> Path:
    """Return the _skill/ directory inside the installed package."""
    pkg_dir = Path(__file__).resolve().parent.parent / "_skill"
    if not pkg_dir.is_dir():
        click.echo("error: _skill/ directory not found in package", err=True)
        sys.exit(1)
    return pkg_dir


# Windows reparse-point constants for the Python 3.10/3.11 fallback.
# Python 3.12+ exposes ``os.path.isjunction`` directly; older versions
# need a manual lstat + reparse-tag check. The constant values are
# stable Win32 ABI (FILE_ATTRIBUTE_REPARSE_POINT = 0x400,
# IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003) so the literals are safe.
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WIN_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def _is_junction(path: Path) -> bool:
    """Return True if ``path`` is a Windows directory junction.

    Junctions look like directories to most os.path APIs and are *not*
    detected by ``Path.is_symlink()``, but they're functionally
    equivalent for our skill-install case (a directory mount point
    that doesn't require admin or Developer Mode to create).
    """
    if sys.platform != "win32":
        return False
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return bool(isjunction(str(path)))
    try:
        st = os.lstat(path)
    except OSError:
        return False
    file_attrs = getattr(st, "st_file_attributes", 0)
    if not (file_attrs & _WIN_FILE_ATTRIBUTE_REPARSE_POINT):
        return False
    return getattr(st, "st_reparse_tag", 0) == _WIN_IO_REPARSE_TAG_MOUNT_POINT


def _is_skill_link(path: Path) -> bool:
    """Return True if ``path`` is a symlink or a Windows junction.

    The skill-install code creates one of two link types depending on
    privileges: a real symlink everywhere, or a directory junction on
    Windows when symlink creation is denied. Both are valid install
    forms; this predicate treats them uniformly.
    """
    return path.is_symlink() or _is_junction(path)


def _normalize_target(target_path: str) -> Path:
    """Append ``_SKILL_NAME`` to a ``--target`` path when the basename
    doesn't already match.

    A bare ``--target ~/.qoderwork/skills/`` (the parent ``skills``
    container) without this normalization is catastrophic: the install
    branch calls ``_remove_existing`` on it, which ``rmtree``s the
    whole directory and wipes every co-located skill. We normalize at
    the CLI boundary so every ``--target`` consumer (install, update,
    uninstall, path, diff) gets the same forgiving behaviour, and emit
    a one-line note when normalization actually happens so the user
    sees where the symlink lands.
    """
    target = Path(target_path)
    if target.name == _SKILL_NAME:
        return target
    normalized = target / _SKILL_NAME
    click.echo(
        f"note: --target {target_path} does not end in '{_SKILL_NAME}'; installing to {normalized}"
    )
    return normalized


def _remove_existing(target: Path) -> None:
    """Remove an existing symlink, junction, or empty real directory at target.

    Junctions are removed via ``rmdir`` (the junction itself, NOT its
    contents) — the path looks like an empty directory to the OS.
    ``shutil.rmtree`` would refuse on the real-symlink branch and
    could recurse into the junction's target on some Python versions,
    so each kind has its own removal path.

    Non-empty real directories are refused even when their basename is
    ``_SKILL_NAME``. Install/update only owns links it created; a copied
    or hand-managed directory may contain user edits and must be removed
    manually before reinstalling.
    """
    if target.is_symlink():
        target.unlink()
        return
    if _is_junction(target):
        target.rmdir()
        return
    if not target.is_dir():
        return

    import shutil

    entries = list(target.iterdir())
    if not entries:
        shutil.rmtree(target)
        return
    sample = ", ".join(sorted(e.name for e in entries)[:5])
    suffix = f" (and {len(entries) - 5} more)" if len(entries) > 5 else ""
    click.echo(
        f"error: refusing to overwrite {target}: it is a non-empty real "
        f"directory, not a managed symlink or junction "
        f"(contains: {sample}{suffix}). Remove it manually before reinstalling.",
        err=True,
    )
    sys.exit(1)


def _try_create_junction(target: Path, source: Path) -> tuple[bool, str]:
    """Create a Windows directory junction. Returns (success, stderr).

    Junctions don't require Developer Mode or admin rights, but they
    can't span volumes — if uv's tool dir lives on a different drive
    from the user's home, ``mklink /J`` fails with "Local volumes are
    required to complete the operation." Caller surfaces stderr in
    that case so the user can either run as admin or relocate.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError) as exc:
        return (False, f"mklink invocation failed: {exc}")
    if result.returncode == 0:
        return (True, "")
    return (False, (result.stderr or result.stdout).strip())


def _install_to(target: Path) -> None:
    """Link _skill/ directory to target path.

    POSIX: creates a real symlink. Windows: tries symlink first, then
    falls back to a directory junction if symlink creation is denied
    (the typical case on a stock Windows install — no admin, no
    Developer Mode). Both forms are functionally equivalent for the
    agent's read-only access.
    """
    skill_root = _skill_root()
    source = skill_root.resolve()

    _remove_existing(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.symlink(source, target, target_is_directory=True)
    except OSError as symlink_exc:
        if sys.platform != "win32":
            click.echo(f"error: symlink failed — {symlink_exc}", err=True)
            sys.exit(1)
        ok, junction_err = _try_create_junction(target, source)
        if ok:
            click.echo(
                f"installed: {target} → {source} "
                "(directory junction; symlink unavailable without "
                "Developer Mode/Administrator)"
            )
            return
        hint = (
            f"symlink failed: {symlink_exc}\n"
            f"junction fallback (mklink /J) also failed: {junction_err}\n"
            "Junctions require source and target on the same volume — "
            "if uv's tool dir and your home directory are on different "
            "drives, run terminal as Administrator or enable Developer "
            "Mode (Settings → Privacy & security → For developers)."
        )
        click.echo(f"error: skill install failed — {hint}", err=True)
        sys.exit(1)
    click.echo(f"installed: {target} → {source}")


def _uninstall_from(target: Path) -> None:
    """Remove a managed skill symlink or junction at target path."""
    if target.is_symlink():
        target.unlink()
        click.echo(f"uninstalled: removed symlink {target}")
    elif _is_junction(target):
        target.rmdir()
        click.echo(f"uninstalled: removed junction {target}")
    elif target.is_dir():
        click.echo(
            f"error: refusing to remove {target}: it is a real directory, "
            "not a managed symlink or junction. Remove it manually if it "
            "is not needed.",
            err=True,
        )
        sys.exit(1)
    else:
        click.echo(f"not installed at {target}", err=True)


def _unique_platforms() -> list[str]:
    """Return platform names, skipping duplicates that share paths."""
    seen_paths: set[Path] = set()
    result: list[str] = []
    for name, gpath in _GLOBAL_PATHS.items():
        if gpath not in seen_paths:
            seen_paths.add(gpath)
            result.append(name)
    return result


def _detect_platforms(cwd: Path, global_install: bool) -> list[str]:
    """Return unique platform IDs whose config directories exist.

    For global_install, checks home-level config dirs (e.g. ``~/.claude/``).
    For local installs, checks project-level dot-directories (e.g. ``.claude/``).
    Platforms sharing ``.agents/skills/`` are detected when ``.agents/`` exists.
    """
    detected: list[str] = []
    for platform in _unique_platforms():
        desc = _PLATFORM_BY_ID[platform]
        if global_install:
            config_dir = _GLOBAL_PATHS[platform].parent.parent
        else:
            config_dir = cwd / desc.local_dir.split("/")[0]

        if config_dir.exists():
            detected.append(platform)

    return detected


def _install_to_platforms(platforms: list[str], cwd: Path, global_install: bool) -> None:
    """Install to a specific set of platform directories."""
    for platform in platforms:
        if global_install:
            _install_to(_GLOBAL_PATHS[platform])
        else:
            local_dir = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME
            _install_to(local_dir)


def _uninstall_from_platforms(platforms: list[str], cwd: Path, global_install: bool) -> None:
    """Uninstall from a specific set of platform directories."""
    for platform in platforms:
        if global_install:
            _uninstall_from(_GLOBAL_PATHS[platform])
        else:
            target = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME
            _uninstall_from(target)


def _resolve_detect_platforms(
    detect: bool,
    all_flag: bool,
    cwd: Path,
    global_install: bool,
) -> list[str] | None:
    """Resolve which platforms to operate on from --detect / --all flags.

    Returns a list of platform IDs, or None if neither flag is set
    (single-platform mode handled by caller).
    """
    if detect:
        platforms = _detect_platforms(cwd, global_install)
        if not platforms:
            click.echo("No agent platforms detected on this system.", err=True)
            click.echo(
                "Install an agent (e.g. Claude Code, Cursor) or use --all to "
                "install to all supported platforms.",
                err=True,
            )
            sys.exit(1)
        return platforms
    if all_flag:
        return _unique_platforms()
    return None


# ── CLI group ──────────────────────────────────────────────────────────


@click.group(name="skill")
def skill_group() -> None:
    """Deploy maxcompute-semantic skill to agent platforms."""


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(format=obj.get("format", "plain"), quiet=obj.get("quiet", False))


@skill_group.command(name="catalog")
@click.option("--json", "json_mode", is_flag=True, help="emit machine-readable JSON")
@click.pass_context
def catalog_cmd(ctx: click.Context, json_mode: bool) -> None:
    """List runtime skills served by `mcs skill get`."""
    skills = [s for s in discover_runtime_skills() if not s.hidden]
    data = [{"name": skill.name, "description": skill.description} for skill in skills]
    r = _renderer(ctx)
    if r.is_envelope:
        r.success({"skills": data})
        return
    if json_mode:
        click.echo(
            json.dumps(
                {"success": True, "data": data},
                ensure_ascii=False,
            )
        )
        return
    if not skills:
        click.echo("No runtime skills found")
        return
    width = max(len(skill.name) for skill in skills)
    for skill in skills:
        click.echo(f"  {skill.name:<{width}}  {skill.description}")


@skill_group.command(name="get")
@click.argument("names", nargs=-1)
@click.option("--all", "get_all", is_flag=True, help="output every runtime skill")
@click.option("--full", is_flag=True, help="include references and templates")
@click.option("--json", "json_mode", is_flag=True, help="emit machine-readable JSON")
@click.pass_context
def get_cmd(
    ctx: click.Context,
    names: tuple[str, ...],
    get_all: bool,
    full: bool,
    json_mode: bool,
) -> None:
    """Output runtime skill content."""
    all_skills = discover_runtime_skills()
    if get_all:
        skills = [s for s in all_skills if not s.hidden]
    else:
        if not names:
            raise click.ClickException("No skill name provided. Usage: mcs skill get <name>")
        skills = []
        for name in names:
            match = next((s for s in all_skills if s.name == name), None)
            if match is None:
                raise click.ClickException(f"Skill not found: {name}")
            skills.append(match)

    payload = []
    for skill in skills:
        item: dict[str, object] = {
            "name": skill.name,
            "content": (skill.dir / "SKILL.md").read_text(encoding="utf-8"),
        }
        if full:
            item["files"] = [
                {"path": path, "content": content}
                for path, content in collect_supplementary_files(skill.dir)
            ]
        payload.append(item)

    r = _renderer(ctx)
    if r.is_envelope:
        r.success({"skills": payload})
        return

    if json_mode:
        click.echo(json.dumps({"success": True, "data": payload}, ensure_ascii=False))
        return

    for idx, skill in enumerate(skills):
        if idx:
            click.echo("\n---\n")
        content = (skill.dir / "SKILL.md").read_text(encoding="utf-8")
        click.echo(content, nl=not content.endswith("\n"))
        if full:
            for path, body in collect_supplementary_files(skill.dir):
                click.echo(f"\n--- {path} ---\n")
                click.echo(body, nl=not body.endswith("\n"))


_DETECT_OPTION = click.option(
    "-D",
    "--detect",
    is_flag=True,
    help="install only to detected (installed) agent platforms",
)


@skill_group.command(name="list")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install paths (default: current dir)",
)
@_DETECT_OPTION
def list_cmd(cwd_path: str | None, detect: bool) -> None:
    """Show installed skill locations.

    Iterates ``_unique_platforms()`` so platforms whose global paths
    collapse onto the same directory (e.g. codex == agents both at
    ``~/.agents/skills/``) report once instead of twice — matches
    the dedup behavior of ``--all`` install/uninstall (review issue
    B15).
    """
    cwd = Path(cwd_path) if cwd_path else Path.cwd()
    if detect:
        # ``list`` reports both global and local status per platform, so
        # ``--detect`` should treat a platform as present when *either*
        # its global home config dir or its local project dot-dir exists.
        seen: set[str] = set()
        platforms = []
        for p in _detect_platforms(cwd, True) + _detect_platforms(cwd, False):
            if p not in seen:
                seen.add(p)
                platforms.append(p)
    else:
        platforms = _unique_platforms()
    for platform in platforms:
        global_path = _GLOBAL_PATHS[platform]
        local_path = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME
        if global_path.is_symlink():
            g_status = f"global: symlink → {global_path.resolve()}"
        elif _is_junction(global_path):
            g_status = f"global: junction → {global_path.resolve()}"
        elif global_path.is_dir():
            g_status = "global: directory"
        else:
            g_status = "global: not installed"
        if local_path.is_symlink():
            l_status = f"local: symlink → {local_path.resolve()}"
        elif _is_junction(local_path):
            l_status = f"local: junction → {local_path.resolve()}"
        elif local_path.is_dir():
            l_status = "local: directory"
        else:
            l_status = "local: not installed"
        click.echo(f"  {platform}:")
        click.echo(f"    {g_status}")
        click.echo(f"    {l_status}")


@skill_group.command(name="install")
@click.option("-g", "global_install", is_flag=True, help="install to home directory (global)")
@click.option(
    "--all", "install_all", is_flag=True, help="install to every supported agent platform"
)
@_DETECT_OPTION
@click.option(
    "-p",
    "--platform",
    type=PLATFORM_TYPE,
    default="agents",
    help="agent platform (default: agents)",
)
@click.option("--target", "target_path", type=click.Path(), help="custom install directory")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install (default: current dir)",
)
def install_cmd(
    global_install: bool,
    install_all: bool,
    detect: bool,
    platform: str,
    target_path: str | None,
    cwd_path: str | None,
) -> None:
    """Deploy skill via symlink. Default: local. -g: global. --all: all platforms."""
    cwd = Path(cwd_path) if cwd_path else Path.cwd()
    if target_path:
        _install_to(_normalize_target(target_path))
        return

    bulk = _resolve_detect_platforms(detect, install_all, cwd, global_install)
    if bulk is not None:
        _install_to_platforms(bulk, cwd, global_install)
        return

    if global_install:
        _install_to(_GLOBAL_PATHS[platform])
    else:
        local_dir = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME
        _install_to(local_dir)


@skill_group.command(name="update")
@click.option("-g", "global_install", is_flag=True, help="update global install")
@click.option("--all", "update_all", is_flag=True, help="update every supported agent platform")
@_DETECT_OPTION
@click.option(
    "-p",
    "--platform",
    type=PLATFORM_TYPE,
    default="agents",
    help="agent platform",
)
@click.option("--target", "target_path", type=click.Path(), help="custom install directory")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install (default: current dir)",
)
def update_cmd(
    global_install: bool,
    update_all: bool,
    detect: bool,
    platform: str,
    target_path: str | None,
    cwd_path: str | None,
) -> None:
    """Re-symlink (identical to install — symlinks always match package)."""
    cwd = Path(cwd_path) if cwd_path else Path.cwd()
    if target_path:
        _install_to(_normalize_target(target_path))
        return

    bulk = _resolve_detect_platforms(detect, update_all, cwd, global_install)
    if bulk is not None:
        _install_to_platforms(bulk, cwd, global_install)
        return

    if global_install:
        _install_to(_GLOBAL_PATHS[platform])
    else:
        local_dir = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME
        _install_to(local_dir)


@skill_group.command(name="uninstall")
@click.option("-g", "global_install", is_flag=True, help="uninstall global install")
@click.option(
    "--all", "uninstall_all", is_flag=True, help="uninstall from every supported agent platform"
)
@_DETECT_OPTION
@click.option(
    "-p",
    "--platform",
    type=PLATFORM_TYPE,
    default="agents",
    help="agent platform",
)
@click.option("--target", "target_path", type=click.Path(), help="custom install directory")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install (default: current dir)",
)
@click.confirmation_option(prompt="Remove deployed skill symlink?")
def uninstall_cmd(
    global_install: bool,
    uninstall_all: bool,
    detect: bool,
    platform: str,
    target_path: str | None,
    cwd_path: str | None,
) -> None:
    """Remove deployed skill symlink."""
    cwd = Path(cwd_path) if cwd_path else Path.cwd()
    if target_path:
        target = _normalize_target(target_path)
        if target.is_symlink() or _is_junction(target) or target.is_dir():
            _uninstall_from(target)
        else:
            click.echo(f"not installed at {target}", err=True)
            sys.exit(1)
        return

    bulk = _resolve_detect_platforms(detect, uninstall_all, cwd, global_install)
    if bulk is not None:
        _uninstall_from_platforms(bulk, cwd, global_install)
        return

    if global_install:
        target = _GLOBAL_PATHS[platform]
    else:
        target = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME

    if target.is_symlink() or _is_junction(target) or target.is_dir():
        _uninstall_from(target)
    else:
        click.echo(f"not installed at {target}", err=True)
        sys.exit(1)


@skill_group.command(name="path")
@click.option("-g", "global_install", is_flag=True, help="show global path")
@click.option(
    "--all", "path_all", is_flag=True, help="show paths for every supported agent platform"
)
@_DETECT_OPTION
@click.option(
    "-p",
    "--platform",
    type=PLATFORM_TYPE,
    default="agents",
    help="agent platform",
)
@click.option("--target", "target_path", type=click.Path(), help="custom install directory")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install (default: current dir)",
)
def path_cmd(
    global_install: bool,
    path_all: bool,
    detect: bool,
    platform: str,
    target_path: str | None,
    cwd_path: str | None,
) -> None:
    """Print deployment path."""
    cwd = Path(cwd_path) if cwd_path else Path.cwd()
    if target_path:
        click.echo(str(_normalize_target(target_path)))
        return

    bulk = _resolve_detect_platforms(detect, path_all, cwd, global_install)
    if bulk is not None:
        for plat in bulk:
            if global_install:
                click.echo(f"{plat}: {_GLOBAL_PATHS[plat]}")
            else:
                click.echo(f"{plat}: {cwd / _LOCAL_DIRS[plat] / _SKILL_NAME}")
        return

    if global_install:
        click.echo(str(_GLOBAL_PATHS[platform]))
    else:
        click.echo(str(cwd / _LOCAL_DIRS[platform] / _SKILL_NAME))


@skill_group.command(name="diff")
@click.option("-g", "global_install", is_flag=True, help="check global install")
@click.option("--all", "diff_all", is_flag=True, help="check every supported agent platform")
@_DETECT_OPTION
@click.option(
    "-p",
    "--platform",
    type=PLATFORM_TYPE,
    default="agents",
    help="agent platform",
)
@click.option("--target", "target_path", type=click.Path(), help="custom install directory")
@click.option(
    "--cwd",
    "cwd_path",
    type=click.Path(),
    default=None,
    help="project directory for local install (default: current dir)",
)
def diff_cmd(
    global_install: bool,
    diff_all: bool,
    detect: bool,
    platform: str,
    target_path: str | None,
    cwd_path: str | None,
) -> None:
    """Check if symlink points to current package _skill/."""
    skill_root = _skill_root()
    cwd = Path(cwd_path) if cwd_path else Path.cwd()

    if target_path:
        _check_diff(_normalize_target(target_path), skill_root, fatal=True)
        return

    bulk = _resolve_detect_platforms(detect, diff_all, cwd, global_install)
    if bulk is not None:
        for plat in bulk:
            if global_install:
                target = _GLOBAL_PATHS[plat]
            else:
                target = cwd / _LOCAL_DIRS[plat] / _SKILL_NAME
            _check_diff(target, skill_root, fatal=False)
        return

    if global_install:
        target = _GLOBAL_PATHS[platform]
    else:
        target = cwd / _LOCAL_DIRS[platform] / _SKILL_NAME

    _check_diff(target, skill_root, fatal=True)


def _check_diff(target: Path, skill_root: Path, fatal: bool = False) -> None:
    """Print diff status for a single target path.

    fatal=True: sys.exit(1) if not installed (single-platform mode).
    fatal=False: just print warning (--all mode).
    """
    if not target.exists():
        click.echo(f"not installed at {target}", err=True)
        if fatal:
            sys.exit(1)
        return

    expected = skill_root.resolve()
    actual = target.resolve()

    is_link = target.is_symlink() or _is_junction(target)
    link_kind = "symlink" if target.is_symlink() else "junction"
    if is_link and actual == expected:
        click.echo(f"{target}: {link_kind} matches current package ({expected})")
    elif is_link and actual != expected:
        click.echo(f"{target}: STALE — points to {actual}, expected {expected}")
        click.echo("run 'mcs skill update' to fix")
    elif target.is_dir() and not is_link:
        click.echo(f"{target}: not a symlink (copy-based install)")
        click.echo("run 'mcs skill install' to switch to symlink")
