"""Static lint for top-level metric expressions.

Pure sqlglot — parses the expression and cross-checks every column
reference against the PackageDB. Returns warnings (not errors) for
references that don't resolve to a known (source, table, column) in
the current profile, so the user can override by simply ignoring the
warning. Unparseable input is a hard error.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

if TYPE_CHECKING:
    from maxcompute_semantic.build.storage import PackageDB


@dataclass
class ValidationResult:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def validate_metric_expression(expression: str, db: PackageDB) -> ValidationResult:
    """Parse ``expression`` and emit warnings for unresolved column refs.

    Returns ``ok=False`` only when sqlglot can't parse the input at all
    (hard reject — the metric won't be saved). All other anomalies are
    warnings — the metric is saved but ``mcs metric show`` surfaces the
    issue so the user can notice schema drift over time.
    """
    try:
        tree = sqlglot.parse_one(expression)
    except sqlglot.errors.ParseError as exc:
        return ValidationResult(ok=False, error=f"could not parse expression: {exc}")
    except Exception as exc:  # noqa: BLE001 — sqlglot raises many types
        return ValidationResult(ok=False, error=f"could not parse expression: {exc}")

    schema = _load_schema(db)
    warnings: list[str] = []
    for col in tree.find_all(exp.Column):
        warning = _check_column_ref(col, schema)
        if warning:
            warnings.append(warning)
    return ValidationResult(ok=True, warnings=warnings)


@dataclass(frozen=True)
class _Schema:
    """In-memory snapshot of the profile's tables and columns."""

    # source -> set[table]
    tables_by_source: dict[str, set[str]]
    # (source, table) -> set[column]
    columns_by_table: dict[tuple[str, str], set[str]]
    # bare column name -> list of (source, table) where it lives
    column_locations: dict[str, list[tuple[str, str]]]
    sources: tuple[str, ...]

    @property
    def is_single_source(self) -> bool:
        return len(self.sources) == 1


def _load_schema(db: PackageDB) -> _Schema:
    tables = db.list_tables()
    if not tables:
        return _Schema(
            tables_by_source={},
            columns_by_table={},
            column_locations={},
            sources=(),
        )
    cols_by_id = db.get_columns_bulk([t["id"] for t in tables])
    tables_by_source: dict[str, set[str]] = defaultdict(set)
    columns_by_table: dict[tuple[str, str], set[str]] = {}
    column_locations: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sources_seen: list[str] = []
    for t in tables:
        sk = t["source_key"]
        name = t["name"]
        if sk not in sources_seen:
            sources_seen.append(sk)
        tables_by_source[sk].add(name)
        col_names = {c["name"] for c in cols_by_id.get(t["id"], [])}
        columns_by_table[(sk, name)] = col_names
        for cn in col_names:
            column_locations[cn].append((sk, name))
    return _Schema(
        tables_by_source=dict(tables_by_source),
        columns_by_table=columns_by_table,
        column_locations=dict(column_locations),
        sources=tuple(sources_seen),
    )


def _check_column_ref(col: exp.Column, schema: _Schema) -> str | None:
    """Return a warning string if ``col`` doesn't resolve cleanly, else None."""
    # sqlglot's Column has ``table`` / ``db`` / ``catalog`` attributes
    # for the dotted segments; what's set depends on how many parts the
    # user wrote.
    name = col.name
    table = col.table or None
    db_seg = col.db or None  # the "schema/database" segment in a 3-part name
    catalog = col.catalog or None

    if catalog:
        # 4-part — out of scope for mcs metrics.
        return (
            f"expression uses 4-part reference '{catalog}.{db_seg}.{table}.{name}' "
            f"which mcs metrics do not support; use <source>.<table>.<col>"
        )

    if db_seg and table:
        # 3-part: source.table.column
        source = db_seg
        if source not in schema.tables_by_source:
            return f"expression references source '{source}' which is not in the current profile"
        if table not in schema.tables_by_source[source]:
            return f"expression references {source}.{table} which is not in the current profile"
        if name not in schema.columns_by_table[(source, table)]:
            return (
                f"expression references column {source}.{table}.{name} "
                f"which is not in the current profile"
            )
        return None

    if table:
        # 2-part: table.column — must resolve uniquely across sources.
        hits = [
            (sk, tn)
            for (sk, tn) in schema.columns_by_table
            if tn == table and name in schema.columns_by_table[(sk, tn)]
        ]
        if not hits:
            return f"expression references {table}.{name} which is not in the current profile"
        if len(hits) > 1:
            srcs = sorted({sk for sk, _ in hits})
            return (
                f"expression references {table}.{name} which is ambiguous "
                f"across sources {srcs}; qualify as <source>.{table}.{name}"
            )
        return None

    # 1-part: bare column.
    locations = schema.column_locations.get(name, [])
    if not locations:
        return f"expression uses bare column '{name}' which is not in the current profile"
    if len(locations) > 1:
        tables = sorted({tn for _, tn in locations})
        return (
            f"expression uses bare column '{name}' which is ambiguous "
            f"across tables {tables}; qualify it"
        )
    if not schema.is_single_source:
        sk, tn = locations[0]
        return (
            f"expression uses bare column '{name}' in a multi-source "
            f"profile; qualify with {sk}.{tn}.{name}"
        )
    return None
