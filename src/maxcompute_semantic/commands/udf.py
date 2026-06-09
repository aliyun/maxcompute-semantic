# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""mcs udf subcommand group — manage MaxCompute UDFs (list, show, search,
create, test, remove) and resources (list, show, remove).

UDF commands need both a MaxComputeClient (for MC API operations like
drop_function, list_resources) and a PackageDB (for local tracking of
UDF metadata discovered during `mcs build`). The profile resolution is
handled by the ``@profile_command`` decorator; verbs create the client
and db from the resolved ``ProfileContext``.

Output: plain (human-readable tables/lines) or json (envelope).
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import click

from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.errors import McsError, map_pyodps_exception
from maxcompute_semantic.versioning import (
    ACTION_UDF_PREFIX,
)

# ── identifier validation ──────────────────────────────────────────────────

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
_NUMERIC_LITERAL_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")


def _validate_identifier(name: str, label: str = "name") -> None:
    """Reject names that are not valid SQL identifiers (or dot-qualified chains).

    MaxCompute UDFs can be schema-qualified (``schema.func_name``), so
    dotted segments are allowed as long as each segment is a valid
    ``[a-zA-Z_][a-zA-Z0-9_]*`` identifier.
    """
    if not _IDENTIFIER_RE.match(name):
        raise click.BadParameter(
            f"invalid SQL identifier: {name!r} — must match [a-zA-Z_][a-zA-Z0-9_]*",
            param_hint=label,
        )


# ── helpers ────────────────────────────────────────────────────────────────


def _sql_string_literal(value: str) -> str:
    """Return a single-quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _validate_single_quoted_literal(value: str) -> str:
    """Return a SQL single-quoted string literal if it is safely quoted."""
    inner = value[1:-1]
    i = 0
    while i < len(inner):
        if inner[i] != "'":
            i += 1
            continue
        if i + 1 < len(inner) and inner[i + 1] == "'":
            i += 2
            continue
        raise click.BadParameter(
            "single-quoted UDF test string literals must escape apostrophes by doubling them",
            param_hint="--args",
        )
    return value


def _format_args_for_sql(args_str: str) -> str:
    """Convert comma-separated args string to SQL literal list.

    Only SQL literal values are accepted. Numbers stay as-is; strings
    get single-quoted. Examples:
      '1, "abc"' → '1, \'abc\''
      '42' → '42'
      '"hello", "world"' → '\'hello\', \'world\''
    """
    if not args_str.strip():
        raise click.BadParameter("empty UDF test argument", param_hint="--args")
    parts = _split_args(args_str)
    formatted = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            raise click.BadParameter("empty UDF test argument", param_hint="--args")
        if stripped.startswith("'") and stripped.endswith("'"):
            formatted.append(_validate_single_quoted_literal(stripped))
            continue
        if stripped.startswith('"') and stripped.endswith('"'):
            inner = stripped[1:-1]
            formatted.append(_sql_string_literal(inner))
            continue
        if _NUMERIC_LITERAL_RE.match(stripped):
            formatted.append(stripped)
            continue
        if stripped.upper() == "NULL":
            formatted.append("NULL")
            continue
        if stripped.upper() in {"TRUE", "FALSE"}:
            formatted.append(stripped.upper())
            continue
        raise click.BadParameter(
            (
                f"unsupported UDF test argument {stripped!r}; "
                "only numeric, NULL, TRUE/FALSE, and quoted string literals are allowed"
            ),
            param_hint="--args",
        )
    return ", ".join(formatted)


def _split_args(args_str: str) -> list[str]:
    """Split comma-separated args, respecting quoted strings."""
    parts: list[str] = []
    current = ""
    in_single = False
    in_double = False
    for ch in args_str:
        if ch == "'" and not in_double:
            in_single = not in_single
            current += ch
        elif ch == '"' and not in_single:
            in_double = not in_double
            current += ch
        elif ch == "," and not in_single and not in_double:
            parts.append(current)
            current = ""
        else:
            current += ch
    if in_single or in_double:
        raise click.BadParameter("unterminated quoted string in UDF test args", param_hint="--args")
    parts.append(current)
    return parts


def _extract_class_name(script_content: str, script_path: str) -> str:
    """Extract the UDF class name from a Python script.

    Looks for a class definition inheriting from a typical UDF base.
    Falls back to deriving from the script filename.
    """
    # Look for: class MyUDF(BaseUDF): or class MyUDF(UDF):
    match = re.search(r"class\s+(\w+)\s*\(", script_content)
    if match:
        return match.group(1)
    # Fallback: derive from script filename stem.
    return Path(script_path).stem


# ── udf group ─────────────────────────────────────────────────────────────


@click.group(name="udf")
def udf_group() -> None:
    """Manage MaxCompute UDFs: list, show, search, create, test, remove."""


# ── udf list ──────────────────────────────────────────────────────────────


@profile_command(udf_group, "list", accepts_schema=False)
def list_cmd(pctx: ProfileContext) -> None:
    """List all UDFs tracked in the current profile's PackageDB."""
    db = pctx.open_db()
    udfs = db.list_udfs()
    db.close()

    if not udfs:
        pctx.renderer.success({"count": 0, "udfs": []})
        return

    headers = ["name", "kind", "signature"]
    rows = [[u["name"], u["kind"], u.get("signature", "") or ""] for u in udfs]

    if pctx.renderer.is_envelope:
        pctx.renderer.success({"count": len(udfs), "udfs": udfs})
    else:
        pctx.renderer.table(headers, rows)


