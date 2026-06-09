from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    Profile,
)
from maxcompute_semantic.commands._sql_name import sql_name


def _profile(*sources: DataSource) -> Profile:
    return Profile(
        name="test",
        compute_project="proj",
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=sources,
    )


class TestSqlName:
    def test_single_source_returns_bare_name(self) -> None:
        p = _profile(DataSource(project="proj", schema="default"))
        assert sql_name("orders", "proj__default", p) == "orders"

    def test_zero_sources_returns_bare_name(self) -> None:
        p = _profile()
        assert sql_name("orders", "proj__default", p) == "orders"

    def test_multi_source_returns_fqn(self) -> None:
        p = _profile(
            DataSource(project="proj", schema="schema_a"),
            DataSource(project="proj", schema="schema_b"),
        )
        assert sql_name("orders", "proj__schema_a", p) == "proj.schema_a.orders"

    def test_multi_source_cross_project(self) -> None:
        p = _profile(
            DataSource(project="prod", schema="warehouse"),
            DataSource(project="crm", schema="public"),
        )
        assert sql_name("users", "crm__public", p) == "crm.public.users"

    def test_source_key_without_separator_falls_back(self) -> None:
        p = _profile(
            DataSource(project="a", schema="s1"),
            DataSource(project="b", schema="s2"),
        )
        assert sql_name("orders", "legacy_key", p) == "orders"

    def test_profile_update_single_to_multi(self) -> None:
        """sql_name reflects current profile state, not cached."""
        single = _profile(DataSource(project="proj", schema="default"))
        assert sql_name("orders", "proj__default", single) == "orders"

        multi = _profile(
            DataSource(project="proj", schema="default"),
            DataSource(project="proj", schema="staging"),
        )
        assert sql_name("orders", "proj__default", multi) == "proj.default.orders"
