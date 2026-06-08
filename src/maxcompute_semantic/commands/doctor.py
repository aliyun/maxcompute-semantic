# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs doctor — run a series of health checks and report status.

Each check is a lightweight, read-only probe.  No mutations, no writes.
Checks are ordered by dependency: profile before auth, auth before
connectivity, connectivity before build data.  A failing check early in
the chain means subsequent dependent checks are skipped (reported as
"skipped: prerequisite failed").

Plain mode prints a line per check with ✅/❌/⚠️ emoji prefix aligned
to short display names; JSON mode emits an envelope with all check
results.  Exit code 0 if all passed, 1 if any failed.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import urllib.parse
from pathlib import Path

import click

from maxcompute_semantic import __version__
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import (
    config_dir,
    link_json_path,
    profile_data_dir,
    profiles_yaml_path,
)
from maxcompute_semantic._internal.update_check import (
    CacheEntry,
    LatestMetadata,
    _base_url,
    fetch_latest_metadata,
    is_disabled,
    write_cache,
)
from maxcompute_semantic.auth.context import resolve_profile_for_project
from maxcompute_semantic.auth.errors import NoProfilesConfiguredError, ProfileNotFoundError
from maxcompute_semantic.auth.resolver import resolve_profile
from maxcompute_semantic.commands.skill import _skill_root
from maxcompute_semantic.mc_client.errors import McsError


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )


CheckResult = tuple[str, str, str | None]
# (name, status, detail)  status: "pass" | "fail" | "skip" | "warn"
#
# ``warn`` was introduced in T19 (versioning-related checks) for
# informational signals that are neither a clean pass nor a blocking
# failure. The doctor's exit-code logic treats ``warn`` like ``skip``
# — present in the summary tally but does not trip exit code 1. The
# renderer maps ``warn`` to a yellow ``⚠️`` line that is visually
# distinct from a green ``✅`` pass.


def _check_version() -> CheckResult:
    return ("version", "pass", f"mcs {__version__}")


def _check_config_dir() -> CheckResult:
    cdir = config_dir()
    if cdir.exists():
        return ("config_dir", "pass", str(cdir))
    return ("config_dir", "skip", f"{cdir} not created yet; run `mcs profile create` first")


def _check_profiles_yaml() -> CheckResult:
    ypath = profiles_yaml_path()
    if not ypath.exists():
        return ("profiles_yaml", "skip", "no profiles.yaml; will use env-var fallback")
    try:
        from maxcompute_semantic.auth.profile_store import _read_raw

        data = _read_raw()
        names = list(data.get("profiles", {}).keys())
        if not names:
            return ("profiles_yaml", "fail", "profiles.yaml exists but defines no profiles")
        return ("profiles_yaml", "pass", f"{len(names)} profile(s): {', '.join(names)}")
    except Exception as exc:
        return ("profiles_yaml", "fail", f"cannot read profiles.yaml: {exc}")


def _check_config_permissions() -> CheckResult:
    """Warn if profiles.yaml or its parent directory have overly permissive modes.

    profiles.yaml may contain access-key secrets. The parent config
    directory should be owner-only (0700) and the file itself should
    be owner-read/write only (0600).  Any group or world bits are
    flagged as a warning with a remediation command.
    """
    ypath = profiles_yaml_path()
    if not ypath.exists():
        return ("config_permissions", "skip", "no profiles.yaml to check")

    issues: list[str] = []

    # Check parent directory permissions.
    try:
        dir_mode = stat.S_IMODE(os.stat(ypath.parent).st_mode)
        if dir_mode & 0o077:
            issues.append(
                f"config dir {ypath.parent} has mode {oct(dir_mode)} "
                f"(expected 0o700); run: chmod 700 {ypath.parent}"
            )
    except OSError as exc:
        issues.append(f"cannot stat config dir: {exc}")

    # Check profiles.yaml file permissions.
    try:
        file_mode = stat.S_IMODE(os.stat(ypath).st_mode)
        if file_mode & 0o077:
            issues.append(
                f"profiles.yaml has mode {oct(file_mode)} (expected 0o600); run: chmod 600 {ypath}"
            )
    except OSError as exc:
        issues.append(f"cannot stat profiles.yaml: {exc}")

    if not issues:
        return (
            "config_permissions",
            "pass",
            "profiles.yaml and config dir have restrictive permissions",
        )

    return ("config_permissions", "warn", "; ".join(issues))


def _env_fallback_is_active(profile_name: str | None) -> bool:
    if profile_name is not None:
        return False
    if not os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"):
        return False
    try:
        resolve_profile(name=None)
    except NoProfilesConfiguredError:
        return True
    except ProfileNotFoundError:
        return False
    return False