# ── udf show ──────────────────────────────────────────────────────────────


@profile_command(udf_group, "show", accepts_schema=False)
@click.argument("name")
def show_cmd(pctx: ProfileContext, name: str) -> None:
    """Show details of a single UDF from PackageDB."""
    prof = pctx.profile
    db = pctx.open_db()

    # Look up in PackageDB.
    udfs = db.list_udfs()
    db.close()

    udf_entry = None
    for u in udfs:
        if u["name"] == name:
            udf_entry = u
            break

    if udf_entry is None:
        raise McsError(
            f"UDF '{name}' not found in local PackageDB",
            remediation="run `mcs build` to discover UDFs, or check the name",
            exit_code=5,
        )

    # Try to enrich from pyodps if available.
    enriched = dict(udf_entry)
    try:
        client = MaxComputeClient(prof)
        odps = client._ensure_odps()
        mc_func = odps.get_function(name, project=prof.compute_project)
        enriched["owner"] = getattr(mc_func, "owner", "")
        enriched["creation_time"] = str(getattr(mc_func, "creation_time", ""))
    except Exception:
        # Enrichment is optional — local data is sufficient.
        pass

    pctx.renderer.success({"udf": enriched})


# ── udf search ────────────────────────────────────────────────────────────


@profile_command(udf_group, "search", accepts_schema=False)
@click.argument("keyword")
def search_cmd(pctx: ProfileContext, keyword: str) -> None:
    """Search UDFs by name, description, or signature substring match."""
    db = pctx.open_db()
    udfs = db.list_udfs()
    db.close()

    kw_lower = keyword.lower()
    matches = []
    for u in udfs:
        searchable = " ".join(
            [u["name"], u.get("description", "") or "", u.get("signature", "") or ""]
        ).lower()
        if kw_lower in searchable:
            matches.append(u)

    pctx.renderer.success({"count": len(matches), "results": matches})


# ── udf create ────────────────────────────────────────────────────────────


