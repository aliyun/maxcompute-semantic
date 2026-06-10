# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MaxCompute SQL parser.

Function mappings are derived from MaxCompute documentation; DDL property
parsers are derived from the official MaxCompute SQL grammar.
"""

from __future__ import annotations

import typing as t

from sqlglot import exp
from sqlglot.dialects.dialect import build_formatted_time, build_timetostr_or_tochar
from sqlglot.helper import seq_get
from sqlglot.parsers.hive import HiveParser
from sqlglot.tokens import TokenType

if t.TYPE_CHECKING:
    from sqlglot.dialects.dialect import Dialect


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _build_dateadd(args: list) -> exp.TsOrDsAdd:
    """DATEADD(dt, n, 'unit') → TsOrDsAdd."""
    return exp.TsOrDsAdd(
        this=seq_get(args, 0),
        expression=seq_get(args, 1),
        unit=seq_get(args, 2),
    )


def _build_datediff(args: list) -> exp.DateDiff:
    """DATEDIFF(dt1, dt2, 'unit') → DateDiff with optional unit."""
    return exp.DateDiff(
        this=seq_get(args, 0),
        expression=seq_get(args, 1),
        unit=seq_get(args, 2),
    )


def _build_datetrunc(args: list) -> exp.TimestampTrunc:
    """DATETRUNC(dt, 'unit') or TRUNC_TIME(dt, 'unit') → TimestampTrunc."""
    return exp.TimestampTrunc(
        this=seq_get(args, 0),
        unit=seq_get(args, 1),
    )


def _build_datepart(args: list) -> exp.Extract:
    """DATEPART(dt, 'unit') → Extract."""
    return exp.Extract(
        this=seq_get(args, 1),
        expression=seq_get(args, 0),
    )


def _build_to_date(args: list, dialect: Dialect) -> exp.TsOrDsToDate | exp.StrToTime:
    """TO_DATE(str, fmt) → StrToTime; TO_DATE(expr) → TsOrDsToDate."""
    if len(args) >= 2:
        return build_formatted_time(exp.StrToTime)(args, dialect)
    return exp.TsOrDsToDate(this=seq_get(args, 0))


def _build_from_utc_timestamp(args: list) -> exp.ConvertTimezone:
    """FROM_UTC_TIMESTAMP(ts, tz) → ConvertTimezone(source_tz='UTC')."""
    return exp.ConvertTimezone(
        source_tz=exp.Literal.string("UTC"),
        target_tz=seq_get(args, 1),
        this=seq_get(args, 0),
    )


def _build_isdate(args: list) -> exp.Not:
    """ISDATE(str) → NOT (TsOrDsToDate(str, safe=True) IS NULL)."""
    return exp.Not(
        this=exp.Is(
            this=exp.TsOrDsToDate(this=seq_get(args, 0), safe=True),
            expression=exp.Null(),
        )
    )


def _build_wm_concat(args: list) -> exp.GroupConcat:
    """WM_CONCAT(sep, col) → GroupConcat (note: arg order is sep, col)."""
    return exp.GroupConcat(
        this=seq_get(args, 1),
        separator=seq_get(args, 0),
    )


class MaxComputeParser(HiveParser):
    LOG_DEFAULTS_TO_LN = True

    FUNCTIONS = {
        **HiveParser.FUNCTIONS,
        # ------------------------------------------------------------------
        # Date arithmetic
        # ------------------------------------------------------------------
        "DATEADD": _build_dateadd,
        "DATE_SUB": lambda args: exp.DateSub(
            this=seq_get(args, 0),
            expression=seq_get(args, 1),
            unit=seq_get(args, 2) or exp.Literal.string("DAY"),
        ),
        "DATEDIFF": _build_datediff,
        "ADD_MONTHS": exp.AddMonths.from_arg_list,
        "MONTHS_BETWEEN": exp.MonthsBetween.from_arg_list,
        # ------------------------------------------------------------------
        # Date extraction
        # ------------------------------------------------------------------
        "DATEPART": _build_datepart,
        "DATETRUNC": _build_datetrunc,
        "TRUNC_TIME": _build_datetrunc,
        # Override Hive: MaxCompute DAY/MONTH/YEAR don't wrap in TsOrDsToDate
        "DAY": exp.Day.from_arg_list,
        "MONTH": exp.Month.from_arg_list,
        "YEAR": exp.Year.from_arg_list,
        "DAYOFMONTH": exp.DayOfMonth.from_arg_list,
        "DAYOFWEEK": exp.DayOfWeek.from_arg_list,
        "DAYOFYEAR": exp.DayOfYear.from_arg_list,
        "HOUR": exp.Hour.from_arg_list,
        "MINUTE": exp.Minute.from_arg_list,
        "SECOND": exp.Second.from_arg_list,
        "QUARTER": exp.Quarter.from_arg_list,
        "WEEKOFYEAR": exp.WeekOfYear.from_arg_list,
        "LAST_DAY": exp.LastDay.from_arg_list,
        "LASTDAY": exp.LastDay.from_arg_list,
        "NEXT_DAY": exp.NextDay.from_arg_list,
        # ------------------------------------------------------------------
        # Current date/time
        # ------------------------------------------------------------------
        "GETDATE": lambda args: exp.CurrentTimestamp(),
        "NOW": lambda args: exp.CurrentTimestamp(),
        "CURRENT_TIMESTAMP": lambda args: exp.CurrentTimestamp(),
        "CURRENT_TIMEZONE": lambda args: exp.CurrentTimezone(),
        # ------------------------------------------------------------------
        # Date conversion
        # ------------------------------------------------------------------
        "DATE_FORMAT": lambda args, dialect: build_formatted_time(exp.TimeToStr)(
            args, dialect
        ),
        "TO_DATE": _build_to_date,
        "TO_CHAR": build_timetostr_or_tochar,
        "FROM_UNIXTIME": lambda args, dialect: build_formatted_time(
            exp.UnixToTime, default=True
        )(args, dialect),
        "TO_MILLIS": exp.UnixMillis.from_arg_list,
        "FROM_UTC_TIMESTAMP": _build_from_utc_timestamp,
        "ISDATE": _build_isdate,
        # ------------------------------------------------------------------
        # String
        # ------------------------------------------------------------------
        "TOLOWER": exp.Lower.from_arg_list,
        "TOUPPER": exp.Upper.from_arg_list,
        "REGEXP_COUNT": exp.RegexpCount.from_arg_list,
        "SPLIT_PART": exp.SplitPart.from_arg_list,
        "SUBSTR": exp.Substring.from_arg_list,
        "REGEXP_SUBSTR": exp.RegexpExtract.from_arg_list,
        # ------------------------------------------------------------------
        # Aggregate
        # ------------------------------------------------------------------
        "WM_CONCAT": _build_wm_concat,
        "COUNT_IF": exp.CountIf.from_arg_list,
        "ARG_MAX": exp.ArgMax.from_arg_list,
        "ARG_MIN": exp.ArgMin.from_arg_list,
        "MAX_BY": exp.ArgMax.from_arg_list,
        "MIN_BY": exp.ArgMin.from_arg_list,
        "ANY_VALUE": exp.AnyValue.from_arg_list,
        "APPROX_DISTINCT": exp.ApproxDistinct.from_arg_list,
        "STDDEV_SAMP": exp.StddevSamp.from_arg_list,
        "COVAR_POP": exp.CovarPop.from_arg_list,
        "COVAR_SAMP": exp.CovarSamp.from_arg_list,
        "CORR": exp.Corr.from_arg_list,
        "MEDIAN": exp.Median.from_arg_list,
        "PERCENTILE_APPROX": exp.ApproxQuantile.from_arg_list,
        # ------------------------------------------------------------------
        # Array
        # ------------------------------------------------------------------
        "ALL_MATCH": exp.ArrayAll.from_arg_list,
        "ANY_MATCH": exp.ArrayAny.from_arg_list,
        "ARRAY_SORT": exp.ArraySort.from_arg_list,
        "ARRAY_DISTINCT": exp.ArrayDistinct.from_arg_list,
        "ARRAY_EXCEPT": exp.ArrayExcept.from_arg_list,
        "ARRAY_JOIN": exp.ArrayToString.from_arg_list,
        "ARRAY_MAX": exp.ArrayMax.from_arg_list,
        "ARRAY_MIN": exp.ArrayMin.from_arg_list,
        "ARRAYS_OVERLAP": exp.ArrayOverlaps.from_arg_list,
        "ARRAYS_ZIP": exp.ArraysZip.from_arg_list,
        "ARRAY_INTERSECT": exp.ArrayIntersect.from_arg_list,
        "ARRAY_POSITION": exp.ArrayPosition.from_arg_list,
        "ARRAY_REMOVE": exp.ArrayRemove.from_arg_list,
        "ARRAY_CONTAINS": exp.ArrayContains.from_arg_list,
        "SLICE": exp.ArraySlice.from_arg_list,
        # ------------------------------------------------------------------
        # Map
        # ------------------------------------------------------------------
        "MAP_CONCAT": exp.MapCat.from_arg_list,
        "MAP_FROM_ENTRIES": exp.MapFromEntries.from_arg_list,
        # ------------------------------------------------------------------
        # JSON
        # ------------------------------------------------------------------
        "FROM_JSON": exp.ParseJSON.from_arg_list,
        "GET_JSON_OBJECT": lambda args, dialect: exp.JSONExtractScalar(
            this=seq_get(args, 0),
            expression=dialect.to_json_path(seq_get(args, 1)),
        ),
        # ------------------------------------------------------------------
        # Misc
        # ------------------------------------------------------------------
        "GET_USER_ID": lambda args: exp.CurrentUser(),
    }

    PROPERTY_PARSERS = {
        **HiveParser.PROPERTY_PARSERS,
        "LIFECYCLE": lambda self: exp.Property(
            this=exp.var("LIFECYCLE"),
            value=self._parse_number(),
        ),
    }

    def _parse_range_clustered_by(self) -> exp.ClusteredByProperty | None:
        """RANGE CLUSTERED BY (cols) [SORTED BY (cols)] INTO n BUCKETS."""
        self._match_text_seq("CLUSTERED", "BY")
        expressions = self._parse_wrapped_csv(self._parse_column)
        sorted_by = None
        if self._match_text_seq("SORTED", "BY"):
            sorted_by = self._parse_wrapped_csv(self._parse_ordered)
        self._match_text_seq("INTO")
        buckets = self._parse_number()
        self._match_text_seq("BUCKETS")
        return exp.ClusteredByProperty(
            expressions=expressions,
            sorted_by=sorted_by or [],
            buckets=buckets or 0,
            is_range=True,
        )

    def _parse_auto_partition(self) -> exp.PartitionedByProperty | None:
        """AUTO PARTITIONED BY (trunc_time_expr)."""
        self._match_text_seq("PARTITIONED", "BY")
        schema = self._parse_wrapped_csv(self._parse_named_expression)
        return exp.PartitionedByProperty(
            this=exp.Schema(expressions=schema),
        )

    def _parse_property(self) -> t.Optional[exp.Expression]:
        if self._match_text_seq("RANGE"):
            return self._parse_range_clustered_by()
        if self._match_text_seq("AUTO"):
            return self._parse_auto_partition()
        return super()._parse_property()

    def _parse_named_expression(self) -> t.Optional[exp.Expression]:
        """Parse ``expr [AS alias]`` for AUTO PARTITIONED BY."""
        this = self._parse_assignment()
        if self._match(TokenType.ALIAS):
            alias = self._parse_id_var()
            return exp.Alias(this=this, alias=alias)
        return this
