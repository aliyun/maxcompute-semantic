# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``commands/_table_resolve.py`` — the shared (source_key,
table) disambiguation helper used by ``mcs memory verify`` /
``mcs package apply`` to resolve user-supplied table references.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._table_resolve import (
    TableResolutionError,
    resolve_table_to_source,
)


def _single_source_profile() -> Profile:
    return Profile(
        name="test",
        compute_project="acme",
        endpoint="https://odps.endpoint",
        auth=AkAuth("ak", "sk"),
        sources=(DataSource(project="acme", schema="warehouse", tables="*"),),
    )


def _multi_source_profile() -> Profile:
    return Profile(
        name="test",
        compute_project="acme",
        endpoint="https://odps.endpoint",
        auth=AkAuth("ak", "sk"),
        sources=(
            DataSource(project="acme", schema="warehouse", tables="*"),
            DataSource(project="acme", schema="staging", tables="*"),
        ),
    )


def _make_db(tmp_path: Path) -> PackageDB:
    return PackageDB(tmp_path / "test.db")


def test_fqn_splits_into_source_key(tmp_path: Path) -> None:
    """``proj.schema.table`` form bypasses the DB and splits
    deterministically — useful when the agent already knows which
    source a table belongs to.
    """
    db = _make_db(tmp_path)
    sk, name = resolve_table_to_source("acme.warehouse.users", db)
    assert sk == "acme__warehouse"
    assert name == "users"


def test_fqn_empty_segment_errors(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with pytest.raises(TableResolutionError, match="empty segment"):
        resolve_table_to_source("acme..users", db)


def test_source_key_kwarg_short_circuits_lookup(tmp_path: Path) -> None:
    """Internal callers (the YAML batch path) pass ``source_key=`` to
    bypass the bare-name DB lookup. Not exposed as a CLI flag — agents
    disambiguate via the 3-segment FQN form instead.
    """
    db = _make_db(tmp_path)
    sk, name = resolve_table_to_source("users", db, source_key="acme__warehouse")
    assert sk == "acme__warehouse"
    assert name == "users"


def test_bare_name_unique_match_auto_resolves(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.upsert_table("acme__warehouse", "users", "h")
    sk, name = resolve_table_to_source("users", db)
    assert sk == "acme__warehouse"
    assert name == "users"


def test_bare_name_ambiguous_errors_with_candidates(tmp_path: Path) -> None:
    """When the same table name exists under two sources, bare-name
    resolve fails with the candidate source_keys listed in the
    remediation hint.
    """
    db = _make_db(tmp_path)
    db.upsert_table("acme__warehouse", "users", "h1")
    db.upsert_table("acme__staging", "users", "h2")
    with pytest.raises(TableResolutionError) as excinfo:
        resolve_table_to_source("users", db)
    assert "exists in 2 sources" in str(excinfo.value.message)
    assert "acme__warehouse" in str(excinfo.value.message)
    assert "acme__staging" in str(excinfo.value.message)


def test_zero_matches_single_source_profile_falls_back(tmp_path: Path) -> None:
    """Single-source profile + zero rows in package = use the active
    source. Lets ``mcs memory verify`` work before ``mcs build``.
    """
    db = _make_db(tmp_path)
    profile = _single_source_profile()
    sk, name = resolve_table_to_source("orders", db, profile=profile)
    assert sk == "acme__warehouse"
    assert name == "orders"


def test_zero_matches_multi_source_profile_errors(tmp_path: Path) -> None:
    """Multi-source profile + zero rows = ambiguous, must
    disambiguate. Silently picking sources[0] would be a footgun.
    """
    db = _make_db(tmp_path)
    profile = _multi_source_profile()
    with pytest.raises(TableResolutionError) as excinfo:
        resolve_table_to_source("orders", db, profile=profile)
    assert "not found" in str(excinfo.value.message)
    assert "2 sources" in str(excinfo.value.message)


def test_zero_matches_no_profile_errors(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with pytest.raises(TableResolutionError, match="not found"):
        resolve_table_to_source("orders", db)


def test_source_key_dot_table_form_resolves(tmp_path: Path) -> None:
    """``source_key.table`` form (what ``mcs show --tables`` displays)
    resolves directly when the row exists. Bypasses the bare-name
    lookup so the agent's first attempt — when it copies a table
    reference back out of the rendered package — lands without
    needing the 3-segment FQN form.
    """
    db = _make_db(tmp_path)
    db.upsert_table("acme__warehouse", "users", "h")
    sk, name = resolve_table_to_source("acme__warehouse.users", db)
    assert sk == "acme__warehouse"
    assert name == "users"


def test_source_key_dot_table_form_falls_through_when_no_match(tmp_path: Path) -> None:
    """When the LHS isn't a registered source_key, the dotted form
    falls through to the bare-name path so user typos (e.g.
    ``orders.customers`` written by mistake) still surface the
    standard "not found" remediation rather than silently succeeding
    or producing a misleading error.
    """
    db = _make_db(tmp_path)
    db.upsert_table("acme__warehouse", "users", "h")
    with pytest.raises(TableResolutionError, match="not found"):
        resolve_table_to_source("not_a_source.users", db)


def test_source_key_dot_table_form_unambiguous_when_table_name_unique(
    tmp_path: Path,
) -> None:
    """Agents reaching for the ``source_key.table`` form when the
    bare name would already be unambiguous still get the right
    source_key (not a different source that happens to also have a
    table named the bare RHS).
    """
    db = _make_db(tmp_path)
    db.upsert_table("acme__warehouse", "users", "h")
    db.upsert_table("acme__staging", "orders", "h2")
    sk, name = resolve_table_to_source("acme__staging.orders", db)
    assert sk == "acme__staging"
    assert name == "orders"
