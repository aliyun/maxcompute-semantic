from maxcompute_semantic.commands.sql_review.types import Hint, Issue


def test_issue_to_dict_roundtrip() -> None:
    issue = Issue(
        severity="error",
        rule="dialect.sqlite-iif",
        message="IIF is SQLite",
        span=(12, 15),
        fix_hint="use CASE WHEN",
    )
    assert issue.to_dict() == {
        "severity": "error",
        "rule": "dialect.sqlite-iif",
        "message": "IIF is SQLite",
        "span": [12, 15],
        "fix_hint": "use CASE WHEN",
    }


def test_hint_to_dict_includes_if_misleading() -> None:
    hint = Hint(
        kind="join.not-declared",
        message="orders ↔ users undeclared",
        confidence="medium",
        evidence={"declared_joins": []},
        if_misleading="run `mcs build --refresh` to rediscover joins",
    )
    d = hint.to_dict()
    assert d["kind"] == "join.not-declared"
    assert d["confidence"] == "medium"
    assert d["if_misleading"].startswith("run `mcs build")
    assert d["evidence"] == {"declared_joins": []}
