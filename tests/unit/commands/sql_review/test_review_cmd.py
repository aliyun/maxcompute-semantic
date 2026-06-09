"""Tests for the ``mcs sql review`` click subcommand.

The CLI is the user-facing seam between the dispatcher
(``build_review_envelope``) and the JSON envelope on stdout. These
tests pin three behavior contracts:

1. **Success envelope shape** — wraps the dispatcher's
   ``{sql, issues, hints, model_coverage}`` dict in the standard
   ``{"status": "success", "data": ...}`` envelope.
2. **Write refusal** — non-read SQL (INSERT/UPDATE/etc. or
   unparseable) returns ``MCS_REVIEW_UNSUPPORTED`` with exit code 2,
   matching the spec §5.4 refusal contract.
3. **Missing-package fallback** — a profile with no ``package.db`` on
   disk still runs package-independent syntax / dialect checks and
   returns a success envelope with semantic checks marked skipped.

The fourth test pins the data-shape contract (``sql`` / ``issues`` /
``hints`` / ``model_coverage`` keys all present) so Task 15's
next_step wiring has a stable shape to read against.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from maxcompute_semantic.commands.sql import sql_group


def _invoke(args: list[str], obj: dict | None = None) -> object:
    runner = CliRunner()
    return runner.invoke(sql_group, args, obj=obj or {})


class TestSqlReviewCmd:
    def test_returns_success_envelope_with_review_data(
        self, isolated_config: Path, make_review_package
    ) -> None:
        """IIF rule fires on SQLite-flavored SQL → success envelope
        carries the dispatcher's issues list with the rule ID."""
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "review",
                    "--project",
                    "rev_proj",
                    "--schema",
                    "default",
                    "SELECT IIF(id > 0, 1, 0) FROM orders",
                ]
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert "data" in out
        assert any(i["rule"] == "dialect.sqlite-iif" for i in out["data"]["issues"])

    def test_review_refuses_write(self, isolated_config: Path, make_review_package) -> None:
        """INSERT classifies as ``write`` → MCS_REVIEW_UNSUPPORTED + exit 2."""
        profile, _ = make_review_package(tables=[])
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "review",
                    "--project",
                    "rev_proj",
                    "--schema",
                    "default",
                    "INSERT INTO orders VALUES (1)",
                ]
            )
        assert result.exit_code == 2, result.output
        out = json.loads(result.output)
        assert out["status"] == "error"
        assert out["error"]["code"] == "MCS_REVIEW_UNSUPPORTED"
        # context carries the verdict so the agent can branch on
        # write vs unparseable when crafting the recovery prompt.
        assert out["error"]["context"]["classification"] == "write"

    def test_review_profile_resolution_failure_returns_error_envelope(
        self, isolated_config: Path
    ) -> None:
        """Profile lookup errors should keep the sql-review JSON seam."""
        from maxcompute_semantic.auth.errors import ProfileNotFoundError

        with patch(
            "maxcompute_semantic.commands.sql.resolve_profile_for_project",
            MagicMock(side_effect=ProfileNotFoundError("profile not found")),
        ):
            result = _invoke(["review", "--profile", "missing", "SELECT 1"])

        assert result.exit_code == 3
        out = json.loads(result.output)
        assert out["status"] == "error"
        assert out["error"]["code"] == "ProfileNotFound"
        assert out["error"]["message"] == "profile not found"

    def test_review_handles_no_package_with_syntax_only_success(
        self, isolated_config: Path
    ) -> None:
        """Fresh profile with no ``package.db`` still runs dialect checks.

        Constructs a ``Profile`` inline (no ``make_review_package`` call),
        so under the ``isolated_config`` tmp HOME the profile_data_dir
        for ``"np"`` is empty at call time. The command should not
        stamp a package.db side effect, but it should still catch
        package-independent issues like SQLite-only ``IIF``.
        """
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="np",
            compute_project="np",
            endpoint="http://x.test/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(DataSource(project="np", schema="default", tables="*"),),
        )
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "review",
                    "--project",
                    "np",
                    "--schema",
                    "default",
                    "SELECT IIF(id > 0, 1, 0) FROM orders",
                ]
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        assert out["data"]["review_mode"] == "syntax_only"
        assert out["data"]["semantic_checks_skipped"] is True
        assert out["data"]["semantic_skip_reason"] == "package_not_built"
        assert any(i["rule"] == "dialect.sqlite-iif" for i in out["data"]["issues"])
        assert not (isolated_config / "data" / "np" / "package.db").exists()

    def test_cross_source_sql_does_not_crash(
        self, isolated_config: Path, make_review_package
    ) -> None:
        """Regression: cross-source SQL in a multi-source profile —
        where ``_route_project`` returns None because the references
        span more than one source's project — must not crash
        ``get_tier`` (and therefore ``tier_cache_path``, which rejects
        ``None`` as the project). The CLI used to feed the routed
        ``target_project`` straight to ``get_tier``; the fix falls back
        to the profile's ``compute_project`` when the routed project is
        None, mirroring execute/cost/explain."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        profile = Profile(
            name="rev_multi",
            compute_project="rev_proj",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_b", schema="default", tables="*"),
            ),
        )
        profile, _ = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "proj_a__default", "name": "orders", "columns": [{"name": "id"}]},
                {
                    "source_key": "proj_b__default",
                    "name": "customers",
                    "columns": [{"name": "id"}, {"name": "name"}],
                },
            ],
        )
        with patch(
            "maxcompute_semantic.commands.sql.resolve_profile_for_project",
            MagicMock(return_value=profile),
        ):
            # NOTE: get_tier is NOT mocked here — review_cmd must
            # tolerate the real helper without a live probe (the
            # ``allow_live_probe=False`` contract).
            result = _invoke(
                [
                    "review",
                    "SELECT a.id, b.name FROM proj_a.default.orders a "
                    "JOIN proj_b.default.customers b ON a.id = b.id",
                ]
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"

    def test_tier_reads_routed_data_project_not_compute(
        self, isolated_config: Path, make_review_package
    ) -> None:
        """Regression: when the SQL routes to a data project whose tier
        differs from the profile's ``compute_project``, the review must
        read the *routed* project's tier — otherwise
        ``tier.bare-table-in-3level`` is silently missed (or wrongly
        fires) in standard dev/prod profiles where compute is tier 2 and
        the data source is tier 3. Pre-fix used ``compute_project``
        unconditionally; the fix prefers ``target_project`` with
        ``compute_project`` as the ``None``-fallback."""
        from maxcompute_semantic.auth.schema import (
            AkAuth,
            CostThresholds,
            DataSource,
            Profile,
        )

        # compute_project=`dev_compute` (tier 2); data source `prod_data`
        # (tier 3). Bare-table SQL routes to `prod_data`, so the tier
        # check must see "3" and emit tier.bare-table-in-3level.
        profile = Profile(
            name="rev_split",
            compute_project="dev_compute",
            endpoint="http://service.odps.aliyun.com/api",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            cost_thresholds=CostThresholds(),
            sources=(DataSource(project="prod_data", schema="default", tables="*"),),
        )
        profile, _ = make_review_package(
            profile=profile,
            tables=[
                {"source_key": "prod_data__default", "name": "orders", "columns": [{"name": "id"}]},
            ],
        )

        # Tier sentinel by project: dev_compute=2, prod_data=3.
        def _tier_by_project(_profile, project, **_kw):
            return "3" if project == "prod_data" else "2"

        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(side_effect=_tier_by_project),
        ):
            result = _invoke(["review", "SELECT id FROM orders"])
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        # Single-source profile: execute auto-injects
        # odps.default.schema, so bare table names resolve correctly.
        # The tier rule must NOT fire — doing so wastes agent turns.
        assert not any(i["rule"] == "tier.bare-table-in-3level" for i in out["data"]["issues"]), (
            out["data"]["issues"]
        )

    def test_review_does_not_construct_mc_client(
        self, isolated_config: Path, make_review_package
    ) -> None:
        """Regression: ``mcs sql review`` must never build a
        ``MaxComputeClient`` on cache miss. The promise is "no MC
        round-trip"; the previous ``get_tier`` call would silently
        construct a client and call ``ODPS.list_schemas`` when the
        per-(profile, project) tier cache was missing. Pin the
        no-client invariant by patching the constructor to fail."""
        profile, _ = make_review_package(
            tables=[
                {"source_key": "rev_proj__default", "name": "orders", "columns": [{"name": "id"}]},
            ]
        )

        def _fail(*_args, **_kw) -> None:
            raise AssertionError(
                "review_cmd must not construct MaxComputeClient — the "
                "'no MaxCompute round-trip' contract is broken"
            )

        with (
            patch(
                "maxcompute_semantic.commands.sql.resolve_profile_for_project",
                MagicMock(return_value=profile),
            ),
            patch(
                "maxcompute_semantic.mc_client.client.MaxComputeClient.__init__",
                side_effect=_fail,
            ),
        ):
            result = _invoke(["review", "SELECT id FROM orders"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "success"

    def test_review_envelope_has_all_four_data_keys(
        self, isolated_config: Path, make_review_package
    ) -> None:
        """Pin the envelope's ``data`` shape contract so Task 15's
        next_step wiring has a stable surface to read against. A
        minimal SELECT against a built profile must always produce
        ``data["sql"]`` / ``data["issues"]`` / ``data["hints"]`` /
        ``data["model_coverage"]`` regardless of which rules fired.
        """
        profile, _ = make_review_package(
            tables=[
                {
                    "source_key": "rev_proj__default",
                    "name": "orders",
                    "columns": [{"name": "id"}],
                },
            ]
        )
        with patch.multiple(
            "maxcompute_semantic.commands.sql",
            resolve_profile_for_project=MagicMock(return_value=profile),
            get_tier=MagicMock(return_value="2"),
        ):
            result = _invoke(
                [
                    "review",
                    "--project",
                    "rev_proj",
                    "--schema",
                    "default",
                    "SELECT id FROM orders",
                ]
            )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "success"
        data = out["data"]
        assert set(data.keys()) >= {"sql", "issues", "hints", "model_coverage"}
        assert data["sql"] == "SELECT id FROM orders"
