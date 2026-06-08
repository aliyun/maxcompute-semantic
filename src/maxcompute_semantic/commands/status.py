# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs status — read-only query over PackageDB + _state.json.

No live MaxCompute queries. Reads from the local build artifacts
produced by ``mcs build``.

Plain mode shows a human-readable summary; --tables adds per-table
detail.  JSON mode emits an envelope.  Quiet mode prints just the
profile name.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir, tier_cache_path
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._profile_command import profile_command


def _emit_by_source(r: Renderer, db: PackageDB, tables_rows: list[dict]) -> None:
    """Emit a per-source summary table."""
    from collections import defaultdict

    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in tables_rows:
        sk = row.get("source_key", "")
        by_source[sk].append(row)

    headers = ["source_key", "tables", "columns"]
    rows = []
    for sk, trs in sorted(by_source.items()):
        col_total = sum(len(db.get_columns(t["id"])) for t in trs)
        rows.append([sk, str(len(trs)), str(col_total)])
    r.table(headers, rows)


def _read_compute_tier(profile: Profile) -> str | None:
    """Return the compute project's cached tier sentinel as ``"2"`` or
    ``"3"``, or ``None`` if the sentinel is missing / unreadable.

    The agent's per-statement SQL emission decision (bare table name
    vs 3-segment ``project.schema.table`` FQN) hinges on this value,
    so status keys the summary tier on ``profile.compute_project``
    specifically rather than the first source's project (which can
    differ in multi-source profiles where compute and data projects
    are split). The sentinel is written by every ``mcs build`` via
    ``commands/build.py``'s ``get_tier(p, p.compute_project, ...)``
    call, so a profile that has been built at least once will have it.
    """
    sentinel = tier_cache_path(profile, profile.compute_project)
    if not sentinel.exists():
        return None
    try:
        content = sentinel.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content if content in {"2", "3"} else None


def _read_state_json(profile_dir: str) -> dict[str, Any] | None:
    """Read _state.json from profile dir; return None if missing or invalid."""
    from pathlib import Path

    state_path = Path(profile_dir) / "_state.json"
    if not state_path.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(state_path.read_text())
        return raw
    except (json.JSONDecodeError, OSError):
        return None


def _compute_age(last_built_at: str | None) -> str:
    """Compute human-readable age from an ISO timestamp."""
    if not last_built_at:
        return "—"
    try:
        built_dt = datetime.fromisoformat(last_built_at)
        if built_dt.tzinfo is None:
            built_dt = built_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - built_dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                return "just now"
            return f"{hours} hours"
        return f"{days} days"
    except (ValueError, TypeError):
        return "—"


