# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""MaxCompute (ODPS) dialect for SQLGlot.

Inherits from Hive — MaxCompute's SQL surface is Hive-derived with
extensions for LIFECYCLE, DATETIME, script mode, and many built-in
functions with different names/signatures.

Keywords and type system are derived from the official MaxCompute SQL
grammar.  Function mappings come from the MaxCompute documentation.
"""

from __future__ import annotations

from sqlglot.dialects.hive import Hive
from sqlglot.tokens import TokenType

from maxcompute_semantic.dialect._generator import MaxComputeGenerator
from maxcompute_semantic.dialect._parser import MaxComputeParser


class MaxCompute(Hive):
    # MaxCompute uses the same case-insensitive identifier strategy as Hive.
    # SAFE_DIVISION, IDENTIFIERS_CAN_START_WITH_DIGIT, etc. are inherited.

    # MaxCompute date format tokens are identical to Hive's (yyyy-MM-dd etc.)
    # so TIME_MAPPING is inherited unchanged.

    class Tokenizer(Hive.Tokenizer):
        # Keywords extracted from the official MaxCompute SQL grammar.
        KEYWORDS = {
            **Hive.Tokenizer.KEYWORDS,
            # ── DDL / table properties ──
            "LIFECYCLE": TokenType.KEY,
            "HUBLIFECYCLE": TokenType.KEY,
            "HUBTABLE": TokenType.KEY,
            "CHANGELOGS": TokenType.KEY,
            "PARTITIONPROPERTIES": TokenType.KEY,
            "CACHEPROPERTIES": TokenType.KEY,
            "PRIVILEGEPROPERTIES": TokenType.KEY,
            "TASKPROPERTIES": TokenType.KEY,
            "STRMPROPERTIES": TokenType.KEY,
            "UDFPROPERTIES": TokenType.KEY,
            # ── ODPS type keywords ──
            "DATETIME": TokenType.DATETIME,
            "TIMESTAMP_NTZ": TokenType.TIMESTAMPNTZ,
            "INTERVAL_DAY_TIME": TokenType.VAR,
            "INTERVAL_YEAR_MONTH": TokenType.VAR,
            "GEOGRAPHY": TokenType.VAR,
            "VECTOR": TokenType.VAR,
            "VECTOR_SEARCH": TokenType.VAR,
            "BLOB": TokenType.VAR,
            # ── ODPS command keywords ──
            "ADD PY": TokenType.COMMAND,
            "DROP RESOURCE": TokenType.COMMAND,
            "LIST RESOURCES": TokenType.COMMAND,
            "LIST JOBS": TokenType.COMMAND,
            "LIST PROJECTS": TokenType.COMMAND,
            "KILL": TokenType.COMMAND,
            "STATUS": TokenType.COMMAND,
            "SETPROJECT": TokenType.COMMAND,
            "EXSTORE": TokenType.COMMAND,
            "WHOAMI": TokenType.COMMAND,
            # ── ODPS DDL extensions ──
            "CLONE": TokenType.VAR,
            "SNAPSHOT": TokenType.VAR,
            "ICEBERG": TokenType.VAR,
            "STREAM": TokenType.VAR,
            "STREAMS": TokenType.VAR,
            "TASK": TokenType.VAR,
            "TASKS": TokenType.VAR,
            "CONNECTION": TokenType.VAR,
            "CONNECTIONS": TokenType.VAR,
            "MODEL": TokenType.VAR,
            "MODELS": TokenType.VAR,
            "OBJECT": TokenType.VAR,
            "OBJECTS": TokenType.VAR,
            "CATALOG": TokenType.VAR,
            "TUNNEL": TokenType.VAR,
            # ── ODPS DDL clauses ──
            "CHANGEOWNER": TokenType.VAR,
            "RECLUSTER": TokenType.VAR,
            "SMALLFILES": TokenType.VAR,
            "RECYCLEBIN": TokenType.VAR,
            "SHARDS": TokenType.VAR,
            "BLOOMFILTER": TokenType.VAR,
            "BITMAP": TokenType.VAR,
            "REBUILD": TokenType.VAR,
            "BUILD": TokenType.VAR,
            "EVOLVE": TokenType.VAR,
            "CDC": TokenType.VAR,
            # ── Column constraints ──
            "NOVALIDATE": TokenType.VAR,
            "NORELY": TokenType.VAR,
            # ── Query extensions ──
            "ZORDER": TokenType.VAR,
            "SELECTIVITY": TokenType.VAR,
            # ── Hint keywords ──
            "MAPJOIN": TokenType.VAR,
            "CONDITIONALJOIN": TokenType.VAR,
            "SKEWJOIN": TokenType.VAR,
            "RANGEJOIN": TokenType.VAR,
            "DYNAMICFILTER": TokenType.VAR,
            "DISTHASHJOIN": TokenType.VAR,
            "DISTMAPJOIN": TokenType.VAR,
            "UNION_TABLE": TokenType.VAR,
            "SUBQUERY_MAPJOIN": TokenType.VAR,
            # ── Authorization ──
            "TRUSTEDPROJECTS": TokenType.VAR,
            "TRUSTEDPROJECT": TokenType.VAR,
            "SECURITYCONFIGURATION": TokenType.VAR,
            "PROJECTPROTECTION": TokenType.VAR,
            "ACCOUNTPROVIDERS": TokenType.VAR,
            "ACCOUNTPROVIDER": TokenType.VAR,
            "PACKAGE": TokenType.VAR,
            "PACKAGES": TokenType.VAR,
            "INSTALL": TokenType.VAR,
            "UNINSTALL": TokenType.VAR,
            "DISALLOW": TokenType.VAR,
            "OFFLINEMODEL": TokenType.VAR,
            "VOLUMEFILE": TokenType.VAR,
            "VOLUMEARCHIVE": TokenType.VAR,
            # ── Script mode ──
            "RETURNS": TokenType.VAR,
            "LOOP": TokenType.VAR,
            # ── Statistic ──
            "STATISTIC": TokenType.VAR,
            "STATISTIC_LIST": TokenType.VAR,
            "NULL_VALUE": TokenType.VAR,
            "DISTINCT_VALUE": TokenType.VAR,
            "TABLE_COUNT": TokenType.VAR,
            "COLUMN_SUM": TokenType.VAR,
            "COLUMN_MAX": TokenType.VAR,
            "COLUMN_MIN": TokenType.VAR,
            "EXPRESSION_CONDITION": TokenType.VAR,
            # ── Misc ODPS ──
            "HISTOGRAM": TokenType.VAR,
            "MASKING": TokenType.VAR,
            "BIND": TokenType.VAR,
            "UNBIND": TokenType.VAR,
            "INFERENCEPARAMETERS": TokenType.VAR,
            "SPLIT_SIZE": TokenType.VAR,
            "SYSTEM_TIME": TokenType.VAR,
            "DEFAULT_VERSION": TokenType.VAR,
            "EMBEDDED": TokenType.VAR,
            "USERDEFINE": TokenType.VAR,
            "SECURE": TokenType.VAR,
            "SAFE": TokenType.VAR,
            "XCOPY": TokenType.VAR,
            "RESTORE": TokenType.VAR,
            "LSN": TokenType.VAR,
            "STORING": TokenType.VAR,
            "SCHEDULE": TokenType.VAR,
            "ENTIRELY": TokenType.VAR,
            # ── MINUS is already mapped to EXCEPT by Hive ──
        }

        NUMERIC_LITERALS = {
            **Hive.Tokenizer.NUMERIC_LITERALS,
            "BD": "DECIMAL",
        }

    Parser = MaxComputeParser
    Generator = MaxComputeGenerator
