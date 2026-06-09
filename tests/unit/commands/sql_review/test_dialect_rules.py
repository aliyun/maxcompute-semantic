from maxcompute_semantic.build.workload import extract_sql_evidence
from maxcompute_semantic.commands.sql_review.rules.dialect import (
    check_sqlite_iif,
    check_sqlite_julianday,
    check_sqlite_strftime,
    check_sqlite_substr_neg,
)
from maxcompute_semantic.commands.sql_review.types import ReviewContext


def _ctx(sql: str) -> ReviewContext:
    return ReviewContext(
        sql=sql,
        evidence=extract_sql_evidence(sql),
        profile=None,
        project="p",
        schema_name=None,
        tier="2",
        db=None,
        classification="read",
    )


class TestSqliteIif:
    def test_iif_call_emits_issue(self) -> None:
        sql = "SELECT IIF(x > 0, 'pos', 'neg') FROM t"
        issues = check_sqlite_iif(_ctx(sql))
        assert len(issues) == 1
        assert issues[0].rule == "dialect.sqlite-iif"
        assert issues[0].severity == "error"
        assert "CASE WHEN" in issues[0].fix_hint

    def test_no_iif_no_issue(self) -> None:
        sql = "SELECT CASE WHEN x > 0 THEN 'pos' ELSE 'neg' END FROM t"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_valid_maxcompute_if_no_issue(self) -> None:
        # Regression: sqlglot collapses both IIF(c,a,b) and IF(c,a,b)
        # into the same three-arg exp.If node, so detecting IIF via the
        # AST's `false` arg falsely flagged valid MaxCompute IF.
        # The rule must look at the source text to distinguish them.
        sql = "SELECT IF(x > 0, 'pos', 'neg') FROM t"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_iif_inside_other_identifier_no_issue(self) -> None:
        # Word-boundary check — `notIIF(...)` or `xx_IIF_yy` should not
        # match. (`my_iif_col` is a column name, not the function.)
        sql = "SELECT my_iif_col FROM t WHERE diff_iif > 0"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_multiple_iif_calls_emit_multiple_issues(self) -> None:
        sql = "SELECT IIF(a, 1, 2), IIF(b, 3, 4) FROM t"
        issues = check_sqlite_iif(_ctx(sql))
        assert len(issues) == 2
        assert all(i.rule == "dialect.sqlite-iif" for i in issues)

    def test_unparseable_sql_with_iif_still_flagged(self) -> None:
        # Source-text detection works even when sqlglot can't parse.
        # This is a feature, not a bug — the IIF token is the signal.
        sql = "SELECT IIF(>>>"
        issues = check_sqlite_iif(_ctx(sql))
        assert len(issues) == 1

    def test_unparseable_sql_without_iif_returns_empty(self) -> None:
        assert check_sqlite_iif(_ctx(">>> not sql <<<")) == []

    def test_iif_inside_string_literal_no_issue(self) -> None:
        # The tokenizer collapses quoted-string contents to a STRING
        # token, so an ``IIF(`` substring inside a literal is invisible
        # to the rule. Regression guard for the source-text-regex
        # approach this rule replaced, which would have flagged this.
        sql = "SELECT 'IIF(x,1,2)' AS txt FROM t"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_iif_inside_line_comment_no_issue(self) -> None:
        sql = "SELECT col FROM t -- IIF(x,1,2)"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_iif_inside_block_comment_no_issue(self) -> None:
        sql = "SELECT col FROM t /* IIF(x,1,2) */"
        assert check_sqlite_iif(_ctx(sql)) == []

    def test_bare_iif_identifier_no_issue(self) -> None:
        # ``IIF`` without a following ``(`` is a column or alias name,
        # not a function call; the L_PAREN-follow check skips it.
        sql = "SELECT IIF FROM t"
        assert check_sqlite_iif(_ctx(sql)) == []


class TestSqliteStrftime:
    def test_strftime_call_emits_issue(self) -> None:
        sql = "SELECT STRFTIME('%Y-%m', dt) FROM events"
        issues = check_sqlite_strftime(_ctx(sql))
        assert len(issues) == 1
        assert issues[0].rule == "dialect.sqlite-strftime"
        assert "DATE_FORMAT" in issues[0].fix_hint or "TO_CHAR" in issues[0].fix_hint

    def test_no_strftime_no_issue(self) -> None:
        sql = "SELECT TO_CHAR(dt, 'YYYY-MM') FROM events"
        assert check_sqlite_strftime(_ctx(sql)) == []


class TestSqliteJulianday:
    def test_julianday_call_emits_issue(self) -> None:
        sql = "SELECT JULIANDAY(end_dt) - JULIANDAY(start_dt) FROM t"
        issues = check_sqlite_julianday(_ctx(sql))
        assert len(issues) == 2  # one per call
        assert all(i.rule == "dialect.sqlite-julianday" for i in issues)

    def test_no_julianday_no_issue(self) -> None:
        sql = "SELECT DATEDIFF(end_dt, start_dt, 'dd') FROM t"
        assert check_sqlite_julianday(_ctx(sql)) == []


class TestSqliteSubstrNeg:
    def test_substr_negative_second_arg_emits_issue(self) -> None:
        sql = "SELECT SUBSTR(s, -3) FROM t"
        issues = check_sqlite_substr_neg(_ctx(sql))
        assert len(issues) == 1
        assert issues[0].rule == "dialect.sqlite-substr-neg"

    def test_substr_positive_arg_no_issue(self) -> None:
        sql = "SELECT SUBSTR(s, 1, 3) FROM t"
        assert check_sqlite_substr_neg(_ctx(sql)) == []