def _check_env_fallback_endpoint(profile_name: str | None = None) -> CheckResult:
    if not _env_fallback_is_active(profile_name):
        return ("env_fallback_endpoint", "skip", "not using env-var fallback")

    endpoint = os.environ.get("MAXCOMPUTE_ENDPOINT", "https://service.odps.aliyun.com/api")
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname or ""
    known_suffixes = (
        ".maxcompute.aliyun.com",
        ".odps.aliyun.com",
    )
    if host.endswith(known_suffixes):
        return ("env_fallback_endpoint", "pass", endpoint)
    return (
        "env_fallback_endpoint",
        "warn",
        "custom env fallback endpoint detected; custom/internal endpoints are supported, "
        "but verify MAXCOMPUTE_ENDPOINT if this was not intentional: "
        f"{endpoint}",
    )


def _check_link_json() -> CheckResult:
    lpath = link_json_path()
    if not lpath.exists():
        return ("link_json", "skip", "no link.json; run `mcs link bind NAME` to bind cwd")
    try:
        raw = json.loads(lpath.read_text(encoding="utf-8"))
        bound = raw.get(os.getcwd(), raw.get(str(Path.cwd())))
        if bound:
            return ("link_json", "pass", f"cwd → profile '{bound}'")
        return (
            "link_json",
            "skip",
            f"cwd {os.getcwd()} not bound; run `mcs link bind NAME`",
        )
    except (json.JSONDecodeError, OSError) as exc:
        return ("link_json", "fail", f"cannot read link.json: {exc}")


def _check_profile_resolution(profile_name: str | None) -> tuple[CheckResult, object | None]:
    """Try to resolve a Profile via the standard chain.

    Returns (check_result, profile_or_None) so downstream checks can
    use the resolved profile if it succeeded.
    """
    try:
        p = resolve_profile_for_project(None, profile_name=profile_name)
        if not p.compute_project:
            detail = "env-var fallback has no compute_project; set MAXCOMPUTE_PROJECT"
            return (("profile_resolution", "fail", detail), None)
        # Describe which slot resolved. The resolver already chose;
        # we just annotate for the user.
        if profile_name:
            slot = "--profile"
        elif os.environ.get("MCS_PROFILE"):
            slot = "MCS_PROFILE"
        elif link_json_path().exists():
            try:
                raw = json.loads(link_json_path().read_text())
                slot = "cwd-link" if raw.get(os.getcwd()) else "env-vars"
            except Exception:
                slot = "env-vars"
        elif os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"):
            slot = "env-vars"
        else:
            slot = "unknown"
        detail = f"profile '{p.name}' via {slot}"
        return (("profile_resolution", "pass", detail), p)
    except McsError as exc:
        return (("profile_resolution", "fail", exc.message), None)


