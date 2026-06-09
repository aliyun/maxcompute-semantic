"""Tests for the sql_review dispatcher (`build_review_envelope`).

The dispatcher is the seam Task 14's CLI integration calls into. It
opens the per-profile ``package.db`` once, builds a ``ReviewContext``,
walks every rule in ``ALL_RULES`` and every hint in ``ALL_HINTS``, and
returns the envelope ``data`` dict ``{sql, issues, hints,
model_coverage, review_mode, semantic_checks_skipped}``.

Pin notes:

- The dispatcher owns the ``PackageDB`` lifecycle — tests pass a
  ``Profile`` only and let the dispatcher resolve the on-disk path via
  ``profile_data_dir``. The smoke-check assertion that ``IIF`` fires
  doubles as proof the dispatcher is reading from the same SQLite
  ``make_review_package`` populated.
- Pattern hint dedup is **not** implemented in v1 — Task 11's contract
  is per-row emission, and the dispatcher comment defers any
  deduplication / capping to a future task. The
  ``test_per_row_hint_emission_preserved`` case pins that contract so
  a future refactor that silently de-dupes pattern hints surfaces here
  rather than at envelope-assembly time.
- Unparseable SQL must not raise. Each rule short-circuits on parse
  failure individually (via ``parse_statements`` returning ``[]``);
  this test pins that those zero-emit paths compose into a clean
  envelope rather than a crash.
"""

from __future__ import annotations

import json

from maxcompute_semantic.commands.sql_review import build_review_envelope


class TestBuildReviewEnvelope:
    def test_envelope_shape(self, make_review_package) -> None:
        """Basic envelope shape + IIF rule fires (also smoke-tests
        that the dispatcher reads from the same DB the fixture wrote)."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        data = build_review_envelope(
            sql="SELECT IIF(id > 0, 1, 0) FROM orders",
            profile=profile,
            project=profile.compute_project,
            schema_name=None,
            tier="2",
        )
        assert "sql" in data and "issues" in data and "hints" in data
        assert "model_coverage" in data
        # IIF should fire — also confirms dispatcher opened the right DB.
        assert any(i["rule"] == "dialect.sqlite-iif" for i in data["issues"])

    def test_aggregates_issues_from_all_rules(self, make_review_package) -> None:
        """Multiple rules cooperate — IIF + missing column → both fire."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        data = build_review_envelope(
            sql="SELECT IIF(id > 0, bogus, 0) FROM orders",
            profile=profile,
            project=profile.compute_project,
            schema_name=None,
            tier="2",
        )
        rules = {i["rule"] for i in data["issues"]}
        assert "dialect.sqlite-iif" in rules
        assert "schema.column-not-found" in rules

    def test_unparseable_sql_returns_empty_envelope(self, make_review_package) -> None:
        """Garbled input must not raise. Each rule short-circuits on parse
        failure individually; this test pins the dispatcher's contract that
        those zero-emit paths compose into a clean envelope rather than a
        crash."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        data = build_review_envelope(
            sql="this is not sql at all !!!",
            profile=profile,
            project=profile.compute_project,
            schema_name=None,
            tier="2",
        )
        assert data["sql"] == "this is not sql at all !!!"
        assert data["issues"] == []
        assert data["hints"] == []
        assert "model_coverage" in data
        assert data["model_coverage"]["coverage_pct"] == 0

    def test_per_row_hint_emission_preserved(self, make_review_package) -> None:
        """Pin the dispatcher does NOT dedup pattern.verified-match hints —
        Task 11's contract says per-row emission, dispatcher owns dedup
        'if/when a workload demands it'. Two verified_queries with the
        same shape must produce two pattern.verified-match hints."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ],
            memories=[
                {
                    "kind": "verified_query",
                    "payload_json": json.dumps(
                        {
                            "question": "Q1",
                            "sql": "SELECT id FROM orders",
                            "table_refs": [
                                {
                                    "source_key": "rev_proj__default",
                                    "table": "orders",
                                }
                            ],
                            "evidence_text": "",
                        }
                    ),
                    "retrieval_text": "Q1",
                },
                {
                    "kind": "verified_query",
                    "payload_json": json.dumps(
                        {
                            "question": "Q2",
                            "sql": "SELECT id FROM orders",
                            "table_refs": [
                                {
                                    "source_key": "rev_proj__default",
                                    "table": "orders",
                                }
                            ],
                            "evidence_text": "",
                        }
                    ),
                    "retrieval_text": "Q2",
                },
            ],
        )
        data = build_review_envelope(
            sql="SELECT id FROM orders",
            profile=profile,
            project=profile.compute_project,
            schema_name=None,
            tier="2",
        )
        pattern_hints = [h for h in data["hints"] if h["kind"] == "pattern.verified-match"]
        assert len(pattern_hints) == 2

    def test_missing_package_db_runs_syntax_only_review(
        self, mock_profile, isolated_config
    ) -> None:
        """No package still permits package-independent checks.

        Uses ``mock_profile`` directly (no ``make_review_package`` call)
        so the profile_data_dir under the ``isolated_config`` tmp root
        contains no ``package.db`` at the time of the call. The
        dispatcher must not create a SQLite side effect, but dialect /
        tier rules should still run and return a success-shaped
        syntax-only envelope.
        """
        data = build_review_envelope(
            sql="SELECT IIF(id > 0, 1, 0) FROM orders",
            profile=mock_profile,
            project=mock_profile.compute_project,
            schema_name=None,
            tier="2",
        )
        assert data["review_mode"] == "syntax_only"
        assert data["semantic_checks_skipped"] is True
        assert data["semantic_skip_reason"] == "package_not_built"
        assert any(i["rule"] == "dialect.sqlite-iif" for i in data["issues"])
        assert data["hints"] == []
        assert data["model_coverage"]["coverage_pct"] == 0
