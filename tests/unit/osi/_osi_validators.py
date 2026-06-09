"""Re-implementation of the OSI upstream validate.py four checks.

Source: https://github.com/open-semantic-interchange/OSI/blob/main/validation/validate.py

Each function takes a parsed dict and returns list[str] of error
messages (empty list = pass). Mirrors the upstream behavior so the
adapter output is held to the same contract.

Note on ``semantic_model`` shape: the OSI spec (and the schema vendored
at ``tests/fixtures/osi/osi-schema.json``) defines ``semantic_model``
at the top level as an **array** of ``SemanticModel`` objects. Upstream
``validate.py`` iterates it with ``for model in data.get("semantic_model", [])``.
We follow the same shape here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# Map OSI dialect enum values to sqlglot dialect names. Dialects not in
# this map are skipped (matches upstream ``SKIP_SQL_VALIDATION`` for
# MDX / TABLEAU / MAQL). ``None`` means "let sqlglot use its default
# dialect", which is how upstream handles ``ANSI_SQL``.
_SQL_DIALECTS_TO_PARSE: dict[str, str | None] = {
    "ANSI_SQL": None,
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
}


def load_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parent.parent.parent / "fixtures" / "osi" / "osi-schema.json"
    )
    with schema_path.open() as f:
        return json.load(f)


def validate_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = []
    for err in validator.iter_errors(data):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")
    return errors


def _iter_models(data: dict[str, Any]) -> list[dict]:
    return list(data.get("semantic_model") or [])


def validate_unique_names(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def _check(items: list[dict], label: str) -> None:
        seen: set[str] = set()
        for item in items:
            name = item.get("name")
            if name is None:
                continue
            if name in seen:
                errors.append(f"unique_names: duplicate {label} name {name!r}")
            seen.add(name)

    for model in _iter_models(data):
        model_name = model.get("name")
        _check(list(model.get("datasets") or []), f"dataset in model {model_name!r}")
        _check(
            list(model.get("relationships") or []),
            f"relationship in model {model_name!r}",
        )
        _check(list(model.get("metrics") or []), f"metric in model {model_name!r}")
        for ds in model.get("datasets") or []:
            _check(
                list(ds.get("fields") or []),
                f"field in dataset {ds.get('name')!r}",
            )
    return errors


def validate_references(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for model in _iter_models(data):
        dataset_names = {ds.get("name") for ds in model.get("datasets") or []}
        for rel in model.get("relationships") or []:
            for end in ("from", "to"):
                target = rel.get(end)
                if target is None:
                    continue
                if target not in dataset_names:
                    errors.append(
                        f"references: relationship {rel.get('name')!r} "
                        f"{end}={target!r} not in datasets"
                    )
    return errors


def _validate_sql_expression(expr: str, dialect: str, context: str) -> str | None:
    """Try to parse ``expr`` as a standalone expression; on ParseError,
    fall back to wrapping it in ``SELECT ...`` (covers bare column refs
    like ``c1``). Returns an error string or ``None`` on success.

    Mirrors upstream ``validate_sql_expression`` in shape so future
    re-syncs stay narrow.
    """
    import sqlglot
    from sqlglot.errors import ParseError

    if dialect not in _SQL_DIALECTS_TO_PARSE or not expr:
        return None
    sg_dialect = _SQL_DIALECTS_TO_PARSE[dialect]
    try:
        sqlglot.parse_one(expr, dialect=sg_dialect)
        return None
    except ParseError:
        pass
    try:
        sqlglot.parse_one(f"SELECT {expr}", dialect=sg_dialect)
        return None
    except ParseError as exc:
        return f"sql: {context}: {exc}"


def validate_sql(data: dict[str, Any]) -> list[str]:
    try:
        import sqlglot  # noqa: F401
    except ImportError:
        return ["Warning: sqlglot not installed, SQL validation skipped"]

    errors: list[str] = []
    for model in _iter_models(data):
        for ds in model.get("datasets") or []:
            for field in ds.get("fields") or []:
                expr = field.get("expression") or {}
                for dialect_expr in expr.get("dialects") or []:
                    dialect = dialect_expr.get("dialect", "ANSI_SQL")
                    sql = dialect_expr.get("expression", "")
                    context = f"dataset {ds.get('name')!r} field {field.get('name')!r}"
                    err = _validate_sql_expression(sql, dialect, context)
                    if err:
                        errors.append(err)
        for metric in model.get("metrics") or []:
            expr = metric.get("expression") or {}
            for dialect_expr in expr.get("dialects") or []:
                dialect = dialect_expr.get("dialect", "ANSI_SQL")
                sql = dialect_expr.get("expression", "")
                context = f"metric {metric.get('name')!r}"
                err = _validate_sql_expression(sql, dialect, context)
                if err:
                    errors.append(err)
    return errors


def validate_all(data: dict[str, Any]) -> list[str]:
    schema = load_schema()
    return (
        validate_schema(data, schema)
        + validate_unique_names(data)
        + validate_references(data)
        + validate_sql(data)
    )
