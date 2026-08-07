# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MaxComputeClient: thin pyodps wrapper with credential refresh + tier cache."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

# pyodps is imported lazily (inside the methods that need it): importing
# ``odps`` eagerly pulls pandas/numpy/pyarrow into every CLI startup, and
# local-only commands (profile list, link, doctor) would pay that cost
# without ever touching MaxCompute.
if TYPE_CHECKING:
    from odps import ODPS  # type: ignore[import-untyped, unused-ignore]

from maxcompute_semantic import __version__ as _mcs_version
from maxcompute_semantic.auth.credential import resolve_credentials
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.errors import maps_pyodps_errors
from maxcompute_semantic.mc_client.cost_gate import enforce_cost_gate
from maxcompute_semantic.mc_client.sql_guard import ensure_sql_write_allowed

_MCS_UA_SUFFIX = f"maxcompute-semantic/{_mcs_version}"


def _mcs_user_agent() -> str:
    """Compose the pyodps ``user_agent`` rest-client kwarg lazily.

    Reading pyodps's default user agent requires importing ``odps.rest``,
    which would defeat the lazy-import design above.
    """
    try:
        from odps.rest import (  # type: ignore[import-untyped, unused-ignore]
            default_user_agent as _pyodps_ua,
        )
    except ImportError:
        return _MCS_UA_SUFFIX
    try:
        return f"{_pyodps_ua()} {_MCS_UA_SUFFIX}"
    except Exception:  # noqa: BLE001 — cosmetic UA suffix; fall back silently
        return _MCS_UA_SUFFIX

if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.envelope import Envelope

logger = logging.getLogger(__name__)

_DEFAULT_SQL_RESULT_MAX_ROWS = 10_000
_SQL_RESULT_MAX_ROWS_ENV = "MCS_SQL_RESULT_MAX_ROWS"

_INSTANCE_TERMINAL_STATUS_NAMES = frozenset(
    {"TERMINATED", "SUCCESS", "SUCCEEDED", "FAILED", "CANCELLED", "CANCELED"}
)
_INSTANCE_CANCELLED_STATUS_NAMES = frozenset({"CANCELLED", "CANCELED"})
_TASK_SUCCESS_STATUS_NAMES = frozenset({"SUCCESS", "SUCCEEDED"})
_TASK_FAILED_STATUS_NAMES = frozenset({"FAILED"})
_TASK_CANCELLED_STATUS_NAMES = frozenset({"CANCELLED", "CANCELED"})
_SUSPENDED_STATUS_NAMES = frozenset({"SUSPENDED"})


def _resolve_result_max_rows(max_rows: int | None) -> int | None:
    """Return the effective SQL result row cap.

    Mirrors odpscmd's display-side limit: it limits rows returned to the
    caller, not the submitted SQL. ``0`` or a negative value disables the cap.
    """
    value = max_rows
    if value is None:
        raw = os.environ.get(_SQL_RESULT_MAX_ROWS_ENV)
        if raw is None or raw.strip() == "":
            value = _DEFAULT_SQL_RESULT_MAX_ROWS
        else:
            try:
                value = int(raw)
            except ValueError:
                logger.debug(
                    "Ignoring invalid %s=%r; using default %d",
                    _SQL_RESULT_MAX_ROWS_ENV,
                    raw,
                    _DEFAULT_SQL_RESULT_MAX_ROWS,
                )
                value = _DEFAULT_SQL_RESULT_MAX_ROWS
    return None if value <= 0 else value


def _resolve_result_offset(result_offset: int | None) -> int:
    """Return a safe zero-based result offset."""
    if result_offset is None or result_offset <= 0:
        return 0
    return result_offset


