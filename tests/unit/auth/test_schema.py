# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for auth/schema.py — Profile + DataSource + TableSpec
dataclasses and their validators.

The Profile shape is a ``(compute_project, sources:
tuple[DataSource, ...])`` pair where each ``DataSource`` carries
its own ``(project, schema, tables)`` triple and each ``TableSpec``
inside a source's enumerated table list carries optional column
whitelist / blacklist.
"""

from __future__ import annotations

import pytest

from maxcompute_semantic.auth.errors import InvalidProfileError
from maxcompute_semantic.auth.schema import (
    AkAuth,
    CostThresholds,
    DataSource,
    ProcessAuth,
    Profile,
    TableSpec,
)


def _ak() -> AkAuth:
    """The canonical test-AK pair (literal values, not env-refs).
    The LTAI- prefix is the Aliyun-AccessKey convention; the
    test_secret is non-functional placeholder text."""
    return AkAuth(access_key_id="FooAKID", access_key_secret="test_secret_redacted")


def _bare_source(project: str = "data_proj", schema: str = "default") -> DataSource:
    """A canonical 'wildcard tables, default schema' DataSource for
    tests that need *some* source but don't care about the specific
    table list."""
    return DataSource(project=project, schema=schema, tables="*")


def _bare_profile(
    name: str = "test-profile",
    compute_project: str = "compute_proj",
    endpoint: str = "https://service.cn-shanghai.maxcompute.aliyun.com/api",
    sources: tuple[DataSource, ...] | None = None,
    auth: AkAuth | ProcessAuth | None = None,
) -> Profile:
    """Convenience factory. ``sources=None`` (the default) seeds a
    single wildcard source; ``sources=()`` explicitly leaves it
    empty (the legal mid-wizard state). The Profile constructor's
    own ``__post_init__`` tuple-coerces a list input, so tests that
    want the list-input → tuple-output path can pass a plain list
    (the type-checker complains; the runtime is fine)."""
    if sources is None:
        sources = (_bare_source(),)
    if auth is None:
        auth = _ak()
    return Profile(
        name=name,
        compute_project=compute_project,
        endpoint=endpoint,
        auth=auth,
        sources=sources,
    )


# ── Auth-type defaults ──────────────────────────────────────────────────────


def test_process_auth_default_timeout() -> None:
    a = ProcessAuth(command="ncs create credential odpsuser --employee-id 1 -o template -t odpscmd")
    assert a.timeout == 60


def test_cost_thresholds_defaults() -> None:
    t = CostThresholds()
    assert t.confirm_cny == 10.0
    assert t.blocked_cny == 100.0


def test_cost_thresholds_is_enabled_default() -> None:
    """Default thresholds (10 / 100 CNY) are enabled."""
    assert CostThresholds().is_enabled() is True


def test_cost_thresholds_is_enabled_explicit_disabled() -> None:
    """Explicit enabled=False disables the gate."""
    assert CostThresholds(enabled=False).is_enabled() is False


def test_cost_thresholds_zero_thresholds_warn_when_enabled() -> None:
    """Non-positive thresholds with enabled=True emit a warning."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        CostThresholds(confirm_cny=0.0, blocked_cny=0.0, enabled=True)
        assert len(w) == 1
        assert "misconfiguration" in str(w[0].message)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        CostThresholds(confirm_cny=-1.0, blocked_cny=-1.0, enabled=True)
        assert len(w) == 1


# ── TableSpec: column whitelist / blacklist ─────────────────────────────────


