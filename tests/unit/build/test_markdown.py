"""Tests for build/markdown.py — MarkdownRenderer projection output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
from maxcompute_semantic.build.markdown import (
    MarkdownRenderer,
    build_role_groups,
    compact_column_entry,
)
from maxcompute_semantic.build.storage import PackageDB

_SK = "test_project__default"
_SOURCE = DataSource(project="test_project", schema="default", tables="*")


def _make_profile(name: str = "test-profile", schema: str = "default") -> Profile:
    return Profile(
        name=name,
        compute_project="test_project",
        endpoint="https://maxcompute.cn-shanghai.aliyuncs.com",
        auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
        sources=(DataSource(project="test_project", schema=schema, tables="*"),),
    )


def _make_db(tmp_path: Path) -> PackageDB:
    return PackageDB(tmp_path / "test.db")


def _populate_db(db: PackageDB) -> None:
    """Insert a card_games table with columns, one join, one UDF."""
    tid = db.upsert_table(_SK, "card_games", "abc123def")
    cols = [
        {
            "name": "game_id",
            "type": "STRING",
            "comment": "unique game identifier",
            "is_partition": 0,
            "is_enum": 0,
            "null_ratio": 0.01,
            "distinct_count": 5000,
        },
        {
            "name": "game_type",
            "type": "STRING",
            "comment": "game category",
            "is_partition": 0,
            "is_enum": 1,
            "sample_values_json": json.dumps(["card", "board", "dice"]),
            "null_ratio": 0.0,
            "distinct_count": 3,
        },
        {
            "name": "ds",
            "type": "STRING",
            "comment": "partition column",
            "is_partition": 1,
        },
    ]
    db.upsert_columns(tid, cols)
    # Insert the join's right-hand ``players`` table so the join is not
    # filtered as a loose_id phantom by render_overview/render_joins.
    # The existence check in those renderers drops any join whose
    # endpoint table is missing from the package — the historical
    # fixture wrote only ``card_games`` and relied on the unfiltered
    # legacy path; now both endpoints must be real tables.
    players_tid = db.upsert_table(_SK, "players", "abc123def")
    db.upsert_columns(
        players_tid,
        [
            {
                "name": "id",
                "type": "STRING",
                "comment": "player primary key",
                "is_partition": 0,
                "null_ratio": 0.0,
                "distinct_count": 100,
            },
        ],
    )
    db.upsert_join(_SK, "card_games", "player_id", _SK, "players", "id", "xxx_id", 0.85, "1:n")
    db.upsert_udf(
        "my_udf", "java", signature="my_udf(INT) -> INT", description="Custom aggregation"
    )


# ── Test 1: render_overview ─────────────────────────────────────────────────


class TestRenderOverview:
    def test_overview_has_frontmatter_project_tier_table_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_overview.md has YAML frontmatter with project, tier, tables count."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "out" / "markdown"
        # Write a .tier-level file for tier detection.
        tier_dir = tmp_path / "out"
        tier_dir.mkdir(parents=True, exist_ok=True)
        (tier_dir / ".tier-level").write_text("3", encoding="utf-8")

        # Override data_dir so _read_tier finds the .tier-level file.
        # For the test we just set tier manually by writing to profile data dir.
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path / "out"))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_overview()

        overview_path = output_dir / "_overview.md"
        assert overview_path.exists()
        content = overview_path.read_text(encoding="utf-8")

        # Verify frontmatter contains key fields.
        assert "project: test_project" in content
        assert "tier: 3-level" in content
        # ``_populate_db`` writes both ``card_games`` and ``players``
        # (the join's right-hand table — required so the join survives
        # the loose_id phantom filter in ``render_overview``).
        assert "tables: 2" in content
        assert "---" in content

    def test_overview_table_list_has_table_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_overview.md frontmatter has table names and column counts in sources."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "card_games" in content
        assert "columns_count:" in content

    def test_overview_entry_includes_columns_index_and_joins_to(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each per-source table entry surfaces a ``columns_index`` (the
        bare column names) and a ``joins_to`` list of first-hop partners.

        Without these the agent has to fan out one ``mcs show --table T``
        per table just to learn which column / partner is relevant for
        the question — that round-trip cost is what drives the missed-
        table failures in the benchmark (e.g. picking
        ``event.type`` for "category" without checking ``budget.category``,
        because ``budget`` looked like an unrelated table in the overview).
        Partition columns are excluded from the index since they're already
        called out in the ``partition`` field.
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        # columns_index is the user-data columns only (no partitions).
        assert "columns_index:" in content
        assert "game_id" in content
        assert "game_type" in content
        # joins_to is derived from the joins table — _populate_db wires
        # card_games.player_id <-> players.id with cardinality 1:n (left
        # is the "1" PK side, right is the "n" fan-out child), so
        # card_games's entry must list ``players via player_id [1:n]``
        # (own-side join column + cardinality from card_games's
        # perspective). players's entry sees the flipped cardinality
        # ``card_games via id [n:1]`` — own column is the PK, partner is
        # the "n" fan-out side. The own-column suffix lets the agent pick
        # the right JOIN key without a per-partner round-trip; the
        # cardinality marker tells the agent when DISTINCT is needed and
        # which side carries the entity count.
        assert "joins_to:" in content
        assert "players via player_id [1:n]" in content
        assert "card_games via id [n:1]" in content

    def test_overview_entry_includes_ai_context_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When annotate batch has written ``ai_context`` for a table the
        overview surfaces it inline so the agent doesn't pay a per-table
        round-trip just to read a one-line table description.

        Tables without ai_context (cold-start / pre-annotation) omit the
        field rather than emit an empty string — the YAML stays compact.
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        # Annotation writes ai_context on the tables row directly.
        db.set_table_ai_context(_SK, "card_games", "One row per card game session.")

        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "ai_context: One row per card game session." in content

    def test_overview_columns_index_carries_type_tag_for_non_string_cols(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``columns_index`` entries gain a ``:type`` suffix for non-STRING
        columns so the agent reaches for ``date(...)`` wraps / numeric
        casts from the always-loaded overview instead of paying a per-
        table ``mcs show`` round-trip just to learn that ``created_at``
        is a DATETIME.

        STRING columns stay bare — the default-text case carries no
        actionable hint, and emitting ``name:string`` for every column
        would bloat the overview without helping the agent decide.
        Parameterized types (``DECIMAL(10,2)``, ``ARRAY<STRING>``) drop
        their parens so the tag stays compact (``decimal`` / ``array``).
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "typed_table", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "amount", "type": "DECIMAL(18,2)", "comment": "", "is_partition": 0},
                {"name": "created_at", "type": "DATETIME", "comment": "", "is_partition": 0},
                {"name": "is_active", "type": "BOOLEAN", "comment": "", "is_partition": 0},
                {"name": "tags", "type": "ARRAY<STRING>", "comment": "", "is_partition": 0},
                {"name": "name", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "id:int" in content
        assert "amount:decimal" in content
        assert "created_at:datetime" in content
        assert "is_active:bool" in content
        assert "tags:array" in content
        # STRING columns stay bare — no ``name:string`` noise.
        assert "name:string" not in content

    def test_overview_columns_index_carries_pk_marker_from_suggestions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """High-confidence (≥0.7) identifier suggestions surface as
        ``[pk]`` / ``[fk]`` / ``[unique]`` markers on the ``columns_index``
        entry — the agent uses them to pick the natural projection
        target instead of a convenience column on a join partner.

        Low-confidence suggestions are suppressed (the overview is
        always-loaded; a wrong PK hint costs more than a missing one).
        The marker source is ``annotation_suggestions``, which
        ``mcs build`` writes deterministically — so even profiles that
        skipped the proposal workflow confirmation pass get the cue.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "order_id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "customer_id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "code", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "note", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="order_id",
            suggested_role="identifier",
            suggested_subtype="primary",
            confidence=0.95,
            evidence=[{"source": "uniqueness", "ratio": 1.0}],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="customer_id",
            suggested_role="identifier",
            suggested_subtype="foreign",
            confidence=0.85,
            evidence=[{"source": "fk_pattern"}],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="code",
            suggested_role="identifier",
            suggested_subtype="unique",
            confidence=0.80,
            evidence=[{"source": "uniqueness", "ratio": 0.99}],
        )
        # ``note`` gets a low-confidence suggestion — must NOT surface.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="note",
            suggested_role="identifier",
            suggested_subtype="unique",
            confidence=0.40,
            evidence=[{"source": "weak"}],
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # PK marker rides alongside the type tag.
        assert "order_id:int [pk]" in content
        assert "customer_id:int [fk]" in content
        # STRING uniques drop the type tag but keep the marker.
        assert "code [unique]" in content
        # Low-confidence ``note`` stays bare — no marker leaked.
        assert "note [unique]" not in content

    def test_overview_columns_index_uses_confirmed_identifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A column annotated as ``role=identifier`` with
        ``id_type=primary/foreign/unique`` surfaces the marker even when
        the build-time suggestion was below the 0.7 confidence floor.

        Real-world trigger: a ``frpm.cdscode``-style column carries a 0.65
        suggestion (sub-floor) but is confirmed by ``mcs package apply``
        as ``identifier:primary``. Without this branch the always-loaded
        overview drops the ``[pk]`` cue and the agent can't tell which
        column is the canonical projection target on "which schools..."
        questions.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "frpm", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "cdscode", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "district_id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "school_name", "type": "STRING", "comment": "", "is_partition": 0},
            ],
        )
        # Suggestion confidence is below the 0.7 floor — alone this
        # would NOT surface a [pk] marker.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="frpm",
            column_name="cdscode",
            suggested_role="identifier",
            suggested_subtype="primary",
            confidence=0.65,
            evidence=[{"source": "uniqueness", "ratio": 1.0}],
        )
        # Operator confirms the role via the proposal workflow.
        db.set_column_semantics(
            source_key=_SK,
            table_name="frpm",
            column_name="cdscode",
            role="identifier",
            id_type="primary",
            semantic_description="California Department School Code",
        )
        # A second confirmed foreign-key without any matching suggestion.
        db.set_column_semantics(
            source_key=_SK,
            table_name="frpm",
            column_name="district_id",
            role="identifier",
            id_type="foreign",
            references_target="frpm.cdscode",
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # Confirmed primary identifier surfaces regardless of suggestion floor.
        assert "cdscode [pk]  # California Department School Code" in content
        # Confirmed FK with no suggestion at all also surfaces.
        assert "district_id:int [fk]" in content
        # Un-annotated column stays bare.
        assert "school_name [" not in content

    def test_overview_columns_index_carries_null_and_const_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Profile-stat warnings (``[null]`` and ``[const]``) surface in
        ``columns_index`` so the agent knows to skip columns that can't
        carry a filter or contribute a meaningful projection.

        A ``cards.asciiname``-style column is 100% NULL in the snapshot; the
        annotated arm picked ``WHERE asciiname LIKE 'Ancestor%'`` (zero
        rows) instead of ``name``. A ``[null]`` marker on the always-
        loaded overview turns that into a visible signal the agent can
        act on without paying a per-table ``mcs show`` round-trip.

        Warning markers override identifier markers — a PK suggestion
        on an empty column is meaningless to downstream SQL, so the
        agent gets the more actionable cue.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "cards", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "name",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 30000,
                },
                {
                    "name": "asciiname",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 1.0,
                    "distinct_count": 0,
                },
                {
                    "name": "edition",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 1,
                },
                {
                    "name": "low_null",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.5,
                    "distinct_count": 100,
                },
            ],
        )
        # Identifier suggestion on the empty column — must be eclipsed
        # by the ``[null]`` warning marker.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="cards",
            column_name="asciiname",
            suggested_role="identifier",
            suggested_subtype="unique",
            confidence=0.85,
            evidence=[{"source": "name_heuristic"}],
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "asciiname [null]" in content
        # Warning eclipses identifier marker.
        assert "asciiname [unique]" not in content
        assert "edition [const]" in content
        # Non-warning columns stay bare. Match the list-item prefix
        # ("- name\n") so the assertion doesn't false-positive on the
        # ``asciiname`` substring.
        assert "- name\n" in content
        assert "- low_null\n" in content
        assert "- name [null]" not in content
        assert "- low_null [null]" not in content
        assert "- low_null [const]" not in content

    def test_overview_columns_index_truncates_wide_tables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tables with more than 20 user columns truncate the
        columns_index to 20 entries plus a single "..." sentinel.

        The cap keeps the overview small for wide-flat / telemetry
        tables — otherwise a single 200-column table would dominate
        every always-loaded overview read.

        With no semantic signal (the case here), the stable-sort
        reorder is a no-op and the surviving entries are the first
        20 DDL-order columns — keeping the prior cold-start behavior
        unchanged.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "wide_table", "h1")
        cols = [
            {"name": f"c{i:03d}", "type": "STRING", "comment": "", "is_partition": 0}
            for i in range(25)
        ]
        db.upsert_columns(tid, cols)
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # 20 columns kept + sentinel "...".
        assert "c000" in content
        assert "c019" in content
        assert "'...'" in content
        # The 21st column must be elided.
        assert "c020" not in content

    def test_overview_columns_index_wide_table_lifts_annotated_into_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a wide table (> 20 user columns) has annotated semantic
        roles past the DDL cap, those columns are lifted into the
        surviving window so the agent sees the high-signal columns
        in the always-loaded overview.

        Without this reorder, an annotated PK / dimension / measure
        defined at DDL position 24 would be silently dropped by the
        first-20 truncation, forcing the agent to round-trip
        ``mcs show --table T`` just to discover the column exists —
        and on wrong-table picks (where the question's answer column
        lives on a different table), the agent never realizes the
        partner table is a better fit because the partner's overview
        entry hid the relevant column.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "wide_table", "h1")
        cols = [
            {"name": f"c{i:03d}", "type": "STRING", "comment": "", "is_partition": 0}
            for i in range(25)
        ]
        cols[24]["type"] = "BIGINT"
        db.upsert_columns(tid, cols)
        # Annotate via the public API so storage rule validation runs.
        # ``c003`` PK, ``c022`` categorical dimension, ``c024`` SUM measure.
        db.set_column_semantics(_SK, "wide_table", "c003", role="identifier", id_type="primary")
        db.set_column_semantics(_SK, "wide_table", "c022", role="dimension", dim_type="categorical")
        db.set_column_semantics(_SK, "wide_table", "c024", role="measure", agg="SUM")
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # All three annotated columns survive the cap.
        assert "c003" in content
        assert "c022" in content
        assert "c024" in content
        # The "..." sentinel appears (truncation happened).
        assert "'...'" in content
        # Since the three annotated columns took three of the 20
        # surviving slots, three unsigned columns at the tail of DDL
        # order must be dropped — c022 and c024 displaced two of
        # c020/c021/c023 (c003 displaces nothing because it's
        # already within the original DDL-first-20 slice).
        elided = [name for name in ("c020", "c021", "c023") if name not in content]
        assert len(elided) >= 2, f"expected ≥2 of c020/c021/c023 elided; got {content}"

    def test_overview_columns_index_wide_table_preserves_ddl_order_within_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wide-table reorder is a stable sort by signal priority,
        so columns within the same tier (e.g. all unsigned default-tier
        columns) appear in DDL order — the agent gets a predictable
        on-disk-order projection rather than an alphabetic shuffle.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "wide_table", "h1")
        cols = [
            {"name": f"c{i:03d}", "type": "STRING", "comment": "", "is_partition": 0}
            for i in range(25)
        ]
        db.upsert_columns(tid, cols)
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # No annotations → all columns are tier 3 → stable sort is a
        # no-op → DDL order preserved → c000 appears before c001 in
        # the rendered text.
        c000_pos = content.find("c000")
        c019_pos = content.find("c019")
        assert c000_pos != -1
        assert c019_pos != -1
        assert c000_pos < c019_pos

    def test_overview_columns_index_narrow_table_preserves_ddl_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tables at or under the 20-column cap skip the reorder
        entirely, so a narrow annotated table renders columns in
        the DDL order they were defined in (rather than an
        annotated-first reshuffle). The reorder is purely a wide-
        table-truncation lifeline; below the cap, every column
        survives, and DDL order is the more readable projection.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "narrow_table", "h1")
        cols = [
            {"name": f"c{i:03d}", "type": "STRING", "comment": "", "is_partition": 0}
            for i in range(5)
        ]
        db.upsert_columns(tid, cols)
        # Annotate c003 as an identifier — DDL order must still place
        # c000 before c003 in the rendered output (no annotation-driven
        # shuffle when the table is under the cap).
        db.set_column_semantics(_SK, "narrow_table", "c003", role="identifier", id_type="primary")
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # All 5 columns present, no "..." sentinel.
        for i in range(5):
            assert f"c{i:03d}" in content
        assert "'...'" not in content
        # DDL order: c000 < c003 in the rendered text (annotated col
        # did NOT get lifted to the front because table is narrow).
        c000_pos = content.find("c000")
        c003_pos = content.find("c003")
        assert c000_pos < c003_pos

    def test_overview_columns_index_wide_table_lifts_pk_marker_from_joins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wide-table reorder also considers identifier markers
        derived from the join graph (not just confirmed
        ``semantic_role``). A column that's a join endpoint and gets
        an inferred ``[pk]`` / ``[fk]`` marker must survive the cap
        even when no operator annotation exists yet — the join graph
        is the agent's primary signal for "where to join" and burying
        it past the truncation defeats the always-loaded overview.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "wide_table", "h1")
        # 25 cols; ``c023`` is the join endpoint on the wide side.
        cols = [
            {"name": f"c{i:03d}", "type": "STRING", "comment": "", "is_partition": 0}
            for i in range(25)
        ]
        # Mark c023 as the join column with high distinct count so
        # the join-graph marker derivation has something to chew on.
        cols[23]["distinct_count"] = 1000
        cols[23]["null_ratio"] = 0.0
        db.upsert_columns(tid, cols)
        # Partner table with an ``id`` PK.
        partner_tid = db.upsert_table(_SK, "partner", "h1")
        db.upsert_columns(
            partner_tid,
            [
                {
                    "name": "id",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 5000,
                }
            ],
        )
        db.upsert_join(_SK, "wide_table", "c023", _SK, "partner", "id", "xxx_id", 0.85, "n:1")
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # c023 (the FK column) survives the cap due to its [fk] marker.
        assert "c023" in content
        assert "'...'" in content

    def test_overview_columns_index_carries_date_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STRING columns whose format_examples look like dates get a
        ``[str-date]`` marker so the agent knows to reach for ``SUBSTR``
        or ``TO_DATE``-wrap (date functions return NULL silently on
        STRING in MaxCompute).

        Non-STRING types (DATE, DATETIME) already carry a ``:date`` /
        ``:datetime`` type tag and skip the hint.
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "birthday",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["1976-01-29", "1981-03-14", "1990-12-25"]),
                },
                {
                    "name": "code",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 50,
                    "sample_values_json": _json.dumps(["CZK", "EUR", "USD"]),
                },
                {
                    "name": "created",
                    "type": "DATE",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 200,
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # STRING column with date-like examples → [str-date: <recipe>].
        # The recipe naming TO_DATE is the load-bearing piece — without
        # it the agent has the marker but not the fix.
        assert "birthday [str-date:" in content
        assert "TO_DATE" in content
        # STRING column with non-date examples → no hint at all.
        assert "- code\n" in content or "- code " in content
        assert "code [str-date" not in content
        assert "code [date" not in content
        # DATE type column gets :date tag, not [str-date] hint.
        assert "created:date" in content
        assert "created [str-date" not in content
        assert "created [date" not in content

    def test_overview_columns_index_carries_semantic_description(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Semantic descriptions are inlined in columns_index so the
        agent sees column meaning without drilling into per-table detail."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "district", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "a11",
                    "type": "DECIMAL",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 77,
                },
                {
                    "name": "code",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 77,
                },
            ],
        )
        # semantic_description is written by the annotation path, not upsert_columns.
        # Set it directly via SQL so the test exercises the rendering path.
        db._conn.execute(
            "UPDATE columns SET semantic_description = ? WHERE table_id = ? AND name = ?",
            ("average salary", tid, "a11"),
        )
        db._conn.commit()

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "a11:decimal  # average salary" in content
        # Column without semantic_description stays bare
        assert "- code" in content or "- code\n" in content

    def test_date_format_hint_majority_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A STRING column needs ≥50% of format_examples to look date-like
        before the ``[str-date]`` marker appears.  One coincidental match
        (e.g. ``"2025-Q1"``) must not trigger the hint."""
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "mixed",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["2025-01-15", "N/A", "unknown", "2025-Q1"]),
                },
                {
                    "name": "dates",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["2025-01-15", "2025-02-20", "2025-03-30"]),
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # Only 1/4 examples look like a date → no marker.
        assert "mixed [str-date" not in content
        assert "mixed [date" not in content
        # 3/3 examples look like dates → [str-date: <recipe>] marker.
        assert "dates [str-date:" in content

    def test_date_format_hint_distinguishes_str_datetime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STRING columns whose ``format_examples`` carry a time component
        get ``[str-datetime]`` instead of ``[str-date]``. The marker shape
        matters because string-comparing a datetime against a date-only
        literal (``col > '2014-09-01'``) lex-includes boundary-day rows
        like ``'2014-09-01 12:34:56'`` (the longer prefix-match sorts
        after), so the agent has to wrap with ``SUBSTR(col, 1, 10)`` to
        recover date-level semantics.

        Single boundary-sensitive sample is enough — even one datetime row
        makes naive string-compare wrong.
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "last_access",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    # All samples carry HH:MM[:SS[.f]].
                    "sample_values_json": _json.dumps(
                        [
                            "2010-07-19 06:55:26.0",
                            "2014-09-01 12:34:56",
                            "2025-01-15T08:00:00",
                        ]
                    ),
                },
                {
                    "name": "mostly_dates_one_time",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    # Mixed: 2 pure dates + 1 with time → any-time-wins
                    # → str-datetime (single boundary-sensitive value is
                    # enough to break naive string compare).
                    "sample_values_json": _json.dumps(
                        ["2025-01-01", "2025-02-02", "2025-03-03 09:30:00"]
                    ),
                },
                {
                    "name": "pure_dates",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["2025-01-01", "2025-02-02", "2025-03-03"]),
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # All-datetime samples → [str-datetime: <recipe>], with the
        # SUBSTR(c,1,10) lex-trap workaround inline.
        assert "last_access [str-datetime:" in content
        assert "last_access [str-date:" not in content
        assert "SUBSTR(c,1,10)" in content
        # Mixed shape, even one datetime → [str-datetime] (conservative).
        assert "mostly_dates_one_time [str-datetime:" in content
        assert "mostly_dates_one_time [str-date:" not in content
        # Pure dates stay on [str-date].
        assert "pure_dates [str-date:" in content
        assert "pure_dates [str-datetime:" not in content

    def test_date_format_hint_pure_time_strings_emit_str_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STRING columns whose ``format_examples`` are pure-time /
        duration strings (``H:MM.fff`` lap times, ``HH:MM:SS`` wall-clock,
        ``M:SS.fff`` response durations) — *without* a leading date —
        get ``[str-time]`` so the agent knows not to call ``HOUR(col)``
        and not to lex-sort the raw STRING. Lex-sort on a mixed-width
        time string is wrong: ``'1:34.188'`` byte-compares as less than
        ``'12:34.188'`` because ``'1' < '2'``, so a ``MIN`` /
        ``ORDER BY ASC`` returns the wrong row.

        Generic data-modeling pattern, not Bird-specific: warehouses
        commonly store lap / split / response / processing times as
        VARCHAR strings (often paired with a numeric ``*_ms`` sibling).
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "lap_time",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    # M:SS.fff lap times — uniform "minutes:seconds.frac".
                    "sample_values_json": _json.dumps(["1:34.188", "1:53.480", "2:30.500"]),
                },
                {
                    "name": "wall_clock",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    # HH:MM:SS times of day.
                    "sample_values_json": _json.dumps(["08:00:00", "12:34:56", "23:59:59"]),
                },
                {
                    "name": "short_time",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    # H:MM bare (no seconds, no fractional).
                    "sample_values_json": _json.dumps(["1:00", "2:30", "12:45"]),
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # All three flavours of pure-time string → [str-time: <recipe>],
        # and the recipe must name SUBSTR / REGEXP_EXTRACT.
        assert "lap_time [str-time:" in content
        assert "wall_clock [str-time:" in content
        assert "short_time [str-time:" in content
        assert "REGEXP_EXTRACT" in content or "SUBSTR" in content
        # And they MUST NOT cross-contaminate with the date markers —
        # an agent that sees [str-date] on a lap-time column would
        # reach for TO_DATE and get NULL.
        for col in ("lap_time", "wall_clock", "short_time"):
            assert f"{col} [str-date" not in content
            assert f"{col} [str-datetime" not in content

    def test_date_format_hint_date_wins_over_time_on_mixed_sample(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a sample carries *both* dates and stray time-shaped
        values, the date marker wins. A column with mostly
        ``YYYY-MM-DD`` rows and a few oddballs like ``'12:00'`` is
        still a date column for SQL purposes — the agent's
        date-wrapping advice applies; the time-only advice does not.
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "mostly_dates",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["2025-01-01", "2025-02-02", "12:00"]),
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "mostly_dates [str-date:" in content
        assert "mostly_dates [str-time" not in content

    def test_date_format_hint_non_time_strings_stay_bare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ``_TIME_RE`` anchor is tight — values that don't look
        like times (currency codes, plain integers, colon-bearing
        non-time strings like ``'note: hi'``) MUST NOT get
        ``[str-time]``. A false positive here would push the agent
        toward REGEXP_EXTRACT on data that has no numeric component.
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "currency",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["CZK", "EUR", "USD"]),
                },
                {
                    "name": "colon_note",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps(["note: hi", "ref: 12", "tag: ok"]),
                },
            ],
        )
        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "currency [str-time" not in content
        assert "colon_note [str-time" not in content

    def test_overview_columns_index_dim_type_time_marks_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A confirmed ``dim_type='time'`` annotation surfaces the
        appropriate date marker even when the column's format_examples
        are absent or wouldn't clear the heuristic threshold — the
        operator's annotation is a stronger signal than sample sniffing.

        The marker shape depends on the underlying type: non-string
        non-native-temporal (BIGINT unix timestamp) gets ``[date]``;
        STRING-typed time-dim gets ``[str-date]`` because date functions
        return NULL silently on STRING in MaxCompute.
        """
        import json as _json

        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "t", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "ts_int",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                    "sample_values_json": _json.dumps([1700000000, 1700001000]),
                },
                {
                    "name": "str_date",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 50,
                },
                {
                    "name": "untouched",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 5,
                },
            ],
        )
        # Confirm time-dimension on the integer epoch column.
        db.set_column_semantics(
            source_key=_SK,
            table_name="t",
            column_name="ts_int",
            role="dimension",
            dim_type="time",
        )
        # Confirm time-dimension on a STRING-typed date column with no
        # sample values — annotation alone must drive the marker.
        db.set_column_semantics(
            source_key=_SK,
            table_name="t",
            column_name="str_date",
            role="dimension",
            dim_type="time",
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # ts_int (BIGINT, dim_type=time) gets [date: <recipe>] marker —
        # the recipe names FROM_UNIXTIME so the agent doesn't compare
        # a raw int against a date literal.
        assert "ts_int:int [date:" in content
        assert "FROM_UNIXTIME" in content
        # str_date (STRING, dim_type=time) gets [str-date: <recipe>]
        # marker — recipe names TO_DATE because YEAR/MONTH return NULL
        # on STRING.
        assert "str_date [str-date:" in content
        assert "str_date [date:" not in content
        # untouched STRING with no time annotation stays bare.
        assert "untouched [date" not in content
        assert "untouched [str-date" not in content

    def test_overview_columns_index_carries_fk_marker_from_join_graph(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Columns participating in confirmed-enough join edges surface
        as ``[fk]`` (and the target side as ``[pk]`` for ``id`` columns)
        even when no annotation or ≥0.7 suggestion exists.

        Real-world trigger: a schools-style dataset's ``frpm.cdscode``
        co-existing with ``frpm.school_code`` — neither carries a
        confirmed identifier role nor a ≥0.7 suggestion, so the
        overview surfaces both as bare names and the agent picks the
        wrong column when answering "list school codes". A
        ``same_name`` join edge (frpm.cdscode = schools.cdscode) is
        the structural signal that ``cdscode`` is the canonical join
        key; surfacing it as ``[fk]`` in the always-loaded overview
        nudges the agent toward it.

        ``loose_id`` and sub-floor confidence rows must NOT surface,
        otherwise the agent gets ``[fk]`` markers pointing at phantom
        targets.
        """
        db = _make_db(tmp_path)
        # Table A: orders with FK columns that lack confirmed roles
        # AND lack ≥0.7 suggestions. Only the join graph carries the
        # signal.
        tid_a = db.upsert_table(_SK, "orders", "hA")
        db.upsert_columns(
            tid_a,
            [
                {
                    "name": "id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
                {
                    "name": "customer_id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 80,
                },
                {
                    "name": "shared_code",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 80,
                },
                {
                    "name": "phantom_id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 80,
                },
                {
                    "name": "weak_link",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 80,
                },
            ],
        )
        tid_b = db.upsert_table(_SK, "customers", "hB")
        db.upsert_columns(
            tid_b,
            [
                {
                    "name": "id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
                {
                    "name": "shared_code",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 80,
                },
            ],
        )
        # xxx_id: orders.customer_id → customers.id (FK→PK)
        db.upsert_join(
            _SK,
            "orders",
            "customer_id",
            _SK,
            "customers",
            "id",
            "xxx_id",
            0.85,
            "n:1",
        )
        # same_name: orders.shared_code = customers.shared_code
        db.upsert_join(
            _SK,
            "orders",
            "shared_code",
            _SK,
            "customers",
            "shared_code",
            "same_name",
            0.65,
            "n:m",
        )
        # loose_id: phantom_id points at a non-existent table → must NOT mark
        db.upsert_join(
            _SK,
            "orders",
            "phantom_id",
            _SK,
            "phantom",
            "id",
            "loose_id",
            0.3,
            "n:m",
        )
        # Sub-floor confidence (< 0.5) → must NOT mark even though kind is xxx_id
        db.upsert_join(
            _SK,
            "orders",
            "weak_link",
            _SK,
            "customers",
            "id",
            "xxx_id",
            0.4,
            "n:1",
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # customer_id is FK side of an xxx_id join → [fk]
        assert "customer_id:int [fk]" in content
        # customers.id is the target of the xxx_id join (literal "id"
        # column) → [pk]
        assert "id:int [pk]" in content
        # shared_code: same_name on both sides → both marked [fk]
        # (frpm-style shared-key signal). STRING columns drop the type
        # tag so the marker rides bare.
        assert "shared_code [fk]" in content
        # loose_id must not surface — the target table is phantom
        assert "phantom_id:int [fk]" not in content
        # sub-floor confidence must not surface either
        assert "weak_link:int [fk]" not in content

    def test_overview_join_graph_fk_overrides_unique_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A column with both a ≥0.7 ``[unique]`` suggestion AND a
        confirmed-enough join-graph FK edge surfaces as ``[fk]``, not
        ``[unique]``. The join-graph signal beats the uniqueness signal
        because FK tells the agent *where to join*, while unique only
        says the column is distinctive.

        Real-world trigger: a ``disp.client_id``-style column — every
        disp row maps to one client (1:1 relationship), so sample
        uniqueness fires a ≥0.7 ``[unique]`` suggestion that masks the
        actionable FK→client.client_id edge. Before this override,
        the agent saw ``client_id [unique]`` and didn't realize it
        should JOIN through disp to reach client rows; the [fk] marker
        nudges it toward the canonical join.
        """
        db = _make_db(tmp_path)
        tid_disp = db.upsert_table(_SK, "disp", "h1")
        db.upsert_columns(
            tid_disp,
            [
                {
                    "name": "disp_id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
                {
                    "name": "client_id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
            ],
        )
        tid_client = db.upsert_table(_SK, "client", "h2")
        db.upsert_columns(
            tid_client,
            [
                {
                    "name": "client_id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
            ],
        )
        # Uniqueness fires a high-confidence [unique] suggestion on
        # disp.client_id (every disp row has a distinct client_id since
        # the relationship is 1:1).
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="disp",
            column_name="client_id",
            suggested_role="identifier",
            suggested_subtype="unique",
            confidence=0.95,
            evidence=[{"source": "uniqueness", "ratio": 1.0}],
        )
        # Join inference confirms the FK edge: disp.client_id → client.client_id.
        db.upsert_join(
            _SK,
            "disp",
            "client_id",
            _SK,
            "client",
            "client_id",
            "same_name",
            0.80,
            "1:1",
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # Override: join graph promotes [unique] → [fk].
        assert "client_id:int [fk]" in content
        # And [unique] must NOT also surface on the same column.
        assert "client_id:int [unique]" not in content

    def test_overview_id_marker_suppresses_const_sampling_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An identifier marker (``[pk]`` / ``[fk]`` / ``[unique]``) wins
        over a ``[const]`` warning derived from the 20-row column sample.

        Real-world trigger: a clinical schema's ``laboratory.id`` column.
        The column is the patient FK on the laboratory fact table and
        joins back to ``patient.id``. A patient typically has many lab
        rows, so the ``LIMIT 20`` sample in ``phase_column_sampling``
        often returns 20 rows for the same patient — ``distinct_count``
        comes back as 1 and ``_stat_marker`` flags ``[const]``. Without
        this override the agent reads ``laboratory.id [const]`` and
        avoids the join, dropping any question that needs to filter
        lab results by patient cohort.

        The fix: when ``id_markers`` already has ``[pk]`` / ``[fk]`` /
        ``[unique]`` (from a confirmed annotation, a ≥0.7 suggestion,
        or the join graph), suppress ``[const]`` — constancy of an
        identifier is almost always a 20-row sampling artifact.
        ``[null]`` is NOT suppressed; a 99%-null sample is far more
        representative than distinct=1 from a single batch.
        """
        db = _make_db(tmp_path)
        tid_lab = db.upsert_table(_SK, "laboratory", "h1")
        db.upsert_columns(
            tid_lab,
            [
                {
                    "name": "id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    # Sampling artifact: 20 lab rows all happened to be
                    # for the same patient → distinct_count == 1 in the
                    # sample, even though the column is the patient FK
                    # carrying hundreds of distinct IDs in the full table.
                    "distinct_count": 1,
                },
            ],
        )
        tid_pat = db.upsert_table(_SK, "patient", "h2")
        db.upsert_columns(
            tid_pat,
            [
                {
                    "name": "id",
                    "type": "BIGINT",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 0.0,
                    "distinct_count": 100,
                },
            ],
        )
        # Join inference confirms the FK edge: laboratory.id → patient.id.
        db.upsert_join(_SK, "laboratory", "id", _SK, "patient", "id", "same_name", 0.80, "n:1")

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        # [const] sampling artifact is suppressed in favor of the
        # join-graph-derived [fk].
        assert "id:int [fk]" in content
        assert "id:int [const]" not in content

    def test_overview_null_warning_not_suppressed_by_id_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``[null]`` warnings are NOT overridden by identifier markers.

        Counterpart guard to
        ``test_overview_id_marker_suppresses_const_sampling_artifact``:
        suppressing ``[const]`` was justified because constancy from a
        20-row sample is unreliable, but a high null ratio in the same
        sample is far more representative. A 99%-null PK column is a
        sign of an effectively-empty column the agent shouldn't reach
        for as a filter target, regardless of the structural marker.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "cards", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "asciiname",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": 1.0,
                    "distinct_count": 0,
                },
            ],
        )
        # High-confidence [unique] suggestion on an empty column.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="cards",
            column_name="asciiname",
            suggested_role="identifier",
            suggested_subtype="unique",
            confidence=0.95,
            evidence=[{"source": "name_heuristic"}],
        )

        profile = _make_profile()
        out = tmp_path / "out"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, out).render_overview()

        content = (out / "_overview.md").read_text(encoding="utf-8")
        assert "asciiname [null]" in content
        # [null] still wins over identifier marker.
        assert "asciiname [unique]" not in content

    def test_compact_column_entry_adds_format_hint(self) -> None:
        """``compact_column_entry`` emits ``format_hint: \"str-date\"`` for
        STRING columns whose format examples look like dates — the
        agent-visible marker that signals "wrap with SUBSTR or TO_DATE
        because date functions return NULL on STRING in MaxCompute"."""
        import json as _json

        col_date_str = {
            "name": "birthday",
            "type": "STRING",
            "sample_values_json": _json.dumps(["1976-01-29", "1981-03-14", "1990-08-07"]),
        }
        out = compact_column_entry(col_date_str)
        assert out["format_hint"] == "str-date"

        col_non_date = {
            "name": "currency",
            "type": "STRING",
            "sample_values_json": _json.dumps(["CZK", "EUR", "USD"]),
        }
        out2 = compact_column_entry(col_non_date)
        assert "format_hint" not in out2

        col_date_typed = {
            "name": "created",
            "type": "DATE",
            "sample_values_json": _json.dumps(["2025-01-01"]),
        }
        out3 = compact_column_entry(col_date_typed)
        assert "format_hint" not in out3

        col_time_str = {
            "name": "lap_time",
            "type": "STRING",
            "sample_values_json": _json.dumps(["1:34.188", "1:53.480", "2:30.500"]),
        }
        out4 = compact_column_entry(col_time_str)
        assert out4["format_hint"] == "str-time"


# ── Test 2: render_table ────────────────────────────────────────────────────


class TestRenderTable:
    def test_table_md_has_yaml_frontmatter_and_column_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """<table>.md has YAML frontmatter with columns list (§5 body-drop)."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "card_games")

        table_path = output_dir / _SK / "card_games.md"
        assert table_path.exists()
        content = table_path.read_text(encoding="utf-8")

        # Frontmatter checks.
        assert "name: card_games" in content
        assert "schema_hash: abc123def" in content
        assert "---" in content

        # Column list in frontmatter.
        assert "game_id" in content
        assert "game_type" in content
        assert "sample_values:" in content  # enum values serialized as YAML list

        # §5 body-drop: no markdown pipe-table body after the closing ---.
        parts = content.split("---", 2)
        assert len(parts) == 3
        body_after_fence = parts[2].strip()
        assert body_after_fence == ""


class TestRenderTableSampleSqls:
    def test_render_table_includes_verified_sample_sqls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """user_verified SQLs must appear in the literal sample_sqls list."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_memory(
            "sample_sql",
            json.dumps(
                {
                    "table": "orders",
                    "source_key": _SK,
                    "sql": "SELECT COUNT(*) FROM orders",
                    "confidence": "user_verified",
                    "shape_key": "abc123",
                }
            ),
            f"sample_sql for {_SK}:orders: SELECT COUNT(*) FROM orders",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "sample_sqls:" in text
        assert "SELECT COUNT(*) FROM orders" in text

    def test_render_table_drops_mined_low_sample_sqls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mined patterns are dropped entirely from per-table markdown.

        Earlier iterations kept ``sample_sql_patterns`` for mined
        entries with progressively stronger redaction so the agent
        could read workload-frequency stats. Each defensive layer
        still leaked: the ``canonical_sql`` shape alone is enough
        for the agent to template-match (e.g. the SPLIT_PART pattern
        below would steer the agent toward SPLIT_PART even on
        unrelated STRING-parsing questions). Drop the block entirely
        — workload signal that survives lives in the ``joins`` block
        and column-level annotations.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "qualifying", "h1")
        db.upsert_columns(
            tid,
            [{"name": "q2", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        db.upsert_memory(
            "sample_sql",
            json.dumps(
                {
                    "table": "qualifying",
                    "source_key": _SK,
                    "sql": "SELECT SPLIT_PART(q2, ':', 1) FROM qualifying",
                    "representative_sql": "SELECT SPLIT_PART(q2, ':', 1) FROM qualifying",
                    "canonical_sql": "SELECT SPLIT_PART(q2, ?, ?) FROM qualifying",
                    "shape_key": "wrong_split",
                    "confidence": "mined_low",
                    "frequency": 1,
                    "verified_count": 0,
                }
            ),
            f"sample_sql for {_SK}:qualifying: ...",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "qualifying")

        text = (output_dir / _SK / "qualifying.md").read_text(encoding="utf-8")
        # Both surfaces are suppressed; the empty lists are omitted
        # from the YAML envelope to keep the agent's preview window
        # focused on what's actually present.
        assert "sample_sqls:" not in text
        assert "sample_sql_patterns:" not in text
        # The pattern's identifying tokens must not leak from any
        # surface — neither shape_key nor SPLIT_PART body.
        assert "wrong_split" not in text
        assert "SPLIT_PART" not in text

    def test_render_table_drops_mined_high_sample_sqls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """High-frequency mined patterns are dropped on the same grounds.

        ``mined_high`` (frequency ≥ 3) reflects a workload pattern
        the user runs repeatedly, but the specific projection /
        filter literals remain question-specific. The template-match
        hazard is identical to ``mined_low`` — only ``user_verified``
        patterns survive in markdown.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "events", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_memory(
            "sample_sql",
            json.dumps(
                {
                    "table": "events",
                    "source_key": _SK,
                    "sql": "SELECT id FROM events WHERE ds = '20260521'",
                    "representative_sql": "SELECT id FROM events WHERE ds = '20260521'",
                    "canonical_sql": "SELECT id FROM events WHERE ds = ?",
                    "confidence": "mined_high",
                    "frequency": 5,
                    "shape_key": "common_shape",
                }
            ),
            f"sample_sql for {_SK}:events: ...",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "events")

        text = (output_dir / _SK / "events.md").read_text(encoding="utf-8")
        assert "sample_sqls:" not in text
        assert "sample_sql_patterns:" not in text
        assert "common_shape" not in text
        assert "20260521" not in text
        assert "ds = ?" not in text

    def test_render_table_keeps_user_verified_sample_sql_pattern(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``user_verified`` patterns survive — they're explicit endorsements.

        ``mcs memory verify`` is how an operator certifies a SQL
        as correct for a real question. Those entries are safe to
        surface as templates because the user already vouched for
        the projection and join shape.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_memory(
            "sample_sql",
            json.dumps(
                {
                    "table": "orders",
                    "source_key": _SK,
                    "sql": "SELECT COUNT(*) FROM orders",
                    "canonical_sql": "SELECT COUNT(*) FROM orders",
                    "shape_key": "orders_count",
                    "confidence": "user_verified",
                    "frequency": 1,
                    "verified_count": 1,
                }
            ),
            f"sample_sql for {_SK}:orders: SELECT COUNT(*) FROM orders",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        # Both surfaces appear for a verified entry.
        assert "sample_sqls:" in text
        assert "SELECT COUNT(*) FROM orders" in text
        assert "sample_sql_patterns:" in text
        assert "user_verified" in text
        assert "orders_count" in text


class TestRenderTableFormatExamples:
    """``sample_values_json`` is captured by ``phase_column_sampling``
    whenever the LIMIT-20 sample stays within the enum gate (distinct
    ≤ 30 AND max_len ≤ 80). A later ``phase_profile_columns`` pass
    runs full-table APPROX_DISTINCT and may downgrade ``is_enum`` to
    0 when the real NDV blows past 30 — but it leaves
    ``sample_values_json`` intact. The classic case is STRING-typed
    timestamps like ``lastaccessdate`` in a community-content
    schema: 20 sampled rows look like an enum, the full table has
    ~50k distinct timestamps, ``is_enum`` flips to 0, and without
    format hints the agent can't tell ``'2014-09-01 12:34:56'`` from
    a bare-date ``'2014-09-01'`` — leading to wrong predicates on
    string-typed time columns.

    Surface up to 3 stored samples as ``format_examples`` so the
    agent sees the on-disk shape regardless of the final
    ``is_enum`` value.
    """

    def test_non_enum_column_with_stored_samples_exposes_format_examples(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "users", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "lastaccessdate",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "is_enum": 0,
                    "distinct_count": 20,
                    "sample_values_json": json.dumps(
                        [
                            "2014-09-01 12:34:56",
                            "2014-09-02 08:11:00",
                            "2014-09-03 22:05:13",
                            "2014-09-04 10:00:00",
                        ]
                    ),
                }
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "users")

        text = (output_dir / _SK / "users.md").read_text(encoding="utf-8")
        assert "format_examples:" in text
        # Capped at 3 so it stays a format hint, not a value enumeration.
        assert "2014-09-01 12:34:56" in text
        assert "2014-09-02 08:11:00" in text
        assert "2014-09-03 22:05:13" in text
        assert "2014-09-04 10:00:00" not in text
        # And the enum-only field must NOT be emitted for non-enum columns —
        # the agent should not infer "these are all the values".
        assert "sample_values:" not in text

    def test_enum_column_still_uses_sample_values_not_format_examples(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """True enums keep emitting the full ``sample_values`` list — the
        new ``format_examples`` branch is only for non-enum columns.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [
                {
                    "name": "currency",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "is_enum": 1,
                    "distinct_count": 3,
                    "sample_values_json": json.dumps(["CZK", "EUR", "USD"]),
                }
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "sample_values:" in text
        assert "format_examples:" not in text
        assert "CZK" in text and "EUR" in text and "USD" in text


# ── Test 3: render_joins ────────────────────────────────────────────────────


class TestRenderJoins:
    def test_joins_md_has_relationships(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_joins.md frontmatter: relationships with left/right table/col, etc."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_joins()

        joins_path = output_dir / "_joins.md"
        assert joins_path.exists()
        content = joins_path.read_text(encoding="utf-8")

        assert "relationships:" in content
        assert "card_games" in content
        assert "player_id" in content
        assert "players" in content
        assert "xxx_id" in content
        assert "1:n" in content

    def test_joins_md_drops_phantom_loose_id_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Joins rows whose right_table doesn't exist in the package
        (``loose_id`` phantoms) must NOT land in ``_joins.md``.

        The phases.py ``loose_id`` heuristic emits a "would join here"
        row for every ``_id`` column with no matching parent — e.g.
        ``employee.manager_id`` produces an edge against a literal
        ``manager`` table even when no ``manager`` table is in the
        profile. Surfacing those phantoms in the relationships list
        would tell the agent a join target exists when it doesn't,
        which costs more than the diagnostic value (the rows still
        live in the DB for ``join_candidates.py`` to score).
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        # Add a loose_id phantom row: right_table ``ghost`` is not in
        # the package, so the renderer must drop it.
        db.upsert_join(_SK, "card_games", "ghost_id", _SK, "ghost", "id", "loose_id", 0.3, "n:m")
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        MarkdownRenderer(db, profile, output_dir).render_joins()

        content = (output_dir / "_joins.md").read_text(encoding="utf-8")
        # Legitimate xxx_id row survives — its endpoints both exist.
        assert "player_id" in content
        # Phantom loose_id row is dropped from the rendered output.
        assert "ghost_id" not in content
        assert "loose_id" not in content

    def test_overview_drops_phantom_partners_from_joins_to(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``joins_to`` in the per-table overview entry must not list
        partners whose table doesn't exist in the package.

        Mirrors ``test_joins_md_drops_phantom_loose_id_targets`` — the
        loose_id heuristic produces these phantoms and they used to
        leak into the always-loaded ``_overview.md``, telling the
        agent a partner table existed when it didn't.
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        # Add a loose_id phantom pointing at a missing ``ghost`` table.
        db.upsert_join(_SK, "card_games", "ghost_id", _SK, "ghost", "id", "loose_id", 0.3, "n:m")
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        MarkdownRenderer(db, profile, output_dir).render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        # The legitimate ``players`` partner is still listed in
        # ``card_games``'s joins_to entry.
        assert "players" in content
        # The phantom ``ghost`` partner must NOT leak through.
        assert "ghost" not in content


# ── Test 4: render_udfs ─────────────────────────────────────────────────────


class TestRenderUdfs:
    def test_udfs_md_has_udf_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_udfs.md frontmatter has udf entries with name, kind, signature, description."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_udfs()

        udfs_path = output_dir / "_udfs.md"
        assert udfs_path.exists()
        content = udfs_path.read_text(encoding="utf-8")

        assert "---" in content
        assert "my_udf" in content
        assert "java" in content
        assert "my_udf(INT) -> INT" in content
        assert "Custom aggregation" in content


# ── Test 5: render_empty_db ─────────────────────────────────────────────────


class TestRenderEmptyDb:
    def test_empty_db_overview_zero_tables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty PackageDB produces overview with 0 tables, no per-table files."""
        db = _make_db(tmp_path)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "tables: 0" in content

        # No per-table .md files should exist.
        md_files = [
            f for f in output_dir.iterdir() if f.suffix == ".md" and f.name != "_overview.md"
        ]
        assert md_files == []


# ── Test 6: render_partial_data ─────────────────────────────────────────────


class TestRenderPartialData:
    def test_db_with_tables_but_no_joins_udfs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB has tables but no joins/udfs -> frontmatter-only files with empty lists."""
        db = _make_db(tmp_path)
        # Insert a table with columns but no joins/udfs.
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "order_id", "type": "INT", "comment": "id", "is_partition": 0},
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_joins()
        renderer.render_udfs()

        joins_content = (output_dir / "_joins.md").read_text(encoding="utf-8")
        assert "relationships:" in joins_content
        assert "---" in joins_content

        udfs_content = (output_dir / "_udfs.md").read_text(encoding="utf-8")
        assert "udfs:" in udfs_content
        assert "---" in udfs_content


# ── Test 7: _state_json_updated ─────────────────────────────────────────────


class TestStateJsonUpdated:
    def test_state_json_after_render_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After render_all, _state.json exists with version=5
        (chain δ per-source partitioning + annotation_coverage rollup),
        profile-level fields, and a ``sources`` map keyed by source_key.
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_all()

        state_path = output_dir / "_state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))

        assert state["version"] == 5
        assert state["udfs_count"] == 1
        assert state["joins_count"] == 1
        assert "last_built_at" in state
        assert state["errors"] == []
        assert state["tables_with_sample_sqls"] == 0
        # v5: annotation_coverage rollup matches _overview.md frontmatter
        cov = state["annotation_coverage"]
        assert "tables_total" in cov
        assert "columns_with_role" in cov
        # Per-source partitioning: the test profile has one source
        # ("test_project", "default") so ``sources`` carries one entry.
        # ``_populate_db`` writes ``card_games`` plus the join's
        # right-hand ``players`` table (added so the join survives the
        # render-time phantom filter).
        assert "sources" in state
        assert _SK in state["sources"]
        assert state["sources"][_SK]["tables_count"] == 2
        assert state["sources"][_SK]["project"] == "test_project"
        assert state["sources"][_SK]["schema"] == "default"

    def test_state_joins_count_excludes_phantom_table_endpoints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_state.json.joins_count`` MUST match the visible row count
        in ``_joins.md`` — the loose_id heuristic's "would-join" phantoms
        (right-side table not in the package) are filtered from both.
        Previously ``render_state`` counted the raw ``list_joins()``
        result, over-reporting against ``_joins.md`` and forcing the
        agent to either trust an inflated count or independently parse
        the markdown to find the real number.
        """
        db = _make_db(tmp_path)
        _populate_db(db)  # adds 1 real join (card_games → players)
        # Add a phantom join: right-side table ``ghost_table`` does NOT
        # exist in the package. ``render_joins`` filters this out;
        # ``render_state`` must do the same.
        db.upsert_join(
            _SK,
            "card_games",
            "ghost_id",
            _SK,
            "ghost_table",
            "id",
            "loose_id",
            0.5,
            "1:n",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_all()

        # Parse _joins.md to get the visible count.
        joins_md = (output_dir / "_joins.md").read_text(encoding="utf-8")
        from ruamel.yaml import YAML

        _yaml = YAML(typ="safe")
        # Frontmatter-only file: split on "---" delimiters.
        body = joins_md.split("---", 2)[1]
        rel = (_yaml.load(body) or {}).get("relationships", []) or []
        visible_count = len(rel)

        state = json.loads((output_dir / "_state.json").read_text(encoding="utf-8"))
        # The raw DB carries 2 joins (1 real + 1 phantom); both
        # ``_joins.md`` and ``_state.json`` must report 1.
        assert len(list(db.list_joins())) == 2  # sanity: DB has both
        assert visible_count == 1
        assert state["joins_count"] == 1, (
            f"state.joins_count {state['joins_count']!r} must match the "
            f"visible row count in _joins.md ({visible_count})"
        )

    def test_state_json_records_tables_with_sample_sqls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When pipeline mines history and finds verified queries for some
        tables, render_all surfaces the count in _state.json so the eval
        with-history gate (cmd_verify_arm_mining) can verify miner output."""
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir, tables_with_sample_sqls=3)
        renderer.render_all()

        state = json.loads((output_dir / "_state.json").read_text(encoding="utf-8"))
        assert state["tables_with_sample_sqls"] == 3


# ── Test 8: overview table list content ──────────────────────────────────────


class TestOverviewTableList:
    def test_overview_frontmatter_has_table_names_and_column_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Overview frontmatter has per-table entries with names and column counts."""
        db = _make_db(tmp_path)
        tid1 = db.upsert_table(_SK, "card_games", "h1")
        db.upsert_columns(
            tid1,
            [
                {"name": "c1", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "c2", "type": "INT", "comment": "", "is_partition": 0},
            ],
        )
        tid2 = db.upsert_table(_SK, "players", "h2")
        db.upsert_columns(
            tid2,
            [
                {"name": "id", "type": "INT", "comment": "", "is_partition": 0},
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "tables: 2" in content
        assert "card_games" in content
        assert "players" in content
        assert "columns_count:" in content


class TestMultiSourceMarkdown:
    """Per-source layout under chain δ: same-named tables across two
    sources land in distinct subdirs, ``_overview.md`` frontmatter has
    per-source entries, ``_state.json.sources`` partitions the counts,
    and ``_joins.md`` qualifies cross-source pairs with source_key.
    """

    def _multi_profile(self) -> Profile:
        return Profile(
            name="multi-test",
            compute_project="acme",
            endpoint="https://odps.endpoint",
            auth=AkAuth(access_key_id="ak", access_key_secret="sk"),
            sources=(
                DataSource(project="acme", schema="warehouse", tables="*"),
                DataSource(project="acme", schema="staging", tables="*"),
            ),
        )

    def test_render_table_lands_in_per_source_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        db = _make_db(tmp_path)
        sk_a = "acme__warehouse"
        sk_b = "acme__staging"
        # Two same-named ``users`` tables under distinct sources.
        for sk in (sk_a, sk_b):
            tid = db.upsert_table(sk, "users", schema_hash=f"h_{sk}")
            db.upsert_columns(
                tid,
                [{"name": "id", "type": "INT", "comment": "", "is_partition": 0}],
            )

        profile = self._multi_profile()
        output_dir = tmp_path / "out"
        renderer = MarkdownRenderer(db, profile, output_dir)
        renderer.render_table(sk_a, "users")
        renderer.render_table(sk_b, "users")

        # Distinct files coexist under their source_key subdirs.
        path_a = output_dir / sk_a / "users.md"
        path_b = output_dir / sk_b / "users.md"
        assert path_a.exists()
        assert path_b.exists()
        # Frontmatter on each carries the right (project, schema).
        a_content = path_a.read_text(encoding="utf-8")
        b_content = path_b.read_text(encoding="utf-8")
        assert "schema: warehouse" in a_content
        assert "schema: staging" in b_content
        assert f"source_key: {sk_a}" in a_content
        assert f"source_key: {sk_b}" in b_content

    def test_overview_has_per_source_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        db = _make_db(tmp_path)
        for sk in ("acme__warehouse", "acme__staging"):
            tid = db.upsert_table(sk, "orders", schema_hash="h")
            db.upsert_columns(
                tid,
                [{"name": "id", "type": "INT", "comment": "", "is_partition": 0}],
            )

        profile = self._multi_profile()
        output_dir = tmp_path / "out"
        MarkdownRenderer(db, profile, output_dir).render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        # Frontmatter ``sources`` array carries both source_keys.
        assert "acme__warehouse" in content
        assert "acme__staging" in content
        # No body section headings (§6 body-drop).
        assert "## Source:" not in content

    def test_state_json_partitions_by_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        db = _make_db(tmp_path)
        # warehouse: 2 tables; staging: 1 table.
        for name in ("orders", "users"):
            db.upsert_table("acme__warehouse", name, "h")
        db.upsert_table("acme__staging", "events", "h")

        profile = self._multi_profile()
        output_dir = tmp_path / "out"
        MarkdownRenderer(db, profile, output_dir).render_all()

        state = json.loads((output_dir / "_state.json").read_text(encoding="utf-8"))
        assert state["version"] == 5
        assert state["sources"]["acme__warehouse"]["tables_count"] == 2
        assert state["sources"]["acme__staging"]["tables_count"] == 1

    def test_joins_md_qualifies_cross_source_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        db = _make_db(tmp_path)
        sk_a = "acme__warehouse"
        sk_b = "acme__staging"
        db.upsert_table(sk_a, "users", "h")
        db.upsert_table(sk_b, "events", "h")
        # Within-source join.
        db.upsert_join(sk_a, "users", "id", sk_a, "orders", "user_id", "link_to", 0.9)
        # Cross-source join.
        db.upsert_join(sk_a, "users", "id", sk_b, "events", "user_id", "link_to", 0.72)

        profile = self._multi_profile()
        output_dir = tmp_path / "out"
        MarkdownRenderer(db, profile, output_dir).render_joins()

        content = (output_dir / "_joins.md").read_text(encoding="utf-8")
        assert "relationships:" in content
        # Cross-source entries qualify both ends with source_key.
        assert f"{sk_a}.users" in content
        assert f"{sk_b}.events" in content

    def test_overview_annotated_tristate_is_per_source(self, tmp_path):
        """Two sources each have a 'users' table. Annotate source A's
        users fully (ai_context + identifier on id), leave source B's
        untouched. The _overview.md frontmatter must show ``annotated: yes``
        for source A's entry and ``annotated: no`` for source B's.
        """
        from maxcompute_semantic.auth.schema import AkAuth, DataSource, Profile
        from maxcompute_semantic.build.markdown import MarkdownRenderer
        from maxcompute_semantic.build.storage import PackageDB

        p = Profile(
            name="ms-prof",
            compute_project="proj_a",
            endpoint="https://example.com",
            auth=AkAuth("aki", "aks"),
            sources=(
                DataSource(project="proj_a", schema="default", tables="*"),
                DataSource(project="proj_b", schema="default", tables="*"),
            ),
        )
        out = tmp_path / "out"
        out.mkdir()
        db = PackageDB(tmp_path / "package.db")
        tid_a = db.upsert_table("proj_a__default", "users", "ha")
        tid_b = db.upsert_table("proj_b__default", "users", "hb")
        db.upsert_columns(
            tid_a, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}]
        )
        db.upsert_columns(
            tid_b, [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}]
        )
        db.set_table_ai_context("proj_a__default", "users", "annotated A")
        db.set_column_semantics(
            "proj_a__default", "users", "id", role="identifier", id_type="primary"
        )
        db.mark_build_complete("proj_a__default", ["users"])
        db.mark_build_complete("proj_b__default", ["users"])

        MarkdownRenderer(db, p, out).render_overview()
        content = (out / "_overview.md").read_text()
        from ruamel.yaml import YAML

        fm = YAML(typ="safe").load(content.split("---")[1])
        src_a = next(s for s in fm["sources"] if s["source_key"] == "proj_a__default")
        src_b = next(s for s in fm["sources"] if s["source_key"] == "proj_b__default")
        assert src_a["tables"][0]["annotated"] == "yes"
        assert src_b["tables"][0]["annotated"] == "no"


class TestRenderTableJoinCandidatesAndSuggestions:
    """render_table frontmatter surfaces join_candidates and
    annotation_suggestions when present, omits them when empty."""

    def test_join_candidates_in_frontmatter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        # ``customers`` is the right-hand side of the join candidate
        # below; it must exist in the package or the phantom-partner
        # filter in ``render_table`` will drop the row.
        cid = db.upsert_table(_SK, "customers", "h2")
        db.upsert_columns(
            cid,
            [{"name": "order_id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_join_candidate(
            left_source_key=_SK,
            left_table="orders",
            left_col="id",
            right_source_key=_SK,
            right_table="customers",
            right_col="order_id",
            confidence=0.85,
            evidence=[{"kind": "name_heuristic"}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "join_candidates:" in text
        assert "orders" in text
        assert "customers" in text

    def test_render_table_drops_phantom_join_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Join candidates whose right-hand table does not exist in
        the package must not surface in per-table markdown. The
        loose_id heuristic emits "would join here" rows whose
        right_table is a literal stripped basename (e.g. ``ghost``
        for ``employee.ghost_id``) even when no such table is in
        the profile; rendering them misleads the agent."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        # Real partner — should surface.
        cid = db.upsert_table(_SK, "customers", "h2")
        db.upsert_columns(
            cid,
            [{"name": "order_id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_join_candidate(
            left_source_key=_SK,
            left_table="orders",
            left_col="id",
            right_source_key=_SK,
            right_table="customers",
            right_col="order_id",
            confidence=0.85,
            evidence=[{"kind": "name_heuristic"}],
        )
        # Phantom partner — should be dropped.
        db.upsert_join_candidate(
            left_source_key=_SK,
            left_table="orders",
            left_col="id",
            right_source_key=_SK,
            right_table="ghost",
            right_col="ghost_id",
            confidence=0.25,
            evidence=[{"kind": "loose_id"}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "join_candidates:" in text
        assert "customers" in text
        assert "ghost" not in text

    def test_annotation_suggestions_in_frontmatter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="id",
            suggested_role="identifier",
            suggested_subtype="primary",
            confidence=0.90,
            evidence=["uniqueness_ratio=0.999"],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "annotation_suggestions:" in text
        assert "identifier" in text

    def test_empty_candidates_and_suggestions_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "join_candidates:" not in text
        assert "annotation_suggestions:" not in text

    def test_low_confidence_suggestions_filtered_from_table_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``render_table`` keeps suggestions with confidence ≥ 0.5 and
        drops anything below. The classifier emits ``fallback /
        name_heuristic`` rows at ~0.35 for every column it has no
        opinion on; rendering all of them would bury the genuine
        signal under ~4-8 YAML lines per column."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [
                {"name": "id", "type": "BIGINT", "comment": "", "is_partition": 0},
                {"name": "name", "type": "STRING", "comment": "", "is_partition": 0},
                {"name": "amount", "type": "DECIMAL(18,2)", "comment": "", "is_partition": 0},
            ],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="id",
            suggested_role="identifier",
            suggested_subtype="primary",
            confidence=0.85,
            evidence=["uniqueness_ratio=0.999"],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="name",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.35,
            evidence=["pattern: fallback", "source: name_heuristic"],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="amount",
            suggested_role="measure",
            suggested_subtype="amount",
            confidence=0.40,
            evidence=["pattern: fallback", "source: name_heuristic"],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "annotation_suggestions:" in text
        # High-confidence (0.85) entry survives.
        assert "column_name: id" in text
        assert "primary" in text
        # Low-confidence (<0.5) fallback rows are dropped.
        assert "column_name: name" not in text
        assert "column_name: amount" not in text
        assert "name_heuristic" not in text

    def test_confidence_rounded_to_two_decimals_in_table_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confidence floats summed across signals leak FP arithmetic
        residue (0.65 → 0.6499999999999999). The agent-facing YAML
        must render the rounded value so it doesn't read as garbage
        data or distort a small model's threshold comparisons."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "league_id", "type": "BIGINT", "comment": "", "is_partition": 0}],
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="league_id",
            suggested_role="identifier",
            suggested_subtype="foreign",
            # 0.55 + 0.1 = 0.6500000000000001 in floating point.
            confidence=0.55 + 0.1,
            evidence=[{"source": "join_candidate", "confidence": 0.5 + 0.1, "side": "left"}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "confidence: 0.65" in text
        assert "confidence: 0.6\n" in text or "confidence: 0.6 " in text
        assert "0.6500000000000001" not in text
        assert "0.6000000000000001" not in text

    def test_null_ratio_rounded_in_table_md_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``null_ratio`` is computed as count_null/count_total — the
        division leaks FP residue identical to the confidence-sum case
        (e.g. 19/100 → 0.18999999999999997). Round at the agent
        boundary; the raw db value is preserved for ranking.

        4-decimal precision (0.01% resolution) is intentional: 2
        decimals would collapse "0.5% null" to 0 and read as "no
        nulls at all", losing the distinction the agent uses to
        decide whether NULL-filtering is needed.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        # 19/100 in float = 0.18999999999999997
        leaky_ratio = 19 / 100
        db.upsert_columns(
            tid,
            [
                {
                    "name": "comment_col",
                    "type": "STRING",
                    "comment": "",
                    "is_partition": 0,
                    "null_ratio": leaky_ratio,
                    "distinct_count": 81,
                },
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        # FP residue must not leak; rounded form must be present.
        assert "0.18999999999999997" not in text
        assert "null_ratio: 0.19" in text

    def test_join_candidate_evidence_uniqueness_ratios_rounded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The join-candidate miner writes ``left_uniqueness_ratio`` /
        ``right_uniqueness_ratio`` inside the evidence list (the
        prefixed pair, not the bare ``uniqueness_ratio`` used in
        annotation-suggestion evidence). Both keys must be rounded at
        the agent boundary; earlier ``_trim_evidence`` only matched
        the bare key and the agent-facing per-table .md kept surfacing
        values like ``0.5875444289908824`` even after the round-extension
        fix landed."""
        db = _make_db(tmp_path)
        _populate_db(db)
        # Both residue patterns observed in a real benchmark smoke
        # artifact for a transactional schema's order.md.
        leaky_left = 0.5875444289908824
        leaky_right = 8.520145410481673e-06
        db.upsert_join_candidate(
            left_source_key=_SK,
            left_table="card_games",
            left_col="account_id",
            right_source_key=_SK,
            right_table="players",
            right_col="account_id",
            confidence=0.6,
            evidence=[
                {
                    "source": "profile_stats",
                    "join_shape": "fk-fk",
                    "left_uniqueness_ratio": leaky_left,
                    "right_uniqueness_ratio": leaky_right,
                },
            ],
            cardinality="n:m",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "card_games")

        text = (output_dir / _SK / "card_games.md").read_text(encoding="utf-8")
        assert "0.5875444289908824" not in text
        assert "8.520145410481673e-06" not in text
        # Rounded forms present — left rounds to 0.59 at 2-decimal
        # precision; right rounds to 0.0 (genuinely near-zero) which
        # is the right signal for the agent.
        assert "left_uniqueness_ratio: 0.59" in text
        assert "right_uniqueness_ratio: 0.0" in text

    def test_confidence_rounded_in_joins_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Join confidence is averaged across signals (uniqueness
        ratio, value overlap, etc.) — the averaging leaks FP residue
        (e.g. 0.6972222222222222 = 25/36 + ...). Round at the agent
        boundary so ``_joins.md`` doesn't read as garbage data."""
        db = _make_db(tmp_path)
        _populate_db(db)
        # Overwrite the populated join's confidence with a leaky float
        # so the test is deterministic regardless of the fixture's
        # round number (0.85 in _populate_db).
        leaky_conf = 25 / 36  # = 0.6944444444444444 — many decimals
        db.upsert_join(
            _SK,
            "card_games",
            "player_id",
            _SK,
            "players",
            "id",
            "xxx_id",
            leaky_conf,
            "1:n",
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        MarkdownRenderer(db, profile, output_dir).render_joins()

        content = (output_dir / "_joins.md").read_text(encoding="utf-8")
        # Rounded confidence present; raw FP residue absent.
        assert "confidence: 0.69" in content
        assert "0.6944444444444444" not in content

    def test_where_count_stripped_for_annotated_column(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a column has already been confirmed as a dimension /
        measure / identifier (``columns.semantic_role`` set), the
        ``where_count`` key inside its ``history_sql`` evidence row
        is removed before the suggestion lands in the per-table .md.
        The role assignment in the confirmed block carries the
        load-bearing signal; ``where_count`` on top of that biases
        the agent toward gratuitous WHERE clauses on questions that
        don't need filtering. Other history_sql evidence
        (``aggregate``, ``group_by_count``) is preserved.
        """
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "satscores", "h1")
        db.upsert_columns(
            tid,
            [{"name": "rtype", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        # Confirm rtype as a dimension via the annotation pass.
        db.set_column_semantics(
            _SK,
            "satscores",
            "rtype",
            role="dimension",
            dim_type="categorical",
        )
        # Suggestion carries both the filter-bias evidence and a
        # benign group_by counter — only the former should be
        # stripped.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="satscores",
            column_name="rtype",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.7,
            evidence=[
                {"source": "history_sql", "where_count": 12, "group_by_count": 4},
            ],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "satscores")

        text = (output_dir / _SK / "satscores.md").read_text(encoding="utf-8")
        # rtype is confirmed as a dimension; suggestion's history_sql
        # row should keep group_by_count but drop where_count.
        assert "dimensions:" in text
        assert "rtype" in text
        assert "annotation_suggestions:" in text
        assert "group_by_count: 4" in text
        assert "where_count" not in text

    def test_where_count_preserved_for_unannotated_column(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """For columns the annotation pass has NOT yet classified,
        ``where_count`` evidence is preserved — the classifier may
        have missed a dimension signal and the agent uses the count
        to weigh manual promotion."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "orders", "h1")
        db.upsert_columns(
            tid,
            [{"name": "region", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        # No set_column_semantics call — region is unannotated.
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="orders",
            column_name="region",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.6,
            evidence=[{"source": "history_sql", "where_count": 8}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "orders")

        text = (output_dir / _SK / "orders.md").read_text(encoding="utf-8")
        assert "annotation_suggestions:" in text
        assert "region" in text
        assert "where_count: 8" in text

    def test_suggestion_dropped_when_only_where_count_on_annotated_col(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An annotated column whose suggestion carries ONLY a
        ``history_sql/where_count`` evidence row has the row stripped,
        then the entire evidence list goes empty, then the suggestion
        is dropped from the rendered .md altogether. Without this the
        agent would see a column listed in both the confirmed
        ``dimensions:`` block and the ``annotation_suggestions:``
        block with no visible evidence — pure noise that pushes
        signal out of the preview window."""
        db = _make_db(tmp_path)
        tid = db.upsert_table(_SK, "satscores", "h1")
        db.upsert_columns(
            tid,
            [{"name": "rtype", "type": "STRING", "comment": "", "is_partition": 0}],
        )
        db.set_column_semantics(
            _SK,
            "satscores",
            "rtype",
            role="dimension",
            dim_type="categorical",
        )
        db.upsert_annotation_suggestion(
            source_key=_SK,
            table_name="satscores",
            column_name="rtype",
            suggested_role="dimension",
            suggested_subtype="categorical",
            confidence=0.7,
            evidence=[{"source": "history_sql", "where_count": 12}],
        )
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))
        MarkdownRenderer(db, profile, output_dir).render_table(_SK, "satscores")

        text = (output_dir / _SK / "satscores.md").read_text(encoding="utf-8")
        # rtype is still in the dimensions block; the noise-only
        # suggestion is gone.
        assert "dimensions:" in text
        assert "rtype" in text
        assert "annotation_suggestions:" not in text


class TestBuildRoleGroups:
    """``build_role_groups`` extracts dimension/measure/identifier from cols."""

    def test_groups_by_semantic_role(self) -> None:
        cols = [
            {
                "name": "id",
                "semantic_role": "identifier",
                "id_type": "primary",
                "semantic_description": "PK",
            },
            {
                "name": "amount",
                "semantic_role": "measure",
                "agg": "SUM",
            },
            {
                "name": "status",
                "semantic_role": "dimension",
                "dim_type": "categorical",
            },
            {
                "name": "ds",
                "semantic_role": None,
            },
        ]
        dims, metrics_, ids = build_role_groups(cols)
        assert dims == [{"name": "status", "dim_type": "categorical"}]
        assert metrics_ == [{"name": "amount", "expr": "amount", "agg": "SUM"}]
        assert ids == [{"name": "id", "type": "primary", "description": "PK"}]

    def test_foreign_identifier_carries_references(self) -> None:
        cols = [
            {
                "name": "order_id",
                "semantic_role": "identifier",
                "id_type": "foreign",
                "references_target": "orders.id",
            }
        ]
        _, _, ids = build_role_groups(cols)
        assert ids == [{"name": "order_id", "type": "foreign", "references": "orders.id"}]

    def test_empty_input_returns_empty_groups(self) -> None:
        assert build_role_groups([]) == ([], [], [])


class TestCompactColumnEntry:
    """``compact_column_entry`` parses sample_values_json + omits empty keys."""

    def test_enum_samples_become_sample_values_list(self) -> None:
        col = {
            "name": "status",
            "type": "STRING",
            "comment": "",
            "is_partition": 0,
            "is_enum": 1,
            "sample_values_json": json.dumps(["new", "paid", "cancelled"]),
        }
        out = compact_column_entry(col)
        assert out["name"] == "status"
        assert out["type"] == "STRING"
        assert out["sample_values"] == ["new", "paid", "cancelled"]
        # Empty / falsy keys are dropped.
        assert "comment" not in out
        assert "is_partition" not in out
        # The raw json string never leaks through.
        assert "sample_values_json" not in out

    def test_non_enum_samples_become_format_examples(self) -> None:
        col = {
            "name": "ts",
            "type": "STRING",
            "is_enum": 0,
            "sample_values_json": json.dumps(["2024-01-01 12:34:56", "2024-01-02 09:00:00"]),
        }
        out = compact_column_entry(col)
        assert out["format_examples"] == [
            "2024-01-01 12:34:56",
            "2024-01-02 09:00:00",
        ]
        assert "sample_values" not in out

    def test_sample_breadth_capped(self) -> None:
        col = {
            "name": "kind",
            "type": "STRING",
            "is_enum": 1,
            "sample_values_json": json.dumps([f"v{i}" for i in range(20)]),
        }
        out = compact_column_entry(col, sample_cap=3)
        assert out["sample_values"] == ["v0", "v1", "v2"]

    def test_long_string_values_truncated(self) -> None:
        long_val = "x" * 200
        col = {
            "name": "blob",
            "type": "STRING",
            "is_enum": 1,
            "sample_values_json": json.dumps([long_val]),
        }
        out = compact_column_entry(col, value_truncate=10)
        # Truncated to value_truncate-1 chars + "…" suffix.
        assert out["sample_values"] == ["x" * 9 + "…"]

    def test_malformed_sample_json_silently_skipped(self) -> None:
        col = {
            "name": "x",
            "type": "STRING",
            "sample_values_json": "not-json",
        }
        out = compact_column_entry(col)
        assert "sample_values" not in out
        assert "format_examples" not in out

    def test_partition_flag_emitted_only_when_true(self) -> None:
        col_p = {"name": "ds", "type": "STRING", "is_partition": 1}
        col_n = {"name": "id", "type": "BIGINT", "is_partition": 0}
        assert compact_column_entry(col_p)["is_partition"] is True
        assert "is_partition" not in compact_column_entry(col_n)

    def test_semantic_description_preserved(self) -> None:
        col = {
            "name": "amount",
            "type": "DECIMAL(10,2)",
            "semantic_description": "Total order amount in cents.",
        }
        out = compact_column_entry(col)
        assert out["semantic_description"] == "Total order amount in cents."


class TestFormatHintInline:
    """``_format_hint_inline`` expands a ``str-date``/``str-datetime``/
    ``str-time``/``date`` hint code into ``<code>: <recipe>`` form so the
    columns_index entry lands with the actionable wrap inline, instead
    of just the bare code. Tests pin the recipe text because the agent
    behaviour (SUBSTR/TO_DATE/FROM_UNIXTIME) depends on naming the wrap
    function explicitly — paraphrasing the recipe to fluff like ``"see
    docs"`` would undo the lift this layer is meant to provide."""

    def test_str_datetime_recipe_names_substr_for_lex_compare(self) -> None:
        """The defining trap for ``str-datetime`` is lex-compare
        boundary inclusion (``col > '2014-09-01'`` includes
        ``'2014-09-01 12:34:56'``). The recipe MUST name SUBSTR(c,1,10)
        — that's the only wrap that fixes the boundary."""
        from maxcompute_semantic.build.markdown import _format_hint_inline

        out = _format_hint_inline("str-datetime")
        assert out.startswith("str-datetime: ")
        assert "SUBSTR(c,1,10)" in out

    def test_str_date_recipe_names_to_date(self) -> None:
        """``str-date`` columns need TO_DATE-wrap (or SUBSTR slicing) —
        the recipe MUST name TO_DATE so the agent doesn't reach for
        YEAR()/MONTH() on a STRING column (which return NULL silently)."""
        from maxcompute_semantic.build.markdown import _format_hint_inline

        out = _format_hint_inline("str-date")
        assert out.startswith("str-date: ")
        assert "TO_DATE" in out

    def test_str_time_recipe_names_regexp_extract(self) -> None:
        """``str-time`` columns have two traps (HOUR(STRING)=NULL +
        mixed-width lex ORDER BY) — the recipe must steer the agent
        toward the SUBSTR / REGEXP_EXTRACT extraction pattern."""
        from maxcompute_semantic.build.markdown import _format_hint_inline

        out = _format_hint_inline("str-time")
        assert out.startswith("str-time: ")
        assert "REGEXP_EXTRACT" in out or "SUBSTR" in out

    def test_date_recipe_names_from_unixtime(self) -> None:
        """Non-STRING ``date`` marker fires on BIGINT-typed unix-timestamp
        columns annotated ``dim_type=time`` — the recipe MUST name
        FROM_UNIXTIME so the agent stops trying to compare an
        unwrapped int against a date literal."""
        from maxcompute_semantic.build.markdown import _format_hint_inline

        out = _format_hint_inline("date")
        assert out.startswith("date: ")
        assert "FROM_UNIXTIME" in out

    def test_unknown_hint_falls_back_to_bare_code(self) -> None:
        """Forward-compat: any new hint variant ``_date_format_hint`` may
        emit in future renders as the bare code (no crash, no fake
        recipe). The renderer keeps working; only the recipe gap is
        visible."""
        from maxcompute_semantic.build.markdown import _format_hint_inline

        assert _format_hint_inline("brand-new-hint") == "brand-new-hint"


# ── Test 9: render_overview — top-level metrics body section ────────────────


class TestOverviewMetricsSection:
    """Pins Task 8 of the top-level-metrics plan: ``_overview.md`` gains
    a ``## Metrics`` body section listing every profile-global metric
    (name + expression + description first line) when the profile carries
    at least one.

    Zero-metric profiles MUST NOT emit the heading — the always-loaded
    overview should not show an empty section the agent has to parse
    past on every call.
    """

    def test_overview_md_includes_metrics_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_db(tmp_path)
        _populate_db(db)
        db.add_metric(
            name="total_revenue",
            expression="SUM(orders.amount)",
            description="Gross order revenue.\nDocs link follows on line 2.",
        )
        db.add_metric(
            name="order_count",
            expression="COUNT(orders.id)",
        )

        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        MarkdownRenderer(db, profile, output_dir).render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "## Metrics" in content
        # Metric names + expressions both surface verbatim — the agent
        # needs the expression body to template the SELECT projection.
        assert "total_revenue" in content
        assert "SUM(orders.amount)" in content
        assert "order_count" in content
        assert "COUNT(orders.id)" in content
        # Only the first line of multi-line descriptions lands inline so
        # the always-loaded overview stays compact.
        assert "Gross order revenue." in content
        assert "Docs link follows on line 2." not in content

    def test_overview_md_skips_metrics_section_when_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A profile with zero metrics emits no ``## Metrics`` heading —
        the section is conditional so the cold-start overview stays
        frontmatter-only (the legacy shape every other test in this file
        already pins).
        """
        db = _make_db(tmp_path)
        _populate_db(db)
        profile = _make_profile()
        output_dir = tmp_path / "markdown"
        monkeypatch.setenv("MCS_DATA_DIR", str(tmp_path))

        MarkdownRenderer(db, profile, output_dir).render_overview()

        content = (output_dir / "_overview.md").read_text(encoding="utf-8")
        assert "## Metrics" not in content
