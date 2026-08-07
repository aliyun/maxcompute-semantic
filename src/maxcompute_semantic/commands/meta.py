# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs meta`` — catalog-metadata discovery verbs.

The four-tier catalog ladder for MaxCompute, all at one CLI level:

  ``list-projects``      enumerate projects the credential can see
  ``list-schemas``       enumerate schemas inside a project
  ``list-tables``        enumerate tables inside a project (+ schema for 3-tier)
  ``describe-table``     column / partition metadata for one table

plus the four specialized verbs that sat under the old ``mcs meta``
sub-group: ``search-tables`` / ``search-columns`` (full-text-like search
across the catalog with a server-side path when the Catalog API is
available and a client-side iterate-and-match fallback otherwise),
``list-partitions`` (partition-key enumeration with a ``--limit``), and
``freshness`` (the table's latest-partition mtime, used by the agent
to gauge whether numbers are stale).

History: these eight verbs used to be split across two CLI groups —
the bottom six lived under ``mcs meta``, and ``list-projects`` /
``list-schemas`` lived under ``mcs profile`` (they were added there
because the source-picker wizard in ``mcs profile create / update``
calls the same underlying ``MaxComputeClient`` methods). The split
was historical accident: catalog metadata isn't a SQL concept (an
agent that hasn't written any SQL yet still wants to enumerate
tables), and ``mcs profile`` is supposed to be about the profile
record itself, not the live data the profile points at. Consolidating
all eight under a single top-level ``mcs meta`` group makes the
hierarchy match the catalog hierarchy.

The function bodies were moved unchanged from
``commands/sql.py`` and ``commands/profile.py``; the click decorators
all point at the local ``meta_group`` instead of the old
``sql_group.group("meta")`` / ``profile_group`` parents. The
underlying profile-resolution helper that
threads the standard profile-resolution chain (``--profile`` flag →
``MCS_PROFILE`` env var → cwd-link binding
→ standard ``ALIBABA_CLOUD_*`` env-vars anonymous fallback) is still
the shared entry point that every verb here calls.

The ``--schema`` / project-tier policy the inner six verbs need
lives in ``commands/_schema_resolve.resolve_schema_for_tier`` —
the same helper ``commands/sql.py`` and ``commands/build.py`` use.
Behavior: 2-level rejects any non-``"default"`` value; 3-level
returns the CLI value if set, else the single-source profile's
schema if there's exactly one source, else raises
:class:`SchemaRequiredError` (exit 2, JSON envelope through
``emit_mcs_error``). Previously these six verbs silently coerced
``None`` → ``"default"`` on tier-3, which masked misconfigured
profiles by hitting the upgrade-synthetic ``default`` slot; the
unified helper makes the failure visible and classified.

Output shape is uniform: a JSON envelope ``{status: "success", data:
{...}}`` on the happy path, ``{status: "error", error: {code,
message}}`` on the failure path, written to stdout via the shared
``_lib.status.emit_status`` and ``json.dumps`` helpers exactly the
same way as before the move.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from maxcompute_semantic._lib.status import emit_mcs_error, emit_status
from maxcompute_semantic.auth.context import make_client_for_project
from maxcompute_semantic.commands._schema_resolve import (
    resolve_project_for_profile,
    resolve_schema_for_tier,
)
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.mc_client.tier import get_tier

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient


@click.group(name="meta")
def meta_group() -> None:
    """Discover MaxCompute catalog metadata: projects, schemas, tables, columns.

    Eight verbs across the four catalog tiers (``list-projects`` /
    ``list-schemas`` / ``list-tables`` / ``describe-table``) plus
    search (``search-tables`` / ``search-columns``), partition
    listing (``list-partitions``), and freshness probing
    (``freshness``). Each verb consults the standard profile-resolution
    chain (``--profile`` flag, ``MCS_PROFILE`` env var, cwd-link from
    ``mcs link bind``, ``ALIBABA_CLOUD_*``
    env-var anonymous fallback) the same way ``mcs sql execute`` /
    ``mcs build`` / the other data verbs do.
    """


# ── catalog top tier: projects ─────────────────────────────────────────────