class TestTableSpec:
    def test_bare_name_validates_with_no_column_filter(self) -> None:
        ts = TableSpec(name="orders")
        ts.validate()
        assert ts.columns is None
        assert ts.columns_exclude == ()

    def test_columns_whitelist_validates(self) -> None:
        ts = TableSpec(name="orders", columns=("id", "customer_id", "amount", "dt"))
        ts.validate()

    def test_columns_exclude_blacklist_validates(self) -> None:
        ts = TableSpec(
            name="employees",
            columns_exclude=("ssn", "salary", "bank_account"),
        )
        ts.validate()

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(InvalidProfileError, match="non-empty string"):
            TableSpec(name="").validate()

    def test_whitespace_only_name_rejected(self) -> None:
        with pytest.raises(InvalidProfileError, match="non-empty string"):
            TableSpec(name="   \t  ").validate()

    def test_columns_and_columns_exclude_are_mutually_exclusive(self) -> None:
        """The two column-filter forms are deliberately exclusive: a
        whitelist already implies "nothing else surfaces", and an
        explicit blacklist on top would be ambiguous. The yaml
        grammar enforces this with the "pick one" rule (spec §5)."""
        ts = TableSpec(name="t", columns=("a", "b"), columns_exclude=("c",))
        with pytest.raises(InvalidProfileError, match="mutually exclusive"):
            ts.validate()

    def test_duplicate_column_in_whitelist_rejected(self) -> None:
        ts = TableSpec(name="orders", columns=("id", "amount", "id"))
        with pytest.raises(InvalidProfileError, match="duplicate column"):
            ts.validate()

    def test_duplicate_column_in_blacklist_rejected(self) -> None:
        ts = TableSpec(name="hr_table", columns_exclude=("ssn", "ssn"))
        with pytest.raises(InvalidProfileError, match="duplicate column"):
            ts.validate()

    def test_non_string_column_entry_rejected(self) -> None:
        ts = TableSpec(name="t", columns=("id", 42))  # type: ignore[arg-type]
        with pytest.raises(InvalidProfileError, match="non-string or empty"):
            ts.validate()

    def test_empty_string_column_entry_rejected(self) -> None:
        ts = TableSpec(name="t", columns=("id", "", "name"))
        with pytest.raises(InvalidProfileError, match="non-string or empty"):
            ts.validate()


# ── DataSource: (project, schema, tables) triple ────────────────────────────


