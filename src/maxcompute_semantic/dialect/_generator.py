# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MaxCompute SQL generator.

Every function mapped in ``_parser.py`` has a corresponding transform
here to ensure parse → AST → generate round-trips correctly.
"""

from __future__ import annotations

from typing import ClassVar

from sqlglot import exp
from sqlglot.dialects.dialect import rename_func, unit_to_str
from sqlglot.generators.hive import HiveGenerator
from sqlglot.transforms import (
    ctas_with_tmp_tables_to_create_tmp_view,
    move_schema_columns_to_partitioned_by,
    preprocess,
    remove_unique_constraints,
)


def _dateadd_sql(self: MaxComputeGenerator, expression: exp.Expression) -> str:
    """TsOrDsAdd / DateAdd / DateSub → DATEADD(dt, n, 'unit')."""
    this = self.sql(expression, "this")
    expr = expression.expression
    unit = unit_to_str(expression)

    if isinstance(expression, exp.DateSub) and expr:
        expr = exp.Mul(this=expr, expression=exp.Literal.number(-1))

    n = self.sql(expr) if expr else "1"
    return f"DATEADD({this}, {n}, {unit})"


def _datediff_sql(self: MaxComputeGenerator, expression: exp.DateDiff) -> str:
    """DateDiff → DATEDIFF(dt1, dt2, 'unit')."""
    this = self.sql(expression, "this")
    expr = self.sql(expression, "expression")
    unit = unit_to_str(expression)
    if unit:
        return f"DATEDIFF({this}, {expr}, {unit})"
    return f"DATEDIFF({this}, {expr})"


def _datetrunc_sql(
    self: MaxComputeGenerator,
    expression: exp.TimestampTrunc | exp.DateTrunc | exp.DatetimeTrunc,
) -> str:
    """TimestampTrunc / DateTrunc → DATETRUNC(dt, 'unit')."""
    this = self.sql(expression, "this")
    unit = unit_to_str(expression)
    return f"DATETRUNC({this}, {unit})"


def _datepart_sql(self: MaxComputeGenerator, expression: exp.Extract) -> str:
    """Extract → DATEPART(dt, 'unit')."""
    this = self.sql(expression, "expression")
    unit = expression.this
    unit_str = unit.name if isinstance(unit, (exp.Var, exp.Literal)) else self.sql(unit)
    return f"DATEPART({this}, '{unit_str}')"


def _groupconcat_sql(self: MaxComputeGenerator, expression: exp.GroupConcat) -> str:
    """GroupConcat → WM_CONCAT(sep, col)."""
    this = self.sql(expression, "this")
    sep = self.sql(expression, "separator") or "','"
    return f"WM_CONCAT({sep}, {this})"


def _substr_sql(self: MaxComputeGenerator, expression: exp.Substring) -> str:
    """Substring → SUBSTR(str, start, len)."""
    this = self.sql(expression, "this")
    start = self.sql(expression, "start")
    length = self.sql(expression, "length")
    if length:
        return f"SUBSTR({this}, {start}, {length})"
    return f"SUBSTR({this}, {start})"


def _instr_sql(self: MaxComputeGenerator, expression: exp.StrPosition) -> str:
    """StrPosition → INSTR(str, substr[, pos])."""
    this = self.sql(expression, "this")
    substr = self.sql(expression, "substr")
    position = self.sql(expression, "position")
    if position:
        return f"INSTR({this}, {substr}, {position})"
    return f"INSTR({this}, {substr})"


def _partitioned_by_sql(
    self: MaxComputeGenerator, expression: exp.PartitionedByProperty
) -> str:
    """Render PARTITIONED BY or AUTO PARTITIONED BY."""
    schema = expression.this
    if schema and any(
        isinstance(col, (exp.Alias, exp.TimestampTrunc))
        for col in (schema.expressions if isinstance(schema, exp.Schema) else [])
    ):
        cols = self.expressions(schema, flat=True)
        return f"AUTO PARTITIONED BY({cols})"
    return f"PARTITIONED BY {self.sql(expression, 'this')}"


def _clusteredbyproperty_sql(
    self: MaxComputeGenerator, expression: exp.ClusteredByProperty
) -> str:
    """Render CLUSTERED BY or RANGE CLUSTERED BY."""
    prefix = "RANGE " if expression.args.get("is_range") else ""
    return f"{prefix}{self.clusteredbyproperty_sql(expression)}"


def _properties_sql(self: MaxComputeGenerator, expression: exp.Properties) -> str:
    """Separate LIFECYCLE from TBLPROPERTIES in output."""
    bare: list[str] = []
    rest: list[exp.Expression] = []
    for prop in expression.expressions:
        if isinstance(prop, exp.Property) and isinstance(prop.this, exp.Var):
            name = prop.this.name.upper()
            if name == "LIFECYCLE":
                bare.append(f"LIFECYCLE {self.sql(prop, 'value')}")
                continue
        rest.append(prop)
    parts: list[str] = []
    if rest:
        clone = expression.copy()
        clone.set("expressions", rest)
        parts.append(super(MaxComputeGenerator, self).properties_sql(clone))
    parts.extend(bare)
    return "\n".join(parts)


def _datatype_sql(self: MaxComputeGenerator, expression: exp.DataType) -> str:
    """Strip length params from VARCHAR/CHAR — MaxCompute uses STRING."""
    dtype = expression.this
    if dtype in (exp.DType.VARCHAR, exp.DType.CHAR, exp.DType.NVARCHAR, exp.DType.NCHAR):
        return "STRING"
    return self.datatype_sql(expression)


class MaxComputeGenerator(HiveGenerator):
    # MaxCompute uses DATETIME as a distinct type, not an alias for TIMESTAMP.
    TYPE_MAPPING: ClassVar[dict[exp.DType, str]] = {
        **HiveGenerator.TYPE_MAPPING,
        exp.DType.DATETIME: "DATETIME",
        exp.DType.VARCHAR: "STRING",
        exp.DType.CHAR: "STRING",
        exp.DType.TEXT: "STRING",
        exp.DType.NVARCHAR: "STRING",
        exp.DType.NCHAR: "STRING",
        exp.DType.TIMESTAMPNTZ: "TIMESTAMP_NTZ",
    }

    TRANSFORMS: ClassVar = {
        **HiveGenerator.TRANSFORMS,
        # ── DDL ──
        exp.Create: preprocess(
            [
                remove_unique_constraints,
                ctas_with_tmp_tables_to_create_tmp_view,
                move_schema_columns_to_partitioned_by,
            ]
        ),
        exp.Properties: _properties_sql,
        exp.PartitionedByProperty: _partitioned_by_sql,
        exp.ClusteredByProperty: _clusteredbyproperty_sql,
        # ── Date arithmetic ──
        exp.TsOrDsAdd: _dateadd_sql,
        exp.DateAdd: _dateadd_sql,
        exp.TimestampAdd: _dateadd_sql,
        exp.DatetimeAdd: _dateadd_sql,
        exp.DateSub: _dateadd_sql,
        exp.DateDiff: _datediff_sql,
        exp.DateTrunc: _datetrunc_sql,
        exp.TimestampTrunc: _datetrunc_sql,
        exp.DatetimeTrunc: _datetrunc_sql,
        # ── Date extraction ──
        exp.Extract: _datepart_sql,
        # ── Current date/time ──
        exp.CurrentTimestamp: lambda self, _: "GETDATE()",
        exp.CurrentDatetime: lambda self, _: "GETDATE()",
        exp.CurrentTimezone: lambda self, _: "CURRENT_TIMEZONE()",
        # ── Date conversion ──
        exp.StrToTime: lambda self, e: f"TO_DATE({self.sql(e, 'this')}, {self.format_time(e)})",
        exp.UnixToTime: lambda self, e: f"FROM_UNIXTIME({self.sql(e, 'this')})",
        # ── String ──
        exp.Lower: rename_func("TOLOWER"),
        exp.Upper: rename_func("TOUPPER"),
        exp.Substring: _substr_sql,
        exp.StrPosition: _instr_sql,
        exp.RegexpCount: rename_func("REGEXP_COUNT"),
        exp.SplitPart: rename_func("SPLIT_PART"),
        exp.RegexpExtract: rename_func("REGEXP_SUBSTR"),
        # ── Aggregate ──
        exp.GroupConcat: _groupconcat_sql,
        exp.ArgMax: lambda self, e: f"ARG_MAX({self.sql(e, 'this')}, {self.sql(e, 'expression')})",
        exp.ArgMin: lambda self, e: f"ARG_MIN({self.sql(e, 'this')}, {self.sql(e, 'expression')})",
        exp.CountIf: rename_func("COUNT_IF"),
        exp.AnyValue: rename_func("ANY_VALUE"),
        exp.ApproxDistinct: rename_func("APPROX_DISTINCT"),
        exp.StddevSamp: rename_func("STDDEV_SAMP"),
        exp.CovarPop: rename_func("COVAR_POP"),
        exp.CovarSamp: rename_func("COVAR_SAMP"),
        exp.Corr: rename_func("CORR"),
        exp.Median: rename_func("MEDIAN"),
        exp.VariancePop: rename_func("VAR_POP"),
        exp.Variance: rename_func("VAR_SAMP"),
        exp.LogicalAnd: rename_func("BOOL_AND"),
        exp.LogicalOr: rename_func("BOOL_OR"),
        # ── Array ──
        exp.ArrayAll: rename_func("ALL_MATCH"),
        exp.ArrayAny: rename_func("ANY_MATCH"),
        exp.ArraySort: rename_func("ARRAY_SORT"),
        exp.ArrayDistinct: rename_func("ARRAY_DISTINCT"),
        exp.ArrayExcept: rename_func("ARRAY_EXCEPT"),
        exp.ArrayToString: rename_func("ARRAY_JOIN"),
        exp.ArrayMax: rename_func("ARRAY_MAX"),
        exp.ArrayMin: rename_func("ARRAY_MIN"),
        exp.ArrayOverlaps: rename_func("ARRAYS_OVERLAP"),
        exp.ArraysZip: rename_func("ARRAYS_ZIP"),
        exp.ArrayIntersect: rename_func("ARRAY_INTERSECT"),
        exp.ArrayPosition: rename_func("ARRAY_POSITION"),
        exp.ArrayRemove: rename_func("ARRAY_REMOVE"),
        exp.ArrayContains: rename_func("ARRAY_CONTAINS"),
        exp.ArraySlice: rename_func("SLICE"),
        # ── Map ──
        exp.MapCat: rename_func("MAP_CONCAT"),
        exp.MapFromEntries: rename_func("MAP_FROM_ENTRIES"),
        # ── JSON ──
        exp.ParseJSON: rename_func("FROM_JSON"),
        # ── Misc ──
        exp.CurrentUser: lambda self, _: "GET_USER_ID()",
        exp.UnixMillis: rename_func("TO_MILLIS"),
        exp.Space: rename_func("SPACE"),
        exp.AddMonths: rename_func("ADD_MONTHS"),
        exp.MonthsBetween: rename_func("MONTHS_BETWEEN"),
        exp.LastDay: rename_func("LAST_DAY"),
        exp.NextDay: rename_func("NEXT_DAY"),
    }
