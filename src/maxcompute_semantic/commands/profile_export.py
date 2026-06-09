# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs profile export`` / ``mcs profile import`` — package portability.

A package built locally can be bundled into a tar.gz and handed to a
teammate, checked into a git repo as a CI fixture, or stashed for
offline inspection. The recipient runs ``mcs profile import`` to
register the profile (with placeholder auth) and drop the package data
at the configured ``package_path`` (default ``data_root()/<name>/``).

What's in the archive:

    manifest.json            mcs metadata (schema_version, exported_at,
                             generator version, profile config sans auth,
                             package summary stats)
    data/                    everything under profile_data_dir(profile):
        package.db
        _state.json
        _overview.md / _joins.md / _udfs.md
        <table>.md  ...

What's NOT in the archive:

    - auth credentials (access keys, ncs commands) — never exported.
      The recipient configures their own auth via ``mcs profile update``
      after import.
    - cwd link bindings — those are local to each machine.

The archive format is plain tar.gz so users can inspect manually:

    tar tzvf my-package.tar.gz   # list contents
    tar xzf my-package.tar.gz manifest.json -O | jq .
"""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click

from maxcompute_semantic import __version__ as MCS_VERSION
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.errors import (
    InvalidProfileError,
    ProfileNotFoundError,
)
from maxcompute_semantic.auth.profile_store import get, load_all
from maxcompute_semantic.auth.schema import AkAuth, CostThresholds, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.mc_client.errors import McsError, PackageNotBuiltError
from maxcompute_semantic.osi import dump_yaml as osi_dump_yaml
from maxcompute_semantic.osi import to_osi_dict
from maxcompute_semantic.versioning import (
    ACTION_INIT,
    commit_after_command,
    reject_if_fork,
)

_MANIFEST_NAME = "manifest.json"
_DATA_DIR = "data"
_MANIFEST_SCHEMA_VERSION = 2

# Local-only state that must not travel inside an export archive. The
# source's per-profile git history isn't portable (every recipient
# bootstraps a fresh ``.git/`` via the auto-init branch of
# ``commit_after_command`` on the inaugural import commit); the
# tier_cache and ``.mcs-lock`` are runtime caches/locks that the
# recipient's machine recreates on first use. Mirrors (and extends)
# the patterns in ``PROFILE_GITIGNORE`` — gitignore excludes them
# from the per-profile git history; this set excludes them from the
# portable archive.
_EXPORT_EXCLUDED = frozenset({".git", "tier_cache", ".mcs-lock", ".mcs-lock.flock"})


def _export_ignore(_src: str, names: list[str]) -> list[str]:
    """``shutil.copytree`` ``ignore`` callback that filters out
    per-machine state (the source's git history, tier sentinel cache,
    process lock, and symlinks) so the archive only carries portable
    package data and never follows links outside the package dir."""
    src = Path(_src)
    return [n for n in names if n in _EXPORT_EXCLUDED or (src / n).is_symlink()]


# tarfile.extractall(filter=...) is Python 3.12+. On 3.11 we add a
# manual pre-extract validation pass that rejects the same things
# the ``data`` filter rejects: absolute paths, parent-traversal,
# symlinks, hardlinks, and non-regular file/directory members.
_HAS_TAR_FILTER = sys.version_info >= (3, 12)


def _validate_archive_members(tar: tarfile.TarFile, dest: Path) -> None:
    """Pre-3.12 manual safety check for ``tar.extractall``.

    Rejects what ``filter='data'`` would on 3.12+:
    absolute paths, parent-traversal escapes, symlink/hardlink
    members, and any non-regular file/directory member type. Raises
    ``tarfile.TarError`` on the first unsafe member.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        if member.name.startswith("/") or member.name.startswith("\\"):
            raise tarfile.TarError(f"absolute path in archive: {member.name}")
        target = (dest_resolved / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise tarfile.TarError(f"parent-traversal in archive: {member.name}") from exc
        if member.issym() or member.islnk():
            raise tarfile.TarError(f"symlink/hardlink in archive: {member.name}")
        if not (member.isreg() or member.isdir()):
            raise tarfile.TarError(f"unsafe member type {member.type!r} in archive: {member.name}")


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )


def _build_manifest(profile: Profile, pdir: Path, *, name_override: str | None = None) -> dict:
    """Build the manifest.json content for an export.

    Reads ``_state.json`` for package summary stats when present;
    otherwise reports counts as None (the archive is still valid for
    transport, just less informative).

    ``name_override`` replaces ``profile.name`` in the manifest so
    the exported archive carries a sanitized name instead of the
    local profile identifier.
    """
    state_path = pdir / "_state.json"
    package: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        # ``_state.json`` v3 (0.4.0a4+) partitions per-source counts
        # under a ``sources`` key. Aggregate to the manifest's
        # profile-level summary by summing over sources.
        sources_state = state.get("sources") or {}
        tables_count = (
            sum(s.get("tables_count") or 0 for s in sources_state.values())
            if sources_state
            else state.get("tables_count")
        )
        # Tier is per-source in v3 — surface the first source's tier
        # as the manifest summary; consumers that care about per-source
        # tier should read ``_state.json`` directly.
        tier = (
            next(iter(sources_state.values())).get("tier") if sources_state else state.get("tier")
        )
        package = {
            "tables_count": tables_count,
            "udfs_count": state.get("udfs_count"),
            "joins_count": state.get("joins_count"),
            "tier": tier,
            "built_at": state.get("last_built_at"),
            "history_skipped": state.get("history_skipped"),
            "tables_with_sample_sqls": state.get("tables_with_sample_sqls"),
        }

    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by_mcs_version": MCS_VERSION,
        "profile": {
            "name": name_override or profile.name,
            "compute_project": profile.compute_project,
            "endpoint": profile.endpoint,
            "sources": [
                {
                    "project": s.project,
                    "schema": s.schema,
                    "source_key": s.source_key(),
                    "tables": (
                        s.tables
                        if isinstance(s.tables, str)
                        else [
                            {
                                "name": ts.name,
                                "columns": list(ts.columns) if ts.columns else None,
                                "columns_exclude": list(ts.columns_exclude),
                            }
                            for ts in s.tables
                        ]
                    ),
                }
                for s in profile.sources
            ],
            "tags": list(profile.tags),
            # auth deliberately omitted — secrets never leave the host
        },
        "package": package,
    }