class TestDataSource:
    def test_default_schema_is_the_literal_default(self) -> None:
        """2-level data projects use the literal string ``"default"``
        for the schema field, matching MaxCompute's 2→3 upgrade
        flat-table landing-zone convention. The dataclass default
        reflects this."""
        ds = DataSource(project="bare_proj")
        ds.validate()
        assert ds.schema == "default"

    def test_wildcard_tables_is_the_default(self) -> None:
        ds = DataSource(project="warehouse", schema="sales")
        ds.validate()
        assert ds.tables == "*"
        assert ds.is_wildcard()
        assert ds.table_names() == ()

    def test_enumerated_tables_via_tablespec_tuple(self) -> None:
        ds = DataSource(
            project="warehouse",
            schema="sales",
            tables=(TableSpec(name="orders"), TableSpec(name="customers")),
        )
        ds.validate()
        assert not ds.is_wildcard()
        assert ds.table_names() == ("orders", "customers")

    def test_bare_string_list_coerces_to_tablespecs(self) -> None:
        """The yaml deserializer hands us a list of strings for the
        ``tables: [orders, customers]`` form. DataSource's
        ``__post_init__`` runs ``_coerce_tablespec_iter`` to lift
        each bare string into a TableSpec with no column scoping.
        Programmatic callers (the wizard, tests) get the same
        convenience."""
        ds = DataSource(
            project="p",
            schema="s",
            tables=["orders", "customers", "line_items"],  # type: ignore[arg-type]
        )
        ds.validate()
        assert isinstance(ds.tables, tuple)
        assert all(isinstance(t, TableSpec) for t in ds.tables)
        assert ds.table_names() == ("orders", "customers", "line_items")
        for ts in ds.tables:
            assert ts.columns is None
            assert ts.columns_exclude == ()

    def test_mixed_strings_and_tablespecs_in_list(self) -> None:
        """A list whose items are a mix of bare table names (no
        columns) and TableSpec objects (with columns) — the
        coercion preserves the TableSpec object as-is and lifts
        each bare string."""
        ts_columns = TableSpec(name="orders", columns=("id", "amount", "dt"))
        ts_exclude = TableSpec(name="customers", columns_exclude=("ssn",))
        ds = DataSource(
            project="p",
            schema="s",
            tables=[ts_columns, "products", ts_exclude, "audit_log"],  # type: ignore[arg-type]
        )
        ds.validate()
        # Order is preserved through the coercion.
        assert ds.table_names() == ("orders", "products", "customers", "audit_log")
        by_name = {t.name: t for t in ds.tables}
        assert by_name["orders"].columns == ("id", "amount", "dt")
        assert by_name["customers"].columns_exclude == ("ssn",)
        # Bare-string entries get TableSpecs with no column filter.
        assert by_name["products"].columns is None
        assert by_name["products"].columns_exclude == ()
        assert by_name["audit_log"].columns is None

    def test_list_with_non_string_non_tablespec_entry_rejected(self) -> None:
        """The coercion path's "must be a string or a TableSpec"
        rule. The yaml grammar (spec §5) is bare-name-string or
        ``{name, columns | columns_exclude}`` dict — the
        deserializer hands the dict-form to a constructor that
        produces a TableSpec, and anything that isn't a string or
        a TableSpec at the post-init coercion is a programming
        error worth flagging clearly."""
        with pytest.raises(InvalidProfileError, match="string or a TableSpec"):
            DataSource(project="p", schema="s", tables=[42])  # type: ignore[arg-type]

    def test_empty_project_rejected(self) -> None:
        with pytest.raises(InvalidProfileError, match="project must be a non-empty"):
            DataSource(project="", schema="s").validate()

    def test_whitespace_only_project_rejected(self) -> None:
        with pytest.raises(InvalidProfileError, match="project must be a non-empty"):
            DataSource(project="  \t ", schema="s").validate()

    def test_empty_schema_rejected(self) -> None:
        with pytest.raises(InvalidProfileError, match="schema is empty"):
            DataSource(project="p", schema="").validate()

    def test_non_wildcard_string_tables_rejected(self) -> None:
        """The only legal string value for ``tables`` is the
        wildcard ``"*"``. Anything else is a typo and the
        deserializer can't disambiguate it from a malformed
        bare-table-name-list, so the validator flags it."""
        ds = DataSource(project="p", schema="s", tables="not_a_wildcard")
        with pytest.raises(InvalidProfileError, match="wildcard"):
            ds.validate()

    def test_empty_tables_tuple_rejected(self) -> None:
        """``tables: []`` is a footgun — the source contributes no
        tables to the build, but the profile saves silently. The
        validator rejects it and points the caller at
        ``mcs meta list-tables`` so agents constructing
        ``--from-spec`` JSON learn the right discovery flow rather
        than blindly emitting an empty list."""
        ds = DataSource(project="p", schema="s", tables=())
        with pytest.raises(InvalidProfileError, match="empty list") as excinfo:
            ds.validate()
        assert "mcs meta list-tables" in excinfo.value.remediation
        assert "--project p" in excinfo.value.remediation
        assert "--schema s" in excinfo.value.remediation

    def test_non_string_non_list_tables_rejected(self) -> None:
        """A tables value that is neither a string nor a list is a
        type error from the yaml. The dataclass validates
        defensively."""
        ds = DataSource.__new__(DataSource)
        object.__setattr__(ds, "project", "p")
        object.__setattr__(ds, "schema", "s")
        # Bypass the normal __init__ + __post_init__ coercion to
        # land an integer in the ``tables`` field, so the validator's
        # type-check branch fires.
        object.__setattr__(ds, "tables", 42)
        with pytest.raises(InvalidProfileError, match="neither the wildcard"):
            ds.validate()

    def test_duplicate_table_name_in_enumerated_list_rejected(self) -> None:
        ds = DataSource(
            project="p",
            schema="s",
            tables=(TableSpec(name="orders"), TableSpec(name="orders")),
        )
        with pytest.raises(InvalidProfileError, match="duplicate table name"):
            ds.validate()

    def test_invalid_tablespec_inside_source_propagates(self) -> None:
        """A bad TableSpec (mutex columns/columns_exclude) inside a
        source's tables list causes the source's validator to
        bubble the inner error up — the same error message the
        TableSpec validator itself emits, since the wrapping is
        just a per-entry ``ts.validate()`` call."""
        bad = TableSpec(name="t", columns=("a",), columns_exclude=("b",))
        ds = DataSource(project="p", schema="s", tables=(bad,))
        with pytest.raises(InvalidProfileError, match="mutually exclusive"):
            ds.validate()

    def test_source_key_format_is_double_underscore_separator(self) -> None:
        """The source-key disambiguator for the PackageDB
        ``(source_project, source_schema)`` index and the export
        tarball's ``data/<source-key>/`` subdir name. MaxCompute
        names don't contain ``__`` by convention, so the
        double-underscore separator is collision-free in practice
        and filesystem-portable."""
        ds = DataSource(project="finance_warehouse", schema="sales")
        assert ds.source_key() == "finance_warehouse__sales"
        # 2-level / upgrade-path form:
        ds2 = DataSource(project="upgraded_2to3", schema="default")
        assert ds2.source_key() == "upgraded_2to3__default"

    def test_qualified_three_segment_form(self) -> None:
        """The agent's SQL uses 3-segment qualified table names
        ``project.schema.table`` per spec §10. The helper produces
        that form deterministically."""
        ds = DataSource(project="finance_warehouse", schema="sales")
        assert ds.qualified("orders") == "finance_warehouse.sales.orders"
        # 2-level / default-schema form keeps the dot separator
        # uniformly — the 3-level compute project's parser accepts
        # ``<2-level-proj>.default.<table>`` against a 2-level data
        # source per the user-verified cross-tier behavior (spec §10).
        ds2 = DataSource(project="bare_proj", schema="default")
        assert ds2.qualified("audit_log") == "bare_proj.default.audit_log"

    # ── qualified_for_connection / connection_hints matrix ─────────────
    #
    # The 4 rows below match the table in the docstring of
    # ``DataSource.qualified_for_connection``. Empirically validated
    # against a live MaxCompute matrix on 2026-05-22 with a paired
    # 3-level test project and two 2-level test projects.

    def test_qualified_for_connection_3level_conn_uses_full_3segment(self) -> None:
        """3-level connections accept 3-segment FQNs universally — the
        ``project.schema.table`` shape works for both same-project and
        cross-project reads. Source schema (default vs non-default) is
        immaterial; the connection's parser requires the full form."""
        ds = DataSource(project="data_proj", schema="sales")
        assert (
            ds.qualified_for_connection("orders", conn_tier="3", compute_project="data_proj")
            == "data_proj.sales.orders"
        )
        assert (
            ds.qualified_for_connection("orders", conn_tier="3", compute_project="other_compute")
            == "data_proj.sales.orders"
        )
        assert ds.connection_hints(conn_tier="3") == {}

    def test_qualified_for_connection_2level_conn_same_project_uses_bare(self) -> None:
        """A 2-level connection reading its own project's tables: the
        bare table name is the only form the parser accepts. 2-segment
        ``project.table`` would be misread as ``schema.table`` and
        3-segment is rejected entirely."""
        ds = DataSource(project="compute_proj", schema="default")
        assert (
            ds.qualified_for_connection("orders", conn_tier="2", compute_project="compute_proj")
            == "orders"
        )
        assert ds.connection_hints(conn_tier="2") == {}

    def test_qualified_for_connection_2level_xproj_default_schema_uses_2segment(self) -> None:
        """2-level conn cross-reading another project's default schema:
        ``other_proj.table`` is the form the 2-level parser accepts.
        Works for both 2-level data sources (their flat namespace IS
        ``default``) and 3-level sources whose target schema happens to
        be ``default`` (the 2-segment form addresses that schema with
        no extra hint)."""
        ds_2level = DataSource(project="other_proj", schema="default")
        assert (
            ds_2level.qualified_for_connection(
                "orders", conn_tier="2", compute_project="compute_proj"
            )
            == "other_proj.orders"
        )
        assert ds_2level.connection_hints(conn_tier="2") == {}

    def test_qualified_for_connection_2level_xproj_non_default_schema_uses_3segment_with_hint(
        self,
    ) -> None:
        """2-level conn cross-reading a 3-level source's non-default
        schema: the only addressing form the engine accepts is the
        full 3-segment FQN, but the 2-level parser only opens up to
        3-segment shapes when ``odps.namespace.schema=true`` is set on
        the session. ``connection_hints`` returns that hint so the
        caller passes both at the same call site."""
        ds = DataSource(project="src_proj", schema="other_schema")
        assert (
            ds.qualified_for_connection("account", conn_tier="2", compute_project="conn_proj")
            == "src_proj.other_schema.account"
        )
        assert ds.connection_hints(conn_tier="2") == {"odps.namespace.schema": "true"}


