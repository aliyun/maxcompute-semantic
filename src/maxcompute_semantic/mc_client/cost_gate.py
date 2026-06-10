# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Cost-threshold gate for ``MaxComputeClient.execute_sql``.

Centralized so every code path that calls ``execute_sql`` gets the
same ``confirm`` / ``blocked`` verdict semantics. The gate is a
no-op when the profile's ``cost_thresholds.is_enabled()`` is False
(the eval harness sets both thresholds to 0 to disable).

The gate runs the cost estimate up-front, so a SQL that exceeds the
threshold is *rejected before submission* and never bills against
the project. Blocked verdicts always raise; confirm verdicts either
prompt (TTY) or raise (non-TTY) unless the caller passed
``assume_yes=True``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import click
import sqlglot

from maxcompute_semantic.errors.auth import AuthFailedError, IdentityNotAuthorizedError
from maxcompute_semantic.mc_client.errors import (
    CostBlockedError,
    CostConfirmRequiredError,
    PermissionDeniedError,
    ProjectNotFoundError,
    SchemaNotFoundError,
    SyntaxErrorMcs,
    TableNotFoundError,
)
from maxcompute_semantic.mc_client.sql_guard import classify_sql

logger = logging.getLogger("maxcompute_semantic")


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").lower().split())


def _parse_single_statement(sql: str) -> sqlglot.exp.Expression | None:
    from maxcompute_semantic.dialect import parse_mc

    try:
        statements = parse_mc(sql, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception:
        return None
    if len(statements) != 1 or not isinstance(statements[0], sqlglot.exp.Expression):
        return None
    return statements[0]


def _has_top_level_limit_zero(statement: sqlglot.exp.Expression) -> bool:
    limit = statement.args.get("limit")
    if not isinstance(limit, sqlglot.exp.Limit):
        return False

    expression = limit.args.get("expression")
    if not isinstance(expression, sqlglot.exp.Literal):
        return False

    return expression.to_py() == 0


def _is_information_schema_table(table: sqlglot.exp.Table) -> bool:
    return (table.db or "").lower() == "information_schema"


def _all_table_refs_are_information_schema(statement: sqlglot.exp.Expression) -> bool:
    tables = list(statement.find_all(sqlglot.exp.Table))
    if not tables:
        return False
    return all(_is_information_schema_table(table) for table in tables)


def _is_low_risk_uncosted_read(sql: str) -> bool:
    """Allow only metadata/probe reads to run when COST cannot plan them."""
    statement = _parse_single_statement(sql)
    if statement is None:
        return False

    normalized = _normalized_sql(sql)
    if normalized == "select 1":
        return True

    classification = classify_sql(sql)
    if classification != "read":
        return False

    if _has_top_level_limit_zero(statement):
        return True

    return _all_table_refs_are_information_schema(statement)


_COST_ESTIMATE_PASSTHROUGH_ERRORS = (
    AuthFailedError,
    IdentityNotAuthorizedError,
    PermissionDeniedError,
    ProjectNotFoundError,
    SchemaNotFoundError,
    SyntaxErrorMcs,
    TableNotFoundError,
)


if TYPE_CHECKING:
    from maxcompute_semantic.mc_client.client import MaxComputeClient


def enforce_cost_gate(
    client: MaxComputeClient,
    sql: str,
    *,
    hints: dict[str, str] | None = None,
    schema: str | None = None,
    assume_yes: bool,
    is_tty: bool,
) -> None:
    """Run the cost estimate and enforce the profile's thresholds.

    Returns ``None`` when the SQL is cleared to execute. Raises:

      - :class:`CostBlockedError` when the verdict is ``blocked`` — always,
        even if ``assume_yes=True`` (the ceiling is a hard refusal).
      - :class:`CostConfirmRequiredError` when the verdict is ``confirm``
        AND ``assume_yes`` is False AND (the context is non-TTY OR the
        TTY user answered No to the click.confirm prompt).

    A profile with ``cost_thresholds.is_enabled() is False`` short-circuits:
    the cost estimate is *not* even called. This is the eval-harness path
    (``confirm_cny=0``, ``blocked_cny=0``) and ensures the agent's
    per-case run never pays for an extra cost-estimate round-trip.
    """
    thresholds = client.profile.cost_thresholds
    if not thresholds.is_enabled():
        return

    try:
        cost = client.cost_estimate(sql, hints=hints, schema=schema)
    except Exception as exc:
        if isinstance(exc, _COST_ESTIMATE_PASSTHROUGH_ERRORS):
            raise
        # COST can reject small metadata/probe SQL shapes that are safe
        # to run without a scan estimate. Everything else fails closed.
        if _is_low_risk_uncosted_read(sql):
            logger.debug(
                "cost estimation failed for low-risk probe SQL; proceeding without cost guard",
                exc_info=True,
            )
            return
        raise CostBlockedError(
            "cost estimation failed and SQL is not an allowed low-risk probe; "
            "refusing to execute without a cost estimate",
            remediation=(
                "run `mcs sql cost` again after simplifying the query, rewrite it "
                "as `SELECT 1`, an INFORMATION_SCHEMA metadata probe, or a LIMIT 0 "
                "schema probe, or set both profile cost thresholds to 0 only for a "
                "trusted workflow that intentionally disables the gate"
            ),
            sql=sql,
        ) from exc

    verdict = cost["verdict"]
    if verdict == "ok":
        return

    cny = cost["estimated_cost_cny"]

    if verdict == "blocked":
        raise CostBlockedError(
            f"estimated cost {cny:.2f} CNY exceeds blocked_cny "
            f"({thresholds.blocked_cny:.2f} CNY); SQL was not submitted",
            remediation=(
                "raise the profile's cost_thresholds.blocked_cny, "
                "set both thresholds to 0 to disable the gate, or "
                "rewrite the SQL to scan less data (add a WHERE on "
                "the partition column, a LIMIT, or a smaller projection)"
            ),
            estimated_cost_cny=cny,
            blocked_cny=thresholds.blocked_cny,
            sql=sql,
        )

    # verdict == "confirm" from here on.
    if assume_yes:
        return

    if is_tty:
        prompt = (
            f"Estimated cost {cny:.2f} CNY exceeds confirm_cny "
            f"({thresholds.confirm_cny:.2f} CNY). Proceed?"
        )
        if click.confirm(prompt, default=False):
            return
        raise CostConfirmRequiredError(
            f"user declined cost-confirm prompt for SQL costing {cny:.2f} CNY",
            remediation="rewrite the SQL to scan less data, or pass --yes",
            estimated_cost_cny=cny,
            confirm_cny=thresholds.confirm_cny,
            sql=sql,
        )

    raise CostConfirmRequiredError(
        f"estimated cost {cny:.2f} CNY exceeds confirm_cny "
        f"({thresholds.confirm_cny:.2f} CNY) and no TTY is available "
        "for an interactive confirm",
        remediation=(
            "pass --yes to confirm in non-interactive mode, raise the "
            "profile's cost_thresholds.confirm_cny, set both thresholds "
            "to 0 to disable the gate, or rewrite the SQL to scan less data"
        ),
        estimated_cost_cny=cny,
        confirm_cny=thresholds.confirm_cny,
        sql=sql,
    )