@meta_group.command("list-projects")
@click.option("--profile", default=None, help="profile name override")
@click.pass_context
def list_projects_cmd(ctx: click.Context, profile: str | None) -> None:
    """List MaxCompute projects the credential has visibility into.

    The agent-side source picker that ``mcs profile create`` /
    ``mcs profile update`` opens calls the same underlying API
    (``MaxComputeClient.list_projects``); this verb exposes the
    catalog-level enumeration step as a standalone command for
    agents working outside the wizard.

    Some credentials (LTAI keys with project-scoped permissions
    only, no catalog-API rights) get back an empty list even
    though they can read individual tables in specific projects.
    The wizard handles that with a "type the project name
    explicitly" fallback; standalone callers see the empty list
    and should treat it as "the catalog API isn't open to this
    AK" rather than "there are no projects".
    """
    client = make_client_for_project(None, profile_name=profile)
    try:
        projects = client.list_projects()
    except McsError as e:
        emit_mcs_error(e)
    emit_status({"projects": projects})


@meta_group.command("list-schemas")
@click.option(
    "--project",
    required=True,
    help="MaxCompute project name",
)
@click.option("--profile", default=None, help="profile name override")
@click.pass_context
def list_schemas_cmd(ctx: click.Context, project: str, profile: str | None) -> None:
    """Enumerate the schemas under one MaxCompute project.

    For 2-tier projects the returned list is the singleton
    ``["default"]`` — MaxCompute exposes a synthetic ``default``
    schema slot in the 2→3 upgrade path so all bare-name tables
    land somewhere addressable. For 3-tier projects the list is
    the actual user-created schemas.

    See ``mcs meta list-projects`` for the enumeration step one
    level up. The wizard's interactive source-picker drills the
    project → schema → table → column hierarchy using the same
    four ``mcs meta`` verbs in order.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    try:
        schemas = client.list_schemas(project=target_project)
    except McsError as e:
        emit_mcs_error(e)
    emit_status({"schemas": schemas})


# ── catalog middle tier: tables ────────────────────────────────────────────


@meta_group.command("list-tables")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.pass_context
def list_tables_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
) -> None:
    """List tables in a project (filtered by schema for 3-tier projects).

    For 3-tier projects, ``--schema`` selects the schema; if
    omitted, ``"default"`` is used (the conventional landing
    schema for tables created without an explicit schema). For
    2-tier projects, ``--schema`` is rejected if it names anything
    other than ``"default"`` — there's no schema layer to filter
    on, and a non-default value is a sign of a confused caller.

    Output: JSON envelope ``{status: "success", data: {tables:
    [name1, name2, …]}}``.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        tables = client.list_tables(schema=effective_schema, project=target_project)
    except McsError as e:
        emit_mcs_error(e)

    emit_status({"tables": tables})


@meta_group.command("describe-table")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.argument("table")
@click.pass_context
def describe_table_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    table: str,
) -> None:
    """Column-level metadata for one table.

    Output: JSON envelope ``{status: "success", data: {table:
    {name, schema: [{name, type, comment, …}, …], partition_columns:
    […], comment, lifecycle, …}}}``.

    The same schema / tier validation as ``list-tables``.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        result = client.describe_table(table, schema=effective_schema, project=target_project)
    except McsError as e:
        emit_mcs_error(e)

    # Enrich with source attribution when PackageDB is available.
    _annotate_source(client, target_project, effective_schema, table, result)

    emit_status(result)


def _annotate_source(
    client: MaxComputeClient,
    project: str | None,
    schema: str | None,
    table: str,
    result: dict,
) -> None:
    """Annotate a describe-table result with a ``source`` field.

    Looks up the (project, schema, table) triple in PackageDB and,
    when found, adds a ``source`` key with the matching source_key.
    Silent no-op when PackageDB is unavailable or the table isn't
    tracked locally.
    """
    profile = client.profile
    if not profile.sources or len(profile.sources) <= 1:
        return
    try:
        from maxcompute_semantic._internal.paths import profile_data_dir
        from maxcompute_semantic.build.storage import PackageDB

        db_path = profile_data_dir(profile) / "package.db"
        if not db_path.exists():
            return
        db = PackageDB(db_path)
        try:
            rows = db.list_tables()
            for row in rows:
                if row["name"] == table:
                    sk = row.get("source_key", "")
                    proj, _, sch = sk.partition("__")
                    if (not project or proj == project) and (not schema or sch == schema):
                        result.setdefault("table", {})["source"] = sk
                        return
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — silent no-op enrichment; see docstring
        return


# ── catalog search ─────────────────────────────────────────────────────────


@meta_group.command("search-tables")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.argument("keyword")
@click.pass_context
def search_tables_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    keyword: str,
) -> None:
    """Fuzzy-match the table catalogue against ``KEYWORD``.

    Uses the MaxCompute Catalog API's search endpoint when the
    credential has the right scope; otherwise falls back to a
    client-side ``list_tables`` + substring match on names,
    comments, and column names. Results are scored and sorted in
    descending relevance order.

    Output: JSON envelope ``{status: "success", data: {results:
    [{table_name, description, score, matched_columns}, …],
    count: N}}``.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        results = client.search_tables(keyword, schema=effective_schema, project=target_project)
    except McsError as e:
        emit_mcs_error(e)

    emit_status({"results": results, "count": len(results)})


