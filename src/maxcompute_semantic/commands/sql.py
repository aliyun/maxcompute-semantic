# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""``mcs sql`` — the SQL-execution verb trio (``execute`` / ``cost``
/ ``explain``).

The standard profile-resolution chain runs through
``auth.context.make_client_for_project``: ``--profile`` flag,
then the ``MCS_PROFILE`` env var, then the cwd-link binding written
by ``mcs link bind``, then the standard ``ALIBABA_CLOUD_*`` /
``MAXCOMPUTE_*`` env-vars anonymous fallback (an in-memory Profile
the resolver constructs from the env without going through the
on-disk yaml). Each verb here just calls the helper and gets back
a ``MaxComputeClient`` already pointed at the right credential.

The catalog-metadata verbs (``list-tables``, ``describe-table``,
``search-tables``, ``search-columns``, ``list-partitions``,
``freshness``) that used to live as a sub-group under
``mcs meta`` were promoted to a top-level ``mcs meta`` group in
the post-v0.4 cleanup; they live in ``commands/meta.py`` now,
alongside the two ``list-projects`` / ``list-schemas`` verbs that
graduated from the ``mcs profile`` group at the same time. Metadata
isn't strictly a SQL concept (an agent that hasn't written any SQL
can still want to enumerate tables), so the relocation matches what
the verbs actually do. The ``--schema`` vs project-tier policy lives
in ``commands/_schema_resolve.resolve_schema_for_tier`` and is
imported by ``commands/sql.py``, ``commands/meta.py``, and
``commands/build.py`` so every verb shares the same resolution rule
(single-source profile auto-fills the schema; multi-source / env-var
fallback raises :class:`SchemaRequiredError` on tier-3 with no
``--schema``).

Tier (2-level vs 3-level) detection is automatic and cached in the
per-(profile, project) ``<data_dir>/tier_cache/<project>`` sentinel.
Cold lookups go through ``mc_client.tier.get_tier`` which calls
``ODPS.list_schemas`` and writes the "2" or "3" answer. 3-level
projects get a ``SET odps.namespace.schema=true`` + a session
``USE <schema>`` injected into the SQL via the per-verb body; 2-
level projects pass the SQL through verbatim.

Project auto-routing: with no ``--project`` flag, ``_route_project``
inspects the SQL via sqlglot and picks the source whose project owns
the referenced bare names — this closes the dev/prod gap so a
standard-mode profile (``compute_project`` = dev sandbox,
``sources[0].project`` = prod data store) targets prod for reads
without forcing ``--project`` on every invocation. Cross-source SQL
keeps ``compute_project`` and lets the engine route via each FQN.

