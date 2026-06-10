# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""ANTLR ↔ SQLGlot comparison tests.

For every SQL in the corpus, verifies:
  1. ANTLR parse result (success / fail) — the ground truth
  2. SQLGlot parse result (success / fail / exp.Command fallback)
  3. Gap detection: ANTLR succeeds but SQLGlot falls to Command or fails

The corpus is organized by ANTLR parser rule category so coverage
gaps can be traced back to specific grammar rules.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlglot import exp

# ── ANTLR parser setup ───────────────────────────────────────────────

_GRAMMAR_DIR = Path(__file__).resolve().parent.parent.parent / "grammar" / "generated"
sys.path.insert(0, str(_GRAMMAR_DIR))

try:
    from antlr4 import CommonTokenStream, InputStream, ParseTreeWalker  # type: ignore[import-untyped]
    from OdpsLexer import OdpsLexer  # type: ignore[import-not-found]
    from OdpsParser import OdpsParser  # type: ignore[import-not-found]
    from OdpsParserListener import OdpsParserListener  # type: ignore[import-not-found]

    ANTLR_AVAILABLE = True
except ImportError:
    ANTLR_AVAILABLE = False

ALL_RULE_NAMES: frozenset[str] = (
    frozenset(OdpsParser.ruleNames) if ANTLR_AVAILABLE else frozenset()
)


class _RuleCoverageListener(OdpsParserListener if ANTLR_AVAILABLE else object):  # type: ignore[misc]
    """Records which parser rules are entered during a parse."""

    def __init__(self) -> None:
        self.covered_rules: set[str] = set()

    def enterEveryRule(self, ctx: object) -> None:
        rule_idx = getattr(ctx, "getRuleIndex", lambda: -1)()
        if ANTLR_AVAILABLE and 0 <= rule_idx < len(OdpsParser.ruleNames):
            self.covered_rules.add(OdpsParser.ruleNames[rule_idx])


class _ErrorListener:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def syntaxError(self, *args: object) -> None:
        self.errors.append(str(args[4]) if len(args) > 4 else str(args))

    def reportAmbiguity(self, *_a: object) -> None:
        pass

    def reportAttemptingFullContext(self, *_a: object) -> None:
        pass

    def reportContextSensitivity(self, *_a: object) -> None:
        pass


def _antlr_parse(sql: str) -> tuple[bool, list[str], set[str]]:
    """Parse *sql* with ANTLR. Returns (success, errors, covered_rules)."""
    lexer = OdpsLexer(InputStream(sql))
    parser = OdpsParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()

    err = _ErrorListener()
    parser.addErrorListener(err)
    tree = parser.script()

    covered: set[str] = set()
    if not err.errors:
        listener = _RuleCoverageListener()
        ParseTreeWalker.DEFAULT.walk(listener, tree)
        covered = listener.covered_rules

    return (not err.errors, err.errors, covered)


# ── SQLGlot parser setup ─────────────────────────────────────────────

from maxcompute_semantic.dialect import parse_mc  # noqa: E402


@dataclass
class ParseResult:
    antlr_ok: bool
    sqlglot_ok: bool
    sqlglot_structural: bool  # True if parsed into a real exp type, not Command


def _sqlglot_parse(sql: str) -> tuple[bool, bool]:
    """Parse *sql* with SQLGlot MaxCompute dialect.

    Returns (success, is_structural).
    ``is_structural`` is False when the result is ``exp.Command``.
    """
    import sqlglot

    try:
        stmts = parse_mc(sql, error_level=sqlglot.ErrorLevel.IGNORE)
    except Exception:
        return (False, False)
    if not stmts or all(s is None for s in stmts):
        return (False, False)
    structural = not all(isinstance(s, exp.Command) for s in stmts if s is not None)
    return (True, structural)


def _compare(sql: str) -> tuple[ParseResult, set[str]]:
    """Returns (ParseResult, set of ANTLR rules covered by this SQL)."""
    if ANTLR_AVAILABLE:
        antlr_ok, _, covered = _antlr_parse(sql)
    else:
        antlr_ok, covered = True, set()
    sg_ok, sg_struct = _sqlglot_parse(sql)
    return (
        ParseResult(antlr_ok=antlr_ok, sqlglot_ok=sg_ok, sqlglot_structural=sg_struct),
        covered,
    )