@meta_group.command("search-columns")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.argument("keyword")
@click.pass_context
def search_columns_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    keyword: str,
) -> None:
    """Fuzzy-match columns across all tables against ``KEYWORD``.

    Client-side only — iterates all tables in the schema, scoring
    column matches by column name, comment, and the table-name
    context of the column. The cost scales with the table count,
    so this is the "I forget which table has the
    ``customer_lifetime_value`` field" verb, not a hot-path
    operation.

    Output: JSON envelope ``{status: "success", data: {results:
    [{table_name, column_name, type, comment, score}, …],
    count: N}}``.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        results = client.search_columns(keyword, schema=effective_schema, project=target_project)
    except McsError as e:
        emit_mcs_error(e)

    emit_status({"results": results, "count": len(results)})


# ── catalog: partitions and freshness ───────────────────────────────────────


@meta_group.command("list-partitions")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.option("--limit", default=100, type=int, help="max partitions to list")
@click.argument("table")
@click.pass_context
def list_partitions_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    limit: int,
    table: str,
) -> None:
    """Enumerate partition specs for a partitioned table.

    Returns the most-recent ``--limit`` partitions plus a
    ``has_more`` flag if the table has additional partitions
    beyond the window. The shape of each partition is the
    MaxCompute partition-spec string (e.g. ``"ds=20240101"`` or
    ``"region=cn,ds=20240101"`` for compound partition keys).

    Non-partitioned tables return ``{is_partitioned: false}``
    without an error — the call is informational, not an error
    path. The latest partition's modification time is the
    primary signal ``mcs meta freshness`` uses; this verb is the
    "show me all the recent partitions" view of the same data.

    Output: JSON envelope ``{status: "success", data:
    {table_name, partitions: [spec1, spec2, …], visible_count,
    has_more, latest_partition, is_partitioned}}``.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        result = client.list_partitions(
            table, schema=effective_schema, limit=limit, project=target_project
        )
    except McsError as e:
        emit_mcs_error(e)

    emit_status(result)


@meta_group.command("freshness")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.argument("table")
@click.pass_context
def freshness_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    table: str,
) -> None:
    """Report the last-modified timestamp for a table.

    For partitioned tables, derives from the most-recent
    partition's modification time. For non-partitioned tables,
    uses the table's own ``last_modified_time`` metadata. The
    agent uses this to decide whether the numbers it's about to
    return are fresh enough for the user's question ("show me
    yesterday's revenue" wants a table updated since midnight;
    "what was Q1 revenue" doesn't care about recency).

    Output: JSON envelope ``{status: "success", data:
    {table_name, is_partitioned, latest_partition,
    last_modified_time, freshness_summary, stale_warning}}``,
    where ``freshness_summary`` is a human-readable
    "updated 3 hours ago" string and ``stale_warning`` is set
    when the freshness exceeds a (configurable) staleness
    threshold.
    """
    client = make_client_for_project(project, profile_name=profile)
    target_project = resolve_project_for_profile(project, profile=client.profile)
    tier = get_tier(client.profile, target_project, client=client)

    try:
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        effective_schema = schema if tier == "3" else None
        result = client.freshness_info(table, schema=effective_schema, project=target_project)
    except McsError as e:
        emit_mcs_error(e)

    emit_status(result)
