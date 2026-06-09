# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""XDG-compliant path helpers for mcs config + data + cache directories.

Config root is ``~/.config/maxcompute-semantic/`` (override via
``MCS_CONFIG_DIR``). Data root is ``~/.local/share/maxcompute-semantic/``
on Unix / ``~/Library/Application Support/maxcompute-semantic/`` on macOS
(override via ``MCS_DATA_DIR`` or ``XDG_DATA_HOME``). Config and data
live in separate XDG-standard directories.

Per-profile data lives at ``data_root()/<profile_name>/`` by default.
A profile may override its data dir explicitly via ``Profile.package_path``
(useful for imported packages, NFS-mounted shared dirs, or eval fixtures
checked into git). When ``package_path`` is set, it takes precedence over
the default; otherwise ``profile_data_dir(profile)`` falls back to the
per-name slot under ``data_root()``.

Historical note: the data root used to be called ``profiles_root()`` and
sat at ``<data>/profiles/``. The 2026-05-14 vocab cleanup renamed it to
``data_root()`` at ``<data>/data/`` because "profiles" was a misleading
label — that directory only ever held *data*, while profile *config*
lives at ``config_dir()/profiles.yaml``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile


def _xdg_cache_home() -> Path:
    """Return the platform-appropriate XDG cache home directory.

    Mirrors ``_xdg_data_home`` for the cache spec:
    - ``XDG_CACHE_HOME`` env var wins.
    - macOS: ``~/Library/Caches``.
    - Linux / other Unix: ``~/.cache``.
    - Windows: ``%LOCALAPPDATA%/Cache`` (LOCALAPPDATA is the standard
      per-user non-roaming dir on Windows; we suffix ``Cache`` for the
      same reason the XDG spec puts the cache under a dedicated subdir).
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "Cache"
    return Path.home() / ".cache"


def _xdg_data_home() -> Path:
    """Return the platform-appropriate XDG data home directory.

    - If ``XDG_DATA_HOME`` is set, use that.
    - On macOS: ``~/Library/Application Support``
    - On other Unix: ``~/.local/share``
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".local" / "share"


def cache_dir() -> Path:
    """Return the cache dir, ``<xdg_cache_home>/maxcompute-semantic`` or
    ``MCS_CACHE_DIR`` override.

    Used by ``_internal/update_check.py`` to store the per-user
    ``update_check.json`` cache (last-checked timestamp, last-seen
    ``latest_version``, fetch error). The directory is created lazily on
    the first write — readers tolerate its absence by returning ``None``
    from ``read_cache``. Symmetric with ``data_root()``'s override
    rules: ``MCS_CACHE_DIR`` is a no-suffix absolute path,
    ``XDG_CACHE_HOME`` gets the ``maxcompute-semantic`` suffix
    appended.
    """
    override = os.environ.get("MCS_CACHE_DIR")
    if override:
        return Path(override)
    return _xdg_cache_home() / "maxcompute-semantic"