# ── SQL Corpus ────────────────────────────────────────────────────────
#
# Each entry: (sql, category, rule_hint)
# - category groups tests for reporting
# - rule_hint names the ANTLR parser rule(s) exercised
#
# SQL strings must end with ';' for the ANTLR parser (its entry rule
# is `script` which expects semicolons).

CORPUS: list[tuple[str, str, str]] = [
    # ── Basic SELECT ──
    ("SELECT 1;", "query", "selectClause"),
    ("SELECT a, b FROM t;", "query", "selectClause+fromClause"),
    ("SELECT * FROM t WHERE a > 1;", "query", "whereClause"),
    ("SELECT * FROM t ORDER BY a;", "query", "orderByClause"),
    ("SELECT * FROM t LIMIT 10;", "query", "limitClause"),
    ("SELECT * FROM t LIMIT 10, 20;", "query", "limitClause offset"),
    ("SELECT * FROM t LIMIT 10 OFFSET 20;", "query", "limitClause OFFSET"),
    ("SELECT DISTINCT a FROM t;", "query", "selectClause DISTINCT"),
    ("SELECT a, COUNT(*) FROM t GROUP BY a;", "query", "groupByClause"),
    ("SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 1;", "query", "havingClause"),
    # ── Subquery / CTE ──
    ("SELECT * FROM (SELECT 1) sub;", "query", "subQuerySource"),
    ("WITH cte AS (SELECT 1) SELECT * FROM cte;", "query", "withClause"),
    # ── JOIN ──
    ("SELECT * FROM t1 JOIN t2 ON t1.id = t2.id;", "query", "joinSource"),
    ("SELECT * FROM t1 LEFT JOIN t2 ON t1.id = t2.id;", "query", "joinSource LEFT"),
    ("SELECT * FROM t1 LEFT SEMI JOIN t2 ON t1.id = t2.id;", "query", "joinSource SEMI"),
    ("SELECT * FROM t1 LEFT ANTI JOIN t2 ON t1.id = t2.id;", "query", "joinSource ANTI"),
    ("SELECT * FROM t1 FULL OUTER JOIN t2 ON t1.id = t2.id;", "query", "joinSource FULL"),
    ("SELECT * FROM t1 CROSS JOIN t2;", "query", "joinSource CROSS"),
    # ── Set operations ──
    ("SELECT 1 UNION ALL SELECT 2;", "query", "setOperator UNION ALL"),
    ("SELECT 1 UNION SELECT 2;", "query", "setOperator UNION"),
    ("SELECT 1 INTERSECT SELECT 2;", "query", "setOperator INTERSECT"),
    ("SELECT 1 MINUS SELECT 2;", "query", "setOperator MINUS"),
    ("SELECT 1 EXCEPT SELECT 2;", "query", "setOperator EXCEPT"),
    # ── Window functions ──
    ("SELECT ROW_NUMBER() OVER(PARTITION BY a ORDER BY b) FROM t;", "query", "windowSpecification"),
    ("SELECT SUM(x) OVER(ORDER BY y ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t;", "query", "windowFrame"),
    # ── Expressions / Operators ──
    ("SELECT a + b, a - b, a * b, a / b, a % b FROM t;", "expr", "mathExpression"),
    ("SELECT a DIV b FROM t;", "expr", "KW_DIV"),
    ("SELECT a || b FROM t;", "expr", "CONCATENATE"),
    ("SELECT a & b, a | b, a ^ b, ~a FROM t;", "expr", "bitwiseOps"),
    ("SELECT CASE WHEN a > 1 THEN 'x' ELSE 'y' END FROM t;", "expr", "caseExpression"),
    ("SELECT CAST(a AS BIGINT) FROM t;", "expr", "castExpression"),
    ("SELECT a IN (1, 2, 3) FROM t;", "expr", "inExpression"),
    ("SELECT a BETWEEN 1 AND 10 FROM t;", "expr", "betweenExpression"),
    ("SELECT a IS NULL, a IS NOT NULL FROM t;", "expr", "isNull"),
    ("SELECT a LIKE '%test%' FROM t;", "expr", "likeExpression"),
    ("SELECT a RLIKE '^[0-9]+$' FROM t;", "expr", "rlikeExpression"),
    ("SELECT EXISTS (SELECT 1 FROM t2 WHERE t2.id = t1.id) FROM t1;", "expr", "existsExpression"),
    # ── Lateral view ──
    ("SELECT t.id, v.col FROM t LATERAL VIEW EXPLODE(arr) v AS col;", "query", "lateralView"),
    # ── Hints ──
    ("SELECT /*+ MAPJOIN(t2) */ * FROM t1 JOIN t2 ON t1.id = t2.id;", "query", "hintClause"),
    # ── Functions (generic rule, not individually named in grammar) ──
    ("SELECT DATEADD(dt, 1, 'dd');", "function", "function"),
    ("SELECT DATEDIFF(dt1, dt2, 'mm');", "function", "function"),
    ("SELECT GETDATE();", "function", "function"),
    ("SELECT WM_CONCAT(',', col);", "function", "function"),
    ("SELECT TOLOWER(name);", "function", "function"),
    ("SELECT TOUPPER(name);", "function", "function"),
    ("SELECT SPLIT_PART(s, ',', 1);", "function", "function"),
    ("SELECT SUBSTR(s, 1, 3);", "function", "function"),
    ("SELECT NVL(a, b);", "function", "function"),
    ("SELECT COALESCE(a, b, c);", "function", "function"),
    ("SELECT IF(a > 0, 'pos', 'neg');", "function", "function"),
    ("SELECT COUNT_IF(x > 0);", "function", "function"),
    # ── DDL: CREATE TABLE ──
    ("CREATE TABLE t (id BIGINT, name STRING);", "ddl", "createTableStatement"),
    ("CREATE TABLE IF NOT EXISTS t (id BIGINT);", "ddl", "createTableStatement ifNotExists"),
    ("CREATE TABLE t (id BIGINT) LIFECYCLE 30;", "ddl", "tableLifecycle"),
    ("CREATE TABLE t (id BIGINT) COMMENT 'test table';", "ddl", "createTableStatement COMMENT"),
    ("CREATE TABLE t (id BIGINT COMMENT 'pk', name STRING COMMENT 'user name');", "ddl", "columnComment"),
    ("CREATE TABLE t (id BIGINT) PARTITIONED BY (ds STRING);", "ddl", "tablePartition"),
    ("CREATE TABLE t (id BIGINT) CLUSTERED BY (id) INTO 10 BUCKETS;", "ddl", "tableBuckets"),
    ("CREATE TABLE t (id BIGINT) RANGE CLUSTERED BY (id) INTO 10 BUCKETS;", "ddl", "tableBuckets RANGE"),
    ("CREATE TABLE t (id BIGINT) CLUSTERED BY (id) SORTED BY (id) INTO 10 BUCKETS;", "ddl", "tableBuckets SORTED"),
    ("CREATE TABLE t (id BIGINT) STORED AS ORC;", "ddl", "tableFileFormat"),
    ("CREATE TABLE t (id BIGINT) TBLPROPERTIES ('k'='v');", "ddl", "tableProperties"),
    ("CREATE TABLE t (id BIGINT) LIFECYCLE 30 TBLPROPERTIES ('k'='v');", "ddl", "lifecycle+tblproperties"),
    ("CREATE TABLE t AS SELECT 1;", "ddl", "CTAS"),
    ("CREATE TABLE t LIKE src;", "ddl", "createTableLike"),
    ("CREATE EXTERNAL TABLE t (id BIGINT) LOCATION 'oss://bucket/path';", "ddl", "externalTable"),
    ("CREATE TABLE t (id BIGINT NOT NULL, name STRING DEFAULT 'unknown');", "ddl", "constraints"),
    ("CREATE TABLE t (id BIGINT, PRIMARY KEY (id));", "ddl", "outOfLineConstraints"),
    # ── DDL: Types (from ANTLR primitiveType rule) ──
    ("CREATE TABLE t (a TINYINT, b SMALLINT, c INT, d BIGINT);", "ddl-types", "intTypes"),
    ("CREATE TABLE t (a FLOAT, b DOUBLE, c DECIMAL(10,2));", "ddl-types", "floatTypes"),
    ("CREATE TABLE t (a STRING, b BOOLEAN, c BINARY);", "ddl-types", "otherPrimitive"),
    ("CREATE TABLE t (a DATE, b DATETIME, c TIMESTAMP);", "ddl-types", "temporalTypes"),
    ("CREATE TABLE t (a ARRAY<INT>, b MAP<STRING,INT>, c STRUCT<x:INT,y:STRING>);", "ddl-types", "complexTypes"),
    # ── DDL: ALTER TABLE ──
    ("ALTER TABLE t ADD COLUMNS (new_col STRING);", "ddl", "alterStatementSuffixAddCol"),
    ("ALTER TABLE t DROP COLUMNS (old_col);", "ddl", "alterStatementSuffixDropCol"),
    ("ALTER TABLE t RENAME TO t2;", "ddl", "alterStatementSuffixRename"),
    ("ALTER TABLE t SET LIFECYCLE 60;", "ddl", "alterTableSetLifecycle"),
    ("ALTER TABLE t ADD IF NOT EXISTS PARTITION (ds='20240101');", "ddl", "alterStatementSuffixAddPartitions"),
    ("ALTER TABLE t DROP IF EXISTS PARTITION (ds='20240101');", "ddl", "alterStatementSuffixDropPartitions"),
    ("ALTER TABLE t SET TBLPROPERTIES ('k'='v');", "ddl", "alterStatementSuffixProperties"),
    # ── DDL: VIEW ──
    ("CREATE VIEW v AS SELECT * FROM t;", "ddl", "createViewStatement"),
    ("DROP VIEW IF EXISTS v;", "ddl", "dropViewStatement"),
    # ── DDL: FUNCTION ──
    ("CREATE FUNCTION my_udf AS 'com.example.MyUDF' USING 'my.jar';", "ddl", "createFunctionStatement"),
    ("DROP FUNCTION IF EXISTS my_udf;", "ddl", "dropFunctionStatement"),
    # ── DDL: DROP ──
    ("DROP TABLE IF EXISTS t;", "ddl", "dropTableStatement"),
    ("DROP TABLE IF EXISTS t PURGE;", "ddl", "dropTableStatement PURGE"),
    # ── DDL: TRUNCATE ──
    ("TRUNCATE TABLE t;", "ddl", "truncateTableStatement"),
    # ── DML: INSERT ──
    ("INSERT INTO TABLE t SELECT * FROM s;", "dml", "insertStatement"),
    ("INSERT OVERWRITE TABLE t SELECT * FROM s;", "dml", "insertStatement OVERWRITE"),
    ("INSERT OVERWRITE TABLE t PARTITION (ds='20240101') SELECT * FROM s;", "dml", "insertPartition"),
    ("INSERT INTO TABLE t VALUES (1, 'a'), (2, 'b');", "dml", "insertValues"),
    # ── DML: Multi-INSERT ──
    ("FROM s INSERT INTO TABLE t1 SELECT a INSERT INTO TABLE t2 SELECT b;", "dml", "multiInsertBranch"),
    # ── DML: MERGE ──
    ("MERGE INTO tgt USING src ON tgt.id = src.id WHEN MATCHED THEN UPDATE SET tgt.v = src.v WHEN NOT MATCHED THEN INSERT VALUES (src.id, src.v);", "dml", "mergeStatement"),
    # ── DML: UPDATE / DELETE ──
    ("UPDATE t SET a = 1 WHERE id = 10;", "dml", "updateStatement"),
    ("DELETE FROM t WHERE id = 10;", "dml", "deleteStatement"),
    # ── SET ──
    ("SET odps.sql.allow.fullscan=true;", "command", "setStatement"),
    # ── EXPLAIN ──
    ("EXPLAIN SELECT * FROM t;", "command", "explainStatement"),
    # ── SHOW / DESCRIBE ──
    ("SHOW TABLES;", "command", "showTablesStatement"),
    ("SHOW PARTITIONS t;", "command", "showPartitions"),
    ("DESCRIBE t;", "command", "describeStatement"),
    # ── ODPS: Resource management ──
    ("ADD FILE /path/to/file.txt;", "odps-resource", "addResource FILE"),
    ("ADD JAR /path/to/my.jar;", "odps-resource", "addResource JAR"),
    ("ADD PY /path/to/my.py;", "odps-resource", "addResource PY"),
    # ── ODPS: CLONE TABLE ──
    ("CLONE TABLE src TO dst;", "odps-ddl", "cloneTableStatement"),
    # ── ODPS: LIFECYCLE on partition ──
    ("ALTER TABLE t PARTITION (ds='20240101') ENABLE LIFECYCLE;", "odps-ddl", "partitionLifecycle ENABLE"),
    ("ALTER TABLE t PARTITION (ds='20240101') DISABLE LIFECYCLE;", "odps-ddl", "partitionLifecycle DISABLE"),
    # ── ODPS: RECLUSTER ──
    ("ALTER TABLE t CLUSTERED BY (id) SORTED BY (id) INTO 10 BUCKETS;", "odps-ddl", "alterStatementSuffixBucketNum"),
    # ── ODPS: MERGE SMALLFILES ──
    ("ALTER TABLE t MERGE SMALLFILES;", "odps-ddl", "alterTableMergeSmallFiles"),
    # ── ODPS: CHANGEOWNER ──
    ("ALTER TABLE t CHANGEOWNER TO 'new_owner';", "odps-ddl", "alterTableChangeOwner"),
    # ── ODPS: Statistic ──
    ("SHOW STATISTIC t;", "odps-statistic", "showStatisticStatement"),
    ("COUNT t;", "odps-statistic", "countTableStatement"),
    # ── ODPS: UNDO / REDO / PURGE ──
    ("PURGE TABLE t;", "odps-command", "purgeStatement"),
    # ── ODPS: READ ──
    ("READ t;", "odps-command", "readStatement"),
    # ── ODPS: Authorization ──
    ("SHOW GRANTS;", "odps-auth", "showGrants"),
    ("GRANT SELECT ON TABLE t TO USER alice;", "odps-auth", "grantStatement"),
    ("REVOKE SELECT ON TABLE t FROM USER alice;", "odps-auth", "revokeStatement"),
]