@profile_command(
    udf_group,
    "create",
    action=ACTION_UDF_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
@click.option(
    "--inline-python",
    type=click.Path(exists=True),
    default=None,
    help="Python script file for inline Python UDF",
)
@click.option("--description", default=None, help="UDF description")
def create_cmd(
    pctx: ProfileContext,
    name: str,
    inline_python: str | None,
    description: str | None,
) -> None:
    """Create a UDF in MaxCompute.

    For MVP, only --inline-python is supported. This reads the Python script
    and executes CREATE FUNCTION SQL via MaxComputeClient.
    """
    if not inline_python:
        raise McsError(
            "--inline-python is required (the only supported creation path in MVP)",
            remediation="pass --inline-python <script.py> with your UDF script",
        )

    prof = pctx.profile
    client = MaxComputeClient(prof)
    db = pctx.open_db()

    _validate_identifier(name)

    # Read script content.
    script_path = Path(inline_python)
    script_content = script_path.read_text(encoding="utf-8")

    # Extract class name for USING clause.
    class_name = _extract_class_name(script_content, inline_python)

    # Build CREATE FUNCTION SQL.
    sql = (
        f"CREATE FUNCTION {name} AS {_sql_string_literal(script_content)} "
        f"USING {_sql_string_literal(f'python:{class_name}')}"
    )

    try:
        # CREATE FUNCTION is pure DDL with no input bytes; pass
        # assume_yes=True so the cost gate doesn't pointlessly bill
        # an estimate for a 0-byte operation.
        client.execute_sql(sql, assume_yes=True, allow_write=True, skip_cost_gate=True)
    except McsError:
        db.close()
        raise

    # Track in PackageDB.
    db.upsert_udf(
        name=name,
        kind="python",
        class_name=class_name,
        description=description or "",
        signature="inline",
    )
    # Mark created_locally.
    db._conn.execute("UPDATE udfs SET created_locally=1 WHERE name=?", (name,))
    db._conn.commit()
    db.close()

    pctx.success(
        {"name": name, "class_name": class_name, "status": "created"},
        commit_summary=f"create {name}",
    )


# ── udf test ──────────────────────────────────────────────────────────────


@profile_command(udf_group, "test", accepts_schema=False)
@click.argument("name")
@click.option("--args", required=True, help="comma-separated args, e.g. '1, \"abc\"'")
def test_cmd(pctx: ProfileContext, name: str, args: str) -> None:
    """Test a UDF by constructing SELECT <name>(args) and executing it."""
    _validate_identifier(name)

    client = MaxComputeClient(pctx.profile)
    formatted_args = _format_args_for_sql(args)
    sql = f"SELECT {name}({formatted_args})"

    envelope = client.execute_sql(sql)

    # Extract result rows from envelope.
    result_data = envelope.to_dict()
    pctx.renderer.success({"sql": sql, "result": result_data.get("data", result_data)})


# ── udf remove ────────────────────────────────────────────────────────────


@profile_command(
    udf_group,
    "remove",
    action=ACTION_UDF_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
@click.option("--delete-resources", is_flag=True, help="also drop associated resources")
def remove_cmd(pctx: ProfileContext, name: str, delete_resources: bool) -> None:
    """Drop a UDF from MaxCompute and remove from PackageDB."""
    _validate_identifier(name)

    prof = pctx.profile
    client = MaxComputeClient(prof)
    db = pctx.open_db()

    odps = client._ensure_odps()

    try:
        mc_func = odps.get_function(name, project=prof.compute_project)
        if delete_resources:
            # Collect resource names from the function before dropping.
            resources = getattr(mc_func, "resources", None) or []
            resource_names = [r.name if hasattr(r, "name") else str(r) for r in resources]
            # Drop the function first.
            odps.drop_function(name, project=prof.compute_project)
            # Then drop each associated resource.
            for rn in resource_names:
                with contextlib.suppress(Exception):
                    # Resource drop failures are non-fatal (may not exist).
                    odps.drop_resource(rn, project=prof.compute_project)
        else:
            odps.drop_function(name, project=prof.compute_project)
    except McsError:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise map_pyodps_exception(e) from e

    # Remove from PackageDB (even if not found locally, idempotent).
    db._conn.execute("DELETE FROM udfs WHERE name=?", (name,))
    db._conn.commit()
    db.close()

    pctx.success({"name": name, "status": "removed"}, commit_summary=f"remove {name}")


# ── udf resource group ────────────────────────────────────────────────────


@udf_group.group("resource")
def resource_group() -> None:
    """Manage MaxCompute resources (list, show, remove)."""


@profile_command(resource_group, "list", accepts_schema=False)
def resource_list_cmd(pctx: ProfileContext) -> None:
    """List all resources in the MaxCompute project."""
    prof = pctx.profile
    client = MaxComputeClient(prof)
    odps = client._ensure_odps()
    try:
        resources = list(odps.list_resources(project=prof.compute_project))
    except Exception as e:
        raise map_pyodps_exception(e) from e

    headers = ["name", "type", "size", "owner"]
    rows = []
    resource_dicts = []
    for r in resources:
        r_name = getattr(r, "name", str(r))
        r_type = getattr(r, "type", "")
        r_size = getattr(r, "size", 0)
        r_owner = getattr(r, "owner", "")
        rows.append([r_name, str(r_type), str(r_size), r_owner])
        resource_dicts.append(
            {"name": r_name, "type": str(r_type), "size": r_size, "owner": r_owner}
        )

    if pctx.renderer.is_envelope:
        pctx.renderer.success({"count": len(resource_dicts), "resources": resource_dicts})
    else:
        pctx.renderer.table(headers, rows)


@profile_command(resource_group, "show", accepts_schema=False)
@click.argument("name")
def resource_show_cmd(pctx: ProfileContext, name: str) -> None:
    """Show details of a single MaxCompute resource."""
    prof = pctx.profile
    client = MaxComputeClient(prof)
    odps = client._ensure_odps()
    try:
        r = odps.get_resource(name, project=prof.compute_project)
    except Exception as e:
        raise map_pyodps_exception(e) from e

    detail = {
        "name": getattr(r, "name", name),
        "type": str(getattr(r, "type", "")),
        "size": getattr(r, "size", 0),
        "owner": getattr(r, "owner", ""),
        "comment": getattr(r, "comment", "") or "",
        "creation_time": str(getattr(r, "creation_time", "")),
        "last_modified_time": str(getattr(r, "last_modified_time", "")),
    }

    pctx.renderer.success({"resource": detail})


@profile_command(
    resource_group,
    "remove",
    action=ACTION_UDF_PREFIX,
    accepts_schema=False,
)
@click.argument("name")
def resource_remove_cmd(pctx: ProfileContext, name: str) -> None:
    """Remove a resource from the MaxCompute project."""
    prof = pctx.profile
    client = MaxComputeClient(prof)
    odps = client._ensure_odps()
    try:
        odps.drop_resource(name, project=prof.compute_project)
    except Exception as e:
        raise map_pyodps_exception(e) from e

    pctx.success({"name": name, "status": "removed"}, commit_summary=f"resource-remove {name}")