def export_profile(
    profile_name: str, output_path: Path, *, name_override: str | None = None
) -> Path:
    """Bundle a profile's package data into a tar.gz.

    Returns the absolute path of the written archive. Raises
    ``ProfileNotFoundError`` if the profile name is unknown,
    ``McsError`` if the package data dir is missing (build first).

    ``name_override`` replaces the profile name in the manifest —
    useful for stripping personal identifiers before sharing.
    """
    profile = get(profile_name)
    pdir = profile_data_dir(profile)
    if not pdir.is_dir() or not (pdir / "package.db").exists():
        raise PackageNotBuiltError(
            f"profile {profile_name!r} has no package data at {pdir}; "
            f"run `mcs build --profile {profile_name}` first",
        )

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = _build_manifest(profile, pdir, name_override=name_override)
    with tempfile.TemporaryDirectory(prefix="mcs-export-") as staging:
        staging_path = Path(staging)
        (staging_path / _MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Copy package data tree under data/ in the archive root. The
        # ignore callback strips per-machine state (.git/, tier_cache/,
        # .mcs-lock) so the recipient's import bootstraps a fresh git
        # history rather than inheriting the source's.
        shutil.copytree(pdir, staging_path / _DATA_DIR, ignore=_export_ignore)
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(staging_path / _MANIFEST_NAME, arcname=_MANIFEST_NAME)
            tar.add(staging_path / _DATA_DIR, arcname=_DATA_DIR)
    return output_path


def import_profile(
    archive_path: Path,
    *,
    name_override: str | None = None,
    package_path_override: Path | None = None,
) -> Profile:
    """Register a profile from an exported tar.gz.

    Reads ``manifest.json`` for profile config + package summary, then
    extracts the ``data/`` tree to the destination (either
    ``package_path_override`` if set, or ``data_root()/<name>/``).

    Auth is initialized to a placeholder ``AkAuth`` with empty credentials —
    recipient must run ``mcs profile update`` to wire their own AK before
    `mcs sql` calls work.

    Raises ``McsError`` on archive shape problems, ``InvalidProfileError``
    if the resulting profile fails validation.
    """
    if not archive_path.is_file():
        raise McsError(
            f"archive not found at {archive_path}",
            code="ARCHIVE_NOT_FOUND",
            exit_code=4,
        )

    with tempfile.TemporaryDirectory(prefix="mcs-import-") as staging:
        staging_path = Path(staging)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                # Security: reject absolute paths, parent-traversal,
                # and unsafe member types. Python 3.12+ has a built-in
                # ``filter='data'`` that does this; on 3.11 we walk the
                # member list ourselves before calling extractall.
                if _HAS_TAR_FILTER:
                    tar.extractall(staging_path, filter="data")
                else:
                    _validate_archive_members(tar, staging_path)
                    tar.extractall(staging_path)  # noqa: S202
        except (tarfile.TarError, OSError) as exc:
            raise McsError(
                f"failed to extract archive {archive_path}: {exc}",
                code="ARCHIVE_EXTRACT_FAILED",
                exit_code=4,
            ) from exc

        manifest_path = staging_path / _MANIFEST_NAME
        if not manifest_path.exists():
            raise McsError(
                f"archive {archive_path} missing {_MANIFEST_NAME}",
                code="ARCHIVE_INVALID",
                exit_code=4,
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise McsError(
                f"archive {archive_path} has invalid manifest.json: {exc}",
                code="ARCHIVE_INVALID",
                exit_code=4,
            ) from exc

        if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise McsError(
                f"archive manifest schema_version "
                f"{manifest.get('schema_version')!r} unsupported "
                f"(expected {_MANIFEST_SCHEMA_VERSION})",
                code="ARCHIVE_VERSION",
                exit_code=4,
            )

        prof_block = manifest.get("profile") or {}
        target_name = name_override or prof_block.get("name")
        if not target_name:
            raise McsError(
                "archive manifest has no profile.name; cannot import",
                code="ARCHIVE_INVALID",
                exit_code=4,
            )

        if target_name in load_all():
            # If we'd be touching an existing profile under this name,
            # block when that profile is a fork — forks are read-only
            # historical snapshots and an import would otherwise clobber
            # the worktree's reset target. Non-fork collisions are also
            # refused, including ``--name`` collisions — choosing a new
            # import name must not overwrite an unrelated local profile.
            reject_if_fork(get(target_name))
            hint = (
                "choose a different --name"
                if name_override
                else "pass --name to import under a different name"
            )
            raise McsError(
                f"profile {target_name!r} already exists locally; {hint}",
                code="PROFILE_EXISTS",
                exit_code=4,
            )

        target_pdir = (
            Path(package_path_override).resolve() if package_path_override is not None else None
        )

        from maxcompute_semantic.auth.schema import DataSource, TableSpec

        sources_in: list[DataSource] = []
        for src_dict in prof_block.get("sources") or ():
            tbls = src_dict.get("tables", "*")
            if isinstance(tbls, list):
                tspec = tuple(
                    TableSpec(
                        name=ts["name"],
                        columns=tuple(ts["columns"]) if ts.get("columns") else None,
                        columns_exclude=tuple(ts.get("columns_exclude") or ()),
                    )
                    for ts in tbls
                )
                sources_in.append(
                    DataSource(
                        project=src_dict["project"],
                        schema=src_dict.get("schema", "default"),
                        tables=tspec,
                    )
                )
            else:
                sources_in.append(
                    DataSource(
                        project=src_dict["project"],
                        schema=src_dict.get("schema", "default"),
                        tables=tbls,
                    )
                )

        profile = Profile(
            name=target_name,
            compute_project=prof_block.get("compute_project", ""),
            endpoint=prof_block.get("endpoint", ""),
            sources=tuple(sources_in),
            # Placeholder auth — recipient configures via mcs profile update.
            auth=AkAuth(access_key_id="", access_key_secret=""),
            cost_thresholds=CostThresholds(),
            tags=tuple(prof_block.get("tags") or ()),
            package_path=target_pdir,
        )
        # Skip validate() at this point — placeholder auth has empty
        # AK fields that fail validation. We register the profile
        # anyway and let the user fix auth via `mcs profile update`.

        archive_data = staging_path / _DATA_DIR
        if not archive_data.is_dir():
            raise McsError(
                f"archive {archive_path} missing data/ subdir",
                code="ARCHIVE_INVALID",
                exit_code=4,
            )

        # Resolve the data destination *after* the Profile is built so
        # profile_data_dir(profile) honors any package_path override.
        # Refuse non-empty destinations instead of merging archive data
        # with stale files from a previous package.
        dest = profile_data_dir(profile)
        if dest.exists() and not dest.is_dir():
            raise McsError(
                f"package destination {dest} exists but is not a directory",
                code="PACKAGE_DEST_INVALID",
                exit_code=4,
            )
        if dest.is_dir() and any(dest.iterdir()):
            raise McsError(
                f"package destination {dest} is not empty; choose an empty --package-path",
                code="PACKAGE_DEST_NOT_EMPTY",
                exit_code=4,
            )
        dest.mkdir(parents=True, exist_ok=True)

        shutil.copytree(archive_data, dest, dirs_exist_ok=True)

        upsert_profile_no_validate(profile)
        return profile


def upsert_profile_no_validate(profile: Profile) -> None:
    """Bypass Profile.validate() for the import case.

    The imported profile has empty AK credentials by design (auth never
    exported); ``Profile.validate()`` would reject this. Recipient runs
    ``mcs profile update`` afterward to set their own auth, at which
    point validation runs again.
    """
    # Inline what profile_store.upsert does, sans validate().
    from maxcompute_semantic._internal.paths import profiles_yaml_path
    from maxcompute_semantic._internal.yaml_io import dump_yaml
    from maxcompute_semantic.auth.profile_store import _profile_to_dict, _read_raw

    raw = _read_raw()
    raw["profiles"][profile.name] = _profile_to_dict(profile)
    dump_yaml(raw, profiles_yaml_path())


# ── Click commands ──────────────────────────────────────────────────────────


@click.command("export")
@click.argument("name")
@click.option(
    "--export-name",
    "export_name",
    default=None,
    help="replace the profile name in the archive manifest (strips local identifiers)",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="output path (default: ./<name>.tar.gz, or ./<name>.osi.yaml with --osi)",
)
@click.option(
    "--osi",
    "osi_mode",
    is_flag=True,
    default=False,
    help="export as OSI (Open Semantic Interchange) YAML instead of tar.gz archive",
)
@click.pass_context
def export_cmd(
    ctx: click.Context,
    name: str,
    export_name: str | None,
    output: Path | None,
    osi_mode: bool,
) -> None:
    """Bundle a profile's package data for sharing (tar.gz by default; --osi for OSI YAML)."""
    r = _renderer(ctx)
    effective_name = export_name or name
    if osi_mode:
        output = (output or Path(f"./{effective_name}.osi.yaml")).resolve()
        try:
            profile = get(name)
        except ProfileNotFoundError as e:
            r.error(e)
            sys.exit(e.exit_code)
        pkg_db_path = profile_data_dir(profile) / "package.db"
        if not pkg_db_path.exists():
            not_built = PackageNotBuiltError(
                f"profile {name!r} has no package.db at {pkg_db_path}; "
                f"run `mcs build --profile {name}` first",
            )
            r.error(not_built)
            sys.exit(not_built.exit_code)
        db = PackageDB(pkg_db_path)
        try:
            osi_data = to_osi_dict(db, semantic_model_name=effective_name)
        except ValueError as e:
            export_error = McsError(str(e))
            r.error(export_error)
            sys.exit(export_error.exit_code)
        finally:
            db.close()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            osi_dump_yaml(osi_data, output)
        except OSError as e:
            export_error = McsError(
                f"failed to write OSI YAML export to {output}: {e}",
                code="EXPORT_FAILED",
                exit_code=4,
            )
            r.error(export_error)
            sys.exit(export_error.exit_code)
        r.success(
            {
                "exported": str(output),
                "format": "osi-yaml",
                "size_bytes": output.stat().st_size,
                "profile": effective_name,
            }
        )
        return

    output = output or Path(f"./{effective_name}.tar.gz")
    try:
        archive = export_profile(name, output, name_override=export_name)
    except (ProfileNotFoundError, McsError) as e:
        r.error(e)
        sys.exit(e.exit_code)
    size = archive.stat().st_size
    r.success(
        {
            "exported": str(archive),
            "size_bytes": size,
            "profile": effective_name,
        }
    )


@click.command("import")
@click.argument(
    "archive",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--name",
    "name_override",
    default=None,
    help="register the imported profile under this name (default: archive's manifest)",
)
@click.option(
    "--package-path",
    "package_path_override",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="extract package data here instead of the default per-profile slot",
)
@click.pass_context
def import_cmd(
    ctx: click.Context,
    archive: Path,
    name_override: str | None,
    package_path_override: Path | None,
) -> None:
    """Register a profile from an exported tar.gz."""
    r = _renderer(ctx)
    try:
        profile = import_profile(
            archive,
            name_override=name_override,
            package_path_override=package_path_override,
        )
    except (McsError, InvalidProfileError) as e:
        r.error(e)
        sys.exit(e.exit_code)
    pdir = profile_data_dir(profile)
    r.success(
        {
            "imported": profile.name,
            "package_path": str(pdir),
            "auth_status": "placeholder — run `mcs profile update` to set credentials",
        }
    )
    commit_after_command(profile, action=ACTION_INIT, summary=f"import from {archive.name}")