def config_dir() -> Path:
    """Return ~/.config/maxcompute-semantic or override via MCS_CONFIG_DIR."""
    override = os.environ.get("MCS_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "maxcompute-semantic"


def data_dir() -> Path:
    """Return XDG data dir / maxcompute-semantic or override via MCS_DATA_DIR.

    Resolution order:
    1. ``MCS_DATA_DIR`` env var — absolute path, no suffix added.
    2. ``XDG_DATA_HOME`` env var — suffixed with ``maxcompute-semantic``.
    3. Platform default — macOS uses ``~/Library/Application Support``,
       other Unix uses ``~/.local/share``, suffixed with ``maxcompute-semantic``.
    """
    override = os.environ.get("MCS_DATA_DIR")
    if override:
        return Path(override)
    return _xdg_data_home() / "maxcompute-semantic"


def data_root() -> Path:
    """Return ``data_dir()/data`` or override via ``MCS_PROFILES_DIR``.

    The env var name (``MCS_PROFILES_DIR``) is kept for backwards
    compatibility with existing user configs and CI yamls; semantically
    it points at the per-profile-data root.
    """
    override = os.environ.get("MCS_PROFILES_DIR")
    if override:
        return Path(override)
    return data_dir() / "data"


def profile_data_dir(profile: Profile | str) -> Path:
    """Return the per-profile data directory.

    Accepts either a ``Profile`` object (preferred — honors
    ``profile.package_path`` if set) or a bare profile name (falls back
    to the default per-name slot). The string form is for call sites
    that only have a name and is equivalent to a ``Profile`` with
    ``package_path=None``.
    """
    # Avoid circular import: Profile lives in auth.schema which imports
    # from this module via the validation path. Local import only.
    from maxcompute_semantic.auth.schema import Profile as _Profile

    if isinstance(profile, _Profile):
        if profile.package_path is not None:
            return Path(profile.package_path)
        return data_root() / profile.name
    # str path — pure name, no package_path override possible.
    return data_root() / profile


def profiles_yaml_path() -> Path:
    return config_dir() / "profiles.yaml"


def link_json_path() -> Path:
    return config_dir() / "link.json"


def tier_cache_path(profile: Profile | str, project: str) -> Path:
    """Return the on-disk one-character sentinel for the cached tier of
    a specific MaxCompute project under this profile's data directory.

    The cache file lives at
    ``<profile_data_dir(profile)>/tier_cache/<project>`` with a single
    character of content — ``"2"`` for a 2-level (no-schema)
    MaxCompute project, ``"3"`` for a 3-level (schema-enabled) one.
    The per-project key in the second path segment exists because a
    multi-source profile spans potentially many MaxCompute projects:
    the AK's home project (``Profile.compute_project``) plus the
    data-side projects each ``DataSource`` in ``Profile.sources``
    declares. Each project's tier is independent state — a single
    profile may straddle a 3-level compute project and a mix of
    2-level and 3-level data sources.

    The ``profile_data_dir(profile)`` resolution honors
    ``Profile.package_path`` when the dataclass form is passed (the
    NFS-mount or imported-package override case), so a profile whose
    package lives off-disk has its tier_cache subdirectory on the same
    off-disk root. The bare-name form ``tier_cache_path("name",
    "proj")`` is the post-``mcs profile remove`` cleanup path's
    convenience — the cache file is deleted alongside the rest of
    the per-profile data dir, so the helper just produces the path
    string without any reading of the (now-gone) profile config.

    The "tier_cache" subdirectory's parent-mkdir is the writer's
    responsibility (``get_tier`` calls
    ``cache_path.parent.mkdir(parents=True, exist_ok=True)`` before
    the write). The reader path tolerates a missing file (the cache
    is a hint, not authoritative; the live probe is the
    source-of-truth).

    See spec §3 "Vocabulary" entry for "tier" for the conceptual
    summary of the 2-level vs 3-level distinction and the
    relationship to MaxCompute's ``odps.namespace.schema`` SQL hint.
    """
    if not isinstance(project, str) or not project.strip():
        raise ValueError(
            f"tier_cache_path requires a non-empty MaxCompute project name "
            f"as the second argument (got {project!r}); the cache is keyed "
            f"per-(profile, project) — caller must pass an explicit project "
            f"name from either profile.compute_project (the AK's home project) "
            f"or one of profile.sources[i].project (a declared data source)."
        )
    return profile_data_dir(profile) / "tier_cache" / project


def profile_git_dir(profile: Profile | str) -> Path:
    """Return ``<profile_data_dir>/.git`` — the per-profile git
    repository's admin directory. Pure path math, no I/O.

    Used by ``versioning/git_repo.py`` to scope all ``git`` subprocess
    invocations to the per-profile repo via ``git -C <repo_root>``
    (where ``repo_root`` is the *parent* of this directory — git's
    own ``-C`` convention is the working directory of the repo, not
    the ``.git`` administrative dir). The function is named after the
    admin dir because the existence check ``profile_git_dir(p).exists()``
    is the canonical "is this profile versioned?" probe used by the
    hook's legacy-profile branch and by ``mcs doctor``'s
    ``_check_profile_versioned``.
    """
    return profile_data_dir(profile) / ".git"


def profile_gitignore_path(profile: Profile | str) -> Path:
    """Return ``<profile_data_dir>/.gitignore``. Committed; contents
    are the constant ``PROFILE_GITIGNORE`` defined in
    ``versioning/gitignore_default.py``."""
    return profile_data_dir(profile) / ".gitignore"


def profile_package_sql_path(profile: Profile | str) -> Path:
    """Return ``<profile_data_dir>/package.sql`` — the textual dump of
    the committed tables of ``package.db``, produced and consumed by
    ``versioning/sql_dump.py``. Committed; the binary ``package.db``
    sibling is *not* committed (it appears in the ``.gitignore``)."""
    return profile_data_dir(profile) / "package.sql"


def profile_lock_path(profile: Profile | str) -> Path:
    """Return ``<profile_data_dir>/.mcs-lock`` — the fcntl lock anchor
    file. The body of the file is the PID of the current holder. The
    file appears in ``.gitignore`` so it never gets committed."""
    return profile_data_dir(profile) / ".mcs-lock"
