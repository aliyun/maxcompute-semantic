# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a user-supplied table reference to a ``(source_key, table)``
pair against a multi-source PackageDB.

The resolver accepts two input shapes:

  1. **3-segment FQN** ``"proj.schema.table"`` — split deterministically
     into ``("proj__schema", "table")``. The fastest path; no DB lookup
     needed, useful for agents constructing a known-qualified reference.
  2. **bare name** alone — looks up across all sources via
     ``db.find_table_by_name(name)``. Single match auto-resolves;
     multiple matches errors with the disambiguation hint listing the
     candidate source_keys; zero matches falls back to the profile's
     first source's ``source_key`` when ``profile`` is provided (so
     ``mcs memory verify`` can record a table that hasn't been built
     yet), or errors when no profile is available.

Internal callers (the YAML batch path under ``mcs package apply``) may
pass an explicit ``source_key=`` to short-circuit the lookup when the
batch entry already carries a ``source:`` field. There is no CLI flag
form — use FQN at the call site instead.

This helper is shared by ``commands/memory.py``
(``verify`` / ``fail`` / ``recall`` / ``note`` table refs) so the
disambiguation logic stays in one place. ``mcs sql`` and ``mcs meta``
don't go through this helper — they take ``--project`` and
``--schema`` flags as their disambiguation surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maxcompute_semantic.mc_client.errors import ErrorCode, McsError

if TYPE_CHECKING:
    from maxcompute_semantic.auth.schema import Profile
    from maxcompute_semantic.build.storage import PackageDB


class TableResolutionError(McsError):
    """Raised when a bare table name is ambiguous across sources or
    doesn't match any row in PackageDB. Carries an exit_code so CLI
    callers can surface it through their normal error envelope.
    """

    code = ErrorCode.TABLE_RESOLUTION
    exit_code = 2


def resolve_table_to_source(
    name: str,
    db: PackageDB,
    *,
    source_key: str | None = None,
    profile: Profile | None = None,
) -> tuple[str, str]:
    """Resolve ``name`` to a ``(source_key, table_name)`` pair.

    See module docstring for the accepted input shapes. The ``profile``
    arg enables the "table not yet in PackageDB" fallback: when the
    bare-name DB lookup returns zero matches and the profile has at
    least one source, the first source's ``source_key`` is returned so
    flows like ``mcs memory verify`` can record an entry against a
    table that hasn't been built yet. The fallback is only safe when
    there's a single source (no ambiguity possible) — for multi-source
    profiles, zero matches still errors so the user is forced to
    disambiguate.

    ``source_key`` is for internal callers (the YAML batch path) only;
    CLI users disambiguate via the 3-segment FQN form.

    Raises ``TableResolutionError`` (exit_code 2) when the bare-name
    branch can't disambiguate.
    """
    if name.count(".") == 2:
        # FQN: ``proj.schema.table``. Split deterministically.
        proj, schema, table = name.split(".", 2)
        if not (proj and schema and table):
            raise TableResolutionError(
                f"FQN {name!r} has empty segment(s) — expected "
                f"``project.schema.table`` with all three non-empty",
                remediation="check the FQN form: project.schema.table",
            )
        return f"{proj}__{schema}", table

    if source_key:
        return source_key, name

    # ``source_key.table`` form — the shape ``mcs show --tables``
    # displays (e.g., ``catalogapi__public.orders``), which agents
    # naturally copy back into ``mcs package apply`` payloads.
    # Resolve directly against the (source_key, name) pair so the
    # agent's first attempt succeeds instead of routing through a
    # bare-name lookup that won't match the dotted literal.
    # MaxCompute identifiers can't contain ``.``, so the only legal
    # single-dot form is this composite key. We only return when the
    # row actually exists; on miss we fall through to the bare-name
    # path so a user typo (``order.customers`` instead of ``orders``)
    # still surfaces the bare-name "not found" remediation.
    if name.count(".") == 1:
        sk_candidate, tbl_candidate = name.split(".", 1)
        if sk_candidate and tbl_candidate and db.get_table(sk_candidate, tbl_candidate):
            return sk_candidate, tbl_candidate

    rows = db.find_table_by_name(name)
    if len(rows) == 1:
        return rows[0]["source_key"], name
    if rows:
        candidates = ", ".join(r["source_key"] for r in rows)
        raise TableResolutionError(
            f"table {name!r} exists in {len(rows)} sources ({candidates}) "
            f"— ambiguous bare-name reference",
            remediation=f"pass the 3-segment FQN ``proj.schema.{name}``",
        )
    # Zero matches in PackageDB. For single-source profiles, fall back
    # to the active source so flows like ``mcs memory verify`` can
    # write entries for tables that haven't been built yet (typical
    # mid-workflow state). Multi-source profiles must disambiguate
    # explicitly — silently picking ``sources[0]`` would be a footgun.
    if profile is not None:
        if len(profile.sources) == 1:
            return profile.sources[0].source_key(), name
        if len(profile.sources) > 1:
            candidates = ", ".join(s.source_key() for s in profile.sources)
            raise TableResolutionError(
                f"table {name!r} not found in package and profile has "
                f"{len(profile.sources)} sources ({candidates}) — "
                f"can't disambiguate",
                remediation=(
                    f"pass the 3-segment FQN ``proj.schema.{name}``, or run `mcs build` first"
                ),
            )
    raise TableResolutionError(
        f"table {name!r} not found in package",
        remediation="run `mcs build` to refresh the package, or check the table name spelling",
    )