# ── Profile: top-level identity + sources list ──────────────────────────────


class TestProfile:
    def test_minimal_with_one_wildcard_source_validates(self) -> None:
        p = _bare_profile()
        p.validate()
        assert p.name == "test-profile"
        assert p.compute_project == "compute_proj"
        assert len(p.sources) == 1
        assert p.sources[0].project == "data_proj"
        assert p.sources[0].schema == "default"
        assert p.sources[0].is_wildcard()

    def test_empty_sources_is_legal_mid_wizard_state(self) -> None:
        """A profile with no sources is the mid-wizard state where
        the user has finished the compute/endpoint/auth prompts but
        hasn't run the source-picker loop yet. The validator accepts
        this so the wizard can save the partial profile before the
        source-picker step. Consumers that *need* sources (``mcs
        build``, ``mcs meta list-tables``) raise their own
        "no sources configured" errors when they hit an empty list.
        """
        p = Profile(
            name="empty-sources",
            compute_project="cp",
            endpoint="https://x.example.com",
            auth=_ak(),
            sources=(),
        )
        p.validate()
        assert p.sources == ()

    def test_multi_source_full_shape(self) -> None:
        """The canonical multi-source profile that the wizard's
        source picker produces. Three sources spanning two
        underlying projects: warehouse.sales (wildcard tables),
        warehouse.hr (column-scoped per-table list with mixed
        whitelist / blacklist), and upgraded_2to3 (a 2-level project
        whose ``default`` schema name is the upgrade-path
        convention)."""
        p = Profile(
            name="multi-source",
            compute_project="my_compute_proj",
            endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
            auth=_ak(),
            sources=(
                DataSource(project="warehouse", schema="sales", tables="*"),
                DataSource(
                    project="warehouse",
                    schema="hr",
                    tables=(
                        TableSpec(name="employees", columns=("emp_id", "name", "dept_id")),
                        TableSpec(name="payroll", columns_exclude=("ssn", "bank_account")),
                        TableSpec(name="orgchart"),
                    ),
                ),
                DataSource(project="upgraded_2to3", schema="default", tables=("audit_log",)),  # type: ignore[arg-type]
            ),
        )
        p.validate()
        assert p.source_keys() == (
            "warehouse__sales",
            "warehouse__hr",
            "upgraded_2to3__default",
        )

    def test_name_too_short_rejected(self) -> None:
        p = _bare_profile(name="ab")
        with pytest.raises(InvalidProfileError, match="profile name"):
            p.validate()

    def test_name_invalid_chars_rejected(self) -> None:
        p = _bare_profile(name="foo/bar")
        with pytest.raises(InvalidProfileError, match="profile name"):
            p.validate()

    def test_empty_compute_project_rejected(self) -> None:
        """The AK's home project (where SQL job-instances run and
        where billing accrues) is required. Without it the pyodps
        client has nothing to connect to."""
        p = _bare_profile(compute_project="")
        with pytest.raises(InvalidProfileError, match="compute_project is empty"):
            p.validate()

    def test_whitespace_only_compute_project_rejected(self) -> None:
        p = _bare_profile(compute_project=" \t ")
        with pytest.raises(InvalidProfileError, match="compute_project is empty"):
            p.validate()

    def test_endpoint_must_be_http_or_https(self) -> None:
        p = _bare_profile(endpoint="ftp://x.example.com")
        with pytest.raises(InvalidProfileError, match="endpoint must start with http"):
            p.validate()

    def test_ak_auth_empty_id_rejected(self) -> None:
        p = _bare_profile(auth=AkAuth(access_key_id="", access_key_secret="x"))
        with pytest.raises(InvalidProfileError, match="access_key_id"):
            p.validate()

    def test_ak_auth_empty_secret_rejected(self) -> None:
        p = _bare_profile(auth=AkAuth(access_key_id="x", access_key_secret=""))
        with pytest.raises(InvalidProfileError, match="access_key_secret"):
            p.validate()

    def test_process_auth_empty_command_rejected(self) -> None:
        p = _bare_profile(auth=ProcessAuth(command=""))
        with pytest.raises(InvalidProfileError, match="auth.command is empty"):
            p.validate()

    def test_process_auth_timeout_out_of_range(self) -> None:
        p = _bare_profile(auth=ProcessAuth(command="ncs ...", timeout=0))
        with pytest.raises(InvalidProfileError, match="auth.timeout"):
            p.validate()
        p2 = _bare_profile(auth=ProcessAuth(command="ncs ...", timeout=601))
        with pytest.raises(InvalidProfileError, match="auth.timeout"):
            p2.validate()

    def test_cost_thresholds_confirm_must_be_strictly_less_than_blocked(self) -> None:
        p = Profile(
            name="bad-cost",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(_bare_source(),),
            cost_thresholds=CostThresholds(confirm_cny=200.0, blocked_cny=100.0),
        )
        with pytest.raises(InvalidProfileError, match="strictly less"):
            p.validate()

    def test_profile_accepts_disabled_cost_thresholds(self) -> None:
        """When the gate is explicitly disabled via enabled=False, the
        validator must not raise even though confirm_cny >= blocked_cny
        would normally be rejected — the gate is off so the ordering
        is moot."""
        p = Profile(
            name="disabled-cost",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(_bare_source(),),
            cost_thresholds=CostThresholds(enabled=False),
        )
        p.validate()  # no raise

    def test_duplicate_source_key_rejected(self) -> None:
        """Two sources with the same (project, schema) collapse to
        the same source-key — the PackageDB's per-row uniqueness
        constraint would conflict, so the Profile validator catches
        it at config time."""
        p = Profile(
            name="dup-src",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(
                DataSource(project="warehouse", schema="sales", tables="*"),
                DataSource(
                    project="warehouse",
                    schema="sales",
                    tables=(TableSpec(name="orders_only"),),
                ),
            ),
        )
        with pytest.raises(InvalidProfileError, match="duplicate data source"):
            p.validate()

    def test_invalid_source_propagates_through_profile_validate(self) -> None:
        bad_source = DataSource(project="", schema="s")
        p = Profile(
            name="bad-source",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(bad_source,),
        )
        with pytest.raises(InvalidProfileError, match="non-empty"):
            p.validate()

    def test_non_datasource_entry_in_sources_rejected(self) -> None:
        """A programmatic caller that passes a non-DataSource thing
        into the sources list (e.g. a tuple of strings) gets a
        clear "not a DataSource" type error rather than a
        confusing AttributeError downstream."""
        p = Profile(
            name="bad-type",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=("not-a-datasource",),  # type: ignore[arg-type]
        )
        with pytest.raises(InvalidProfileError, match="not a DataSource"):
            p.validate()

    def test_post_init_coerces_list_of_sources_to_tuple(self) -> None:
        """The Profile dataclass is frozen, so its fields are stored
        as tuples internally for hashability. The ``__post_init__``
        accepts a list as a programmatic-caller convenience and
        converts to a tuple in place via ``object.__setattr__`` (the
        standard frozen-dataclass-post-init pattern)."""
        sources_list = [
            DataSource(project="a", schema="x"),
            DataSource(project="b", schema="y"),
        ]
        p = Profile(
            name="list-srcs",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=sources_list,  # type: ignore[arg-type]
        )
        assert isinstance(p.sources, tuple)
        assert len(p.sources) == 2
        # The list isn't aliased into the tuple — the dataclass
        # holds its own immutable copy.
        sources_list.append(DataSource(project="c", schema="z"))
        assert len(p.sources) == 2

    def test_source_by_key_internal_double_underscore_form(self) -> None:
        p = Profile(
            name="lookup-test",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(
                DataSource(project="alpha", schema="s1"),
                DataSource(project="beta", schema="s2"),
            ),
        )
        match = p.source_by_key("alpha__s1")
        assert match is not None
        assert match.project == "alpha"
        assert match.schema == "s1"

    def test_source_by_key_user_dot_form(self) -> None:
        """The user-facing ``<project>.<schema>`` dotted form is
        normalized to the internal ``<project>__<schema>`` source-key
        by ``source_by_key`` so the spec §6 user-facing dot syntax
        and the spec §3 internal storage key agree on the same
        identity for a given source."""
        p = Profile(
            name="dot-form",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(DataSource(project="warehouse", schema="sales"),),
        )
        match = p.source_by_key("warehouse.sales")
        assert match is not None
        assert match.project == "warehouse"
        assert match.schema == "sales"

    def test_source_by_key_returns_none_when_no_match(self) -> None:
        p = _bare_profile()
        assert p.source_by_key("nonexistent.schema") is None
        assert p.source_by_key("nonexistent__schema") is None

    def test_source_keys_preserves_insertion_order(self) -> None:
        """The order matters for the ``mcs profile show`` per-source
        section ordering and the export tarball's data/<src-key>/
        subdir layout — both follow the order the operator added
        sources in via the ``mcs profile create`` / ``mcs profile
        update`` source picker."""
        p = Profile(
            name="ordered",
            compute_project="cp",
            endpoint="https://x",
            auth=_ak(),
            sources=(
                DataSource(project="charlie", schema="x"),
                DataSource(project="alpha", schema="y"),
                DataSource(project="bravo", schema="z"),
            ),
        )
        assert p.source_keys() == ("charlie__x", "alpha__y", "bravo__z")


def test_description_defaults_empty():
    p = Profile(
        name="prof",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=_ak(),
    )
    assert p.description == ""
    p.validate()  # must not raise


def test_description_accepts_text():
    p = Profile(
        name="prof",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=_ak(),
        description="analyze monthly active users across orders and payments",
    )
    p.validate()
    assert p.description.startswith("analyze monthly")


def test_description_over_length_rejected():
    p = Profile(
        name="prof",
        compute_project="proj",
        endpoint="https://service.cn-shanghai.maxcompute.aliyun.com/api",
        auth=_ak(),
        description="x" * 4001,
    )
    with pytest.raises(InvalidProfileError, match="description"):
        p.validate()