def _reader_total_count(reader: Any) -> int | None:
    """Best-effort total row count from pyodps readers."""
    for attr in ("count", "record_count"):
        value = getattr(reader, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                logger.debug("Ignoring unreadable reader.%s row count", attr, exc_info=True)
                continue
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


class MaxComputeClient:
    """Wraps pyodps ODPS with mcs-specific concerns: lazy init, cred refresh.

    Public API:
      - execute_sql(sql, *, hints, timeout, use_interactive, max_rows,
        result_offset) -> Envelope
      - run_sql_async(sql, *, hints, schema, assume_yes) -> instance ID
      - list_schemas() -> list[str]
      - list_projects() -> list[str]
      - list_tables(*, schema) -> list[str]
      - describe_table(name, *, schema) -> dict
      - cost_estimate(sql, *, hints, timeout) -> dict
      - can_access_table(table, *, schema) -> dict
      - search_tables(keyword, *, schema) -> list[dict]   [Catalog FTS + fallback]
      - search_columns(keyword, *, schema) -> list[dict]  [client-side only]
      - list_partitions(table, *, schema, limit) -> dict
      - freshness_info(table, *, schema) -> dict
      - list_functions / explain
      - sample_table(table, *, schema, limit, partition) -> dict
      - profile_table(table, *, schema, limit, partition) -> dict
      - run_sql_async(sql, *, hints) -> str                 [instance ID]
      - get_instance_status(instance_id) -> dict
      - wait_for_instance(instance_id, *, timeout, interval) -> dict
      - get_instance_result(instance_id, *, max_rows, result_offset) -> dict
      - cancel_instance(instance_id) -> dict
      - list_recent_instances(*, limit) -> list[dict]
    """

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._odps: ODPS | None = None
        self._creds_expiration: datetime | None = None
        self._tier: str | None = None  # populated by tier.get_tier()
        self._project_tier_cache: dict[str, str] = {}

    def _source_key(self, project: str | None, schema: str | None) -> str:
        """Derive the canonical ``project__schema`` source attribution tag.

        Used as the ``source_key`` arg to ``map_pyodps_exception`` so that
        ``TableNotFoundError`` / ``PermissionDeniedError`` get a
        ``[source=...]`` prefix in multi-source profiles. 2-tier projects
        canonicalize to ``__default`` (the synthetic schema slot the
        Profile / PackageDB layer already uses).
        """
        proj = project or self._profile.compute_project
        return f"{proj}__{schema or 'default'}"

    def _ensure_odps(self) -> ODPS:
        """Return cached ODPS instance, refreshing if creds expired."""
        if self._odps is not None and self._creds_still_valid():
            return self._odps
        from odps import ODPS  # type: ignore[import-untyped, unused-ignore]
        from odps.accounts import (  # type: ignore[import-untyped, unused-ignore]
            StsAccount,
        )

        creds = resolve_credentials(self._profile.auth)
        if creds.security_token:
            # pyodps doesn't accept security_token as a standalone kwarg;
            # STS credentials must be wrapped in a StsAccount and passed
            # as access_id.  StsAccount expires internally via
            # expired_hours (computed from expiration if available).
            hours = 12  # default fallback
            if creds.expiration:
                remaining = creds.expiration - datetime.now(timezone.utc)
                hours = max(int(remaining.total_seconds() / 3600), 1)
            account = StsAccount(
                creds.access_key_id,
                creds.access_key_secret,
                creds.security_token,
                expired_hours=hours,
            )
            self._odps = ODPS(
                access_id=account,
                project=self._profile.compute_project,
                endpoint=self._profile.endpoint,
                rest_client_kwargs={"user_agent": _mcs_user_agent()},
            )
        else:
            self._odps = ODPS(
                access_id=creds.access_key_id,
                secret_access_key=creds.access_key_secret,
                project=self._profile.compute_project,
                endpoint=self._profile.endpoint,
                rest_client_kwargs={"user_agent": _mcs_user_agent()},
            )
        self._creds_expiration = creds.expiration
        return self._odps

    def _creds_still_valid(self) -> bool:
        """STS valid if expiration is more than 60s in the future. AK never expires."""
        if self._creds_expiration is None:
            return True
        return datetime.now(timezone.utc) < self._creds_expiration - timedelta(seconds=60)

    @property
    def profile(self) -> Profile:
        return self._profile

    @maps_pyodps_errors(sql_arg="sql")
    def execute_sql(
        self,
        sql: str,
        *,
        hints: dict[str, str] | None = None,
        timeout: int = 30,
        use_interactive: bool = False,
        schema: str | None = None,
        assume_yes: bool = False,
        max_rows: int | None = None,
        result_offset: int | None = None,
        allow_write: bool = False,
        skip_cost_gate: bool = False,
    ) -> Envelope:
        import sys
        import time

        from maxcompute_semantic.errors import TimeoutError as McsTimeoutError
        from maxcompute_semantic.mc_client.hints import build_hints
        from maxcompute_semantic.mc_client.tier import get_tier

        started = time.monotonic()
        ensure_sql_write_allowed(sql, allow_write=allow_write)

        # Prime tier + init connection — ODPSError here (e.g. AccessKeyIdNotFound
        # on fake AK) is reclassified by the @maps_pyodps_errors decorator.
        if self._tier is None:
            self._tier = get_tier(self._profile, self._profile.compute_project, client=self)

        # Cost gate runs before submission. Short-circuits when the profile
        # has cost_thresholds disabled (eval harness sets both to 0); raises
        # CostBlockedError / CostConfirmRequiredError otherwise.
        if not skip_cost_gate:
            enforce_cost_gate(
                self,
                sql,
                hints=hints,
                schema=schema,
                assume_yes=assume_yes,
                is_tty=sys.stdin.isatty(),
            )

        odps = self._ensure_odps()
        final_hints = build_hints(
            tier=self._tier,
            schema=schema,
            user_hints=hints,
        )

        if use_interactive:
            # pyodps `execute_sql_interactive` routes to the interactive
            # quota — MCQA v1 on legacy projects, MaxQA (MCQA 2.0) on
            # post-2025-11 projects. Both bypass Fuxi cold-start so the
            # probe stays sub-second on warm runtimes. Callers must
            # handle the case where the project has no interactive
            # quota configured — see _auth_probe._run_auth_test for the
            # fallback pattern.
            instance = odps.execute_sql_interactive(sql, hints=final_hints)
        else:
            instance = odps.run_sql(sql, hints=final_hints)
            try:
                instance.wait_for_success(interval=2, timeout=timeout)
            except TimeoutError as e:
                # The instance keeps running server-side after the sync wait
                # elapses. Surface its id + logview so callers (the `mcs sql
                # execute` CLI) can hand off to the async lifecycle instead of
                # discarding the already-submitted job.
                instance_id = str(getattr(instance, "id", "") or "")
                try:
                    logview = instance.get_logview_address()
                except Exception:  # noqa: BLE001 — best-effort; the timeout error must still raise
                    logview = ""
                raise McsTimeoutError(
                    f"SQL execution exceeded the synchronous {timeout}s wait; "
                    f"instance {instance_id} is still running",
                    remediation=(
                        "the query is still running — do not resubmit. Poll it "
                        f"with `mcs sql wait --project {self._profile.compute_project}"
                        f" {instance_id}`, then read rows with `mcs sql result "
                        f"--project {self._profile.compute_project} {instance_id}`"
                        + (f". Logview: {logview}" if logview else "")
                    ),
                    sql=sql,
                    instance_id=instance_id,
                    logview_url=logview,
                ) from e
        return self._build_success_envelope(
            instance,
            started,
            sql=sql,
            max_rows=max_rows,
            result_offset=result_offset,
        )

    def _build_success_envelope(
        self,
        instance: Any,
        started: float,
        *,
        sql: str = "",
        max_rows: int | None = None,
        result_offset: int | None = None,
    ) -> Envelope:
        import time

        from odps import errors as odps_errors

        # Lazy import to dodge circular dependency:
        # next_step.py -> commands/sql.py -> ... -> mc_client.client.
        from maxcompute_semantic.commands.sql_review.next_step import next_step_for_sql
        from maxcompute_semantic.mc_client.envelope import Envelope
        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        try:
            rows, schema, result_meta = self._read_instance_rows(
                instance,
                max_rows=max_rows,
                result_offset=result_offset,
            )
        except odps_errors.ODPSError as e:
            # The SQL was already submitted; derive sql from instance if available.
            instance_sql = getattr(instance, "_sql", "") or sql
            raise map_pyodps_exception(e, sql=instance_sql) from e
        data: dict[str, Any] = {
            "schema": schema,
            "rows": rows,
            "row_count": len(rows),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "logview_url": instance.get_logview_address(),
            **result_meta,
        }
        next_step = next_step_for_sql(sql) if sql else ""
        if next_step:
            data["next_step"] = next_step
        return Envelope.success(data)

    def _read_instance_rows(
        self,
        instance: Any,
        *,
        max_rows: int | None = None,
        result_offset: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
        """Read rows from a completed instance as (rows, schema).

        Returns tuple of (rows_as_dicts, column_schema, result_metadata).
        """
        effective_max_rows = _resolve_result_max_rows(max_rows)
        effective_offset = _resolve_result_offset(result_offset)
        with instance.open_reader() as reader:
            # Tunnel readers expose a public ``schema``; the REST result
            # reader pyodps falls back to (CsvRecordReader) does not — its
            # schema, when the service provides one, lives on ``_schema``.
            reader_schema = getattr(reader, "schema", None)
            tunnel_reader = reader_schema is not None
            if reader_schema is None:
                reader_schema = getattr(reader, "_schema", None)
            if not tunnel_reader:
                logger.warning(
                    "instance tunnel unavailable; pyodps fell back to the "
                    "limited REST result reader (the server may cap results, "
                    "typically at 10000 rows); column types %s",
                    "not provided by the server (reported as string)"
                    if reader_schema is None
                    else "taken from the result descriptor",
                )
            total_row_count = _reader_total_count(reader)
            read_count = effective_max_rows + 1 if effective_max_rows is not None else None
            if reader_schema is not None:
                col_names = [c.name for c in reader_schema.columns]
                schema = [
                    {"name": c.name, "type": str(c.type)} for c in reader_schema.columns
                ]
                rows: list[dict[str, Any]] = []
                has_more = False
                records = self._read_record_window(
                    reader, offset=effective_offset, count=read_count
                )
                for record in records:
                    if effective_max_rows is not None and len(rows) == effective_max_rows:
                        has_more = True
                        break
                    rows.append({col_names[i]: record[i] for i in range(len(col_names))})
            else:
                rows, schema, has_more = self._read_schemaless_rows(
                    reader,
                    offset=effective_offset,
                    count=read_count,
                    max_rows=effective_max_rows,
                )

        if (
            not has_more
            and total_row_count is not None
            and total_row_count > effective_offset + len(rows)
        ):
            has_more = True
        meta: dict[str, Any] = {
            "returned_rows": len(rows),
            "result_max_rows": effective_max_rows,
            "result_offset": effective_offset,
            "next_offset": effective_offset + len(rows) if has_more else None,
            "truncated": has_more,
            "has_more": has_more,
            "fetch_path": "instance_tunnel" if tunnel_reader else "rest_fallback",
        }
        if total_row_count is not None:
            meta["total_row_count"] = total_row_count
        return rows, schema, meta

    @staticmethod
    def _read_schemaless_rows(
        reader: Any,
        *,
        offset: int,
        count: int | None,
        max_rows: int | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], bool]:
        """Read rows from a reader that exposes no typed schema.

        pyodps returns such a reader (``CsvRecordReader``) when the instance
        tunnel is unavailable and the service provides no result descriptor:
        column names come from the CSV header row and every value is a
        string. Column metadata only exists after the header has been
        consumed, so the window is materialized first.
        """
        try:
            records = list(
                MaxComputeClient._read_record_window(reader, offset=offset, count=count)
            )
        except TypeError:
            # pyodps's CsvRecordReader raises TypeError on an empty result
            # body (its header parse iterates a None row).
            logger.debug("REST result reader produced no parseable body", exc_info=True)
            records = []
        has_more = max_rows is not None and len(records) > max_rows
        if has_more:
            records = records[:max_rows]
        columns = (
            getattr(reader, "_columns", None)
            or getattr(reader, "_csv_columns", None)
            or (getattr(records[0], "_columns", None) if records else None)
        )
        if columns:
            col_names = [str(c.name) for c in columns]
            schema = [{"name": str(c.name), "type": str(c.type)} for c in columns]
        elif records:
            col_names = [f"col_{i}" for i in range(len(records[0]))]
            schema = [{"name": name, "type": "string"} for name in col_names]
            logger.warning(
                "REST result reader exposes no column names; using positional names"
            )
        else:
            col_names = []
            schema = []
        rows = [
            {col_names[i]: record[i] for i in range(len(col_names))} for record in records
        ]
        return rows, schema, has_more

    @staticmethod
    def _read_record_window(reader: Any, *, offset: int, count: int | None) -> Any:
        """Return an iterator over a result window.

        PyODPS readers support slice-style access (``reader[start:stop]``),
        which lets instance tunnel readers fetch a result window without
        rewriting SQL. For lightweight test doubles or older compatible
        readers, fall back to client-side skipping.
        """
        if offset > 0:
            stop = None if count is None else offset + count
            try:
                return iter(reader[offset:stop])
            except (AttributeError, TypeError, ValueError, IndexError):
                pass

        iterator = iter(reader)
        for _ in range(offset):
            try:
                next(iterator)
            except StopIteration:
                return iter(())
        if count is None:
            return iterator
        return MaxComputeClient._take_records(iterator, count)

    @staticmethod
    def _take_records(iterator: Any, count: int) -> Any:
        for _ in range(count):
            try:
                yield next(iterator)
            except StopIteration:
                return

    def list_schemas(self, *, project: str | None = None) -> list[str]:
        """Return schema names in the named project.

        ``project`` defaults to the profile's ``compute_project``; pass
        a ``DataSource.project`` to enumerate schemas in a cross-project
        data source. The connection is bound to ``compute_project``;
        pyodps's ``list_schemas(project=<name>)`` routes the metadata
        query to any project the AK has Describe-level cross-project
        access to.
        """
        from odps import (  # type: ignore[import-untyped, unused-ignore]
            errors as odps_errors,
        )

        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        odps = self._ensure_odps()
        proj = project or self._profile.compute_project
        try:
            return [s.name for s in odps.list_schemas(project=proj)]
        except odps_errors.NotSupportedError:
            return ["default"]
        except odps_errors.InternalServerError as exc:
            msg = str(exc).lower()
            if "not 3-tier" in msg or "not 3 tier" in msg:
                return ["default"]
            raise map_pyodps_exception(exc, source_key=self._source_key(proj, None)) from exc
        except Exception as exc:
            raise map_pyodps_exception(exc, source_key=self._source_key(proj, None)) from exc

    @maps_pyodps_errors()
    def list_projects(self) -> list[str]:
        """Return sorted list of project names accessible by current identity."""
        odps = self._ensure_odps()
        return sorted(p.name for p in odps.list_projects())

    def list_tables(self, *, schema: str | None = None, project: str | None = None) -> list[str]:
        """Return table names in the named project, optionally filtered by schema.

        ``project`` defaults to the profile's ``compute_project``; pass
        a ``DataSource.project`` to enumerate tables in a cross-project
        data source.
        """
        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        odps = self._ensure_odps()
        proj = project or self._profile.compute_project
        try:
            return [t.name for t in odps.list_tables(project=proj, schema=schema)]
        except Exception as exc:
            raise map_pyodps_exception(exc, source_key=self._source_key(proj, schema)) from exc

    def describe_table(
        self, name: str, *, schema: str | None = None, project: str | None = None
    ) -> dict:
        """Return table metadata (columns, partition columns, comment, type).

        ``project`` defaults to the profile's ``compute_project``; pass
        a ``DataSource.project`` for cross-project metadata reads.

        Output shape is the ``data.table`` node of the mcs envelope so
        downstream consumers (profile-build, agent) get a stable schema.
        """
        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        odps = self._ensure_odps()
        proj = project or self._profile.compute_project
        try:
            table = odps.get_table(name, project=proj, schema=schema)
            # pyodps get_table is lazy — table_schema triggers the REST call.
            cols = [
                {"name": c.name, "type": str(c.type), "comment": c.comment or ""}
                for c in table.table_schema.columns
            ]
            part_cols = [
                {"name": c.name, "type": str(c.type), "comment": c.comment or ""}
                for c in table.table_schema.partitions
            ]
            return {
                "table": {
                    "name": table.name,
                    "comment": table.comment or "",
                    "type": table.type.value if hasattr(table.type, "value") else str(table.type),
                    "schema": cols,
                    "partition_columns": part_cols,
                    "description": table.comment or "",
                    "primary_key": "",
                    # Table-level last data modification timestamp. Used by
                    # the build pipeline to detect data changes (new rows on
                    # an unchanged schema) so --refresh can re-sample. For
                    # partitioned tables this is the table's own mtime, which
                    # MaxCompute updates on partition data writes.
                    "last_data_modified_time": _dt_to_iso(
                        getattr(table, "last_data_modified_time", None)
                    ),
                    "extra_metadata": {},
                }
            }
        except Exception as exc:
            raise map_pyodps_exception(exc, source_key=self._source_key(proj, schema)) from exc

    @maps_pyodps_errors(sql_arg="sql")
    def cost_estimate(
        self,
        sql: str,
        *,
        hints: dict[str, str] | None = None,
        timeout: int = 30,
        schema: str | None = None,
    ) -> dict:
        """Run cost estimation via ODPS ``execute_sql_cost``.

        Returns a dict with estimated_input_bytes, estimated_cost_cny,
        verdict (ok/confirm/blocked), and threshold configuration from
        the profile's ``cost_thresholds``.

        The caller branches on ``verdict`` instead of computing the gate
        itself — matching the ``sql-cost`` shim contract.
        """
        from maxcompute_semantic.mc_client.hints import build_hints
        from maxcompute_semantic.mc_client.tier import get_tier

        # Prime tier + init connection — ODPSError reclassified by decorator.
        if self._tier is None:
            self._tier = get_tier(self._profile, self._profile.compute_project, client=self)

        odps = self._ensure_odps()
        final_hints = build_hints(
            tier=self._tier,
            schema=schema,
            user_hints=hints,
        )
        cost = odps.execute_sql_cost(sql, hints=final_hints)

        bytes_in = cost.input_size or 0
        thresholds = self._profile.cost_thresholds
        cny_per_gb = 0.3
        bytes_per_gb = 1073741824
        cny = bytes_in / bytes_per_gb * cny_per_gb

        # Disabled sentinel (both thresholds <= 0) collapses to verdict=ok —
        # otherwise cny=0 >= 0 would always yield "blocked". Eval harness
        # relies on this short-circuit.
        if not thresholds.is_enabled():
            verdict = "ok"
        elif cny >= thresholds.blocked_cny:
            verdict = "blocked"
        elif cny >= thresholds.confirm_cny:
            verdict = "confirm"
        else:
            verdict = "ok"

        return {
            "estimated_input_bytes": bytes_in,
            "estimated_cost_cny": cny,
            "verdict": verdict,
            "thresholds": {
                "confirm_cny": thresholds.confirm_cny,
                "blocked_cny": thresholds.blocked_cny,
            },
        }

    def get_project_tier(self, project: str) -> str:
        """Return ``"2"`` or ``"3"`` for *project*; cached in-process.

        Wraps :func:`mc_client.tier.get_tier` so callers don't have to import
        it and don't pay the disk-cache read twice on consecutive lookups
        for the same project within one process.
        """
        from maxcompute_semantic.mc_client.tier import get_tier

        cached = self._project_tier_cache.get(project)
        if cached is not None:
            return cached
        tier = get_tier(self._profile, project, client=self)
        self._project_tier_cache[project] = tier
        return tier

    def execute_fq_sql(
        self,
        sql: str,
        *,
        projects: list[str],
        timeout: int = 120,
        assume_yes: bool = False,
    ) -> Envelope:
        """Run SQL with fully-qualified 3-part table refs spanning *projects*.

        Auto-injects ``odps.namespace.schema=true`` when any project in
        *projects* is 3-level. Use this instead of :meth:`execute_sql` when
        the SQL embeds ``project.schema.table`` refs whose owning project
        is not the connection's ``compute_project`` (so the per-call
        ``schema=`` kwarg doesn't fire).
        """
        needs_ns = any(self.get_project_tier(p) == "3" for p in projects)
        from maxcompute_semantic.mc_client.hints import namespace_schema_hints

        return self.execute_sql(
            sql,
            hints=namespace_schema_hints(needs_ns),
            timeout=timeout,
            assume_yes=assume_yes,
        )

    def cost_estimate_fq(
        self,
        sql: str,
        *,
        projects: list[str],
        timeout: int = 30,
    ) -> dict:
        """Cost-estimate variant of :meth:`execute_fq_sql`."""
        needs_ns = any(self.get_project_tier(p) == "3" for p in projects)
        from maxcompute_semantic.mc_client.hints import namespace_schema_hints

        return self.cost_estimate(sql, hints=namespace_schema_hints(needs_ns), timeout=timeout)

    def can_access_table(self, table: str, *, schema: str | None = None) -> dict:
        """Check whether current identity can access a table.

        Uses cost_estimate("SELECT * FROM <table> LIMIT 0") as a probe.
        If the cost estimate succeeds, the user has at least SELECT access.
        If it fails with TableNotFoundError or PermissionDeniedError, access is denied.
        """
        from maxcompute_semantic.mc_client.errors import (
            McsError,
            PermissionDeniedError,
            TableNotFoundError,
        )
        from maxcompute_semantic.mc_client.hints import build_hints
        from maxcompute_semantic.mc_client.tier import get_tier

        try:
            # Prime tier + init — ODPSError here (e.g. AccessKeyIdNotFound) must
            # classify through map_pyodps_exception.
            if self._tier is None:
                self._tier = get_tier(self._profile, self._profile.compute_project, client=self)

            hints = build_hints(
                tier=self._tier,
                schema=schema,
            )
        except Exception as exc:
            from maxcompute_semantic.mc_client.errors import map_pyodps_exception

            raise map_pyodps_exception(exc) from exc

        # Simple probe SQL works for both tiers — hints handle namespace resolution.
        probe_sql = f"SELECT * FROM {table} LIMIT 0"

        try:
            self.cost_estimate(probe_sql, hints=hints, schema=schema)
            return {
                "table": table,
                "schema": schema,
                "allowed": True,
                "check_mode": "cost_estimate",
            }
        except TableNotFoundError as e:
            return {
                "table": table,
                "schema": schema,
                "allowed": False,
                "reason": "table_not_found",
                "check_error_code": e.code,
            }
        except PermissionDeniedError as e:
            return {
                "table": table,
                "schema": schema,
                "allowed": False,
                "reason": "permission_denied",
                "check_error_code": e.code,
            }
        except McsError as e:
            return {
                "table": table,
                "schema": schema,
                "allowed": False,
                "reason": "other_error",
                "check_error_code": e.code,
            }

    # ── Catalog search (server-side FTS + client-side fallback) ──────────

    def search_tables(
        self,
        keyword: str,
        *,
        schema: str | None = None,
        page_size: int = 50,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search tables by keyword — server-side FTS with client-side fallback.

        Resolution order:
        1. Catalog API (``ODPS.catalog_rest``) — fast, server-side full-text.
        2. Client-side iteration — slow but always available. Iterates all
           tables in the project and filters by case-insensitive substring
           match on table name and comment.

        Args:
            keyword: Search term (case-insensitive substring).
            schema: Optional schema scope.
            page_size: Max results from Catalog API (max 100).

        Returns:
            Sorted list of dicts with keys: table_name, description, score,
            matched_columns (empty for catalog results).
        """
        from maxcompute_semantic.mc_client.catalog import catalog_search_tables

        odps = self._ensure_odps()
        proj = project or self._profile.compute_project

        # Try server-side first.
        catalog_results = catalog_search_tables(
            odps, proj, keyword, schema=schema, page_size=page_size
        )
        if catalog_results is not None:
            # Convert catalog shape to unified search shape.
            return sorted(
                [
                    {
                        "table_name": r["name"],
                        "description": r["comment"],
                        "score": 5,
                        "matched_columns": [],
                    }
                    for r in catalog_results
                ],
                key=lambda item: (-item["score"], item["table_name"]),
            )

        # Fallback: client-side iteration.
        return self._search_tables_client_side(keyword, schema=schema, project=proj)

    def _search_tables_client_side(
        self, keyword: str, *, schema: str | None = None, project: str | None = None
    ) -> list[dict[str, Any]]:
        """Client-side table search — iterates all tables, substring match."""
        from odps import (  # type: ignore[import-untyped, unused-ignore]
            errors as odps_errors,
        )

        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        tokens = [t.lower() for t in keyword.split() if t.strip()] or [keyword.lower()]
        odps = self._ensure_odps()
        matches: list[dict[str, Any]] = []
        skipped_column_tables: list[str] = []

        kw: dict[str, Any] = {"project": project or self._profile.compute_project}
        if schema:
            kw["schema"] = schema

        try:
            iterator = list(odps.list_tables(**kw))
        except Exception as exc:
            raise map_pyodps_exception(
                exc, source_key=self._source_key(kw["project"], schema)
            ) from exc

        for table in iterator:
            name = table.name
            comment = getattr(table, "comment", "") or ""
            searchable = f"{name} {comment}".lower()
            matched_columns: list[str] = []
            score = 0
            for token in tokens:
                if token in searchable:
                    score += 5
            if score == 0:
                # Try column names/comments too.
                try:
                    for column in table.table_schema.columns:
                        text = f"{column.name} {getattr(column, 'comment', '') or ''}".lower()
                        if any(token in text for token in tokens):
                            score += 2
                            matched_columns.append(column.name)
                except odps_errors.ODPSError:
                    skipped_column_tables.append(name)
                    logger.debug(
                        "failed to read columns for table %s during table search",
                        name,
                        exc_info=True,
                    )
            if score:
                matches.append(
                    {
                        "table_name": name,
                        "description": comment,
                        "score": score,
                        "matched_columns": matched_columns,
                    }
                )

        if skipped_column_tables:
            logger.debug(
                "search_tables skipped %d table(s) with unreadable columns: %s",
                len(skipped_column_tables),
                ", ".join(skipped_column_tables),
            )

        return sorted(matches, key=lambda item: (-item["score"], item["table_name"]))

    def search_columns(
        self,
        keyword: str,
        *,
        schema: str | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search columns across all tables by keyword — client-side only.

        Iterates all tables and their columns, scoring matches by
        column name, comment, and table+column context.

        Args:
            keyword: Search term (case-insensitive, space-separated tokens).
            schema: Optional schema scope.

        Returns:
            Sorted list of dicts with keys: table_name, column_name, type,
            comment, score.
        """
        from odps import (  # type: ignore[import-untyped, unused-ignore]
            errors as odps_errors,
        )

        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        tokens = [t.lower() for t in keyword.split() if t.strip()] or [keyword.lower()]
        odps = self._ensure_odps()
        matches: list[dict[str, Any]] = []
        skipped_schema_tables: list[str] = []

        kw: dict[str, Any] = {"project": project or self._profile.compute_project}
        if schema:
            kw["schema"] = schema

        try:
            iterator = list(odps.list_tables(**kw))
        except Exception as exc:
            raise map_pyodps_exception(
                exc, source_key=self._source_key(kw["project"], schema)
            ) from exc

        for table in iterator:
            try:
                columns = table.table_schema.columns
            except odps_errors.ODPSError:
                skipped_schema_tables.append(table.name)
                logger.debug(
                    "failed to read schema for table %s during column search",
                    table.name,
                    exc_info=True,
                )
                continue
            for column in columns:
                score = 0
                col_name = column.name
                col_comment = getattr(column, "comment", "") or ""
                col_type = str(column.type)
                text = f"{col_name} {col_comment}".lower()
                searchable = f"{table.name} {text}".lower()
                for token in tokens:
                    if token in col_name.lower():
                        score += 8
                    if token in text:
                        score += 4
                    if token in searchable:
                        score += 2
                if score:
                    matches.append(
                        {
                            "table_name": table.name,
                            "column_name": col_name,
                            "type": col_type,
                            "comment": col_comment,
                            "score": score,
                        }
                    )

        if skipped_schema_tables:
            logger.debug(
                "search_columns skipped %d table(s) with unreadable schema: %s",
                len(skipped_schema_tables),
                ", ".join(skipped_schema_tables),
            )

        return sorted(
            matches,
            key=lambda item: (-item["score"], item["table_name"], item["column_name"]),
        )

    # ── Additional meta methods ──────────────────────────────────────────

    def list_partitions(
        self,
        table_name: str,
        *,
        schema: str | None = None,
        limit: int = 100,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List partition specs for a table with latest-partition info.

        Args:
            table_name: Table name.
            schema: Optional schema scope.
            limit: Maximum partitions to return (default 100).

        Returns:
            Dict with table_name, partitions (list of spec strings),
            visible_count, has_more, limit, latest_partition,
            is_partitioned.
        """
        from itertools import islice

        odps = self._ensure_odps()
        kw: dict[str, Any] = {"project": project or self._profile.compute_project}
        if schema:
            kw["schema"] = schema
        table = odps.get_table(table_name, **kw)
        part_cols = table.table_schema.partitions
        is_partitioned = bool(part_cols)

        if not is_partitioned:
            return {
                "table_name": table_name,
                "partitions": [],
                "visible_count": 0,
                "has_more": False,
                "limit": limit,
                "latest_partition": None,
                "is_partitioned": False,
            }

        window = list(islice(table.iterate_partitions(), limit + 1))
        has_more = len(window) > limit
        partitions = [str(p.partition_spec) for p in window[:limit]]

        # Try get_max_partition for latest partition.
        latest_partition: str | None = None
        getter = getattr(table, "get_max_partition", None)
        if callable(getter):
            for kwargs in ({"skip_empty": True}, {}):
                try:
                    p = getter(**kwargs)
                    if p is not None:
                        latest_partition = str(p.partition_spec)
                        break
                except TypeError:
                    continue
                except Exception:  # best-effort probe; fall back below
                    logger.debug("get_max_partition probe failed; falling back", exc_info=True)
        if latest_partition is None and partitions:
            latest_partition = partitions[-1]

        return {
            "table_name": table_name,
            "partitions": partitions,
            "visible_count": len(partitions),
            "has_more": has_more,
            "limit": limit,
            "latest_partition": latest_partition,
            "is_partitioned": True,
        }

    def freshness_info(
        self,
        table_name: str,
        *,
        schema: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get data freshness info for a table.

        Derives freshness from the latest partition's modification time.
        Not a native ODPS API — the value is approximate.

        Args:
            table_name: Table name.
            schema: Optional schema scope.

        Returns:
            Dict with table_name, is_partitioned, latest_partition,
            last_modified_time, freshness_summary, stale_warning.
        """
        odps = self._ensure_odps()
        kw: dict[str, Any] = {"project": project or self._profile.compute_project}
        if schema:
            kw["schema"] = schema
        try:
            table = odps.get_table(table_name, **kw)
            is_partitioned = bool(table.table_schema.partitions)
        except Exception as exc:
            from maxcompute_semantic.mc_client.errors import map_pyodps_exception

            raise map_pyodps_exception(exc, source_key=self._source_key(project, schema)) from exc

        if not is_partitioned:
            last_modified = getattr(table, "last_data_modified_time", None)
            return {
                "table_name": table_name,
                "is_partitioned": False,
                "latest_partition": None,
                "last_modified_time": _dt_to_iso(last_modified),
                "freshness_summary": (
                    f"Non-partitioned table; last data modification: {_dt_to_iso(last_modified)}"
                    if last_modified
                    else "Non-partitioned table; modification time unavailable"
                ),
                "stale_warning": None,
            }

        # Find latest partition.
        latest_partition: str | None = None
        getter = getattr(table, "get_max_partition", None)
        if callable(getter):
            for kwargs in ({"skip_empty": True}, {}):
                try:
                    p = getter(**kwargs)
                    if p is not None:
                        latest_partition = str(p.partition_spec)
                        break
                except TypeError:
                    continue
                except Exception:  # best-effort probe; fall back below
                    logger.debug("get_max_partition probe failed; falling back", exc_info=True)

        # If no get_max_partition, use last partition from iteration.
        if latest_partition is None:
            from itertools import islice

            parts = list(islice(table.iterate_partitions(), 200))
            if parts:
                latest_partition = str(parts[-1].partition_spec)

        last_modified = getattr(table, "last_data_modified_time", None)

        freshness_summary = "Latest partition data unavailable"
        stale_warning: str | None = None
        if last_modified:
            from datetime import datetime as dt

            now = dt.now(timezone.utc)
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            age_hours = (now - last_modified).total_seconds() / 3600
            if age_hours < 1:
                freshness_summary = (
                    f"Data updated within the last hour (partition: {latest_partition})"
                )
            elif age_hours < 24:
                freshness_summary = (
                    f"Data updated {age_hours:.1f} hours ago (partition: {latest_partition})"
                )
            else:
                age_days = age_hours / 24
                freshness_summary = (
                    f"Data updated {age_days:.1f} days ago (partition: {latest_partition})"
                )
                if age_days > 7:
                    stale_warning = f"Table data is {age_days:.0f} days old; may be stale"

        return {
            "table_name": table_name,
            "is_partitioned": True,
            "latest_partition": latest_partition,
            "last_modified_time": _dt_to_iso(last_modified),
            "freshness_summary": freshness_summary,
            "stale_warning": stale_warning,
        }

    def sample_table(
        self,
        table: str,
        *,
        schema: str | None = None,
        limit: int = 20,
        partition: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Sample rows from a table via pyodps read_table.

        For partitioned tables without an explicit partition, auto-detects
        the latest partition via list_partitions().

        Returns dict with: table_name, rows (list of dicts), row_count,
        partition_used (str or None).
        """
        odps = self._ensure_odps()
        proj = project or self._profile.compute_project

        # Resolve partition if table is partitioned and no partition given.
        partition_used: str | None = partition
        if partition is None:
            part_info = self.list_partitions(table, schema=schema, limit=100, project=proj)
            if part_info.get("is_partitioned") and part_info.get("latest_partition"):
                partition_used = part_info["latest_partition"]

        kw: dict[str, Any] = {"project": proj}
        if schema:
            kw["schema"] = schema

        table_obj = odps.get_table(table, **kw)

        # Build read_table options
        read_kw: dict[str, Any] = {"limit": limit}
        if partition_used:
            read_kw["partition"] = partition_used

        rows: list[dict[str, Any]] = []
        col_names: list[str] = [c.name for c in table_obj.table_schema.columns]

        with table_obj.open_reader(**read_kw) as reader:
            for record in reader:
                rows.append({col_names[i]: record[i] for i in range(len(col_names))})

        return {
            "table_name": table,
            "rows": rows,
            "row_count": len(rows),
            "partition_used": partition_used,
        }

    def profile_table(
        self,
        table: str,
        *,
        schema: str | None = None,
        limit: int = 20,
        partition: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Profile a table: sample rows and compute per-column statistics.

        Calls sample_table() internally, then computes null_count,
        distinct_count, min/max for each column.

        Returns dict with: table_name, columns (list of stats dicts),
        row_count, partition_used.
        """
        sample = self.sample_table(
            table, schema=schema, limit=limit, partition=partition, project=project
        )
        rows = sample["rows"]

        columns_stats: list[dict[str, Any]] = []
        if rows:
            for col_name in rows[0]:
                values = [row[col_name] for row in rows]
                null_count = sum(1 for v in values if v is None)
                non_null = [v for v in values if v is not None]
                distinct_count = len({str(v) for v in non_null})

                col_stat: dict[str, Any] = {
                    "column_name": col_name,
                    "null_count": null_count,
                    "distinct_count": distinct_count,
                    "sample_size": len(values),
                }

                # Try min/max for numeric-ish values
                try:
                    numeric = [float(v) for v in non_null]
                    col_stat["min"] = min(numeric)
                    col_stat["max"] = max(numeric)
                except (ValueError, TypeError):
                    # For string values, try alphabetical min/max
                    str_vals = [str(v) for v in non_null]
                    if str_vals:
                        col_stat["min"] = min(str_vals)
                        col_stat["max"] = max(str_vals)
                    else:
                        col_stat["min"] = None
                        col_stat["max"] = None

                columns_stats.append(col_stat)

        return {
            "table_name": table,
            "columns": columns_stats,
            "row_count": len(rows),
            "partition_used": sample["partition_used"],
        }

    # ── Async job lifecycle methods ──────────────────────────────────────

    @maps_pyodps_errors(sql_arg="sql")
    def run_sql_async(
        self,
        sql: str,
        *,
        hints: dict[str, str] | None = None,
        schema: str | None = None,
        assume_yes: bool = False,
        allow_write: bool = False,
    ) -> str:
        """Submit SQL asynchronously — return instance ID without waiting.

        This is the primitive for `job submit`.
        """
        import sys

        from maxcompute_semantic.mc_client.hints import build_hints
        from maxcompute_semantic.mc_client.tier import get_tier

        ensure_sql_write_allowed(sql, allow_write=allow_write)

        # Prime tier + init — ODPSError reclassified by decorator.
        if self._tier is None:
            self._tier = get_tier(self._profile, self._profile.compute_project, client=self)

        enforce_cost_gate(
            self,
            sql,
            hints=hints,
            schema=schema,
            assume_yes=assume_yes,
            is_tty=sys.stdin.isatty(),
        )

        odps = self._ensure_odps()
        final_hints = build_hints(
            tier=self._tier,
            schema=schema,
            user_hints=hints,
        )

        instance = odps.run_sql(sql, hints=final_hints)
        instance_id = str(instance.id)
        logger.info("submitted async instance %s", instance_id)
        return instance_id

    @maps_pyodps_errors()
    def get_instance_status(self, instance_id: str) -> dict[str, Any]:
        """Get status of a running/completed instance.

        Returns dict: instance_id, status, status_name, lifecycle_state,
        terminal, successful, task_statuses, start_time, end_time, name,
        logview_url.
        """
        odps = self._ensure_odps()
        instance = odps.get_instance(instance_id)
        instance.reload()
        return _instance_status_payload(instance_id, instance)

    @maps_pyodps_errors()
    def wait_for_instance(
        self,
        instance_id: str,
        *,
        timeout: int = 600,
        interval: int = 3,
    ) -> dict[str, Any]:
        """Wait for instance to complete, polling every interval seconds.

        Raises TimeoutError if not completed within timeout.
        Returns final status dict.
        """
        import time

        from maxcompute_semantic.mc_client.errors import McsError
        from maxcompute_semantic.mc_client.errors import TimeoutError as McsTimeoutError

        if timeout <= 0:
            raise McsError(
                "wait timeout must be greater than 0 seconds",
                code="InvalidArgument",
                exit_code=2,
                remediation="pass --timeout with a positive number of seconds",
                timeout=timeout,
            )
        if interval <= 0:
            raise McsError(
                "wait interval must be greater than 0 seconds",
                code="InvalidArgument",
                exit_code=2,
                remediation="pass --interval with a positive number of seconds",
                interval=interval,
            )

        odps = self._ensure_odps()
        instance = odps.get_instance(instance_id)

        started = time.monotonic()
        while True:
            instance.reload()
            status = str(instance.status)
            status_name = _normalized_status_name(instance.status)
            if _is_terminal_instance_status(status_name):
                break
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise McsTimeoutError(
                    f"Instance {instance_id} did not complete within {timeout}s (status: {status})",
                    remediation="raise --timeout or cancel the job",
                )
            time.sleep(interval)

        return _instance_status_payload(instance_id, instance)

    @maps_pyodps_errors()
    def get_instance_result(
        self,
        instance_id: str,
        *,
        max_rows: int | None = None,
        result_offset: int | None = None,
    ) -> dict[str, Any]:
        """Read result rows from a completed instance.

        Returns dict: instance_id, schema, rows, row_count.
        """
        odps = self._ensure_odps()
        instance = odps.get_instance(instance_id)
        rows, schema, result_meta = self._read_instance_rows(
            instance,
            max_rows=max_rows,
            result_offset=result_offset,
        )

        return {
            "instance_id": instance_id,
            "schema": schema,
            "rows": rows,
            "row_count": len(rows),
            **result_meta,
        }

    @maps_pyodps_errors()
    def cancel_instance(self, instance_id: str) -> dict[str, Any]:
        """Cancel a running instance.

        Returns dict: instance_id, status (actual terminal state).
        """
        odps = self._ensure_odps()
        instance = odps.get_instance(instance_id)
        instance.stop()
        instance.reload()
        status = str(instance.status).rsplit(".", 1)[-1]

        return {
            "instance_id": instance_id,
            "status": status,
            "cancelled": status.upper() in _INSTANCE_CANCELLED_STATUS_NAMES,
        }

    @maps_pyodps_errors()
    def list_recent_instances(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """List recent instances.

        Returns list of dicts: id, status, start_time, name.
        """
        odps = self._ensure_odps()
        instances = list(odps.list_instances(limit=limit))

        result = []
        for inst in instances:
            start_time = getattr(inst, "start_time", None)
            status_name = _normalized_status_name(inst.status)
            result.append(
                {
                    "id": inst.id,
                    "status": str(inst.status),
                    "status_name": status_name,
                    "terminal": _is_terminal_instance_status(status_name),
                    "start_time": _dt_to_iso(start_time) if start_time else None,
                    "name": getattr(inst, "name", "") or "",
                }
            )
        return result

    def list_functions(self) -> list[dict[str, str | None]]:
        """Return UDF metadata from the profile compute project.

        PyODPS exposes function catalog entries as ``Function`` objects.
        The list endpoint does not provide a stable argument signature, so
        mcs stores the fields the local package schema can represent:
        function name, broad kind, implementation class, and optional
        description placeholder.
        """
        from maxcompute_semantic.mc_client.errors import map_pyodps_exception

        odps = self._ensure_odps()
        project = self._profile.compute_project
        try:
            functions = list(odps.list_functions(project=project))
        except Exception as exc:
            raise map_pyodps_exception(exc, source_key=self._source_key(project, None)) from exc

        discovered: list[dict[str, str | None]] = []
        for function in functions:
            name = _safe_function_attr(function, "name")
            if not name:
                continue
            class_name = _safe_function_attr(function, "class_type")
            discovered.append(
                {
                    "name": name,
                    "kind": _function_kind(function, class_name=class_name),
                    "signature": None,
                    "class_name": class_name,
                    "description": None,
                }
            )
        return discovered

    @maps_pyodps_errors(sql_arg="sql")
    def explain(
        self,
        sql: str,
        *,
        hints: dict[str, str] | None = None,
        timeout: int = 120,
        schema: str | None = None,
    ) -> dict:
        """Get the execution plan for a SQL statement via EXPLAIN.

        Runs `EXPLAIN <sql>` through pyodps, waits for completion, and
        reads the plan text from instance task results.

        Returns dict with keys: plan (str), logview_url (str), elapsed_ms (int).
        """
        import time

        from odps import (  # type: ignore[import-untyped, unused-ignore]
            errors as odps_errors,
        )

        from maxcompute_semantic.errors import TimeoutError as McsTimeoutError
        from maxcompute_semantic.mc_client.hints import build_hints
        from maxcompute_semantic.mc_client.tier import get_tier

        explain_sql = f"EXPLAIN {sql}"
        started = time.monotonic()

        # Prime tier + init — ODPSError reclassified by decorator.
        if self._tier is None:
            self._tier = get_tier(self._profile, self._profile.compute_project, client=self)

        odps = self._ensure_odps()
        final_hints = build_hints(
            tier=self._tier,
            schema=schema,
            user_hints=hints,
        )
        instance = odps.run_sql(explain_sql, hints=final_hints)
        try:
            instance.wait_for_success(interval=2, timeout=timeout)
        except TimeoutError as e:
            raise McsTimeoutError(
                f"EXPLAIN execution exceeded {timeout}s",
                remediation="raise --timeout or simplify the SQL",
                sql=sql,
            ) from e

        # Read plan text from instance task results.
        plan_text = ""
        try:
            results = instance.get_task_results()
            if results:
                # get_task_results returns a dict of task_name -> result_text
                for result in results.values():
                    plan_text += result
        except odps_errors.ODPSError:
            logger.debug("get_task_results failed, falling back to open_reader", exc_info=True)
            # If get_task_results fails, try open_reader
            try:
                with instance.open_reader() as reader:
                    plan_text = reader.read()
            except odps_errors.ODPSError:
                logger.debug("open_reader fallback also failed", exc_info=True)
                plan_text = "(plan text unavailable)"

        elapsed_ms = int((time.monotonic() - started) * 1000)

        return {
            "plan": plan_text,
            "logview_url": instance.get_logview_address(),
            "elapsed_ms": elapsed_ms,
        }


def _safe_function_attr(function: Any, attr: str) -> str | None:
    """Read a pyodps Function attribute without forcing a hard failure.

    Some pyodps XML descriptors raise while trying to lazy-load optional
    fields from a detached mock/object. UDF discovery should still keep
    the catalog entry when the function name is present.
    """
    try:
        value = getattr(function, attr)
    except Exception:  # noqa: BLE001 — pyodps lazy-load may raise; see docstring
        return None
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _function_kind(function: Any, *, class_name: str | None) -> str:
    """Map pyodps Function flags to the broad UDF kind stored by mcs."""
    if _truthy_function_attr(function, "is_sql_function"):
        return "sql"

    language = _safe_function_attr(function, "program_language")
    if language:
        return language.lower()

    if _truthy_function_attr(function, "is_embedded_function"):
        return "embedded"

    if class_name and "." in class_name:
        return "java"
    return "udf"


def _truthy_function_attr(function: Any, attr: str) -> bool:
    value = _safe_function_attr(function, attr)
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def _normalized_status_name(status: Any) -> str:
    """Return a stable uppercase status name for enum values and strings."""
    enum_name = getattr(status, "name", None)
    if isinstance(enum_name, str) and enum_name:
        raw = enum_name
    else:
        enum_value = getattr(status, "value", None)
        raw = str(enum_value if enum_value is not None else status)
        raw = raw.rsplit(".", 1)[-1]
    return raw.strip().replace("-", "_").replace(" ", "_").upper()


def _is_terminal_instance_status(status_name: str) -> bool:
    return status_name in _INSTANCE_TERMINAL_STATUS_NAMES


def _task_status_payloads(instance: Any) -> list[dict[str, Any]]:
    get_task_statuses = getattr(instance, "get_task_statuses", None)
    if not callable(get_task_statuses):
        return []
    statuses = get_task_statuses()
    if not isinstance(statuses, dict):
        return []

    payloads: list[dict[str, Any]] = []
    for task_name, task in statuses.items():
        status = getattr(task, "status", None)
        start_time = getattr(task, "start_time", None)
        end_time = getattr(task, "end_time", None)
        payloads.append(
            {
                "name": str(task_name),
                "type": getattr(task, "type", "") or "",
                "status": str(status),
                "status_name": _normalized_status_name(status),
                "start_time": _dt_to_iso(start_time) if start_time else None,
                "end_time": _dt_to_iso(end_time) if end_time else None,
            }
        )
    return payloads


def _lifecycle_state(instance_status_name: str, task_statuses: list[dict[str, Any]]) -> str:
    task_names = {str(task["status_name"]) for task in task_statuses}
    if task_names & _TASK_CANCELLED_STATUS_NAMES:
        return "cancelled"
    if task_names & _TASK_FAILED_STATUS_NAMES:
        return "failed"
    if task_names and task_names <= _TASK_SUCCESS_STATUS_NAMES:
        return "success"

    if instance_status_name in _TASK_CANCELLED_STATUS_NAMES:
        return "cancelled"
    if instance_status_name in _TASK_FAILED_STATUS_NAMES:
        return "failed"
    if instance_status_name in _TASK_SUCCESS_STATUS_NAMES:
        return "success"
    if instance_status_name in _SUSPENDED_STATUS_NAMES or task_names & _SUSPENDED_STATUS_NAMES:
        return "suspended"
    if instance_status_name == "TERMINATED":
        return "terminated"
    if instance_status_name in {"RUNNING", "WAITING", "PENDING", "QUEUED"}:
        return "running"
    if task_names & {"RUNNING", "WAITING", "PENDING", "QUEUED"}:
        return "running"
    return "unknown"


def _successful_for_lifecycle(lifecycle_state: str) -> bool | None:
    if lifecycle_state == "success":
        return True
    if lifecycle_state in {"failed", "cancelled"}:
        return False
    return None


def _instance_status_payload(instance_id: str, instance: Any) -> dict[str, Any]:
    status = getattr(instance, "status", None)
    status_name = _normalized_status_name(status)
    task_statuses = _task_status_payloads(instance)
    lifecycle_state = _lifecycle_state(status_name, task_statuses)
    start_time = getattr(instance, "start_time", None)
    end_time = getattr(instance, "end_time", None)

    return {
        "instance_id": instance_id,
        "status": str(status),
        "status_name": status_name,
        "lifecycle_state": lifecycle_state,
        "terminal": _is_terminal_instance_status(status_name)
        or lifecycle_state in {"success", "failed", "cancelled"},
        "successful": _successful_for_lifecycle(lifecycle_state),
        "task_statuses": task_statuses,
        "start_time": _dt_to_iso(start_time) if start_time else None,
        "end_time": _dt_to_iso(end_time) if end_time else None,
        "name": getattr(instance, "name", "") or "",
        "logview_url": instance.get_logview_address(),
    }


def _dt_to_iso(dt: datetime | None) -> str | None:
    """Convert datetime to ISO format string; return None if dt is None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
