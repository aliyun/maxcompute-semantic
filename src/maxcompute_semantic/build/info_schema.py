"""INFORMATION_SCHEMA source detection (tenant-level vs project-level).

Provides:
- ``detect_info_schema_source(profile, client, cache_dir)`` — probes
  whether the current AK can reach the tenant-level
  INFORMATION_SCHEMA, falls back to project-level, falls back to
  ``"none"`` if neither is available; caches the result in
  ``cache_dir / ".info-schema-source"``.
- ``build_history_sql(project, source, lookback_days, limit)`` —
  builds the SQL to mine the recent task history for the named project
  via the chosen INFORMATION_SCHEMA source.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.mc_client.hints import namespace_schema_hints

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient

_INFO_SCHEMA_SENTINEL = ".info-schema-source"
_HISTORY_SOURCE_VALUES = ("tenant", "project", "none")
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _yesterday_yyyymmdd() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime("%Y%m%d")


def _ds_list_from_lookback(lookback_days: int) -> list[str]:
    """Generate ds partition values (YYYYMMDD) for the last N days."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(1, lookback_days + 1)]


def _classify_pyodps_exception(exc: Exception) -> str:
    """Classify a pyodps exception for info-schema probing.

    Returns one of: 'unavailable', 'transient_failure'.
    """
    from odps import errors as odps_errors  # type: ignore[import-untyped]

    str(exc).lower()
    if isinstance(exc, odps_errors.NoPermission):
        # ACL/authorization errors mean the source is unavailable (not transient).
        return "unavailable"
    if isinstance(exc, odps_errors.ODPSError):
        code = getattr(exc, "code", "") or ""
        if code in ("NoSuchObject", "ObjectNotExist", "NotFound"):
            return "unavailable"
        return "transient_failure"
    # Non-ODPS exceptions (network, timeout) — transient.
    return "transient_failure"


def _probe_info_schema(
    source: str,
    project: str,
    client: MaxComputeClient,
) -> str:
    """Run a cheap LIMIT 1 query against the source view; classify the result.

    Returns one of: 'available', 'unavailable', 'transient_failure'.

    ``project`` is the data project (used in the SQL:
    ``task_catalog = '<project>'`` for tenant form; project form has no
    project name in the SQL).

    Tenant form needs ``odps.namespace.schema=true`` hint (resolved via
    the ``hints`` parameter, NOT a SET prefix in the SQL — SET+SELECT
    is a multi-statement query that causes ParseError with run_sql()).
    Project form uses the bare 2-segment view name
    ``information_schema.tasks_history``; no namespace hint is needed
    because ``information_schema`` is a special/reserved schema name
    that ODPS resolves regardless of namespace settings.
    """
    if source not in ("tenant", "project"):
        raise ValueError(f"_probe_info_schema: bad source {source!r}")
    if not _PROJECT_NAME_RE.match(project):
        return "unavailable"
    ds = _yesterday_yyyymmdd()
    probe_hints: dict[str, str] | None = None
    if source == "tenant":
        sql = (
            "SELECT 1 FROM SYSTEM_CATALOG.INFORMATION_SCHEMA.tasks_history "
            f"WHERE ds = '{ds}' AND task_catalog = '{project}' LIMIT 1"
        )
        probe_hints = namespace_schema_hints(True)
    else:
        sql = f"SELECT 1 FROM information_schema.tasks_history WHERE ds = '{ds}' LIMIT 1"
    try:
        # assume_yes=True: this is a cheap LIMIT 1 probe to validate
        # info_schema accessibility. We don't want the cost gate to
        # block a build over a 1-row availability probe.
        client.execute_sql(sql, timeout=30, hints=probe_hints, assume_yes=True)
        return "available"
    except Exception as exc:
        return _classify_pyodps_exception(exc)


def _write_sentinel(cache_dir: Path, value: str) -> None:
    if value not in _HISTORY_SOURCE_VALUES:
        raise ValueError(f"_write_sentinel: bad value {value!r}")
    p = cache_dir / _INFO_SCHEMA_SENTINEL
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding="utf-8")
    except OSError:
        pass