def _format_date(iso_str: str | None) -> str:
    """Format an ISO timestamp as YYYY-MM-DD; em-dash if None."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "—"


_group = click.Group()  # private container for profile_command registration


@profile_command(_group, "status", accepts_schema=False)
@click.option("--tables", "show_tables", is_flag=True, help="show per-table detail")
@click.option("--by-source", is_flag=True, help="group tables by data source")
def status_cmd(
    pctx: ProfileContext,
    show_tables: bool,
    by_source: bool,
) -> None:
    """Show build status for a profile (reads local data only).

    Uses auto-resolution: --profile → MCS_PROFILE → link binding
    in cwd → env-var fallback.
    """
    r = pctx.renderer
    p = pctx.profile

    # Locate the profile's data directory. profile_data_dir honors
    # profile.package_path if set, otherwise falls back to the default
    # ``data_root()/<profile.name>/`` slot.
    profile_dir = profile_data_dir(p)
    db_path = profile_dir / "package.db"

    if not db_path.exists():
        no_build = {"profile": p.name, "build_status": "no build data"}
        r.quiet_essential(no_build, "profile")
        r.success(no_build)
        return

    # Open PackageDB (read-only query, no live MC calls).
    db = PackageDB(db_path)
    try:
        tables_rows = db.list_tables()
        joins_rows = db.list_joins()
        udf_rows = db.list_udfs()
        # Top-level metrics are profile-global entities (see the
        # top-level-metrics plan): the count surfaces alongside
        # tables / udfs / joins so the agent can detect whether the
        # profile carries any metric definitions without an extra
        # ``mcs metric list`` round-trip. Lives under
        # ``data.metrics_count`` in the envelope; the dict-iteration
        # path in ``Renderer.success`` propagates the same key to the
        # human-readable summary.
        metrics_rows = db.list_metrics()

        # Read _state.json for freshness info; tier is read separately
        # from the compute-project sentinel because the summary's tier
        # governs the agent's SQL emission form (3-segment FQN on
        # 3-level, bare names on 2-level) — which is decided by the
        # compute project's tier, not the first data source's.
        state = _read_state_json(str(profile_dir))
        compute_tier = _read_compute_tier(p)
        if compute_tier is not None:
            tier = f"{compute_tier}-level"
        else:
            # Fallback for builds that pre-date the per-(profile,
            # project) tier_cache layout, or where the sentinel was
            # manually deleted: trust state.json's recorded tier field.
            sources_state = (state or {}).get("sources") or {}
            if sources_state:
                tier = next(iter(sources_state.values())).get("tier", "—")
            else:
                tier = state.get("tier", "—") if state else "—"
        last_built_at = state.get("last_built_at") if state else None

        # If state doesn't have last_built_at, derive from the most recent table.
        if not last_built_at and tables_rows:
            built_dates = [row["last_built_at"] for row in tables_rows if row.get("last_built_at")]
            if built_dates:
                last_built_at = max(built_dates)

        age = _compute_age(last_built_at)
        built_date = _format_date(last_built_at)
        has_history = bool(state and state.get("has_history")) if state else False

        summary: dict[str, Any] = {
            "profile": p.name,
            "compute_project": p.compute_project,
            "tier": tier,
            "tables": len(tables_rows),
            # ``metrics_count`` sits between tables and udfs so the
            # inventory block reads top-down in entity order:
            # tables → metrics → udfs → joins.
            "metrics_count": len(metrics_rows),
            "udfs": len(udf_rows),
            "joins": len(joins_rows),
            "built_date": built_date,
            "age": age,
            "has_history": has_history,
        }

        if show_tables and tables_rows:
            # Fetch annotation-coverage breakdown for the Annotated column.
            coverage = db.annotation_coverage(per_table=True)
            per_table_cov = coverage.get("per_table", {})

            table_details = []
            for row in tables_rows:
                cols = db.get_columns(row["id"])
                col_count = len(cols)
                enum_cols = [c["name"] for c in cols if c.get("is_enum")]
                enum_str = ", ".join(enum_cols) if enum_cols else "—"
                table_built = _format_date(row.get("last_built_at"))
                errors_json = row.get("errors_json")
                if errors_json:
                    try:
                        err_data = json.loads(errors_json)
                        err_code = err_data.get("code", err_data.get("error", "ERROR"))
                        status = f"ERROR: {err_code}"
                    except (json.JSONDecodeError, TypeError):
                        status = "ERROR"
                else:
                    status = "OK"

                # Annotated tristate: "yes", "no", or "partial(N/M)".
                # per_table_cov is nested by source_key → name so same-named
                # tables across sources keep their independent state.
                sk = row.get("source_key", "")
                coverage_info = per_table_cov.get(sk, {}).get(row["name"], {})
                annotated = coverage_info.get("tristate", "—")

                table_details.append(
                    {
                        "source_key": sk,
                        "name": row["name"],
                        "table_type": row.get("table_type") or "—",
                        "col_count": col_count,
                        "enum_cols": enum_str,
                        "built_date": table_built,
                        "status": status,
                        "annotated": annotated,
                        "columns_total": coverage_info.get("columns_total", col_count),
                        "columns_annotated": coverage_info.get("columns_annotated", 0),
                        "columns_with_description": coverage_info.get(
                            "columns_with_description",
                            0,
                        ),
                        "has_ai_context": coverage_info.get("has_ai_context", False),
                        # Data-freshness baseline captured at the last sample
                        # (local; no live probe). Compare against a live
                        # `mcs meta freshness <table>` to see if the source
                        # has newer data than the package reflects.
                        "data_modified_at": row.get("data_modified_at"),
                        "last_sampled_at": row.get("last_sampled_at"),
                    }
                )
            summary["table_details"] = table_details

        if by_source and tables_rows:
            _emit_by_source(r, db, tables_rows)
    finally:
        db.close()

    r.quiet_essential(summary, "profile")
    r.success(summary)