def _check_auth(p: object) -> CheckResult:
    if p is None:
        return ("auth", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.credential import resolve_credentials
    from maxcompute_semantic.auth.schema import AkAuth, Profile

    assert isinstance(p, Profile)
    try:
        creds = resolve_credentials(p.auth)
        cred_type = "AK" if isinstance(p.auth, AkAuth) else "process"
        has_token = bool(creds.security_token)
        detail = f"{cred_type} credentials resolved"
        if has_token:
            exp = creds.expiration
            detail += f" (STS, expires {exp.isoformat() if exp else 'unknown'})"
        return ("auth", "pass", detail)
    except McsError as exc:
        return ("auth", "fail", exc.message)
    except Exception as exc:
        return ("auth", "fail", str(exc))


def _check_connectivity(p: object) -> CheckResult:
    if p is None:
        return ("connectivity", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.mc_client.client import MaxComputeClient

    assert isinstance(p, Profile)
    try:
        client = MaxComputeClient(p)
        # assume_yes=True: SELECT 1 is a 0-byte connectivity probe; the
        # cost gate would otherwise re-run a cost estimate over a no-op.
        envelope = client.execute_sql("SELECT 1", hints={"odps.timeout": "30"}, assume_yes=True)
        if envelope.status == "success":
            return ("connectivity", "pass", f"SELECT 1 OK on {p.compute_project}")
        return ("connectivity", "fail", f"SELECT 1 returned status={envelope.status}")
    except McsError as exc:
        return ("connectivity", "fail", f"{exc.code}: {exc.message}")
    except Exception as exc:
        msg = str(exc)
        if len(msg) > 100:
            msg = msg[:100] + "..."
        return ("connectivity", "fail", msg)


def _check_tier(p: object) -> CheckResult:
    if p is None:
        return ("tier", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.mc_client.tier import get_tier

    assert isinstance(p, Profile)
    override = os.environ.get("MCS_TIER_OVERRIDE")
    if override:
        return ("tier", "pass", f"tier={override} (MCS_TIER_OVERRIDE)")

    pdir = profile_data_dir(p)
    cache_path = pdir / "tier_cache" / p.compute_project
    if cache_path.exists():
        cached = cache_path.read_text().strip()
        return ("tier", "pass", f"tier={cached}-level (cached)")

    try:
        from maxcompute_semantic.mc_client.client import MaxComputeClient

        client = MaxComputeClient(p)
        tier = get_tier(p, p.compute_project, client=client)
        return ("tier", "pass", f"tier={tier}-level (live probe)")
    except McsError as exc:
        return ("tier", "fail", f"{exc.code}: {exc.message}")
    except Exception as exc:
        msg = str(exc)
        if len(msg) > 100:
            msg = msg[:100] + "..."
        return ("tier", "fail", f"cannot probe tier: {msg}")


def _check_build_data(p: object) -> CheckResult:
    if p is None:
        return ("build_data", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.schema import Profile

    assert isinstance(p, Profile)
    pdir = profile_data_dir(p)
    db_path = pdir / "package.db"

    if not db_path.exists():
        return ("build_data", "skip", "no package.db; run `mcs build`")

    overview = pdir / "_overview.md"
    state = pdir / "_state.json"
    pieces = ["package.db"]
    if overview.exists():
        pieces.append("_overview.md")
    if state.exists():
        pieces.append("_state.json")

    # Per-source .md subdirectories (normal for all profiles since chain δ).
    source_dirs = [d for d in pdir.iterdir() if d.is_dir() and d.name != "tier_cache"]
    md_count = sum(len(list(d.glob("*.md"))) for d in source_dirs)
    pieces.append(f"{md_count} table .md files across {len(source_dirs)} source(s)")

    try:
        state_data = json.loads(state.read_text()) if state.exists() else {}
        last_built = state_data.get("last_built_at", "—")
        pieces.append(f"built {last_built}")
    except Exception:
        pass

    return ("build_data", "pass", ", ".join(pieces))


def _check_skill_install() -> CheckResult:
    """Check whether the skill is symlinked into any standard platform dir."""
    skill_root = _skill_root()
    # All 7 platforms from commands/skill.py.
    platforms = [
        ("claude-code (local)", Path(".claude/skills/maxcompute-semantic")),
        ("claude-code (global)", Path.home() / ".claude/skills/maxcompute-semantic"),
        ("agents (local)", Path(".agents/skills/maxcompute-semantic")),
        ("agents (global)", Path.home() / ".agents/skills/maxcompute-semantic"),
        ("cursor (local)", Path(".cursor/skills/maxcompute-semantic")),
        ("cursor (global)", Path.home() / ".cursor/skills/maxcompute-semantic"),
        ("gemini (global)", Path.home() / ".gemini/skills/maxcompute-semantic"),
        ("qwen (global)", Path.home() / ".qwen/skills/maxcompute-semantic"),
        ("opencode (global)", Path.home() / ".config/opencode/skills/maxcompute-semantic"),
    ]
    from maxcompute_semantic.commands.skill import _is_junction

    installed = []
    broken = []
    for label, target in platforms:
        if target.is_symlink() or _is_junction(target):
            resolved = target.resolve()
            if resolved == skill_root.resolve():
                installed.append(label)
            else:
                broken.append(f"{label} → {resolved} (expected {skill_root.resolve()})")
        elif target.is_dir():
            broken.append(f"{label}: directory instead of symlink")

    if installed:
        return ("skill_install", "pass", f"installed: {', '.join(installed)}")
    if broken:
        return ("skill_install", "fail", f"broken: {'; '.join(broken)}")
    return ("skill_install", "skip", "no skill symlink; run `mcs skill install`")


# ── versioning-related checks (T19) ────────────────────────────────


def _check_git_available() -> CheckResult:
    """Probe the system ``git`` binary. Fail if missing — versioning
    can't work without it. The remediation names ``MCS_NO_VERSIONING=1``
    as the explicit opt-out so the rest of mcs keeps working without
    versioning."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            check=False,
            timeout=5,
            text=True,
        )
    except FileNotFoundError:
        # Soft warn, not fail: the auto-commit hook silently
        # tolerates missing git (write succeeds, snapshot skipped).
        # Explicit versioning verbs still raise GitNotAvailable —
        # but `mcs build` / `annotate` / `memory` keep working, so
        # missing git is degraded-but-functional, not broken.
        return (
            "git_available",
            "warn",
            (
                "the `git` binary is not on PATH; per-profile "
                "versioning is disabled for this session. Install git "
                "(macOS: `brew install git` or `xcode-select --install`; "
                "Debian/Ubuntu: `apt-get install git`; "
                "RHEL/CentOS/Fedora: `yum install git`; "
                "Windows: `winget install --id Git.Git` or download from "
                "https://git-scm.com/download/win) "
                "— or set MCS_NO_VERSIONING=1 to silence this warning"
            ),
        )
    except subprocess.TimeoutExpired:
        return (
            "git_available",
            "fail",
            "`git --version` timed out after 5s (check PATH for a wrapper)",
        )
    if proc.returncode == 0:
        return ("git_available", "pass", proc.stdout.strip())
    return (
        "git_available",
        "fail",
        f"`git --version` exited {proc.returncode}: {proc.stderr.strip()}",
    )


def _check_profile_versioned(p: object) -> CheckResult:
    """Whether the resolved profile's data dir is a git repo.

    For fork-kind profiles the check inspects the parent's data dir
    (the parent's ``.git/`` is the shared object database that the
    fork's detached worktree reads from). Warn (not fail) when the
    profile pre-dates the per-profile versioning layer — the user can
    still read/write the profile; the auto-init on the next write
    upgrades it.
    """
    if p is None:
        return ("profile_versioned", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.errors import ProfileNotFoundError
    from maxcompute_semantic.auth.profile_store import get as get_profile_by_name
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.versioning.git_repo import GitRepo

    assert isinstance(p, Profile)
    if p.kind == "fork":
        try:
            parent = get_profile_by_name(p.parent_profile or "")
        except ProfileNotFoundError:
            return (
                "profile_versioned",
                "fail",
                (
                    f"fork {p.name!r} references parent "
                    f"{(p.parent_profile or '')!r} which is missing "
                    f"from profiles.yaml — the fork is a double-orphan"
                ),
            )
        repo = GitRepo(profile_data_dir(parent))
        subject_profile = parent.name
    else:
        repo = GitRepo(profile_data_dir(p))
        subject_profile = p.name

    if not repo.exists():
        return (
            "profile_versioned",
            "warn",
            (
                f"profile {subject_profile!r} is not versioned — run "
                f"`mcs profile enable-versioning --profile {subject_profile}` "
                f"to create the inaugural commit"
            ),
        )
    try:
        head_sha = repo.rev_parse("HEAD")
        subject = repo.commit_subject(head_sha)
    except Exception:
        return (
            "profile_versioned",
            "pass",
            f"profile {subject_profile!r} is versioned (no commits yet)",
        )
    return (
        "profile_versioned",
        "pass",
        f"profile {subject_profile!r} is versioned (HEAD {head_sha[:12]} — {subject})",
    )


def _check_working_tree_clean(p: object, prev_status: str) -> CheckResult:
    """Whether the profile's git working tree has any uncommitted
    changes. Warn (not fail) on dirty: the next mcs write command's
    hook packages the dirty state as a ``recover: pre-existing
    changes`` commit, so this is informational about the pending
    recovery rather than a blocking failure. Skip when the upstream
    ``profile_versioned`` check did not pass.
    """
    if prev_status != "pass":
        return (
            "working_tree_clean",
            "skip",
            "skipped: prerequisite ``profile_versioned`` did not pass",
        )
    if p is None:
        return ("working_tree_clean", "skip", "skipped: prerequisite failed")
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.versioning.git_repo import GitRepo

    assert isinstance(p, Profile)
    if p.kind == "fork":
        from maxcompute_semantic.auth.errors import ProfileNotFoundError
        from maxcompute_semantic.auth.profile_store import get as get_profile_by_name

        try:
            target = get_profile_by_name(p.parent_profile or "")
        except ProfileNotFoundError:
            return (
                "working_tree_clean",
                "skip",
                "skipped: fork parent missing from profiles.yaml",
            )
    else:
        target = p

    repo = GitRepo(profile_data_dir(target))
    try:
        dirty = repo.has_uncommitted_changes()
    except Exception as exc:
        return ("working_tree_clean", "fail", f"cannot read working tree: {exc}")
    if not dirty:
        return ("working_tree_clean", "pass", "profile working tree is clean")
    # Count the dirty entries for the detail line.
    try:
        from maxcompute_semantic.versioning.git_repo import GitRepo as _GR

        status_out = _GR(profile_data_dir(target))._run("status", "--porcelain", check=True)
        n = sum(1 for ln in status_out.splitlines() if ln.strip())
    except Exception:
        n = 0
    return (
        "working_tree_clean",
        "warn",
        (
            f"{n} uncommitted change(s) in the working tree — the next "
            f"write command will roll them into a `recover: pre-existing "
            f"changes` commit"
        ),
    )


def _check_forks_healthy() -> CheckResult:
    """System-level fork audit. Walks every fork in profiles.yaml
    regardless of parent. Pass if every fork is healthy; warn if any
    orphan / ghost; fail if any double-orphan (parent profile is gone
    from yaml — hand-edit fix-up needed). Read-only: never self-heals
    (the self-heal path is the explicit ``mcs profile fork-list`` verb).
    """
    from maxcompute_semantic.auth.errors import ProfileNotFoundError
    from maxcompute_semantic.auth.profile_store import get as get_profile_by_name
    from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
    from maxcompute_semantic.versioning.errors import GitNotAvailable
    from maxcompute_semantic.versioning.git_repo import GitRepo

    try:
        all_profiles = load_all_profiles()
    except Exception as exc:
        return ("forks_healthy", "skip", f"cannot read profiles.yaml: {exc}")
    forks = [f for f in all_profiles.values() if f.kind == "fork"]
    if not forks:
        return ("forks_healthy", "pass", "no forks registered")

    healthy = 0
    orphan = 0
    ghost = 0
    double_orphan: list[str] = []

    for fork in forks:
        try:
            parent = get_profile_by_name(fork.parent_profile or "")
        except ProfileNotFoundError:
            double_orphan.append(fork.name)
            continue

        parent_dir = profile_data_dir(parent)
        repo = GitRepo(parent_dir)
        if not repo.exists():
            orphan += 1
            continue

        wt_path = Path(fork.package_path) if fork.package_path is not None else None
        if wt_path is None or not wt_path.is_dir():
            ghost += 1
            continue

        try:
            ok = repo.merge_base_is_ancestor(fork.git_sha or "", "HEAD")
        except GitNotAvailable:
            return (
                "forks_healthy",
                "skip",
                "git binary not available; fork-state determination skipped",
            )
        except Exception:
            ok = False
        if ok:
            healthy += 1
        else:
            orphan += 1

    total = len(forks)
    if double_orphan:
        return (
            "forks_healthy",
            "fail",
            (
                f"{len(double_orphan)} double-orphan fork(s) — parent "
                f"profile missing from profiles.yaml: "
                f"{', '.join(sorted(double_orphan))}. Hand-edit fix-up "
                f"needed; see `mcs profile fork-list` for the affected "
                f"names."
            ),
        )
    if orphan or ghost:
        return (
            "forks_healthy",
            "warn",
            (
                f"{total} fork(s): {healthy} healthy, {orphan} orphan, "
                f"{ghost} ghost (run `mcs profile fork-list` to inspect "
                f"and self-heal the ghost entries)"
            ),
        )
    return ("forks_healthy", "pass", f"{total} fork(s), all healthy")


def _check_inference_logic_current() -> CheckResult:
    """System-level audit: profiles whose ``package.db`` was last
    derived under an older :data:`INFERENCE_LOGIC_VERSION` than the
    CLI now ships. Read-only. Profiles without a built ``package.db``
    are not stale — they're simply unbuilt and counted as ``ok``.

    Triggered when the user upgrades ``mcs`` and the new release
    shipped a bumped logic-version constant; the remediation is the
    offline ``mcs build --refresh`` path which re-runs Phase 7c +
    re-renders markdown using already-cached sample data, no MC
    round-trips.
    """
    from maxcompute_semantic.auth.profile_store import load_all as load_all_profiles
    from maxcompute_semantic.build._logic_version import INFERENCE_LOGIC_VERSION
    from maxcompute_semantic.build.storage import PackageDB

    try:
        all_profiles = load_all_profiles()
    except Exception as exc:
        return ("inference_logic_current", "skip", f"cannot read profiles.yaml: {exc}")
    if not all_profiles:
        return (
            "inference_logic_current",
            "pass",
            "no profiles registered",
        )

    stale: list[tuple[str, int]] = []
    checked = 0
    for prof in all_profiles.values():
        pdir = profile_data_dir(prof)
        db_path = pdir / "package.db"
        if not db_path.is_file():
            continue
        try:
            db = PackageDB(db_path)
        except Exception:
            continue
        try:
            stored = db.get_inference_logic_version()
        finally:
            db.close()
        checked += 1
        if stored < INFERENCE_LOGIC_VERSION:
            stale.append((prof.name, stored))

    if not checked:
        return (
            "inference_logic_current",
            "pass",
            "no built profiles",
        )
    if not stale:
        return (
            "inference_logic_current",
            "pass",
            f"{checked} profile(s) on logic v{INFERENCE_LOGIC_VERSION}",
        )
    names = ", ".join(f"{n} (v{v})" for n, v in sorted(stale))
    return (
        "inference_logic_current",
        "warn",
        (
            f"{len(stale)} of {checked} profile(s) built under older "
            f"inference logic (current v{INFERENCE_LOGIC_VERSION}): "
            f"{names}. Run `mcs build --refresh --profile <name>` on "
            f"each to re-derive offline (no MaxCompute calls)."
        ),
    )


def _check_package_sql_parses(p: object) -> CheckResult:
    """Read the profile's ``package.sql`` and execute it against an
    in-memory sqlite3 connection to confirm it parses. On failure,
    point at ``mcs profile reset --to <previous-sha>`` and name the
    most-recent prior commit that touched ``package.sql`` as the
    candidate target (sourced via ``git log -- package.sql``)."""
    if p is None:
        return ("package_sql_parses", "skip", "skipped: prerequisite failed")
    import sqlite3

    from maxcompute_semantic.auth.schema import Profile

    assert isinstance(p, Profile)
    pdir = profile_data_dir(p)
    sql_path = pdir / "package.sql"
    if not sql_path.exists():
        return (
            "package_sql_parses",
            "skip",
            "no package.sql on disk (profile may pre-date versioning or never built)",
        )
    try:
        sql_text = sql_path.read_text(encoding="utf-8")
    except OSError as exc:
        return ("package_sql_parses", "fail", f"cannot read {sql_path}: {exc}")

    try:
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(sql_text)
    except sqlite3.DatabaseError as exc:
        # Find the most-recent prior commit that touched package.sql,
        # if the profile is git-versioned. The newest commit is the
        # corrupting one; the previous one is the rollback target.
        from maxcompute_semantic.versioning.git_repo import GitRepo

        repo = GitRepo(pdir)
        target_hint = ""
        if repo.exists():
            try:
                history = repo.log(limit=2, paths=("package.sql",))
                if len(history) >= 2:
                    target_hint = (
                        f" Roll back via "
                        f"`mcs profile reset --to {history[1].short_sha} "
                        f"--profile {p.name}`."
                    )
            except Exception:
                pass
        return (
            "package_sql_parses",
            "fail",
            (f"package.sql at {sql_path} failed to parse: {exc}.{target_hint}"),
        )

    line_count = sum(1 for _ in sql_text.splitlines())
    return (
        "package_sql_parses",
        "pass",
        f"package.sql parses cleanly ({line_count} lines)",
    )


def _run_update_check_fetch() -> tuple[LatestMetadata | None, str]:
    """Run the shared fetch for both update-checks.

    Returns ``(metadata_or_none, error_summary)``. ``error_summary``
    is the short human-readable string the channel-reachable check
    prints on FAIL — it pulls the exception class name out of the
    fetcher's various failure modes (DNS, refused, 5xx, bad JSON,
    bad schema). The fetcher itself returns ``None`` on every
    failure mode without a discriminator, so to distinguish for the
    user-facing message we re-run a thin variant that lets the
    exception surface, captures the type, and lets ``None`` mean
    "no error, the metadata is in the first return slot."

    Side effect: on a successful fetch, the result is written to the
    update-check cache so the *next* foreground mcs command's
    banner reflects the same state without firing its own daemon
    probe. The spec calls this "doctor doubles as a cache warmup."
    Cache write failures are silent — the doctor output isn't
    affected.
    """
    import urllib.error
    import urllib.request

    from maxcompute_semantic import __version__

    md = fetch_latest_metadata()
    if md is None:
        # Re-issue to capture the exception class for the message.
        # Same timeout; the second probe is in the noise compared to
        # the first one's timeout already elapsing.
        url = f"{_base_url()}/latest.json"
        try:
            with urllib.request.urlopen(url, timeout=5.0) as resp:
                _body = resp.read(64 * 1024)
        except urllib.error.HTTPError as e:
            return (None, f"HTTP {e.code} from {url}")
        except urllib.error.URLError as e:
            return (None, f"URLError from {url}: {e.reason}")
        except TimeoutError:
            return (None, f"timed out talking to {url}")
        except OSError as e:
            return (None, f"OSError from {url}: {e}")
        # Got a body but the first fetch said None — so the body
        # parsed-as-JSON or the schema check failed. Try once more to
        # pull a discriminating error.
        try:
            parsed = json.loads(_body.decode("utf-8"))
            from maxcompute_semantic._internal.update_check import (
                LatestMetadata as _LM,
            )
            from maxcompute_semantic._internal.update_check import (
                MalformedMetadataError,
                UnsupportedSchemaError,
            )

            _LM.from_dict(parsed)
            return (None, f"{url} response shape is not the expected metadata")
        except UnsupportedSchemaError as e:
            return (None, f"unsupported schema_version: {e}")
        except MalformedMetadataError as e:
            return (None, f"malformed metadata: {e}")
        except Exception as e:
            return (None, f"unparseable response from {url}: {e}")

    # Successful fetch — warm the banner cache so the next foreground
    # mcs command's finally-block-read sees the up-to-date state.
    with contextlib.suppress(Exception):
        write_cache(CacheEntry.from_fetch(md, current_version=__version__))
    return (md, "")


def _check_update_channel_reachable(
    fetch_result: tuple[LatestMetadata | None, str],
) -> CheckResult:
    """The fetch closure's result is the input; this just renders the
    pass/fail line.

    Pass: metadata is non-None. The detail line shows the resolved
    base URL and the published ``latest_version`` for cross-reference
    with the version-current check on the next line.

    Fail: metadata is None. The detail line includes the short error
    summary and a remediation pointer at the ``MCS_UPDATE_BASE_URL``
    env var override.
    """
    md, err = fetch_result
    if md is None:
        return (
            "update_channel",
            "fail",
            (
                f"could not reach {_base_url()}/latest.json: {err}. "
                f"Override with MCS_UPDATE_BASE_URL or check connectivity "
                f"(corporate proxy / DNS). The banner and the auto-upgrade "
                f"flow both read from this endpoint."
            ),
        )
    return (
        "update_channel",
        "pass",
        f"{_base_url()}/latest.json reachable; latest_version={md.latest_version}",
    )


def _check_update_version_current(
    fetch_result: tuple[LatestMetadata | None, str],
) -> CheckResult:
    """The shared-fetch consumer for the version comparison.

    Skip-with-prereq-failed when ``fetch_result`` is (None, _) — the
    channel-reachable check just printed the network error, so we
    don't duplicate it here.

    Otherwise consult ``is_disabled`` for the hard-block path and
    compare the running version against ``latest_version`` for the
    soft "upgrade available" path. The status taxonomy:

      * PASS: ``Version(current) >= Version(latest_version)``. Detail:
        "mcs <current> is at or ahead of published latest
        <latest_version>".
      * SKIP-info: an upgrade is available but the running version is
        not in disabled[]/below min. Detail: "upgrade available:
        <current> → <latest>; run `mcs update`". The doctor's
        overall exit code is unaffected (SKIP doesn't trip the
        "any fail" rule).
      * FAIL: ``is_disabled`` returns True. Detail: the human-readable
        reason from ``is_disabled``.

    The current version comes from ``maxcompute_semantic.__version__``
    (which uses ``importlib.metadata.version`` so it reflects what pip
    or uv installed, not whatever pyproject.toml says).
    """
    md, _err = fetch_result
    if md is None:
        return (
            "update_version",
            "skip",
            "skipped: update_channel check failed (see line above)",
        )

    from packaging.version import InvalidVersion, Version

    from maxcompute_semantic import __version__

    blocked, reason = is_disabled(md, __version__)
    if blocked:
        return ("update_version", "fail", reason)

    try:
        cur_v = Version(__version__)
        new_v = Version(md.latest_version)
    except InvalidVersion as e:
        # Running an unparseable version (the ``0+unknown`` fallback)
        # is reported as a skip with a hint.
        return (
            "update_version",
            "skip",
            (
                f"could not compare versions: {e} "
                f"(current={__version__!r}, "
                f"latest={md.latest_version!r}). "
                f"Reinstalling mcs from a known wheel fixes "
                f"``__version__``'s fallback path."
            ),
        )

    if cur_v >= new_v:
        return (
            "update_version",
            "pass",
            f"mcs {__version__} is at or ahead of published latest {md.latest_version}",
        )

    notice_suffix = f" (notice: {md.notice!r})" if md.notice else ""
    # An available upgrade is informational only — the doctor's any-
    # fail exit-code rule says SKIP doesn't trip the failure. We use
    # the "skip" status with a body that explicitly says "info" so the
    # human output is readable. The standard skip-emoji from the
    # existing doctor table (⚠️) is the right tone for "actionable
    # advisory."
    return (
        "update_version",
        "skip",
        (
            f"upgrade available: {__version__} → {md.latest_version}"
            f"{notice_suffix}. Run `mcs update` to upgrade."
        ),
    )


@click.command("doctor")
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--offline",
    is_flag=True,
    help="skip network probes (auth, connectivity, tier, update channel)",
)
@click.pass_context
def doctor_cmd(ctx: click.Context, profile: str | None, offline: bool) -> None:
    """Run health checks and report status.

    Checks: version, config dir, profiles.yaml, link.json, profile
    resolution, auth credentials, MaxCompute connectivity (SELECT 1),
    tier, build data, skill installation, update channel reachable,
    update version current.

    The two update checks share one HTTP fetch of
    ``MCS_UPDATE_BASE_URL/latest.json`` (default OSS host); the
    channel-reachable check is the "network up" verdict, the
    version-current check is the "is this version on the publisher's
    disabled list / behind min_supported / behind latest" verdict.
    The combined fetch result is also written to the update-check
    cache, so the next foreground mcs command's banner reflects the
    same state without firing its own probe.

    ``--offline`` skips all network probes including the update
    channel pair. Use it for CI pre-flight or local-only sanity
    checks.

    Exit code: 0 if no FAILs; 1 if any FAIL. The update-version
    "upgrade available" line is a SKIP (advisory only) and does not
    affect the exit code; the disabled / below-min cases are FAILs
    that do.
    """
    r = _renderer(ctx)

    # Plain mode streams each check line the moment that check returns,
    # so the slow network probes (auth, connectivity, tier, update
    # channel) don't make the whole command sit silent — the earlier
    # local checks paint immediately and the user watches the slow ones
    # land one at a time. Envelope (json/yaml) mode can't stream a
    # single document, so it batches and emits once at the end.
    streaming = not r.is_envelope

    # Short display names for alignment. ``max_name`` is computed from
    # the full known name set (not the running tally) so the column is
    # stable from the very first streamed line.
    _DISPLAY = {
        "version": "version",
        "config_dir": "config",
        "profiles_yaml": "profiles",
        "config_permissions": "permissions",
        "env_fallback_endpoint": "env endpoint",
        "link_json": "link",
        "profile_resolution": "profile",
        "git_available": "git",
        "profile_versioned": "versioned",
        "working_tree_clean": "clean",
        "forks_healthy": "forks",
        "inference_logic_current": "inference",
                "auth": "auth",
        "connectivity": "connect",
        "tier": "tier",
        "build_data": "build",
        "package_sql_parses": "package.sql",
        "skill_install": "skill",
        "update_channel": "update channel",
        "update_version": "update version",
    }
    _EMOJI = {"pass": "✅", "fail": "❌", "skip": "⚠️ ", "warn": "⚠️ "}
    _FG = {"pass": "green", "fail": "red", "skip": "yellow", "warn": "yellow"}
    _SKIP_SHORT = "skipped: prerequisite failed"
    max_name = max(len(v) for v in _DISPLAY.values())

    checks: list[CheckResult] = []

    def emit(result: CheckResult) -> CheckResult:
        """Record a check result and, in plain mode, print its line now.

        Returns the result so callers that also need the value (e.g. to
        feed a downstream check) can keep using it inline.
        """
        checks.append(result)
        if not streaming:
            return result
        name, status, detail = result
        display_name = _DISPLAY.get(name, name)
        if (
            status == "skip"
            and detail
            and detail.startswith("skipped:")
            and "prerequisite" not in detail
            and "--offline" not in detail
        ):
            # Collapse the verbose "skipped: <whatever>" strings back to
            # the canonical short form except for the structured ones
            # the checks emit (``--offline`` skip from the gate, the
            # ``update_version`` "prerequisite failed" dependency).
            detail = _SKIP_SHORT
        line = f"  {_EMOJI[status]} {display_name:<{max_name}}  {detail}"
        click.secho(line, fg=_FG[status])

        return result

    # 1. Version
    emit(_check_version())

    # 2. Config dir
    emit(_check_config_dir())

    # 3. profiles.yaml
    emit(_check_profiles_yaml())

    # 3a. Config file permissions
    emit(_check_config_permissions())

    # 4. link.json
    emit(_check_link_json())

    # 5. Profile resolution (returns profile object for downstream checks)
    res_result, resolved_profile = _check_profile_resolution(profile)
    emit(res_result)

    # 5a. Env-var fallback endpoint advisory
    emit(_check_env_fallback_endpoint(profile))

    # 5b-5e. Versioning-layer checks (T19). All read-only, no network.
    # ``git_available`` is system-level; ``profile_versioned`` and
    # ``working_tree_clean`` use the resolved profile; ``forks_healthy``
    # walks profiles.yaml independently.
    emit(_check_git_available())
    versioned_result = emit(_check_profile_versioned(resolved_profile))
    emit(_check_working_tree_clean(resolved_profile, versioned_result[1]))
    emit(_check_forks_healthy())
    emit(_check_inference_logic_current())

    # shutil.which probe (no network), so it runs under --offline too.
    emit(_check_ncs_available(resolved_profile))

    # 6-9. Dependent checks (use resolved profile)
    if offline:
        emit(("auth", "skip", "skipped: --offline"))
        emit(("connectivity", "skip", "skipped: --offline"))
        emit(("tier", "skip", "skipped: --offline"))
        emit(_check_build_data(resolved_profile))
    else:
        emit(_check_auth(resolved_profile))
        emit(_check_connectivity(resolved_profile))
        emit(_check_tier(resolved_profile))
        emit(_check_build_data(resolved_profile))

    # 9a. package.sql parse probe (T19). Read-only.
    emit(_check_package_sql_parses(resolved_profile))

    # 10. Skill install
    emit(_check_skill_install())

    # 11-12. New update-channel pair — also gated by --offline.
    if offline:
        emit(("update_channel", "skip", "skipped: --offline"))
        emit(("update_version", "skip", "skipped: --offline"))
    else:
        fetch_result = _run_update_check_fetch()
        emit(_check_update_channel_reachable(fetch_result))
        emit(_check_update_version_current(fetch_result))

    # Report. ``warn`` is informational (T19): present in the summary
    # but does not trip the exit-1 rule, parallel to ``skip``.
    any_fail = any(s == "fail" for _, s, _ in checks)
    any_warn = any(s == "warn" for _, s, _ in checks)
    any_skip = any(s == "skip" for _, s, _ in checks)

    if r.is_envelope:
        if any_fail:
            summary_str = "some checks failed"
        elif any_warn:
            summary_str = "some checks warned"
        elif any_skip:
            summary_str = "some checks skipped"
        else:
            summary_str = "all passed"
        payload = {
            "checks": [{"name": n, "status": s, "detail": d} for n, s, d in checks],
            "summary": summary_str,
        }
        r.success(payload)
    else:
        # Lines were already streamed by ``emit`` as each check ran;
        # only the summary tally remains.
        n_pass = sum(1 for _, s, _ in checks if s == "pass")
        n_fail = sum(1 for _, s, _ in checks if s == "fail")
        n_warn = sum(1 for _, s, _ in checks if s == "warn")
        n_skip = sum(1 for _, s, _ in checks if s == "skip")
        click.echo()
        parts = []
        if n_fail:
            parts.append(f"❌ {n_fail} failed")
        if n_warn:
            parts.append(f"⚠️  {n_warn} warned")
        if n_skip:
            parts.append(f"⚠️  {n_skip} skipped")
        if n_pass:
            parts.append(f"✅ {n_pass} passed")
        summary = " · ".join(parts)
        if n_fail:
            fg = "red"
        elif n_warn or n_skip:
            fg = "yellow"
        else:
            fg = "green"
        click.secho(summary, fg=fg, bold=True)

    sys.exit(1 if any_fail else 0)