# ── Test class ────────────────────────────────────────────────────────


@dataclass
class CorpusResult:
    sql: str
    category: str
    rule_hint: str
    result: ParseResult
    antlr_rules: set[str]


@pytest.fixture(scope="module")
def all_results() -> list[CorpusResult]:
    out = []
    for sql, cat, rule in CORPUS:
        r, covered = _compare(sql)
        out.append(CorpusResult(sql=sql, category=cat, rule_hint=rule, result=r, antlr_rules=covered))
    return out


class TestAntlrVsSqlglot:
    """For every SQL that ANTLR accepts, SQLGlot must also accept it."""

    @pytest.mark.parametrize(
        "sql, category, rule",
        [(sql, cat, rule) for sql, cat, rule in CORPUS],
        ids=[f"[{cat}] {rule}" for _, cat, rule in CORPUS],
    )
    def test_sqlglot_parses_what_antlr_parses(self, sql, category, rule):
        r, _ = _compare(sql)
        if not r.antlr_ok:
            pytest.skip(f"ANTLR cannot parse (public grammar may lack this rule): {rule}")
        assert r.sqlglot_ok, f"SQLGlot failed to parse SQL that ANTLR accepts: {sql}"

    @pytest.mark.parametrize(
        "sql, category, rule",
        [(sql, cat, rule) for sql, cat, rule in CORPUS],
        ids=[f"[{cat}] {rule}" for _, cat, rule in CORPUS],
    )
    def test_sqlglot_structural_not_command(self, sql, category, rule):
        """SQLGlot should parse structurally (not fall to exp.Command) for core SQL."""
        r, _ = _compare(sql)
        if not r.antlr_ok:
            pytest.skip(f"ANTLR cannot parse: {rule}")
        if category in ("odps-resource", "odps-command", "odps-statistic", "odps-auth", "command"):
            pytest.skip(f"Command/ODPS-specific — exp.Command fallback acceptable: {rule}")
        assert r.sqlglot_structural, (
            f"SQLGlot fell to exp.Command for SQL that should be structural: {sql}"
        )