def detect_info_schema_source(
    profile: Profile,
    client: MaxComputeClient,
    cache_dir: Path,
    *,
    requested: str = "auto",
) -> str:
    """Return one of 'tenant', 'project', 'none'.

    ``requested='auto'`` reads the cached sentinel (probes
    tenant -> project -> none on miss). ``requested='tenant'`` /
    ``requested='project'`` force that source — skips cache read
    but writes back on success. Failures on explicit choice do NOT
    fall back.

    Both INFORMATION_SCHEMA forms resolve relative to the compute
    project: the tenant form's SYSTEM_CATALOG view is reachable
    only via tenant-level access granted on the authenticated
    project, and the project form's bare ``information_schema``
    namespace binds to whichever project ``execute_sql`` is
    running against (``compute_project``). The probe therefore
    keys on ``compute_project`` regardless of how many
    ``DataSource`` entries the profile carries — every source in
    a multi-source profile resolves through the same probe answer.
    Per-source mining in ``phase_mine_history`` already gets
    correct per-catalog attribution via the tenant form's
    ``task_catalog = '<source.project>'`` WHERE-clause filter
    (project-form mining is naturally compute-scoped, no
    attribution variation is possible).
    """
    project = profile.compute_project
    if requested not in ("auto", "tenant", "project"):
        raise ValueError(f"detect_info_schema_source: bad requested {requested!r}")

    if requested in ("tenant", "project"):
        result = _probe_info_schema(requested, project, client)
        if result == "available":
            _write_sentinel(cache_dir, requested)
            return requested
        return "none"

    # auto: cache -> probe tenant -> probe project -> none
    sentinel = cache_dir / _INFO_SCHEMA_SENTINEL
    try:
        cached = sentinel.read_text(encoding="utf-8").strip()
        if cached in _HISTORY_SOURCE_VALUES:
            return cached
    except OSError:
        pass

    for src in ("tenant", "project"):
        result = _probe_info_schema(src, project, client)
        if result == "available":
            _write_sentinel(cache_dir, src)
            return src
        # On transient_failure: don't cache, but continue probing
        # the next source — a transient tenant failure shouldn't
        # block a project-level check that might succeed.
    _write_sentinel(cache_dir, "none")
    return "none"


def build_history_sql(
    project: str,
    source: str,
    lookback_days: int = 14,
    limit: int = 2000,
) -> str:
    """Build the TASKS_HISTORY mining SQL for the given source.

    Computes ``ds_list`` internally from ``lookback_days``.
    Caller is responsible for ``--no-history`` gating; this builder
    only formats the SQL string. The tenant form does NOT include a
    SET prefix — ``namespace.schema`` is passed via the ``hints``
    parameter in ``execute_sql()`` (SET+SELECT triggers ParseError
    with ``run_sql()``).
    """
    if source not in ("tenant", "project"):
        raise ValueError(f"build_history_sql: bad source {source!r}")
    if not _PROJECT_NAME_RE.match(project):
        raise ValueError(f"build_history_sql: unsafe project name {project!r}")
    ds_list = _ds_list_from_lookback(lookback_days)
    ds_in = ",".join(f"'{d}'" for d in ds_list)
    if source == "tenant":
        return (
            "SELECT operation_text, signature "
            "FROM SYSTEM_CATALOG.INFORMATION_SCHEMA.tasks_history "
            f"WHERE ds IN ({ds_in}) "
            "AND status = 'Terminated' "
            f"AND task_catalog = '{project}' "
            "AND task_type = 'SQL' "
            f"ORDER BY start_time DESC LIMIT {limit}"
        )
    return (
        "SELECT operation_text, signature "
        "FROM information_schema.tasks_history "
        f"WHERE ds IN ({ds_in}) "
        "AND status = 'Terminated' "
        "AND task_type = 'SQL' "
        f"ORDER BY start_time DESC LIMIT {limit}"
    )