Output is always a JSON envelope on stdout, written via the shared
``_lib.status.emit_status`` helper: ``{"status": "success", "data":
{...}}`` on the happy path and ``{"status": "error", "error":
{"code": "<McsError-code>", "message": "<message>", "remediation":
"<remediation>", "context": {...}}}`` on the classified-error path.
The ``remediation`` field carries the human-actionable next step the
``McsError`` subclass attached at raise time (e.g. "request SELECT
access from table owner") and is the agent's primary cue for what
to do next; ``context`` carries arbitrary kwargs the raiser passed
(typically the failing ``sql`` and, when applicable, ``source_key``).
Unclassified errors bubble out as a normal Python traceback the same
way every other ``mcs`` verb does.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, NoReturn

import click
import sqlglot

from maxcompute_semantic._lib.status import emit_status
from maxcompute_semantic.auth.context import (
    ProfileContext,
    make_client_for_project,
    resolve_profile_for_project,
)
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.commands._schema_resolve import resolve_schema_for_tier
from maxcompute_semantic.errors import TimeoutError as McsTimeoutError
from maxcompute_semantic.mc_client.errors import McsError, WriteOpRejectedError
from maxcompute_semantic.mc_client.sql_guard import (
    classify_sql as _classify_sql,
)
from maxcompute_semantic.mc_client.sql_guard import (
    last_parse_error as _last_parse_error,
)
from maxcompute_semantic.mc_client.sql_preprocess import split_set_hints
from maxcompute_semantic.mc_client.tier import get_tier

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.mc_client.client import MaxComputeClient


def _parse_tables(sql: str) -> list[sqlglot.exp.Table]:
    """Flat list of ``Table`` references in *sql*; ``[]`` on parse failure."""
    try:
        from maxcompute_semantic.dialect import parse_mc

        statements = parse_mc(sql, error_level=sqlglot.ErrorLevel.IGNORE)
    except Exception:
        return []
    tables: list[sqlglot.exp.Table] = []
    for statement in statements:
        if statement is None:
            continue
        tables.extend(statement.find_all(sqlglot.exp.Table))
    return tables


def _source_keys_for_two_segment(
    db: PackageDB,
    profile: Profile,
    db_segment: str,
    table_name: str,
    *,
    preferred_project: str | None,
) -> set[str]:
    """Resolve a two-segment ``db.table`` reference to package sources.

    ``db`` can mean schema (3-level ``schema.table``) or project
    (2-level ``project.table``). Schema-form wins. If the preferred
    SQL-session project has that schema, only that project is eligible;
    a miss there must not fall through to another project's same-named
    schema. Project-form is the 2-level/default-schema shape only.
    """
    segment = db_segment.lower()
    preferred = (preferred_project or "").lower()

    schema_sources = [src for src in profile.sources if (src.schema or "").lower() == segment]
    if schema_sources:
        preferred_sources = [
            src for src in schema_sources if preferred and src.project.lower() == preferred
        ]
        if preferred_sources:
            return {
                sk
                for src in preferred_sources
                if (sk := db.lookup_source_key(src.project, src.schema, table_name))
            }
        return {
            sk
            for src in schema_sources
            if (sk := db.lookup_source_key(src.project, src.schema, table_name))
        }

    return {
        sk
        for src in profile.sources
        if src.project.lower() == segment and (src.schema or "default").lower() == "default"
        if (sk := db.lookup_source_key(src.project, src.schema, table_name))
    }


def _resolve_source_keys(sql: str, profile: Profile) -> set[str]:
    """Resolve every table reference in *sql* to a profile ``source_key``.

    Qualified ``project.schema.table`` forms are looked up against
    their explicit catalog/db. **Bare names iterate over
    ``profile.sources``** until one matches — the dev/prod fix: the
    earlier helper anchored on ``profile.compute_project`` and
    silently missed every source that didn't happen to live in the
    dev project (the typical DataWorks standard-mode shape). Returns
    ``set()`` when ``package.db`` is missing or nothing resolves.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    db_path = profile_data_dir(profile) / "package.db"
    if not db_path.exists():
        return set()

    source_keys: set[str] = set()
    db: PackageDB | None = None
    try:
        db = PackageDB(db_path)
        for table in _parse_tables(sql):
            if table.catalog:
                sk = db.lookup_source_key(table.catalog, table.db or "default", table.name)
                if sk:
                    source_keys.add(sk)
                continue
            if table.db:
                source_keys.update(
                    _source_keys_for_two_segment(
                        db,
                        profile,
                        table.db,
                        table.name,
                        preferred_project=profile.compute_project,
                    )
                )
                continue
            for src in profile.sources:
                sk = db.lookup_source_key(src.project, src.schema, table.name)
                if sk:
                    source_keys.add(sk)
                    break
    except Exception:
        return set()
    finally:
        if db is not None:
            db.close()
    return source_keys


def _resolve_target_project(sql: str, profile: Profile) -> str | None:
    """Pick the MaxCompute project the client should default to for *sql*.

    Returns the unique project name when every referenced table
    resolves to one source, or the explicit catalog of a 3-segment
    FQN. Returns ``None`` when references span multiple projects so
    the caller keeps ``profile.compute_project`` and lets the engine
    route via each FQN. With zero resolved references (``SELECT 1``,
    DDL touching a never-built name, parse failure) falls back to
    ``sources[0].project`` so the typical single-source dev/prod
    profile still targets the data-side project rather than the
    compute sandbox.
    """
    if not profile.sources:
        return None

    projects: set[str] = set()
    for table in _parse_tables(sql):
        if table.catalog:
            projects.add(table.catalog)
    projects.update(sk.split("__", 1)[0] for sk in _resolve_source_keys(sql, profile))

    if len(projects) == 1:
        return next(iter(projects))
    if len(projects) > 1:
        return None
    return profile.sources[0].project


def _route_project(explicit: str | None, profile_name: str | None, sql: str) -> str | None:
    """Choose the project the ``mcs sql`` verb should target.

    Explicit ``--project X`` always wins (unchanged behavior). When
    nothing is pinned, resolve the active profile and let
    :func:`_resolve_target_project` decide based on the SQL's table
    references. This is the seam that closes the dev/prod gap: a
    standard-mode profile (``compute_project`` = dev sandbox,
    ``sources[0].project`` = prod store) auto-routes bare names to
    the prod side without forcing ``--project`` on every invocation.
    """
    if explicit is not None:
        return explicit
    profile_obj = resolve_profile_for_project(None, profile_name=profile_name)
    return _resolve_target_project(sql, profile_obj)


def _resolve_source_tags(sql: str, profile: Profile) -> str:
    """Build the ``[source=p__s] `` prefix(es) used on classified
    error envelopes — empty string when nothing resolves, one tag
    per source in sorted order otherwise.
    """
    source_keys = _resolve_source_keys(sql, profile)
    if not source_keys:
        return ""
    return "".join(f"[source={sk}] " for sk in sorted(source_keys))


def _emit_mcs_error(sql: str, profile: Profile | None, exc: McsError) -> NoReturn:
    """Print the source-tagged error envelope on stdout and exit.

    Collapses the three verbatim except-blocks in execute/cost/explain
    into one path so the envelope shape, the ``[source=...]`` prefix,
    and the ``exit_code`` propagation stay in lockstep.
    """
    source_tags = _resolve_source_tags(sql, profile) if profile is not None else ""
    error_env = {
        "status": "error",
        "error": {
            "code": exc.code,
            "message": f"{source_tags}{exc.message}",
            "remediation": exc.remediation,
            "context": dict(exc.context),
        },
    }
    print(json.dumps(error_env, ensure_ascii=False))
    sys.exit(exc.exit_code)


def _guard_sql_execution(sql: str, profile: str | None, *, allow_write: bool) -> None:
    """Apply the read-only SQL guard shared by sync and async submit paths."""
    if allow_write:
        return
    verdict = _classify_sql(sql)
    if verdict == "read":
        return

    try:
        profile_obj = resolve_profile_for_project(None, profile_name=profile)
    except McsError as e:
        _emit_mcs_error(sql, None, e)
    if verdict == "write":
        exc = WriteOpRejectedError(
            "SQL is a DML/DDL write; mcs sql execute refuses by default",
            remediation=(
                "pass --allow-write to confirm the write intent, "
                "or use the dedicated verb (mcs udf *, "
                "mcs build) for managed write paths"
            ),
            sql=sql,
        )
    else:  # "unparseable"
        parse_detail = _last_parse_error()
        hint = f" Parse error: {parse_detail}." if parse_detail else ""
        exc = WriteOpRejectedError(
            f"SQL could not be parsed as a known read shape; mcs sql execute fails closed.{hint}",
            remediation=(
                "fix the SQL syntax error described in the message field, "
                "or double embedded single quotes in string literals "
                "(for example 'O''Reilly', not backslash escaping), "
                "or pass --allow-write if the statement is intentional "
                "(typical for MaxCompute-specific DDL like ADD JAR or SET LABEL)"
            ),
            sql=sql,
        )
    _emit_mcs_error(sql, profile_obj, exc)


def _split_or_emit(sql: str) -> tuple[str, dict[str, str]]:
    """Extract SET→hints; emit+exit if the SQL is only SETs (no query).

    Shared by the ``mcs sql execute`` / ``submit`` verbs so the write guard,
    project routing, cost gate, and pyodps submission all see the SET-free
    SQL and the extracted hints. (``cost`` / ``explain`` / ``review`` are
    wired separately.) A standalone ``SET k=v`` (no query) is rejected with
    ``WriteOpRejectedError`` because there is nothing to execute. If the SQL
    cannot be tokenized, ``split_set_hints`` returns it unchanged and the
    downstream write guard classifies it as ``unparseable`` (friendly
    parse-error remediation, e.g. the doubled-quote hint).
    """
    stripped_sql, set_hints = split_set_hints(sql)
    if not stripped_sql.strip():
        _emit_mcs_error(
            sql,
            None,
            WriteOpRejectedError(
                "SQL contained only SET statements with no query; nothing to execute",
                remediation=(
                    "add a SELECT/INSERT/... statement after the SET, or remove "
                    "the SET if you only meant to set a session property"
                ),
                sql=sql,
            ),
        )
    return stripped_sql, set_hints


def _client_and_schema_for_sql(
    project: str | None,
    profile: str | None,
    schema: str | None,
    sql: str,
) -> tuple[MaxComputeClient, str]:
    """Resolve routed client + tier-aware schema for SQL execution verbs."""
    target_project = _route_project(project, profile, sql)
    client = make_client_for_project(target_project, profile_name=profile)
    tier = get_tier(client.profile, client.profile.compute_project, client=client)
    resolved_schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
    return client, resolved_schema


@click.group(name="sql")
def sql_group() -> None:
    """Execute SQL or estimate cost against MaxCompute."""


# ── sql execute ──────────────────────────────────────────────────────────────


@sql_group.command("execute")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema (omit for 2-level; required for 3-level)")
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--max-rows",
    default=None,
    type=click.IntRange(min=0),
    help=(
        "maximum result rows to return (default: 10000; set 0 for unlimited; "
        "MCS_SQL_RESULT_MAX_ROWS also overrides the default)"
    ),
)
@click.option(
    "--offset",
    "result_offset",
    default=0,
    type=click.IntRange(min=0),
    help="zero-based result row offset to return from (default: 0)",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    default=False,
    help=(
        "skip the cost-confirm prompt — for non-interactive callers and "
        "agents. Has no effect on the blocked-cost ceiling (which is a "
        "hard refusal)."
    ),
)
@click.option(
    "--allow-write",
    "allow_write",
    is_flag=True,
    default=False,
    help=(
        "permit DML/DDL writes (INSERT/UPDATE/DELETE/MERGE/CREATE/"
        "DROP/ALTER/TRUNCATE/GRANT/REVOKE) and statements sqlglot "
        "cannot classify as a known read shape (e.g. ADD JAR). "
        "Without this flag, mcs sql execute is read-only and refuses "
        "before any cost-estimate round-trip."
    ),
)
@click.option(
    "--timeout",
    default=30,
    type=click.IntRange(min=1),
    help=(
        "seconds to wait synchronously for the result (default: 30). On "
        "timeout the query keeps running — the command returns its "
        "instance_id + logview so you can continue with `mcs sql wait` / "
        "`mcs sql result` instead of resubmitting."
    ),
)
@click.argument("sql")
@click.pass_context
def execute_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    max_rows: int | None,
    result_offset: int,
    assume_yes: bool,
    allow_write: bool,
    timeout: int,
    sql: str,
) -> None:
    """Execute a SQL statement against MaxCompute.

    Tier resolution is automatic (cached tier_cache/<project> sentinel
    + probe). 3-level projects receive SET namespace/schema hints;
    2-level projects pass the SQL as-is. --schema is required for
    3-level projects; for 2-level projects only `default` is accepted.

    Cost gate runs before submission: when the estimate exceeds the
    profile's ``cost_thresholds.confirm_cny`` the CLI either prompts
    (TTY) or refuses with ``CostConfirmRequired`` (non-TTY); when it
    exceeds ``blocked_cny`` it always refuses with ``CostBlocked``.
    Pass ``--yes`` / ``-y`` to bypass the confirm prompt in
    non-interactive callers.

    Output: the Envelope JSON on stdout.
    """
    stripped_sql, set_hints = _split_or_emit(sql)
    _guard_sql_execution(stripped_sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, stripped_sql)
        envelope = client.execute_sql(
            stripped_sql,
            schema=schema,
            hints=set_hints or None,
            assume_yes=assume_yes,
            max_rows=max_rows,
            result_offset=result_offset,
            allow_write=allow_write,
            timeout=timeout,
        )
    except McsTimeoutError as e:
        # The sync wait elapsed but the instance is still running. Hand it off
        # to the async lifecycle so the caller continues with the instance_id
        # (sql wait / sql result) instead of resubmitting the query.
        instance_id = str(e.context.get("instance_id") or "")
        if client is None or not instance_id:
            _emit_mcs_error(sql, client.profile if client is not None else None, e)
            return  # _emit_mcs_error is NoReturn (sys.exit); this is for readers + linters
        try:
            status = client.get_instance_status(instance_id)
        except Exception:
            status = {
                "instance_id": instance_id,
                "logview_url": e.context.get("logview_url", ""),
                "lifecycle_state": "running",
                "terminal": False,
            }
        # Include the routed project so the follow-up commands target the same
        # project the query ran in (a multi-source profile routes execute to a
        # source project that differs from the profile's default compute project).
        proj = client.profile.compute_project
        prof = f"--profile {profile} " if profile else ""
        status["sync_timed_out"] = True
        status["next_step"] = (
            f"mcs sql wait {prof}--project {proj} {instance_id}; "
            f"then mcs sql result {prof}--project {proj} {instance_id}"
        )
        emit_status(status)
        return
    except McsError as e:
        _emit_mcs_error(sql, client.profile if client is not None else None, e)

    # Emit the envelope dict as JSON on stdout.
    print(json.dumps(envelope.to_dict(), ensure_ascii=False))


# ── sql async lifecycle ─────────────────────────────────────────────────────


@sql_group.command("submit")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema (omit for 2-level; required for 3-level)")
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    default=False,
    help=(
        "skip the cost-confirm prompt — for non-interactive callers and "
        "agents. Has no effect on the blocked-cost ceiling."
    ),
)
@click.option(
    "--allow-write",
    "allow_write",
    is_flag=True,
    default=False,
    help="permit DML/DDL writes; without this flag submit is read-only like execute.",
)
@click.argument("sql")
def submit_cmd(
    project: str | None,
    schema: str | None,
    profile: str | None,
    assume_yes: bool,
    allow_write: bool,
    sql: str,
) -> None:
    """Submit SQL asynchronously and return the MaxCompute instance ID."""
    stripped_sql, set_hints = _split_or_emit(sql)
    _guard_sql_execution(stripped_sql, profile, allow_write=allow_write)

    client = None
    try:
        client, schema = _client_and_schema_for_sql(project, profile, schema, stripped_sql)
        instance_id = client.run_sql_async(
            stripped_sql,
            schema=schema,
            hints=set_hints or None,
            assume_yes=assume_yes,
            allow_write=allow_write,
        )
        try:
            result = client.get_instance_status(instance_id)
        except McsError as status_error:
            result = {
                "instance_id": instance_id,
                "status": "Submitted",
                "status_probe_error": status_error.to_envelope()["error"],
            }
    except McsError as e:
        _emit_mcs_error(sql, client.profile if client is not None else None, e)

    emit_status(result)


@sql_group.command("status")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--profile", default=None, help="profile name override")
@click.argument("instance_id")
def status_cmd(project: str | None, profile: str | None, instance_id: str) -> None:
    """Show status for a MaxCompute SQL instance."""
    client = None
    try:
        client = make_client_for_project(project, profile_name=profile)
        result = client.get_instance_status(instance_id)
    except McsError as e:
        _emit_mcs_error("", client.profile if client is not None else None, e)

    emit_status(result)


@sql_group.command("wait")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--timeout",
    default=600,
    type=click.IntRange(min=1),
    help="wait timeout in seconds",
)
@click.option(
    "--interval",
    default=3,
    type=click.IntRange(min=1),
    help="poll interval in seconds",
)
@click.argument("instance_id")
def wait_cmd(
    project: str | None,
    profile: str | None,
    timeout: int,
    interval: int,
    instance_id: str,
) -> None:
    """Wait for a MaxCompute SQL instance to reach a terminal status."""
    client = None
    try:
        client = make_client_for_project(project, profile_name=profile)
        result = client.wait_for_instance(instance_id, timeout=timeout, interval=interval)
    except McsError as e:
        _emit_mcs_error("", client.profile if client is not None else None, e)

    emit_status(result)


@sql_group.command("result")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--profile", default=None, help="profile name override")
@click.option(
    "--max-rows",
    default=None,
    type=click.IntRange(min=0),
    help=(
        "maximum result rows to return (default: 10000; set 0 for unlimited; "
        "MCS_SQL_RESULT_MAX_ROWS also overrides the default)"
    ),
)
@click.option(
    "--offset",
    "result_offset",
    default=0,
    type=click.IntRange(min=0),
    help="zero-based result row offset to return from (default: 0)",
)
@click.argument("instance_id")
def result_cmd(
    project: str | None,
    profile: str | None,
    max_rows: int | None,
    result_offset: int,
    instance_id: str,
) -> None:
    """Read result rows for a completed MaxCompute SQL instance."""
    client = None
    try:
        client = make_client_for_project(project, profile_name=profile)
        result = client.get_instance_result(
            instance_id,
            max_rows=max_rows,
            result_offset=result_offset,
        )
    except McsError as e:
        _emit_mcs_error("", client.profile if client is not None else None, e)

    emit_status(result)


@sql_group.command("cancel")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--profile", default=None, help="profile name override")
@click.argument("instance_id")
def cancel_cmd(project: str | None, profile: str | None, instance_id: str) -> None:
    """Cancel a running MaxCompute SQL instance."""
    client = None
    try:
        client = make_client_for_project(project, profile_name=profile)
        result = client.cancel_instance(instance_id)
    except McsError as e:
        _emit_mcs_error("", client.profile if client is not None else None, e)

    emit_status(result)


# ── sql cost ─────────────────────────────────────────────────────────────────


@profile_command(sql_group, "cost")
@click.argument("sql")
def cost_cmd(pctx: ProfileContext, sql: str) -> None:
    """Estimate the cost of a SQL statement via pyodps execute_sql_cost.

    Same tier resolution as `mcs sql execute`. --schema is required for
    3-level projects; for 2-level projects only `default` is accepted.

    Converts estimated input bytes to CNY at 0.3/GB and applies a cost gate:

      verdict = "ok"      (< 10 CNY)   — agent may proceed
      verdict = "confirm" (10-100 CNY) — agent should ask user
      verdict = "blocked" (>= 100 CNY) — agent must refuse

    The exit code is 0 even for 'blocked' verdicts (the verdict is in
    the JSON payload). On error, exits with the classified exit_code
    (4 for auth failures, 5 for permission/not-found).
    """
    stripped_sql, set_hints = _split_or_emit(sql)
    client = None
    try:
        target_project = _route_project(pctx.project_override, pctx.profile.name, stripped_sql)
        client = make_client_for_project(target_project, profile_name=pctx.profile.name)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, pctx.schema_override, profile=client.profile)
        result = client.cost_estimate(stripped_sql, schema=schema, hints=set_hints or None)
    except McsError as e:
        _emit_mcs_error(stripped_sql, client.profile if client is not None else pctx.profile, e)

    emit_status(result)


# ── sql explain ───────────────────────────────────────────────────────────────


@sql_group.command("explain")
@click.option(
    "--project",
    default=None,
    help="MaxCompute project name",
)
@click.option("--schema", default=None, help="schema name (omit for 2-level projects)")
@click.option("--profile", default=None, help="profile name override")
@click.option("--timeout", default=120, type=int, help="execution timeout in seconds")
@click.argument("sql")
@click.pass_context
def explain_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    timeout: int,
    sql: str,
) -> None:
    """Get the execution plan for a SQL statement.

    Runs EXPLAIN <sql> and returns the plan text.
    Tier resolution is automatic (3-level projects get namespace/schema hints).
    """
    stripped_sql, set_hints = _split_or_emit(sql)
    client = None
    try:
        target_project = _route_project(project, profile, stripped_sql)
        client = make_client_for_project(target_project, profile_name=profile)
        tier = get_tier(client.profile, client.profile.compute_project, client=client)
        schema = resolve_schema_for_tier(tier, schema, profile=client.profile)
        result = client.explain(stripped_sql, timeout=timeout, schema=schema, hints=set_hints or None)
    except McsError as e:
        _emit_mcs_error(stripped_sql, client.profile if client is not None else None, e)

    emit_status(result)


# ── sql review ───────────────────────────────────────────────────────────────


@sql_group.command("review")
@click.option("--project", default=None, help="MaxCompute project name")
@click.option(
    "--schema",
    default=None,
    help="schema (omit for 2-level; required for 3-level)",
)
@click.option("--profile", default=None, help="profile name override")
@click.argument("sql")
@click.pass_context
def review_cmd(
    ctx: click.Context,
    project: str | None,
    schema: str | None,
    profile: str | None,
    sql: str,
) -> None:
    """Pre-execution conformance check on SQL: returns issues + hints + model_coverage.

    Read-only — refuses writes. Stateless — reads the profile's package and
    memory when available. Without a package, still runs package-independent
    syntax / dialect checks and marks semantic checks skipped.
    """
    from maxcompute_semantic.commands.sql_review import build_review_envelope
    from maxcompute_semantic.commands.sql_review.types import (
        ReviewUnsupportedError,
    )
    from maxcompute_semantic.mc_client.envelope import Envelope

    profile_obj: Profile | None = None
    try:
        profile_obj = resolve_profile_for_project(project, profile_name=profile)
        # Sibling-parity routing: bare table refs auto-route to the prod
        # store on standard-mode profiles via ``_route_project``. The
        # standalone ``_resolve_profile_for_project`` call above stays —
        # ``profile_obj`` is consumed downstream by ``build_review_envelope``,
        # ``get_tier``, and ``_emit_mcs_error``; the duplicate resolution
        # inside ``_route_project`` is the trade-off siblings already accept.
        target_project = _route_project(project, profile, sql)
    except McsError as e:
        _emit_mcs_error(sql, profile_obj, e)
    assert profile_obj is not None

    # Refuse writes / unparseable. The dispatcher would happily run rules
    # against an unparseable string (each rule short-circuits on parse
    # failure individually, per the dispatcher's contract), but the
    # spec §5.4 refusal contract for the CLI seam folds both into one
    # MCS_REVIEW_UNSUPPORTED envelope so the agent can branch on
    # ``classification`` rather than parsing per-rule emit lists.
    verdict = _classify_sql(sql)
    if verdict != "read":
        _emit_mcs_error(
            sql,
            profile_obj,
            ReviewUnsupportedError(
                f"mcs sql review is read-only; got SQL classified as {verdict!r}",
                remediation=(
                    "for writes, run `mcs sql execute --allow-write`; "
                    "for unparseable input, simplify the SQL or pass it "
                    "directly to `mcs sql execute`"
                ),
                classification=verdict,
            ),
        )

    # ``mcs sql review`` promises no MaxCompute round-trip
    # (``references/query.md``), so we pin ``allow_live_probe=False``:
    # on cache miss ``get_tier`` returns ``"3"`` (operationally-safe
    # default) instead of constructing a client and calling
    # ``list_schemas``. Prefer the routed ``target_project`` (the data
    # project the SQL actually references) over the profile's
    # ``compute_project`` — they can diverge in dev/prod profiles where
    # the compute project is cached as tier 2 but the source project is
    # tier 3, and reading the wrong sentinel would silently mask
    # ``tier.bare-table-in-3level``. Cross-source SQL routes
    # ``target_project=None`` (``_route_project`` bails when refs span
    # sources) and ``tier_cache_path`` rejects ``None``; in that case
    # fall back to ``compute_project``.
    tier_project = target_project or profile_obj.compute_project
    tier = get_tier(profile_obj, tier_project, allow_live_probe=False)

    data = build_review_envelope(
        sql=sql,
        profile=profile_obj,
        project=target_project,
        schema_name=schema,
        tier=tier,
    )

    envelope = Envelope.success(data)
    print(json.dumps(envelope.to_dict(), ensure_ascii=False))