# ── ANTLR rule coverage ──────────────────────────────────────────────

# Rules that are internal plumbing (appear in every parse) or grammar
# infrastructure that don't correspond to user-facing SQL constructs.
# Excluding these from the coverage target avoids noise.
_INFRASTRUCTURE_RULES: frozenset[str] = frozenset({
    "script", "statement", "emptyStatement", "compoundStatement",
    "execStatement", "identifier", "nonReserved",
    "stringLiteral", "simpleStringLiteral", "charSetStringLiteral",
    "constant", "number", "tableOrPartition",
    "tableOrTableId", "tableName", "tableId",
    "columnName", "columnNameList",
    "partitionSpec", "partitionVal", "partitionValList",
    "type", "primitiveType", "builtinType", "builtinTypeOrUdt",
    "listType", "structType", "mapType", "unionType",
    "columnNameType", "columnNameTypeList",
    "columnNameTypeConstraintList", "columnNameTypeOrConstraint",
    "tablePropertiesList", "tableProperties", "tablePropertiesPrefixed",
    "keyValueProperty", "keyProperty",
    "columnRef", "tableOrColumnRef", "tableAndColumnRef",
    "expressionsInParenthese", "expressionsNotInParenthese",
    "columnRefOrderInParenthese", "columnRefOrderNotInParenthese",
    "columnRefOrder",
    "ifExists", "ifNotExists", "orReplace",
    "sysFuncNames", "descFuncNames", "functionIdentifier",
    "aliasIdentifier", "aliasList",
    "functionName", "functionArgument",
})


