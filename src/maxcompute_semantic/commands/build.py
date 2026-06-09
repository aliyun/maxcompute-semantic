"""mcs build — run the full package-build pipeline.

Resolves profile via auto-resolution (same chain as sql/status),
primes tier, opens PackageDB, runs BuildPipeline, and outputs
a summary via Renderer.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import resolve_profile_for_project
from maxcompute_semantic.auth.credential import resolve_credentials
from maxcompute_semantic.build.errors import RebuildRequiredError
from maxcompute_semantic.build.pipeline import BuildOptions, BuildPipeline
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._schema_resolve import resolve_schema_for_tier
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.mc_client.tier import get_tier
from maxcompute_semantic.memory.package_doc import generate_package_docs
from maxcompute_semantic.versioning import (
    ACTION_BUILD,
    ACTION_REFRESH,
    commit_after_command,
    reject_if_fork,
)

_TRUTHY = {"1", "true", "yes", "on"}


def _renderer(ctx: click.Context) -> Renderer:
    obj = ctx.obj or {}
    return Renderer(
        format=obj.get("format", "plain"),
        quiet=obj.get("quiet", False),
    )


@click.command("build")
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--schema",
    default=None,
    help="schema override for 3-level projects",
)
@click.option("--refresh", is_flag=True, help="incremental rebuild (schema-hash diff)")
@click.option(
    "--fresh",
    is_flag=True,
    help=(
        "rebuild every table from scratch, ignoring resume state. "
        "By default an interrupted build resumes — already-built, "
        "unchanged tables are skipped; --fresh forces a full re-sample"
    ),
)
@click.option(
    "--refresh-min-age-hours",
    type=click.FloatRange(min=0.0),
    default=24.0,
    show_default=True,
    help=(
        "re-sample a schema-unchanged table whose data changed only if its "
        "last sample is older than this many hours (throttle for hot tables; "
        "0 disables the throttle — re-sample on any data change)"
    ),
)
@click.option("--tables", help="only these tables (comma-separated)")
@click.option("--no-history", is_flag=True, help="skip history mining (eval mode / cold start)")
@click.option("--no-sampling", is_flag=True, help="skip column value sampling")
@click.option("--no-joins", is_flag=True, help="skip JOIN inference")
@click.option("--no-udf", is_flag=True, help="skip UDF discovery")
@click.option(
    "--include-views",
    is_flag=True,
    help=(
        "include VIRTUAL_VIEW / OBJECT_TABLE in sampling and profiling phases "
        "(default: skip — their underlying SQL re-execution is expensive)"
    ),
)
@click.option(
    "--profile-level",
    type=click.Choice(["none", "light", "deep"]),
    default="light",
    show_default=True,
    help="semantic profiling depth",
)
@click.option(
    "--profile-budget-cny",
    type=float,
    default=3.0,
    show_default=True,
    help="max estimated cost for profiling/deep validation",
)
@click.option(
    "--join-candidate-limit",
    type=int,
    default=5,
    show_default=True,
    help="max join candidates kept per table",
)
@click.option(
    "--with-vectors",
    is_flag=True,
    help="rebuild vector embeddings (requires maxcompute-semantic[vec])",
)
@click.option(
    "--parallel",
    type=str,
    default="auto",
    show_default=True,
    help=(
        "concurrent worker threads for per-table sampling and profiling. "
        "'auto' (default) scales to min(table_count, 32); "
        "an integer overrides (1 disables fan-out)"
    ),
)
@click.pass_context
def build_cmd(
    ctx: click.Context,
    profile: str | None,
    schema: str | None,
    refresh: bool,
    fresh: bool,
    refresh_min_age_hours: float,
    tables: str | None,
    no_history: bool,
    no_sampling: bool,
    no_joins: bool,
    no_udf: bool,
    include_views: bool,
    profile_level: str,
    profile_budget_cny: float,
    join_candidate_limit: int,
    with_vectors: bool,
    parallel: str,
) -> None:
    """Run the full package-build pipeline for a profile.

    Build is profile-scoped: ``--profile`` selects the profile (or
    auto-resolves via MCS_PROFILE → cwd-link → env-var fallback).
    The profile determines compute project, data sources, and table
    scope.
    """
    r = _renderer(ctx)

    # Parse --parallel: "auto" → None (auto-scale), integer string → int.
    parallel_value: int | None = None
    if parallel.lower() != "auto":
        try:
            parallel_value = int(parallel)
        except ValueError:
            r.error(
                McsError(
                    code="InvalidArgument",
                    message=f"--parallel must be 'auto' or an integer, got '{parallel}'",
                )
            )
            sys.exit(1)

    # MCS_NO_HISTORY env var is backward-compat alias for --no-history.
    mcs_no_history = os.environ.get("MCS_NO_HISTORY", "").strip().lower()
    if mcs_no_history in _TRUTHY and not no_history:
        no_history = True

    # Resolve profile via auto-resolution chain (no --project on build;
    # build is profile-scoped — see the docstring).
    try:
        p = resolve_profile_for_project(None, profile_name=profile)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)
    # Read-only fork guard. Raises ProfileReadOnly before any client
    # construction or credential resolution, so a fork-targeted build
    # exits cleanly with the spec's two-option remediation.
    try:
        reject_if_fork(p)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    # Resolve credentials + create client.
    try:
        resolve_credentials(p.auth)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    client = MaxComputeClient(p)

    # Prime tier.
    try:
        tier = get_tier(p, p.compute_project, client=client)
        client._tier = tier
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    # Resolve --schema. Profiles carry schema information on their
    # sources, and an explicit --schema is accepted as a one-off
    # override. ``"default"`` is a valid 3-level schema name —
    # MaxCompute parks flat tables there after a 2→3 upgrade — so
    # accept it explicitly. The shared resolver raises
    # ``SchemaRequiredError`` on tier-3 + no-schema + multi-source /
    # env-var fallback; route it through the standard ``r.error``
    # path so the failure shows up with code ``SchemaRequired`` and
    # exit 2 in the JSON envelope.
    try:
        schema = resolve_schema_for_tier(tier, schema, profile=p)
    except McsError as e:
        r.error(e)
        sys.exit(e.exit_code)

    # Open PackageDB at the profile's data dir. Profile is 1:1 with its
    # package — the dir is either ``data_root()/<profile.name>/`` (default)
    # or whatever ``profile.package_path`` points at (custom / imported).
    db_path = profile_data_dir(p) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        db = PackageDB(db_path)
    except RebuildRequiredError as e:
        r.error(e)
        sys.exit(e.exit_code)

    # Build options from click flags.
    tables_filter = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    opts = BuildOptions(
        no_history=no_history,
        no_sampling=no_sampling,
        no_joins=no_joins,
        no_udf=no_udf,
        refresh=refresh,
        fresh=fresh,
        refresh_min_age_hours=refresh_min_age_hours,
        tables_filter=tables_filter,
        profile_level=profile_level,
        profile_budget_cny=profile_budget_cny,
        join_candidate_limit=join_candidate_limit,
        include_views=include_views,
        parallel=parallel_value,
    )

    # Wire up a stderr-progress callback for plain-mode output. JSON
    # mode skips the per-phase narration to keep stdout machine-readable
    # (the structured envelope at the end already conveys what happened).
    # Color the [N/7] step prefixes dim so the body text stays readable;
    # the ✓ on the completion line is bold green for the celebratory
    # "everything done" cue.
    def _render_progress(msg: str) -> None:
        # Step prefixes like "[3/7] " get dimmed so the body of the
        # message reads as the headline.
        import re as _re

        m = _re.match(r"^(\[\d+/\d+\])\s*(.*)", msg)
        if m:
            click.echo(
                click.style(m.group(1), dim=True) + " " + m.group(2),
                err=True,
            )
            return
        if msg.startswith("✓ "):
            click.secho(f"🎉 {msg[2:]}", fg="green", bold=True, err=True)
            return
        click.echo(msg, err=True)

    progress = (lambda _msg: None) if r.is_envelope else _render_progress

    # Announce the build target up front — gives users a clear picture
    # of what's about to happen before the per-phase output starts.
    # Multi-source profiles list every source key so users see the
    # full scope; the legacy single-source path keeps the compact
    # ``project.schema`` form for back-compat with the v0.3 banner.
    if not r.is_envelope:
        if len(p.sources) > 1:
            keys = ", ".join(s.source_key() for s in p.sources)
            target = f"{len(p.sources)} sources: {keys}"
        elif p.sources:
            first = p.sources[0]
            target = f"{first.project}.{first.schema}"
        else:
            target = p.compute_project
        click.secho(
            f"🚀 Building profile '{p.name}' "
            f"(compute={p.compute_project}, target={target}, tier={tier}-level)",
            bold=True,
            err=True,
        )

    # Run the pipeline.
    try:
        summary = BuildPipeline(client, db, p, opts, progress=progress).run()
        # Post-build: generate package_doc memory entries.
        doc_count = generate_package_docs(db)
        sample_sql_count = len(db.list_memories(kind="sample_sql"))
        summary.memory_count = doc_count + sample_sql_count

        if with_vectors:
            vec_count = db.reindex_vectors()
            summary.vector_count = vec_count
            if vec_count >= 0:
                if not r.is_envelope:
                    click.echo(f"   vector embeddings: {vec_count} entries indexed", err=True)
            elif not r.is_envelope:
                click.echo(
                    "   vector embeddings: skipped (install maxcompute-semantic[vec])",
                    err=True,
                )
        else:
            summary.vector_count = -1
            if not r.is_envelope:
                click.echo(
                    "   vector embeddings: skipped (pass --with-vectors to rebuild)",
                    err=True,
                )

        suggestion_count = db.count_annotation_suggestions()
        if suggestion_count > 0 and not r.is_envelope:
            click.echo(
                f"   💡 {suggestion_count} annotation suggestions generated"
                " — run `mcs package propose --from-suggestions` to review",
                err=True,
            )
    except McsError as e:
        db.close()
        r.error(e)
        sys.exit(e.exit_code)
    finally:
        db.close()

    # Output build summary.
    summary_data = {
        "profile": p.name,
        "compute_project": p.compute_project,
        "tier": tier,
        "tables_built": summary.tables_built,
        "tables_skipped": summary.tables_skipped,
        "tables_new": summary.tables_new,
        "tables_changed": summary.tables_changed,
        "tables_removed": summary.tables_removed,
        "tables_unchanged": summary.tables_unchanged,
        "tables_resumed": summary.tables_resumed,
        "memory_count": summary.memory_count,
        "vector_count": summary.vector_count,
        "annotation_suggestions_count": suggestion_count,
        "elapsed_seconds": summary.elapsed_seconds,
        "parallel_workers": summary.parallel_workers,
        "phases_skipped": summary.phases_skipped,
        "errors": summary.errors,
        "warnings": summary.warnings,
    }
    r.success(summary_data)

    # T8: auto-commit hook at the success-path tail. PackageDB was closed
    # in the finally block above, so the package.sql dump (which opens
    # its own sqlite connection inside the hook) sees a flushed file.
    # ``MCS_NO_VERSIONING=1`` short-circuits inside the hook (T5 step 1) —
    # no per-call env check needed here.
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit_after_command(
        p,
        action=ACTION_REFRESH if refresh else ACTION_BUILD,
        summary=f"{p.name} @ {ts}",
    )
