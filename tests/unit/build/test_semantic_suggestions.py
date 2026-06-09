"""Tests for build/semantic_suggestions.py — annotation suggestion logic."""

from __future__ import annotations

from maxcompute_semantic.build.semantic_suggestions import suggest_column_semantics


class TestSuggestColumnSemantics:
    def test_suggest_identifier_from_uniqueness_and_join_candidate(self) -> None:
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.999,
                    "approx_ndv": 1000,
                    "row_count": 1001,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "cards",
                    "left_col": "uuid",
                    "right_table": "legalities",
                    "right_col": "uuid",
                    "confidence": 0.86,
                },
            ],
        )

        assert suggestions[0].column_name == "uuid"
        assert suggestions[0].suggested_role == "identifier"
        assert suggestions[0].suggested_subtype in {"primary", "foreign"}

    def test_suggest_dimension_and_measure_from_workload(self) -> None:
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "rarity": {"type": "STRING", "approx_ndv": 5, "row_count": 1000},
                "convertedmanacost": {"type": "DOUBLE", "approx_ndv": 20, "row_count": 1000},
            },
            workload_summary={
                "group_by_counts": {"cards.rarity": 4},
                "aggregate_counts": {"SUM(cards.convertedmanacost)": 3},
                "where_counts": {"cards.rarity": 8},
            },
            join_candidates=[],
        )

        by_col = {s.column_name: s for s in suggestions}
        assert by_col["rarity"].suggested_role == "dimension"
        assert by_col["rarity"].suggested_subtype == "categorical"
        assert by_col["convertedmanacost"].suggested_role == "measure"
        assert by_col["convertedmanacost"].suggested_subtype == "SUM"

    def test_attribute_fallback_for_unclear_columns(self) -> None:
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "notes": {"type": "STRING", "approx_ndv": 500, "row_count": 100},
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )

        assert suggestions[0].suggested_role == "attribute"
        assert suggestions[0].confidence <= 0.35

    def test_dimension_from_low_cardinality_without_workload(self) -> None:
        """approx_ndv <= 10 alone triggers dimension (tiered), even without workload."""
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "order_status": {"type": "STRING", "approx_ndv": 5, "row_count": 10000},
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert len(suggestions) == 1
        assert suggestions[0].suggested_role == "dimension"
        assert suggestions[0].suggested_subtype == "categorical"
        assert suggestions[0].confidence >= 0.35

    def test_constant_value_ndv1_falls_through_to_attribute(self) -> None:
        """approx_ndv=1 (constant column or single non-null value) must
        NOT trigger the NDV-tier dimension boost — a column with no
        variance cannot discriminate rows in GROUP BY or WHERE, so
        promoting it to dimension is a false positive that pollutes
        the annotated dimensions list. With no other signal it should
        fall through to attribute fallback.
        """
        suggestions = suggest_column_semantics(
            table_name="session_props",
            columns={
                # Stand-in for the build-session pattern where every
                # sampled value collapsed to the literal "-" placeholder.
                "placeholder_field": {
                    "type": "STRING",
                    "approx_ndv": 1,
                    "row_count": 5000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "attribute"
        assert suggestions[0].confidence <= 0.35

    def test_ndv2_low_ratio_still_hits_tiny_tier(self) -> None:
        """Inverse-direction guard: a column with NDV=2 (the smallest
        legitimate categorical, e.g. a boolean flag) and a low ratio
        must still hit the NDV-tier ``tiny`` boost so we don't
        accidentally exclude real low-cardinality dimensions while
        excluding constants.
        """
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "is_active": {
                    "type": "STRING",
                    "approx_ndv": 2,
                    "row_count": 10000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "dimension"
        assert suggestions[0].confidence >= 0.35

    def test_dimension_from_high_ndv_low_ratio_clinical_style(self) -> None:
        """Clinical/scientific categorical codes routinely sit in the
        100–500 NDV range with a low row-share — diagnosis codes,
        species names, ICD categories, department codes. Each value
        applies to many rows (avg ≥ 5), so GROUP BY / WHERE-equality
        is meaningful and the column should be surfaced as a
        dimension by ``mcs package apply`` even without name
        suffixes or workload hits. The ``large`` NDV tier
        (ndv ≤ 500, ratio < 0.20) is the dedicated path.

        Concrete profile: a 1.2k-row patient table with 219 distinct
        diagnosis labels (ratio ≈ 0.18). Before the large tier,
        this fell through to attribute fallback because ndv > 100
        exhausted the medium tier and no other signal applied.
        """
        suggestions = suggest_column_semantics(
            table_name="patient",
            columns={
                "diagnosis": {
                    "type": "STRING",
                    "approx_ndv": 219,
                    "row_count": 1238,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "dimension"
        assert suggestions[0].suggested_subtype == "categorical"
        assert suggestions[0].confidence >= 0.35

    def test_large_tier_ratio_gate_rejects_high_uniqueness(self) -> None:
        """The ``large`` tier's 0.20 ratio gate excludes columns that
        are too unique to act as a useful grouping key. ndv=200 in a
        500-row table (ratio 0.40) means each value covers ~2.5 rows
        — too sparse for meaningful aggregation, so it stays
        attribute. Without the ratio gate the tier would mis-promote
        free-text fields like ``notes`` or ``summary`` that happen
        to have NDV in the large-tier band.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "memo": {
                    "type": "STRING",
                    "approx_ndv": 200,
                    "row_count": 500,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "attribute"

    def test_large_tier_skips_numeric_columns(self) -> None:
        """The ``large`` tier is STRING-only — a numeric column with
        ndv in the (100, 500] band is virtually always a counter,
        measurement, or precomputed aggregate, NOT a categorical
        dimension. ``avg_pages_read`` BIGINT ndv≈400 ratio 0.18 is
        the canonical anti-pattern: without the type gate the large
        tier would mis-promote it to dimension, defeating the
        pre-aggregated-name attribute fallback that surfaces the
        "do NOT re-aggregate" hint.
        """
        suggestions = suggest_column_semantics(
            table_name="metrics",
            columns={
                "bigint_count": {
                    "type": "BIGINT",
                    "approx_ndv": 200,
                    "row_count": 2000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        # Type-heuristic metric @0.40 wins because no metric-suppression
        # signals apply (no id name, no pre-aggregated prefix, no high
        # uniqueness) — confirms the large tier didn't intervene to
        # flip the role to dimension.
        assert suggestions[0].suggested_role == "measure"

    def test_large_tier_ndv_ceiling_at_500(self) -> None:
        """ndv > 500 exhausts every tier including ``large``. Even
        with a very low ratio (large tables with hundreds of distinct
        labels do exist) the suggester refuses to promote without a
        name/workload/format signal — 500+ distinct values is past
        the boundary where a flat ``dimensions:`` list stays useful
        for downstream consumers.
        """
        suggestions = suggest_column_semantics(
            table_name="events",
            columns={
                "tag": {
                    "type": "STRING",
                    "approx_ndv": 501,
                    "row_count": 100000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "attribute"

    def test_constant_value_with_workload_still_classified(self) -> None:
        """When a user explicitly GROUP BYs a constant column, the
        workload signal is strong enough on its own (+0.45) to clear
        the dim gate even with the NDV-tier boost suppressed.
        This documents that the NDV=1 guard only closes the silent
        promotion path — explicit user behavior is still respected.
        """
        suggestions = suggest_column_semantics(
            table_name="events",
            columns={
                "schema_version": {
                    "type": "STRING",
                    "approx_ndv": 1,
                    "row_count": 5000,
                },
            },
            workload_summary={
                "group_by_counts": {"events.schema_version": 4},
                "aggregate_counts": {},
                "where_counts": {},
            },
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "dimension"

    def test_dimension_from_name_suffix(self) -> None:
        """_type / _status / _label suffixes signal dimension even without workload."""
        suggestions = suggest_column_semantics(
            table_name="bond",
            columns={
                "bond_type": {"type": "STRING", "approx_ndv": 3, "row_count": 100},
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "dimension"
        assert suggestions[0].confidence >= 0.35

    def test_identifier_from_bare_id_suffix_via_join_candidate(self) -> None:
        """Flat-camel naming (``driverid``, ``raceid``,
        ``qualifyid``) doesn't match ID_NAME_RE — the regex requires
        ``_id$`` or ``^id$`` to avoid catching English-word collisions
        like ``paid``, ``void``, ``droid``. The suggester must still
        classify these columns as identifiers when a high-confidence
        join candidate is present, because the JC ranker has already
        verified the column behaves like a join key.
        """
        suggestions = suggest_column_semantics(
            table_name="qualifying",
            columns={
                "raceid": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.05,
                    "approx_ndv": 1,
                    "row_count": 20,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "qualifying",
                    "left_col": "raceid",
                    "right_table": "races",
                    "right_col": "raceid",
                    "confidence": 0.59,
                },
            ],
        )

        assert suggestions[0].suggested_role == "identifier"
        # left_col with confidence >= 0.5 → +0.45 → above 0.3 threshold,
        # but the column has no name-match and uniqueness < 0.98 so the
        # JC boost alone carries it across.
        assert suggestions[0].confidence > 0.3
        # Low uniqueness → foreign subtype.
        assert suggestions[0].suggested_subtype == "foreign"

    def test_low_confidence_join_candidate_does_not_flip_attribute_to_identifier(
        self,
    ) -> None:
        """JC confidence < 0.4 only contributes a weak +0.15, which
        alone (without name or uniqueness signals) keeps the column at
        attribute fallback.
        """
        suggestions = suggest_column_semantics(
            table_name="logs",
            columns={
                "session": {"type": "STRING", "approx_ndv": 100, "row_count": 1000},
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "logs",
                    "left_col": "session",
                    "right_table": "events",
                    "right_col": "session",
                    "confidence": 0.22,
                },
            ],
        )
        assert suggestions[0].suggested_role == "attribute"

    def test_only_one_primary_per_table_with_multiple_unique_columns(self) -> None:
        """Tables can have multiple columns at uniqueness ≥ 0.99 (e.g. int PK
        + UUID natural key). Without dedup the agent sees two ``primary``
        suggestions and has to guess which is THE PK — the schema only stores
        one. The dedupe pass keeps the strongest candidate as primary and
        demotes the rest to ``unique`` (a separately-valid ``id_type``).

        Tie-break: confidence → DDL ordinal → name length → non-STRING type
        → alphabetical. Here ``id`` wins because it comes first in DDL order
        and is shorter — both schema-design heuristics.
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                # DDL order: id first, uuid second — both 100% unique.
                "id": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )

        by_col = {s.column_name: s for s in suggestions}
        assert by_col["id"].suggested_subtype == "primary"
        assert by_col["uuid"].suggested_subtype == "unique"
        # Demoted column carries a ``dedupe`` evidence entry pointing at winner.
        assert any(
            e.get("source") == "dedupe" and e.get("primary_winner") == "id"
            for e in by_col["uuid"].evidence
        )

    def test_dedupe_prefers_ddl_ordinal_over_jc_confidence_boost(self) -> None:
        """Among already-qualified primaries (uniqueness ≥ 0.99), the
        column declared FIRST in DDL wins regardless of which one
        accumulated more confidence from join-candidate boosts. Reason:
        join-candidate boosts stack for any column referenced by other
        tables — in a star schema BOTH the surrogate PK and the natural
        key get boosted, so confidence doesn't reliably distinguish
        "true" primary from natural key among already-unique columns.
        Schema designer's column order is the cleanest signal of intent.

        Real-world ``cards``-style entity:
        ``id`` (BIGINT autoincrement, declared first) and ``uuid``
        (STRING natural key, declared later) both 100% unique. ``uuid``
        appears as left_col in 5+ JOIN candidates against child tables;
        ``id`` only appears as a same-table identifier. JC-confidence-first
        sort made ``uuid`` win primary against the canonical answer.
        DDL-first sort restores ``id`` as primary.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                # DDL position 0 — surrogate int PK, no JC boost.
                "order_id": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
                # DDL position 1 — natural key STRING, heavy JC boost.
                "row_uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                # Strong JC on row_uuid — child tables FK to the natural key.
                {
                    "left_table": "orders",
                    "left_col": "row_uuid",
                    "right_table": "order_items",
                    "right_col": "order_uuid",
                    "confidence": 0.85,
                },
            ],
        )
        by_col = {s.column_name: s for s in suggestions}
        # DDL-first tie-break: order_id (position 0) wins primary;
        # row_uuid (position 1, higher confidence) demoted to unique.
        assert by_col["order_id"].suggested_subtype == "primary"
        assert by_col["row_uuid"].suggested_subtype == "unique"

    def test_single_primary_passthrough_no_dedupe(self) -> None:
        """When only one column qualifies as primary, dedupe is a no-op —
        no spurious ``unique`` demotions or ``dedupe`` evidence entries.
        """
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "id": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
                "name": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.5,
                    "approx_ndv": 500,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        assert by_col["id"].suggested_subtype == "primary"
        # No dedupe metadata on the lone primary.
        assert not any(e.get("source") == "dedupe" for e in by_col["id"].evidence)

    def test_dedupe_breaks_tie_by_ddl_order_when_confidence_equal(self) -> None:
        """With identical confidence, DDL ordinal position decides. Mirrors
        the convention that PKs are conventionally declared as column #1.

        Both columns: BIGINT, 100% unique, no name match, no join
        candidate — classifier emits the same confidence (0.35 from
        uniqueness alone). Both names are the same length and both are
        non-STRING, so the tie cascades all the way to DDL order: the
        first-declared column wins.
        """
        suggestions = suggest_column_semantics(
            table_name="t",
            columns={
                "alpha_pk": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
                "betas_pk": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        # alpha_pk declared first wins primary; betas_pk demoted.
        assert by_col["alpha_pk"].suggested_subtype == "primary"
        assert by_col["betas_pk"].suggested_subtype == "unique"

    def test_timestamp_column_with_high_uniqueness_not_identifier(self) -> None:
        """``users.LastAccessDate`` style: TIMESTAMP column with near-unique
        per-row values would clear the uniqueness ≥ 0.98 gate and end up
        tagged ``identifier/primary`` without the date-type carve-out.
        That misclassification both pollutes join inference (the agent
        might propose joining tables on event timestamps) and hides the
        time-dimension role the column actually plays.

        The expected outcome is dimension/time — the dimension branch
        picks up the ``date`` name suffix via TIME_NAME_RE.
        """
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "lastaccessdate": {
                    "type": "TIMESTAMP",
                    "uniqueness_ratio": 0.998,
                    "approx_ndv": 5000,
                    "row_count": 5010,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "identifier"
        # No identifier evidence should have accumulated.
        assert not any(
            e.get("source") in ("profile_stats", "name_heuristic")
            and (e.get("uniqueness_ratio") is not None or e.get("pattern") == "id_suffix")
            for e in s.evidence
        )

    def test_date_type_column_with_id_suffix_name_not_identifier(self) -> None:
        """Schema with a DATE column named like ``order_id_date`` — the
        name carries an ``id`` substring but the DATE type wins: this is
        not an identifier even though ID_NAME_RE would otherwise boost
        confidence on the name.

        Constructed name here doesn't actually match ID_NAME_RE (requires
        terminal ``_id``/``^id$``/``uuid$``/``_key$``/``code$``) — the
        test pins the broader behavior: any DATE/DATETIME/TIMESTAMP
        column is exempt from the identifier branch entirely.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "event_id": {  # name matches ID_NAME_RE BUT type is DATE
                    "type": "DATE",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 100,
                    "row_count": 100,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "identifier"

    def test_string_typed_date_column_not_identifier(self) -> None:
        """Real-world load: ``users.LastAccessDate`` is declared STRING
        in MaxCompute (the importer keeps SQLite DATETIME values as text
        for safe round-tripping), so the ``"DATE" in col_type`` branch of
        the carve-out misses it. Without the STRING+name carve-out, every
        per-event timestamp clears uniqueness ≥ 0.98 and the column lands
        at ``identifier/primary``, then demotes the real numeric PK to
        ``unique`` — exactly the misclassification seen in an earlier
        ``users``-style table build at 0.4.0a30.
        """
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "lastaccessdate": {
                    "type": "STRING",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 5000,
                    "row_count": 5010,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "identifier"

    def test_bare_id_with_sample_noise_uniqueness_classified_primary(self) -> None:
        """Real-world ``set_translations.id``-style regression:
        the true surrogate PK has
        sampled uniqueness 0.98 because the dump's bootstrap sample
        doesn't fully exhaust the value space on a multi-thousand-row
        table. Without the bare-PK-name carve-out, the strict ≥ 0.99
        threshold at the primary-vs-foreign branch tagged the column
        ``identifier/foreign`` — and the agent's ``mcs package apply``
        pass then hallucinated ``references: sets.id`` to satisfy
        the foreign subtype, producing wrong joins like
        ``set_translations.id = sets.id`` instead of the correct
        ``set_translations.setcode = sets.code``.

        The carve-out limits the leniency to columns literally named
        ``id`` or ``uuid`` (the two universal surrogate-PK names);
        suffix names like ``raceid``/``user_id``/``setcode`` still
        require the strict ≥ 0.99 threshold so genuine FK columns
        with incidentally-high uniqueness stay tagged foreign.
        """
        suggestions = suggest_column_semantics(
            table_name="set_translations",
            columns={
                "id": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.98,
                    "approx_ndv": 980,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "identifier"
        assert suggestions[0].suggested_subtype == "primary"

    def test_bare_uuid_with_sample_noise_uniqueness_classified_primary(self) -> None:
        """Same carve-out as bare ``id`` — bare ``uuid`` is the
        universal surrogate-PK name on entity tables (e.g. ``cards.uuid``).
        Sampled uniqueness 0.98 due to bootstrap-sample drift must
        still land as primary (not foreign) so the agent doesn't
        hallucinate an FK target.

        Note: the high-uniqueness boost (+0.35) requires sampled
        uniqueness ≥ 0.98 to fire — below that the column never even
        clears the identifier-confidence gate (id_conf > 0.3) without
        a join-candidate boost. So 0.98 is the smallest sample-noise
        signal where the bare-PK carve-out is observable.
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.98,
                    "approx_ndv": 980,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "identifier"
        assert suggestions[0].suggested_subtype == "primary"

    def test_suffix_id_with_modest_uniqueness_stays_foreign(self) -> None:
        """Inverse-direction guard for the bare-PK carve-out:
        non-bare names like ``raceid``/``user_id`` MUST keep the
        strict ≥ 0.99 primary threshold. These columns are almost
        always foreign references (a junction table's FK column
        can clear uniqueness 0.95 if the parent table is small),
        and tagging them primary would let the agent's annotation
        pass write ``id_type: primary`` and the dedupe pass would
        then have to fight false primaries against the real PK.
        """
        suggestions = suggest_column_semantics(
            table_name="qualifying",
            columns={
                # ``raceid`` doesn't match ID_NAME_RE on its own
                # (regex requires terminal ``_id`` / ``^id$``), so it
                # only crosses the identifier threshold via a JC boost.
                "raceid": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.97,
                    "approx_ndv": 970,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "qualifying",
                    "left_col": "raceid",
                    "right_table": "races",
                    "right_col": "raceid",
                    "confidence": 0.85,
                },
            ],
        )
        assert suggestions[0].suggested_role == "identifier"
        # Bare-PK carve-out does NOT apply — uniqueness 0.97 < 0.99 →
        # foreign.
        assert suggestions[0].suggested_subtype == "foreign"

    def test_time_named_column_classified_as_time_dimension(self) -> None:
        """Real-world entity-catalog regression: temporal STRING
        columns named ``date`` / ``releasedate`` have hundreds of
        distinct values (one per release / ruling day) and so blow
        past the NDV-tier dimension boost (max 100 NDV at the
        "medium" tier). Without a name-only time-dim signal they
        fall through to ``attribute``, hiding the temporal nature
        from the annotation agent.

        - ``rulings.date`` — 110 distinct dates over 87k rows
        - ``sets.releasedate`` — 347 distinct dates over 551 rows
        - ``cards.originalreleasedate`` — 370 distinct dates

        All three should land as ``dimension/time`` purely from name
        evidence, regardless of NDV cardinality.
        """
        suggestions = suggest_column_semantics(
            table_name="sets",
            columns={
                "releasedate": {
                    "type": "STRING",
                    "approx_ndv": 347,
                    "row_count": 551,
                },
                "date": {
                    "type": "STRING",
                    "approx_ndv": 110,
                    "row_count": 87769,
                },
                "created_at": {
                    "type": "STRING",
                    "approx_ndv": 50000,
                    "row_count": 60000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        assert by_col["releasedate"].suggested_role == "dimension"
        assert by_col["releasedate"].suggested_subtype == "time"
        assert by_col["date"].suggested_role == "dimension"
        assert by_col["date"].suggested_subtype == "time"
        assert by_col["created_at"].suggested_role == "dimension"
        assert by_col["created_at"].suggested_subtype == "time"

    def test_duration_time_named_column_stays_attribute(self) -> None:
        """Inverse-direction guard for the time-dim name boost:
        columns ending in bare ``time$`` (``laptime``,
        ``fastestlaptime``, ``responsetime``) are durations /
        measurements, not temporal moments. They MUST NOT be
        promoted to ``dimension/time`` from name alone — that would
        suggest the agent annotate them as time dimensions and the
        downstream NL→SQL agent would then try to ``GROUP BY``
        them as time buckets, producing nonsensical aggregates.

        The fix uses ``TIME_DIM_NAME_RE`` (excludes bare ``time$``)
        for the dim boost, separate from the broader ``TIME_NAME_RE``
        used in the identifier carve-out.
        """
        suggestions = suggest_column_semantics(
            table_name="results",
            columns={
                "fastestlaptime": {
                    "type": "STRING",
                    "approx_ndv": 4714,
                    "row_count": 23179,
                },
                "time": {
                    "type": "STRING",
                    "approx_ndv": 5484,
                    "row_count": 23179,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        # Both should fall through to attribute — high NDV ratio means
        # no NDV-tier boost, no name-based time-dim boost (because
        # TIME_DIM_NAME_RE excludes bare ``time$``).
        assert by_col["fastestlaptime"].suggested_role == "attribute"
        assert by_col["time"].suggested_role == "attribute"

    def test_metric_name_substring_in_identifier_suffix_not_measure(self) -> None:
        """METRIC_NAME_RE uses unanchored substring matching to catch
        camelCase compounds like ``viewcount`` / ``bountyamount``.
        Side effect without the guard: an identifier-suffix column
        whose name contains a metric token as substring gets wrongly
        promoted to ``metric/SUM``.

        Real-world ``users.accountid``-style column
        (BIGINT, uniqueness 0.92): suggested ``metric/SUM`` because
        the ``count`` substring matched METRIC_NAME_RE, but the
        agent's confirmed annotation is ``identifier/unique``. The
        guard checks for ``id`` / ``uuid`` / ``key`` / ``code``
        endings — those columns are almost always FKs / unique IDs,
        not measurements, regardless of substring collisions.
        """
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "accountid": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.92,
                    "approx_ndv": 37273,
                    "row_count": 40325,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        # Must NOT be metric — the guard suppresses the false-positive
        # metric suggestion. Will fall through to attribute (the agent
        # can promote to identifier on review).
        assert suggestions[0].suggested_role != "measure"

    def test_metric_name_in_real_measure_column_still_measure(self) -> None:
        """Inverse-direction guard for the identifier-suffix metric
        suppression: ``viewcount`` / ``bountyamount`` / ``answercount``
        end in ``count`` / ``amount`` (metric tokens, NOT identifier
        endings), so they MUST continue to land as ``metric``. Verifies
        the guard doesn't over-suppress.
        """
        suggestions = suggest_column_semantics(
            table_name="posts",
            columns={
                "viewcount": {"type": "BIGINT", "approx_ndv": 3845, "row_count": 91966},
                "bountyamount": {"type": "BIGINT", "approx_ndv": 7, "row_count": 38930},
                "answercount": {"type": "BIGINT", "approx_ndv": 31, "row_count": 91966},
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        # bountyamount has approx_ndv=7 which hits the NDV-tiny dim
        # tier (+0.35) on top of the metric name boost (+0.35). The
        # dim then wins on the tied score, which is correct — a
        # column with only 7 distinct values is a categorical
        # signal even if the name says "amount". We only require
        # that viewcount/answercount stay as metric.
        assert by_col["viewcount"].suggested_role == "measure"
        assert by_col["answercount"].suggested_role == "measure"

    def test_same_name_only_jc_does_not_promote_generic_text_to_identifier(
        self,
    ) -> None:
        """Regression: an entity-catalog schema has a same_name edge
        ``cards.name = sets.name = foreign_data.name`` recorded by the
        join engine at confidence 0.6+. Without this guard the
        suggester takes that JC as identifier evidence (+0.45 boost),
        crossing the 0.3 threshold and tagging the STRING ``name``
        column as ``identifier/foreign``. But ``name`` is a
        human-readable text column, not an identifier.

        Guard: a same_name-ONLY JC (no link_to / xxx_id corroboration)
        must NOT promote a column to identifier unless the column's
        shape independently looks like an ID (name suffix
        id/uuid/key/code OR integer type).
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "name": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.4,
                    "approx_ndv": 4000,
                    "row_count": 10000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "cards",
                    "left_col": "name",
                    "right_table": "sets",
                    "right_col": "name",
                    "confidence": 0.65,
                    "evidence": [{"kind": "same_name"}],
                },
            ],
        )
        # STRING ``name`` doesn't end in id/uuid/key/code and isn't
        # integer-typed → guard suppresses the JC boost → no other
        # identifier signal → falls through to attribute.
        assert suggestions[0].suggested_role == "attribute"

    def test_same_name_jc_still_promotes_id_shaped_column(self) -> None:
        """Inverse-direction guard: when a same_name JC fires on a
        column whose NAME already looks like an ID (suffix
        id/uuid/key/code), the JC boost must still apply because the
        name shape is independent corroboration that the column is an
        identifier. Concrete case: an entity-catalog schema has
        ``cards.uuid = legalities.uuid`` recorded as same_name; the
        suffix ``uuid`` is the corroboration that distinguishes this
        from a coincidental ``name`` collision.
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.05,
                    "approx_ndv": 100,
                    "row_count": 2000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "cards",
                    "left_col": "uuid",
                    "right_table": "legalities",
                    "right_col": "uuid",
                    "confidence": 0.65,
                    "evidence": [{"kind": "same_name"}],
                },
            ],
        )
        # uuid suffix → name_shaped_like_id → JC boost passes through.
        assert suggestions[0].suggested_role == "identifier"

    def test_same_name_jc_still_promotes_integer_typed_column(self) -> None:
        """Integer-typed corroboration: a BIGINT column with a
        same_name JC keeps the boost because BIGINT/INT is itself a
        strong identifier signal (text content columns aren't
        integer-typed). Concrete case: a multi-entity schema has
        ``races.raceid = qualifying.raceid`` — both BIGINT — recorded
        as same_name. Suppressing the boost here would regress
        identifier suggestions for legitimate FK columns the engine
        already cross-validated.
        """
        suggestions = suggest_column_semantics(
            table_name="races",
            columns={
                "raceid": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.4,
                    "approx_ndv": 400,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "races",
                    "left_col": "raceid",
                    "right_table": "qualifying",
                    "right_col": "raceid",
                    "confidence": 0.65,
                    "evidence": [{"kind": "same_name"}],
                },
            ],
        )
        # BIGINT type → type_is_integer → JC boost passes through.
        # raceid also ends in "id" → name_shaped_like_id is True too,
        # either path keeps the boost.
        assert suggestions[0].suggested_role == "identifier"

    def test_link_to_jc_always_promotes_regardless_of_name_shape(self) -> None:
        """The guard only applies to same_name-ONLY JCs. ``link_to``
        / ``xxx_id`` kinds reflect the engine recognizing an FK
        pattern (e.g. ``cards.set_id → sets.id``), not a coincidental
        name match — those JCs must always pass through even on
        generic-text-shaped column names.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "customer": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.05,
                    "approx_ndv": 50,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "orders",
                    "left_col": "customer",
                    "right_table": "users",
                    "right_col": "id",
                    "confidence": 0.65,
                    "evidence": [{"kind": "link_to"}],
                },
            ],
        )
        # link_to kind → not same_name-only → guard inactive → boost applies.
        assert suggestions[0].suggested_role == "identifier"

    def test_mixed_kind_jc_passes_through_guard(self) -> None:
        """Mixed-kind evidence (e.g. same_name + xxx_id) is NOT
        same_name-only, so the guard doesn't activate. This is the
        canonical safe case — the JC engine independently detected
        the xxx_id pattern, and the same_name kind is just an extra
        signal.
        """
        suggestions = suggest_column_semantics(
            table_name="t",
            columns={
                "owner": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.05,
                    "approx_ndv": 50,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[
                {
                    "left_table": "t",
                    "left_col": "owner",
                    "right_table": "u",
                    "right_col": "owner",
                    "confidence": 0.65,
                    "evidence": [{"kind": "same_name"}, {"kind": "xxx_id"}],
                },
            ],
        )
        assert suggestions[0].suggested_role == "identifier"

    def test_high_uniqueness_string_with_descriptive_name_stays_attribute(
        self,
    ) -> None:
        """Regression: a multi-entity schema had 4/9 ``circuits``-style columns
        wrongly tagged ``identifier`` via the uniqueness-only path.

        STRING ``name`` / ``url`` columns (uniqueness 0.986) cleared
        the 0.98 gate and got +0.35 to id_conf with no other identifier
        signal — agent then had to demote them to attribute.

        Guard: high uniqueness alone is no longer sufficient for
        identifier. The boost requires id-shape name OR integer type.
        """
        suggestions = suggest_column_semantics(
            table_name="circuits",
            columns={
                "name": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.986,
                    "approx_ndv": 71,
                    "row_count": 72,
                },
                "url": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.986,
                    "approx_ndv": 71,
                    "row_count": 72,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        # Neither column is integer-typed and neither name ends in
        # id/uuid/key/code, so the uniqueness boost is suppressed.
        # Falls through to attribute.
        assert by_col["name"].suggested_role == "attribute"
        assert by_col["url"].suggested_role == "attribute"

    def test_high_uniqueness_double_geographic_stays_attribute(self) -> None:
        """Regression: ``circuits.lat`` / ``circuits.lng`` (DOUBLE,
        uniqueness 1.0 in the formula_1 sample) were tagged
        ``identifier/primary`` and ``identifier/unique`` respectively,
        polluting the agent's PK signal. Geographic coordinates are
        attribute (numeric measurements), not identifiers.

        Guard: DOUBLE type with no id-shape name should NOT receive
        the uniqueness boost.
        """
        suggestions = suggest_column_semantics(
            table_name="circuits",
            columns={
                "lat": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 72,
                    "row_count": 72,
                },
                "lng": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 72,
                    "row_count": 72,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        assert by_col["lat"].suggested_role != "identifier"
        assert by_col["lng"].suggested_role != "identifier"

    def test_high_uniqueness_string_with_id_suffix_still_identifier(self) -> None:
        """Inverse-direction guard: STRING columns whose name ends in
        ``id`` / ``uuid`` / ``key`` / ``code`` still get the
        uniqueness boost. ``uuid`` (STRING, uniqueness 1.0) must still
        land as identifier — its name is the strongest possible
        identifier signal.
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 56822,
                    "row_count": 56822,
                },
                "setcode": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.99,
                    "approx_ndv": 600,
                    "row_count": 600,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        assert by_col["uuid"].suggested_role == "identifier"
        assert by_col["setcode"].suggested_role == "identifier"

    def test_high_uniqueness_bigint_no_id_name_still_identifier(self) -> None:
        """Integer-typed columns clear the guard via the type path even
        without an id-shape name. ``circuits.circuitid`` (BIGINT,
        uniqueness 0.986) and a hypothetical bare ``id`` (BIGINT) must
        still land as identifier.
        """
        suggestions = suggest_column_semantics(
            table_name="circuits",
            columns={
                "circuitid": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.986,
                    "approx_ndv": 71,
                    "row_count": 72,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "identifier"

    def test_string_id_column_still_identifier_after_time_carve_out(self) -> None:
        """Regression guard: the time-type carve-out must not affect
        STRING / BIGINT identifier classification. ``uuid`` STRING with
        high uniqueness still lands as identifier.
        """
        suggestions = suggest_column_semantics(
            table_name="cards",
            columns={
                "uuid": {
                    "type": "STRING",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 1000,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "identifier"
        assert suggestions[0].suggested_subtype == "primary"

    def test_dirty_string_numeric_demoted_from_measure(self) -> None:
        """Real-world ``laboratory.crp``-style column: ~26% numeric-castable, rest are
        codes like "negative" / "trace". History SQL has ``AVG(crp)`` (the
        DBA wrote a partial-data report), so without the cast_rate guard
        the suggester would emit ``metric/AVG`` and the agent would
        confidently ``AVG(crp)`` over data MC silently NULL-casts.

        The expected outcome is anything-but-metric, with the demotion
        trail surfaced on whichever role wins (here, attribute fallback).
        """
        suggestions = suggest_column_semantics(
            table_name="laboratory",
            columns={
                "crp": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.4,
                    "approx_ndv": 40,
                    "row_count": 100,
                    "cast_rate": 0.26,
                },
            },
            workload_summary={
                "group_by_counts": {},
                "aggregate_counts": {"AVG(laboratory.crp)": 3},
                "where_counts": {},
            },
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "measure"
        # Demotion trail attached to the winning role's evidence.
        assert any(
            e.get("source") == "profile_stats"
            and e.get("demoted_from") == "measure"
            and e.get("tier") == "dirty_string_numeric"
            for e in s.evidence
        )

    def test_clean_string_numeric_stays_as_measure(self) -> None:
        """A STRING column that happens to be fully numeric (e.g. money
        stored as text for safe round-tripping) clears the 0.99 cast_rate
        threshold, so the metric branch still wins."""
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "amount": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.5,
                    "approx_ndv": 500,
                    "row_count": 1000,
                    "cast_rate": 1.0,
                },
            },
            workload_summary={
                "group_by_counts": {},
                "aggregate_counts": {"SUM(orders.amount)": 5},
                "where_counts": {},
            },
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "measure"
        # No spurious demotion evidence on the clean column.
        assert not any(e.get("demoted_from") == "measure" for e in s.evidence)

    def test_numeric_typed_measure_unaffected_by_missing_cast_rate(self) -> None:
        """DOUBLE / BIGINT metric columns don't get cast_rate (the build
        phase only emits ``__numeric_count`` for STRING types), so the
        suggester must not penalize them for the missing field. A DOUBLE
        column with AVG() history must still classify as metric/AVG.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "total_amount": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 0.9,
                    "approx_ndv": 900,
                    "row_count": 1000,
                    # No cast_rate key — same as profile didn't compute it.
                },
            },
            workload_summary={
                "group_by_counts": {},
                "aggregate_counts": {"AVG(orders.total_amount)": 4},
                "where_counts": {},
            },
            join_candidates=[],
        )
        assert suggestions[0].suggested_role == "measure"

    def test_dirty_string_numeric_without_workload_unaffected(self) -> None:
        """If history_sql has no aggregate over the column, metric_conf
        never crosses the 0.3 gate, so the cast_rate guard is a no-op.
        The column should fall to its natural classification (attribute
        here — STRING with no other signals)."""
        suggestions = suggest_column_semantics(
            table_name="laboratory",
            columns={
                "crp": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.4,
                    "approx_ndv": 40,
                    "row_count": 100,
                    "cast_rate": 0.26,
                },
            },
            workload_summary={
                "group_by_counts": {},
                "aggregate_counts": {},
                "where_counts": {},
            },
            join_candidates=[],
        )
        s = suggestions[0]
        # No metric workload → no demotion evidence (guard never fired).
        assert not any(e.get("demoted_from") == "measure" for e in s.evidence)

    def test_bigint_without_metric_keyword_not_classified_as_measure(self) -> None:
        """Regression for the operator-precedence bug in `_classify_column`'s
        metric branch — `A and B or C` parses as `(A and B) or C`, so the
        unparenthesized `METRIC_NAME_RE.search(name) and "DOUBLE" in type or
        "BIGINT" in type` form would boost ANY BIGINT column to metric
        regardless of name. The parenthesized form must require both the
        name pattern AND the numeric type. A `user_id` BIGINT with no
        aggregate workload must NOT land in the metric role."""
        suggestions = suggest_column_semantics(
            table_name="users",
            columns={
                "user_id": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.5,
                    "approx_ndv": 500,
                    "row_count": 1000,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        assert suggestions[0].suggested_role != "measure"

    def test_continuous_numeric_without_workload_suggests_measure(self) -> None:
        """Real-world regression: a clinical-lab style table has 25+ DOUBLE
        columns named with domain abbreviations (``got``, ``gpt``, ``ldh``,
        ``alp``, ``hgb``, ``hct``, ``plt``, ``tp``, ``alb`` etc.) — none of
        these match ``METRIC_NAME_RE`` (which only covers generic English
        tokens like ``amount`` / ``count`` / ``score``). Without history-SQL
        aggregate evidence (a brand-new build with no ``mcs memory verify``
        entries yet), every numeric measurement column falls through to
        ``attribute/fallback`` and the agent has to manually override 20+
        columns per table in ``mcs package apply``.

        DOUBLE / FLOAT / DECIMAL columns whose name isn't id-shaped and
        whose values don't look row-unique (uniqueness < 0.95) should
        default to ``metric/AVG`` — that's the safest aggregation for an
        unknown continuous quantity (SUM assumes additivity that ratios
        and concentrations lack).
        """
        suggestions = suggest_column_semantics(
            table_name="laboratory",
            columns={
                "hgb": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 0.16,
                    "approx_ndv": 16,
                    "row_count": 100,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "measure"
        assert s.suggested_subtype == "AVG"
        # Evidence trail must surface the type-heuristic origin so the
        # agent reading suggestions sees this came from type alone, not
        # from a name match or history-SQL aggregate.
        assert any(
            e.get("source") == "type_heuristic" and e.get("tier") == "continuous_numeric"
            for e in s.evidence
        )

    def test_integer_numeric_with_real_ndv_without_workload_suggests_measure(
        self,
    ) -> None:
        """BIGINT / INT measurement columns (e.g. lab counts, particle
        counts) without aggregate workload should default to metric when
        the name isn't id-shaped AND the column has non-trivial NDV (>= 10
        distinct values). The NDV floor protects against tiny enums like
        ``thrombosis (0/1/2)`` or ``ana_titer`` from being promoted —
        those are categorical dimensions even though their type is
        integer. A BIGINT column with 12+ distinct values across the
        sample looks like a measurement, not a flag.
        """
        suggestions = suggest_column_semantics(
            table_name="laboratory",
            columns={
                "wbc": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.12,
                    "approx_ndv": 12,
                    "row_count": 100,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "measure"
        assert any(
            e.get("source") == "type_heuristic" and e.get("tier") == "integer_numeric_ndv10+"
            for e in s.evidence
        )

    def test_low_ndv_integer_stays_dimension_not_measure(self) -> None:
        """Inverse-direction guard for A48-1: BIGINT with ndv < 10 is
        almost certainly a categorical enum (thrombosis 0/1/2; status
        codes), so the type-heuristic metric default must NOT fire.
        The column should fall through to the dimension branch via the
        NDV-tier-tiny boost.
        """
        suggestions = suggest_column_semantics(
            table_name="patient",
            columns={
                "thrombosis": {
                    "type": "BIGINT",
                    "uniqueness_ratio": 0.03,
                    "approx_ndv": 3,
                    "row_count": 100,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "measure"
        # NDV=3 hits tiny dim tier → should land as dimension/categorical
        # (or whatever the dim branch decides), not metric.
        assert s.suggested_role == "dimension"

    def test_continuous_numeric_with_id_suffix_name_not_measure(self) -> None:
        """Inverse-direction guard for A48-1: a DOUBLE column whose name
        ends in id/uuid/key/code is almost certainly a foreign key or
        identifier stored as a floating type (rare but happens with
        ``score_code`` etc.), NOT a measurement. The
        ``name_looks_like_identifier`` guard from the existing metric
        block must also gate the type-heuristic default so we don't
        promote it.
        """
        suggestions = suggest_column_semantics(
            table_name="orders",
            columns={
                "price_code": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 0.5,
                    "approx_ndv": 50,
                    "row_count": 100,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "measure"

    def test_row_unique_continuous_numeric_not_measure(self) -> None:
        """Inverse-direction guard for A48-1: a DOUBLE column with
        uniqueness ≥ 0.95 is per-row data (geographic coordinates,
        precise measurements) — those are attributes, not measurements
        you'd ``AVG`` over. ``circuits.lat`` / ``circuits.lng`` style:
        72 distinct values across 72 rows means each row carries its
        own latitude; aggregating them would produce a meaningless
        centroid.
        """
        suggestions = suggest_column_semantics(
            table_name="circuits",
            columns={
                "lat": {
                    "type": "DOUBLE",
                    "uniqueness_ratio": 1.0,
                    "approx_ndv": 72,
                    "row_count": 72,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role != "measure"

    def test_str_date_format_hint_suggests_time_dimension(self) -> None:
        """Real-world regression: a clinical-history style table has a
        STRING ``description`` column whose stored values are ISO dates
        (``"1995-04-13"``). The build phase correctly detects this and
        writes ``format_hint='str-date'`` on the column, but the
        classifier never consults ``format_hint`` — so the column falls
        through to ``attribute/fallback`` (the name doesn't match any
        TIME_DIM regex). The agent then has to override in
        ``mcs package apply``.

        When ``format_hint='str-date'`` is present, the classifier must
        promote to ``dimension/time`` even when the column name carries
        no temporal signal — the build phase's content-driven format
        detection is stronger evidence than name regex.
        """
        suggestions = suggest_column_semantics(
            table_name="patient",
            columns={
                "description": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.95,
                    "approx_ndv": 950,
                    "row_count": 1000,
                    "format_hint": "str-date",
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "dimension"
        assert s.suggested_subtype == "time"
        assert any(
            e.get("source") == "profile_stats" and e.get("format_hint") == "str-date"
            for e in s.evidence
        )

    def test_str_datetime_format_hint_also_suggests_time_dimension(self) -> None:
        """Same as above but for ``format_hint='str-datetime'`` (STRING
        column storing ISO timestamps with time component). Both variants
        of the format hint should produce ``dimension/time`` — the
        downstream agent reads the format hint separately to choose the
        right wrapping function (``TO_DATE`` vs ``TO_TIMESTAMP``).
        """
        suggestions = suggest_column_semantics(
            table_name="events",
            columns={
                "event_ts": {
                    "type": "STRING",
                    "uniqueness_ratio": 0.9,
                    "approx_ndv": 9000,
                    "row_count": 10000,
                    "format_hint": "str-datetime",
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "dimension"
        assert s.suggested_subtype == "time"

    def test_pre_aggregated_name_suppresses_type_heuristic_measure(self) -> None:
        """A numeric column whose name starts with ``avg`` / ``mean`` /
        ``num`` / ``cnt`` / ``count`` / etc. stores an already-aggregated
        value (per-school average score, per-school student count etc.)
        and must NOT be suggested as ``metric/AVG`` by the
        type-heuristic. Otherwise the agent's ``mcs package apply``
        writes ``metrics: - {agg: AVG}`` and the downstream LLM
        mechanically applies ``AVG(avg_pages_read)`` to a column whose
        gold answer is the column selected raw — exactly the
        education-dataset failure shape observed in benchmark-full
        42424155: precomputed per-row aggregates wrapped in extra
        aggregation by the SQL generator.
        """
        suggestions = suggest_column_semantics(
            table_name="school_scores",
            columns={
                "avg_pages_read": {
                    "type": "BIGINT",
                    "approx_ndv": 400,
                    "row_count": 2200,
                    "uniqueness_ratio": 0.18,
                },
                "num_takers": {
                    "type": "BIGINT",
                    "approx_ndv": 1200,
                    "row_count": 2200,
                    "uniqueness_ratio": 0.55,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        # Both columns get an explicit attribute suggestion with the
        # pre_aggregated evidence, NOT a metric suggestion.
        assert by_col["avg_pages_read"].suggested_role == "attribute"
        assert by_col["num_takers"].suggested_role == "attribute"
        # Confidence is boosted above the 0.5 markdown surface threshold
        # so the agent actually sees the hint during annotation.
        assert by_col["avg_pages_read"].confidence >= 0.5
        assert by_col["num_takers"].confidence >= 0.5
        # Evidence carries the explicit "do not re-aggregate" note.
        for col in ("avg_pages_read", "num_takers"):
            assert any(
                e.get("pattern") == "pre_aggregated"
                and "do NOT re-aggregate" in (e.get("note") or "")
                for e in by_col[col].evidence
            ), f"{col} missing pre_aggregated evidence note"

    def test_pre_aggregated_name_workload_aggregate_still_wins(self) -> None:
        """If the user's historical SQL aggregates a pre-aggregated-named
        column (i.e. they're knowingly rolling up the precomputed
        averages), trust the workload signal — only the weakest
        type-heuristic default is suppressed by the name guard.
        ``avg_response_time_ms`` with explicit ``AVG(...)`` in
        history_sql still lands as ``metric/AVG``.
        """
        suggestions = suggest_column_semantics(
            table_name="hourly_stats",
            columns={
                "avg_response_time_ms": {
                    "type": "DOUBLE",
                    "approx_ndv": 8000,
                    "row_count": 10000,
                    "uniqueness_ratio": 0.8,
                },
            },
            workload_summary={
                "group_by_counts": {},
                "aggregate_counts": {"AVG(hourly_stats.avg_response_time_ms)": 5},
                "where_counts": {},
            },
            join_candidates=[],
        )
        s = suggestions[0]
        assert s.suggested_role == "measure"
        assert s.suggested_subtype == "AVG"

    def test_pre_aggregated_name_explicit_metric_keyword_still_wins(self) -> None:
        """``avg_score`` matches both PRE_AGGREGATED_NAME_RE (``avg`` prefix)
        AND METRIC_NAME_RE (``score`` substring). The explicit
        ``METRIC_NAME_RE`` match is a stronger signal than the
        type-heuristic suppression — let metric win so that columns the
        schema-designer named with both aggregation prefix AND a
        domain metric word still surface as metric. The pre-aggregated
        guard only blocks the WEAKEST (type-only, name-blind)
        default.
        """
        suggestions = suggest_column_semantics(
            table_name="rankings",
            columns={
                "avg_score": {
                    "type": "DOUBLE",
                    "approx_ndv": 5000,
                    "row_count": 10000,
                    "uniqueness_ratio": 0.5,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        s = suggestions[0]
        # METRIC_NAME_RE-driven ``score`` substring fires the name-heuristic
        # boost (+0.35 SUM default) which beats the pre-aggregated suppression.
        assert s.suggested_role == "measure"

    def test_pre_aggregated_short_prefix_excluded_from_match(self) -> None:
        """Bare ``avg`` / ``count`` (no trailing separator or letter)
        should NOT match the pre-aggregated guard — these are commonly
        query-time aliases (``SELECT COUNT(*) AS count``) materialized
        into derived tables and need to remain available as
        metric/SUM defaults. The guard requires a trailing ``_`` or
        an additional alphabetic char.
        """
        suggestions = suggest_column_semantics(
            table_name="t",
            columns={
                # No name match → type-heuristic fires → metric.
                "count": {
                    "type": "BIGINT",
                    "approx_ndv": 500,
                    "row_count": 1000,
                    "uniqueness_ratio": 0.4,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        # ``count`` matches METRIC_NAME_RE substring so it becomes metric/SUM
        # via the name-heuristic path (not the type-heuristic path); the
        # pre-aggregated guard is bypassed because ``count`` alone (with no
        # trailing letter/underscore) doesn't match PRE_AGGREGATED_NAME_RE.
        assert suggestions[0].suggested_role == "measure"

    def test_pre_aggregated_num_prefix_requires_underscore(self) -> None:
        """``num`` is a prefix of common non-aggregate English words
        (``number``, ``numerator``, ``numerical``, ``numeric``,
        ``numbers``). Without an ``_`` guard, the regex falsely matches
        all of these and the suggester emits a "do NOT re-aggregate"
        hint for a plain identifier like ``qualifying.number`` (the
        driver racing number — gold needs raw selection from a JOINED
        ``drivers`` table, not pre-aggregated). benchmark-full
        42441573's formula_1 cases (0114, 0117, 0136) hit this. The
        ``num_`` snake_case form is the canonical aggregation-prefix
        shape (``num_takers``, ``num_orders``) and remains accepted by
        the guard.
        """
        suggestions = suggest_column_semantics(
            table_name="qualifying",
            columns={
                # FALSE POSITIVE under the old regex: ``num`` + ``b`` →
                # match. Should now NOT match — ``number`` is an
                # identifier, not an aggregate.
                "number": {
                    "type": "BIGINT",
                    "approx_ndv": 50,
                    "row_count": 9000,
                    "uniqueness_ratio": 0.005,
                },
                # Same FP class as ``number`` — must not match.
                "numerator": {
                    "type": "BIGINT",
                    "approx_ndv": 800,
                    "row_count": 10000,
                    "uniqueness_ratio": 0.08,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        for col in ("number", "numerator"):
            for ev in by_col[col].evidence:
                assert ev.get("pattern") != "pre_aggregated", (
                    f"{col} falsely matched PRE_AGGREGATED_NAME_RE; the "
                    f"`num` prefix must require a `_` separator to "
                    f"avoid matching `number` / `numerator` etc."
                )

    def test_pre_aggregated_num_underscore_still_matches(self) -> None:
        """Sanity check: the canonical ``num_X`` snake_case form (the
        only shape ``num`` matches after the fix) still triggers the
        pre-aggregated guard. Regression guard for the underscore-
        requirement change in PRE_AGGREGATED_NAME_RE.
        """
        suggestions = suggest_column_semantics(
            table_name="t",
            columns={
                "num_takers": {
                    "type": "BIGINT",
                    "approx_ndv": 1200,
                    "row_count": 2200,
                    "uniqueness_ratio": 0.55,
                },
                "cnt_orders": {
                    "type": "BIGINT",
                    "approx_ndv": 800,
                    "row_count": 5000,
                    "uniqueness_ratio": 0.16,
                },
            },
            workload_summary={"group_by_counts": {}, "aggregate_counts": {}, "where_counts": {}},
            join_candidates=[],
        )
        by_col = {s.column_name: s for s in suggestions}
        for col in ("num_takers", "cnt_orders"):
            assert by_col[col].suggested_role == "attribute"
            assert any(ev.get("pattern") == "pre_aggregated" for ev in by_col[col].evidence), (
                f"{col} should still match PRE_AGGREGATED_NAME_RE via `_` form"
            )