class TestAntlrRuleCoverage:
    """Verify the SQL corpus exercises a sufficient set of ANTLR parser rules."""

    @pytest.mark.skipif(not ANTLR_AVAILABLE, reason="antlr4 runtime not installed")
    def test_rule_coverage_report(self, all_results: list[CorpusResult], capsys):
        covered: set[str] = set()
        for cr in all_results:
            covered |= cr.antlr_rules

        targetable = ALL_RULE_NAMES - _INFRASTRUCTURE_RULES
        covered_targetable = covered & targetable
        uncovered = targetable - covered

        pct = len(covered_targetable) / len(targetable) * 100 if targetable else 0

        with capsys.disabled():
            print(f"\n\n=== ANTLR Rule Coverage ===\n")
            print(f"Total rules:          {len(ALL_RULE_NAMES)}")
            print(f"Infrastructure (skip):{len(_INFRASTRUCTURE_RULES):>4}")
            print(f"Targetable rules:     {len(targetable)}")
            print(f"Covered by corpus:    {len(covered_targetable)}")
            print(f"Coverage:             {pct:.1f}%")

            if uncovered:
                print(f"\n--- {len(uncovered)} Uncovered Rule(s) ---\n")
                for r in sorted(uncovered):
                    print(f"  {r}")

    @pytest.mark.skipif(not ANTLR_AVAILABLE, reason="antlr4 runtime not installed")
    def test_rule_coverage_minimum(self, all_results: list[CorpusResult]):
        """Fail if rule coverage drops below the minimum threshold.

        Bump this threshold as the corpus grows.
        """
        covered: set[str] = set()
        for cr in all_results:
            covered |= cr.antlr_rules

        targetable = ALL_RULE_NAMES - _INFRASTRUCTURE_RULES
        covered_targetable = covered & targetable
        pct = len(covered_targetable) / len(targetable) * 100 if targetable else 0

        min_pct = 30  # bump as corpus grows
        assert pct >= min_pct, (
            f"ANTLR rule coverage {pct:.1f}% is below minimum {min_pct}%. "
            f"Add more SQL to the CORPUS to cover uncovered rules."
        )


