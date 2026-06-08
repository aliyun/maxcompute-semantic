# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for sql_review tests.

Each fixture returns either a callable factory (when the test needs
to control rows) or a fully-built object (when the test wants a
boilerplate DB).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


def _mock_profile(name: str = "rev_proj", project: str = "rev_proj"):
    from maxcompute_semantic.auth.schema import (
        AkAuth,
        CostThresholds,
        DataSource,
        Profile,
    )

    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=(DataSource(project=project, schema="default", tables="*"),),
    )


def _mock_multi_source_profile(name: str = "rev_proj", project: str = "rev_proj"):
    from maxcompute_semantic.auth.schema import (
        AkAuth,
        CostThresholds,
        DataSource,
        Profile,
    )

    return Profile(
        name=name,
        compute_project=project,
        endpoint="http://service.odps.aliyun.com/api",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        cost_thresholds=CostThresholds(),
        sources=(
            DataSource(project=project, schema="schema_a", tables="*"),
            DataSource(project=project, schema="schema_b", tables="*"),
        ),
    )


@pytest.fixture
def mock_profile():
    return _mock_profile()


@pytest.fixture
def make_review_ctx() -> Callable[..., tuple]:
    """Return a callable building ``(PackageDB, ReviewContext)`` for a rule test.

    Centralises the boilerplate that every rule-test module would
    otherwise duplicate: open the package DB at *db_path*, then
    construct a ``ReviewContext`` that pins ``project`` /
    ``classification`` / ``schema_name`` to the values shared by every
    rule test (read-classification, no schema-name override, the
    profile's compute project). The caller still owns the lifecycle —
    close the returned ``db`` in a ``finally`` block.

    The ``tier`` kwarg defaults to ``"2"`` (matching the most common
    flat-namespace fixture) and can be overridden for tier-rule tests.
    """
    from maxcompute_semantic.build.storage import PackageDB
    from maxcompute_semantic.build.workload import extract_sql_evidence
    from maxcompute_semantic.commands.sql_review.types import ReviewContext

    def _make(sql, profile, db_path, *, tier: str = "2"):
        db = PackageDB(db_path)
        return db, ReviewContext(
            sql=sql,
            evidence=extract_sql_evidence(sql),
            profile=profile,
            project=profile.compute_project,
            schema_name=None,
            tier=tier,
            db=db,
            classification="read",
        )

    return _make


@pytest.fixture
def make_review_package(
    isolated_config: Path,
) -> Callable[..., tuple]:
    """Build a real ``PackageDB`` on disk and return ``(profile, db_path)``.

    The fixture is the single seam between rule code and the storage
    layer for sql_review tests; rule tests get a real SQLite-backed
    ``PackageDB`` populated with the rows they need rather than mocks.

    Keyword arguments:

    - ``profile`` — a ``Profile`` to back the package directory; if
      omitted, a default ``rev_proj`` profile is used.
    - ``tables`` — iterable of dicts shaped::

          {
              "source_key": "rev_proj__default",
              "name": "orders",
              "ai_context": "Customer order facts.",   # optional
              "columns": [
                  {
                      "name": "amount",
                      "type": "DECIMAL",                # optional, default STRING
                      "comment": "...",                 # optional
                      "is_partition": False,            # optional
                      # any of the following keys triggers
                      # ``set_column_semantics``:
                      "semantic_role": "measure",
                      "dim_type": ...,
                      "agg": "sum",
                      "id_type": ...,
                      "references_target": ...,
                      "semantic_description": ...,
                  },
                  ...
              ],
          }

    - ``joins`` — iterable of dicts forwarded as ``**kwargs`` into
      ``PackageDB.upsert_join`` (keys ``left_source_key``,
      ``left_table``, ``left_col``, ``right_source_key``,
      ``right_table``, ``right_col``, ``kind``, ``confidence``,
      and optional ``cardinality``).
    - ``memories`` — iterable of dicts forwarded as ``**kwargs`` into
      ``PackageDB.upsert_memory`` (keys ``kind``, ``payload_json``,
      ``retrieval_text``, optional ``tags_json``).

    Note: the fixture's external ergonomics keep ``"semantic_role"``
    as the dict key (matching the agent-facing annotation surface);
    it is translated to ``role=`` at the ``set_column_semantics``
    boundary inside the fixture.
    """
    from maxcompute_semantic._internal.paths import profile_data_dir
    from maxcompute_semantic.build.storage import PackageDB

    def _make(
        *,
        profile=None,
        tables=None,
        joins=None,
        memories=None,
    ):
        if profile is None:
            profile = _mock_profile()
        pdir = profile_data_dir(profile)
        pdir.mkdir(parents=True, exist_ok=True)
        db_path = pdir / "package.db"
        db = PackageDB(db_path)
        try:
            for t in tables or []:
                tid = db.upsert_table(t["source_key"], t["name"], "hash")
                if t.get("ai_context"):
                    db.set_table_ai_context(t["source_key"], t["name"], t["ai_context"])
                # Batch all column rows into one upsert_columns call.
                # PackageDB.upsert_columns deletes any column on the
                # table whose name is not in the provided list, so a
                # per-column loop would silently drop earlier columns.
                col_rows = [
                    {
                        "name": c["name"],
                        "type": c.get("type", "STRING"),
                        "comment": c.get("comment", ""),
                        "is_partition": int(c.get("is_partition", False)),
                    }
                    for c in t.get("columns", [])
                ]
                if col_rows:
                    db.upsert_columns(tid, col_rows)
                for c in t.get("columns", []):
                    semantic = {
                        k: c[k]
                        for k in (
                            "semantic_role",
                            "dim_type",
                            "agg",
                            "id_type",
                            "references_target",
                            "semantic_description",
                        )
                        if k in c
                    }
                    if semantic:
                        if "semantic_role" in semantic:
                            semantic["role"] = semantic.pop("semantic_role")
                        db.set_column_semantics(t["source_key"], t["name"], c["name"], **semantic)
            for j in joins or []:
                db.upsert_join(**j)
            for m in memories or []:
                db.upsert_memory(**m)
        finally:
            db.close()
        return profile, db_path

    return _make