class TestCoverageReport:
    """Generate a combined coverage summary."""

    def test_print_coverage_report(self, all_results: list[CorpusResult], capsys):
        categories: dict[str, dict[str, int]] = {}
        for cr in all_results:
            cat = cr.category
            if cat not in categories:
                categories[cat] = {"total": 0, "antlr_ok": 0, "sg_ok": 0, "sg_struct": 0}
            categories[cat]["total"] += 1
            if cr.result.antlr_ok:
                categories[cat]["antlr_ok"] += 1
            if cr.result.sqlglot_ok:
                categories[cat]["sg_ok"] += 1
            if cr.result.sqlglot_structural:
                categories[cat]["sg_struct"] += 1

        with capsys.disabled():
            print("\n\n=== ANTLR vs SQLGlot Coverage Report ===\n")
            print(f"{'Category':<20} {'Total':>5} {'ANTLR':>6} {'SGlot':>6} {'Struct':>6} {'Gaps':>5}")
            print("-" * 60)
            totals = {"total": 0, "antlr_ok": 0, "sg_ok": 0, "sg_struct": 0}
            for cat in sorted(categories):
                c = categories[cat]
                gaps = c["antlr_ok"] - c["sg_struct"]
                print(f"{cat:<20} {c['total']:>5} {c['antlr_ok']:>6} {c['sg_ok']:>6} {c['sg_struct']:>6} {gaps:>5}")
                for k in totals:
                    totals[k] += c[k]
            gaps = totals["antlr_ok"] - totals["sg_struct"]
            print("-" * 60)
            print(f"{'TOTAL':<20} {totals['total']:>5} {totals['antlr_ok']:>6} {totals['sg_ok']:>6} {totals['sg_struct']:>6} {gaps:>5}")

            gap_list = [
                cr for cr in all_results
                if cr.result.antlr_ok and not cr.result.sqlglot_structural
            ]
            if gap_list:
                print(f"\n--- {len(gap_list)} SQLGlot Gap(s) ---\n")
                for cr in gap_list:
                    print(f"  [{cr.category}] {cr.rule_hint}: {cr.sql[:70]}")
