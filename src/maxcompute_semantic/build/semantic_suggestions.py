# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Annotation suggestion generation from workload evidence, profile stats,
and column naming patterns.

Suggestions are stored in the ``annotation_suggestions`` table with
``status='suggested'`` and are **never** written to confirmed
annotation columns (``semantic_role``, ``dim_type``, etc.). The agent
should review suggestions before writing annotations via the proposal workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from maxcompute_semantic.build._naming import (
    DIM_NAME_RE,
    ID_NAME_RE,
    METRIC_NAME_RE,
    PRE_AGGREGATED_NAME_RE,
    TIME_DIM_NAME_RE,
    TIME_NAME_RE,
)

# Confidence ceiling applied (via min()) to any role whose evidence
# includes workload signals — protects against runaway accumulation
# when multiple evidence sources stack.
_WORKLOAD_CAP = 0.85
# Fixed confidence assigned to the attribute-fallback role when no
# strong signal classifies a column. Not a cap; a default value.
_NAME_ONLY_CONFIDENCE = 0.35

# NDV-tier dimension boosts: (max_ndv, boost, tier_label, max_ratio, string_only).
# Boost magnitudes are calibrated so ``tiny`` and ``large`` NDV alone
# clear the dim_conf > 0.3 gate, while ``small`` and ``medium`` need
# either an additional signal or a low-enough ratio to avoid
# false-positive on high-row-count tables. ``max_ratio`` is the per-
# tier upper bound on ``approx_ndv / row_count`` — ``None`` means no
# ratio gate. ``string_only`` restricts the tier to STRING/VARCHAR/CHAR
# types; numeric types in the same NDV band are nearly always counters
# or measures, not categorical dimensions. The ``large`` tier
# (NDV up to 500, ratio < 0.20, STRING-only) was added to lift
# clinical-style categorical codes (``diagnosis`` ≈ 219 distinct
# values in a ~1.2k-row patient table) out of the attribute bucket:
# each value applies to ≥ 5 rows on average, so GROUP BY / WHERE-
# equality on the column is meaningful, and ``mcs package apply``
# would otherwise miss surfacing it as a dimension because NDV > 100
# and no name pattern matches. STRING-only because numeric columns
# in the same NDV band (``avg_pages_read`` BIGINT ndv≈400) are
# pre-aggregated measures or counters that should stay attribute.
_NDV_DIMENSION_TIERS: tuple[tuple[int, float, str, float | None, bool], ...] = (
    (10, 0.35, "tiny", None, False),
    (30, 0.25, "small", None, False),
    (100, 0.15, "medium", 0.10, False),
    (500, 0.35, "large", 0.20, True),
)


@dataclass(frozen=True)
class ColumnSuggestion:
    column_name: str
    suggested_role: str
    suggested_subtype: str | None
    confidence: float
    evidence: list[dict[str, Any]]


def suggest_column_semantics(
    *,
    table_name: str,
    columns: dict[str, dict[str, Any]],
    workload_summary: dict[str, Any],
    join_candidates: list[dict[str, Any]],
) -> list[ColumnSuggestion]:
    """Generate unconfirmed annotation suggestions for each column.

    ``columns`` maps column_name → profile stats dict.
    ``workload_summary`` is ``WorkloadSummary.to_jsonable()``.
    ``join_candidates`` is the output from ``list_join_candidates``.
    """
    suggestions: list[ColumnSuggestion] = []

    group_by_counts = workload_summary.get("group_by_counts", {})
    aggregate_counts = workload_summary.get("aggregate_counts", {})
    where_counts = workload_summary.get("where_counts", {})

    for col_name, stats in columns.items():
        # Skip partition columns.
        if stats.get("is_partition", 0) == 1:
            continue

        role, subtype, conf, evidence_list = _classify_column(
            col_name,
            table_name,
            stats,
            group_by_counts,
            aggregate_counts,
            where_counts,
            join_candidates,
        )

        if role is not None:
            suggestions.append(
                ColumnSuggestion(
                    column_name=col_name,
                    suggested_role=role,
                    suggested_subtype=subtype,
                    confidence=conf,
                    evidence=evidence_list,
                )
            )

    return _dedupe_primary_identifiers(suggestions, columns)


def _dedupe_primary_identifiers(
    suggestions: list[ColumnSuggestion],
    columns: dict[str, dict[str, Any]],
) -> list[ColumnSuggestion]:
    """Keep at most one ``identifier/primary`` per table; demote rest to ``unique``.

    Multiple columns can clear the uniqueness ≥ 0.99 gate at once (e.g. a table
    with an int autoincrement PK plus a UUID natural key both at 100% unique).
    Letting both surface as ``primary`` forces the annotation agent to guess
    which is THE primary, and the on-disk schema only stores one. Concrete
    failure shape: a ``cards`` table with both an int autoincrement ``id``
    PK and a ``uuid`` natural key both surface as primary; the agent picks
    ``uuid`` even when the surrogate ``id`` is the gold answer.

    Tie-break (in priority order):
      1. Earlier ordinal position in DDL — schema designers conventionally
         declare the primary key as column #1 across every relational
         dialect; this is the strongest signal of designer intent for
         "which of these unique columns is THE primary."
      2. Non-STRING type — integer surrogate PKs are more canonical than
         string natural keys when both qualify.
      3. Shorter column name — canonical PKs are short (``id`` < ``uuid``).
      4. Higher classification confidence — final disambiguator if the
         schema sorts ambiguously. Confidence is deliberately last here:
         the join-candidate boost stacks for any column referenced by
         other tables, which in star schemas hits BOTH the surrogate
         PK and the natural key, so it doesn't reliably pick out the
         "true" primary among already-qualified candidates.
      5. Alphabetical — deterministic last resort.

    Losers keep their evidence and gain a ``dedupe`` entry naming the winner,
    so the agent has the full context when reviewing suggestions.
    """
    primaries = [
        s
        for s in suggestions
        if s.suggested_role == "identifier" and s.suggested_subtype == "primary"
    ]
    if len(primaries) <= 1:
        return suggestions

    col_order = {name: i for i, name in enumerate(columns)}

    def sort_key(s: ColumnSuggestion) -> tuple[int, int, int, float, str]:
        col_type = columns.get(s.column_name, {}).get("type", "").upper()
        return (
            col_order.get(s.column_name, len(col_order)),
            1 if "STRING" in col_type else 0,
            len(s.column_name),
            -s.confidence,
            s.column_name,
        )

    primaries_sorted = sorted(primaries, key=sort_key)
    winner = primaries_sorted[0].column_name
    demoted = {s.column_name for s in primaries_sorted[1:]}

    result: list[ColumnSuggestion] = []
    for s in suggestions:
        if s.column_name in demoted:
            result.append(
                ColumnSuggestion(
                    column_name=s.column_name,
                    suggested_role=s.suggested_role,
                    suggested_subtype="unique",
                    confidence=s.confidence,
                    evidence=[
                        *s.evidence,
                        {
                            "source": "dedupe",
                            "demoted_from": "primary",
                            "primary_winner": winner,
                        },
                    ],
                )
            )
        else:
            result.append(s)
    return result


def _classify_column(
    col_name: str,
    table_name: str,
    stats: dict[str, Any],
    group_by_counts: dict[str, int],
    aggregate_counts: dict[str, int],
    where_counts: dict[str, int],
    join_candidates: list[dict[str, Any]],
) -> tuple[str | None, str | None, float, list[dict[str, Any]]]:
    """Classify a column into identifier, dimension, metric, or attribute."""
    evidence: list[dict[str, Any]] = []
    uniqueness = stats.get("uniqueness_ratio")
    approx_ndv = stats.get("approx_ndv")
    row_count = stats.get("row_count", 0)
    col_type = stats.get("type", "").upper()
    col_key = f"{table_name}.{col_name}"
    best_role = None
    best_subtype = None
    best_conf = 0.0

    # --- Identifier ---
    id_evidence: list[dict[str, Any]] = []
    id_conf = 0.0

    # Date / timestamp columns are time dimensions, not identifiers — even
    # when their values happen to be near-unique. Event timestamps with
    # sub-second precision frequently clear the uniqueness ≥ 0.98 gate, and
    # without this carve-out a column like ``users.LastAccessDate`` ends up
    # tagged ``identifier/primary``, which both pollutes the agent's join
    # picker and hides the column from the time-dimension role its name
    # already implies. ``DATE``/``DATETIME``/``TIMESTAMP`` substring match
    # is the lightest predicate that covers all MaxCompute time types
    # (including parametric forms like ``TIMESTAMP_NTZ`` if they ever
    # appear in user schemas).
    #
    # The STRING + TIME_NAME_RE branch catches the second common shape: a
    # column declared STRING in MaxCompute that actually stores ISO-formatted
    # date or timestamp text, named ``creation_date`` / ``last_access_date``
    # / ``created_at`` etc. This is the default representation when CSV /
    # SQLite / legacy systems are loaded into MaxCompute without explicit
    # type-coercion. Without the carve-out these STRING-typed timestamp
    # columns clear the uniqueness gate (every event has a distinct
    # millisecond) and land at ``identifier/primary``, demoting the
    # actual numeric primary key down to ``unique`` and confusing the
    # agent's join picker — exactly the failure mode seen in an
    # earlier community-content ``users``-table build where ``lastaccessdate``
    # (STRING-typed) outranked ``id`` (BIGINT).
    is_time_type = any(t in col_type for t in ("DATE", "DATETIME", "TIMESTAMP")) or (
        "STRING" in col_type and TIME_NAME_RE.search(col_name) is not None
    )

    # Name pattern.
    if not is_time_type and ID_NAME_RE.search(col_name):
        id_conf += 0.3
        id_evidence.append({"source": "name_heuristic", "pattern": "id_suffix"})

    # High uniqueness — gated by name/type shape.
    #
    # Uniqueness alone is NOT sufficient to suggest identifier. STRING
    # / DOUBLE columns whose name has no identifier shape and whose
    # type isn't integer routinely clear the 0.98 gate without being
    # identifiers — they're just descriptive attributes with a unique
    # value per row in the sample. Common false positives observed in
    # historical benchmark-smoke (multi-entity schema, 4/9 of ``circuits``):
    #   - ``circuits.name`` (STRING, uniqueness 0.986) — track name
    #   - ``circuits.url``  (STRING, uniqueness 0.986) — Wikipedia URL
    #   - ``circuits.lat``  (DOUBLE, uniqueness 1.0)   — latitude
    #   - ``circuits.lng``  (DOUBLE, uniqueness 1.0)   — longitude
    #   - ``drivers.driverref`` (STRING, uniqueness 0.994) — short ref
    #   - ``member.{first_name,last_name,phone}`` (STRING, uniqueness ~1)
    #   - ``posts.body``    (STRING, uniqueness 1)     — post content
    # Promoting these to ``identifier`` dilutes the agent's join-key
    # signal and forces ``mcs package apply`` to undo the
    # misclassification. The gate requires either an id-shape name
    # (``ID_NAME_RE`` matches OR bare suffix ``id`` / ``uuid`` / ``key``
    # / ``code``) OR an integer type (``BIGINT`` / ``INT``). Genuine
    # uniqueness-based identifiers still pass: ``id`` / ``uuid`` /
    # ``setcode`` clear via the suffix path; ``circuitid`` /
    # ``driverid`` clear via integer-type-with-id-suffix. JC-derived
    # identifiers (``link_to`` / ``xxx_id`` evidence) flow through
    # the separate JC-boost block below, so e.g. a foreign key on a
    # STRING column that joins to another table still gets surfaced.
    col_lower_for_uniq = col_name.lower()
    uniq_name_supports_id = (
        ID_NAME_RE.search(col_name) is not None
        or col_lower_for_uniq.endswith("id")
        or col_lower_for_uniq.endswith("uuid")
        or col_lower_for_uniq.endswith("key")
        or col_lower_for_uniq.endswith("code")
    )
    uniq_type_supports_id = "BIGINT" in col_type or "INT" in col_type
    if (
        not is_time_type
        and uniqueness is not None
        and uniqueness >= 0.98
        and (uniq_name_supports_id or uniq_type_supports_id)
    ):
        id_conf += 0.35
        id_evidence.append({"source": "profile_stats", "uniqueness_ratio": uniqueness})

    # Referenced in a join candidate. The ranker has already weighed
    # uniqueness, workload frequency, and join shape into the JC's
    # confidence — trust that as strong identifier evidence. A column
    # that joins to another table at confidence ≥ 0.4 is almost
    # certainly an ID column even if it uses the bare-suffix naming
    # the regex deliberately skips (``driverid``, ``raceid``,
    # ``qualifyid``, ``setid`` — see ``_naming.py`` for why we don't
    # widen the regex). The boost is graded by JC confidence so a
    # weakly-ranked candidate doesn't flip a noisy column to ID.
    best_jc_conf = 0.0
    best_jc_side: str | None = None
    best_jc_only_same_name = False
    for jc in join_candidates:
        jc_conf = float(jc.get("confidence") or 0.0)
        side: str | None = None
        if jc.get("left_col") == col_name and jc.get("left_table") == table_name:
            side = "left"
        elif jc.get("right_col") == col_name and jc.get("right_table") == table_name:
            side = "right"
        if side is not None and jc_conf > best_jc_conf:
            best_jc_conf = jc_conf
            best_jc_side = side
            ev = jc.get("evidence") or []
            kinds = {e.get("kind") for e in ev if isinstance(e, dict) and e.get("kind")}
            best_jc_only_same_name = bool(kinds) and kinds == {"same_name"}

    # Guard against ``same_name``-only JCs promoting generic-text columns
    # to identifier. ``same_name`` is the join engine's weakest signal —
    # it fires whenever two tables happen to have an identically-named
    # column, regardless of content semantics. On an entity-catalog schema
    # this produced JCs on ``cards.name = sets.name = foreign_data.name``
    # with confidence 0.6+, which then drove the suggester to mark all
    # three ``name`` columns as ``identifier/foreign`` — but ``name`` is
    # a human-readable text content column, not an identifier. The
    # agent correctly classified them as ``attribute`` during ``mcs
    # annotate batch``, but the misleading suggestion is still noise.
    #
    # The guard applies only when the JC is exclusively ``same_name``
    # (no other-kind evidence) AND the column shape doesn't already
    # imply identifier: name doesn't end in id/uuid/key/code AND type
    # isn't integer. ``link_to`` / ``xxx_id`` JCs always pass through
    # since those kinds reflect the engine recognizing an FK pattern,
    # not a coincidental name match.
    col_lower_for_jc = col_name.lower()
    name_shaped_like_id = (
        ID_NAME_RE.search(col_name) is not None
        or col_lower_for_jc.endswith("id")
        or col_lower_for_jc.endswith("uuid")
        or col_lower_for_jc.endswith("key")
        or col_lower_for_jc.endswith("code")
    )
    type_is_integer = "BIGINT" in col_type or "INT" in col_type
    jc_boost_suppressed_by_same_name = (
        best_jc_only_same_name and not name_shaped_like_id and not type_is_integer
    )

    if best_jc_side is not None and not is_time_type and not jc_boost_suppressed_by_same_name:
        if best_jc_conf >= 0.5:
            jc_boost = 0.45 if best_jc_side == "left" else 0.35
        elif best_jc_conf >= 0.4:
            jc_boost = 0.30 if best_jc_side == "left" else 0.25
        else:
            jc_boost = 0.15
        id_conf += jc_boost
        id_evidence.append(
            {"source": "join_candidate", "confidence": best_jc_conf, "side": best_jc_side}
        )

    if id_conf > 0.3:
        # Bare ``id`` / ``uuid`` is the table's own surrogate PK by
        # overwhelming SQL convention — accept slightly-lower sampled
        # uniqueness (≥ 0.95) because finite-sample bootstrap noise on
        # large tables can drop a true PK's measured ratio from 1.0 down
        # to ~0.98. Without this carve-out the strict ≥ 0.99 threshold
        # mis-tags the PK as ``foreign`` and the agent (during
        # ``mcs package apply``) then hallucinates a plausible-looking
        # ``references_target`` to satisfy the foreign subtype, producing
        # wrong joins. Concrete regression: a translations table's
        # ``id`` (true PK, sampled uniqueness 0.98) got tagged
        # ``identifier/foreign`` and the agent wrote
        # ``references: sets.id`` into the annotation, so subsequent
        # queries joined translations to its parent sets table on
        # ``st.id = s.id`` instead of the correct
        # ``st.setcode = s.code``. Non-bare names (``raceid``,
        # ``user_id``, ``setcode``) keep the strict ≥ 0.99 threshold so
        # genuine FK columns with incidentally-high uniqueness still
        # land as ``foreign``.
        col_lower = col_name.lower()
        is_bare_pk_name = col_lower in {"id", "uuid"}
        if uniqueness is not None and (
            uniqueness >= 0.99 or (is_bare_pk_name and uniqueness >= 0.95)
        ):
            subtype = "primary"
        else:
            subtype = "foreign"
        best_role = "identifier"
        best_subtype = subtype
        best_conf = min(id_conf, _WORKLOAD_CAP)
        evidence = id_evidence

    # --- Metric ---
    agg_key = f"SUM({col_key})"
    avg_key = f"AVG({col_key})"
    metric_evidence: list[dict[str, Any]] = []
    metric_conf = 0.0

    for func, key in [("SUM", agg_key), ("AVG", avg_key)]:
        if key in aggregate_counts:
            metric_conf += 0.55
            metric_evidence.append(
                {"source": "history_sql", "aggregate": func, "count": aggregate_counts[key]}
            )

    # METRIC_NAME_RE uses unanchored substring matching so it picks up
    # camelCase compounds like ``viewcount`` / ``bountyamount`` /
    # ``answercount``. Side effect: a column whose name has an
    # identifier-style ending (``id`` / ``uuid`` / ``code`` / ``key``)
    # but happens to contain a metric token as a substring matches
    # too — ``accountid`` matches the ``count`` token, ``scorecardid``
    # would match ``score`` and ``code``. These are FKs / unique
    # identifiers, not measurements; without the guard the suggester
    # writes ``metric/SUM`` and the annotation agent has to undo a
    # wrong suggestion. Concrete regression in a forum-domain
    # benchmark-smoke run: ``users.accountid`` (BIGINT, uniqueness
    # 0.92) was suggested ``metric/SUM`` because of the ``count``
    # substring, even though the agent confirmed it as
    # ``identifier/unique``.
    col_lower_for_metric = col_name.lower()
    name_looks_like_identifier = (
        col_lower_for_metric.endswith("id")
        or col_lower_for_metric.endswith("uuid")
        or col_lower_for_metric.endswith("key")
        or col_lower_for_metric.endswith("code")
    )
    if (
        METRIC_NAME_RE.search(col_name)
        and ("DOUBLE" in col_type or "BIGINT" in col_type)
        and not name_looks_like_identifier
    ):
        metric_conf += 0.35
        metric_evidence.append({"source": "name_heuristic", "pattern": "metric_name"})

    # Type-heuristic metric default. Domain-specific schemas (clinical
    # labs with ``hgb`` / ``hct`` / ``ldh`` / ``alt``; instrumentation
    # with ``rpm`` / ``psi`` / ``mwh``; finance with ``bps`` / ``pnl``)
    # use abbreviation column names that never match METRIC_NAME_RE's
    # generic English vocabulary (``amount`` / ``count`` / ``score``
    # etc.). On a freshly-built profile with no ``mcs memory verify``
    # entries — i.e. no history-SQL aggregate evidence — every numeric
    # measurement column falls through to ``attribute/fallback`` and
    # the agent has to manually override 20+ columns per table in
    # ``mcs package apply``. Concrete observation in recent benchmark
    # build sessions: a typical clinical-lab style table emits ~25
    # ``attribute`` suggestions for what are clearly numeric
    # measurements, forcing every batch annotation to override.
    #
    # The heuristic boosts numeric columns based on TYPE alone when:
    #   - the column hasn't already been promoted to metric via name
    #     match or workload (guard: metric_conf < 0.3 below the gate)
    #   - the name doesn't already look like an identifier
    #     (id/uuid/key/code suffix) — those are FKs, not measurements
    #   - the name didn't match METRIC_NAME_RE (already boosted above)
    #   - uniqueness < 0.95 (or uniqueness is None) — row-unique numeric
    #     columns (geographic coordinates, precise per-row timestamps
    #     stored as floats) are attributes, not measurements you'd
    #     ``AVG`` over; ``circuits.lat`` / ``circuits.lng`` style.
    # The 0.40 boost beats the NDV-tier-tiny dim boost (0.35) so a
    # DOUBLE column with low NDV (e.g. a small sample) still lands as
    # metric — that's the right call for continuous measurements where
    # NDV is low only because the sample is small.
    #
    # Continuous numeric types (DOUBLE/FLOAT/DECIMAL/NUMERIC) get the
    # boost unconditionally on NDV — concentrations and ratios
    # legitimately have low cardinality in small samples. Integer
    # numeric types (BIGINT/INT) require approx_ndv >= 10 to avoid
    # promoting tiny enums like ``thrombosis (0/1/2)`` or status flags
    # that are categorical dimensions even though their type is
    # integer.
    is_continuous_numeric = any(t in col_type for t in ("DOUBLE", "FLOAT", "DECIMAL", "NUMERIC"))
    is_integer_numeric = "BIGINT" in col_type or "INT" in col_type
    uniqueness_blocks_metric = uniqueness is not None and uniqueness >= 0.95
    # Pre-aggregated name guard: when the column name itself signals
    # an already-aggregated value (``avg_score``, ``mean_age``,
    # ``num_takers``, ``cnt_orders`` etc. — see ``PRE_AGGREGATED_NAME_RE``),
    # suppress the type-heuristic metric default so the agent's
    # ``mcs package apply`` doesn't write ``metrics: - {agg: AVG}``
    # for it. Failure shape: a BIGINT column whose name starts with
    # an aggregation prefix has no workload evidence, the type
    # heuristic alone promotes it to ``metric/AVG`` @0.40 conf, the
    # agent writes ``agg: AVG`` into the annotation, and the LLM
    # mechanically generates ``AVG(col)`` even though gold selects the
    # column raw (it is already an average per group). Explicit
    # workload evidence (``SUM(col)`` / ``AVG(col)`` in history_sql)
    # and explicit ``METRIC_NAME_RE`` substring matches are NOT
    # suppressed — the name-prefix guard only blocks the weakest,
    # name-blind type-heuristic fallback.
    is_pre_aggregated_name = PRE_AGGREGATED_NAME_RE.search(col_name) is not None and (
        is_continuous_numeric or is_integer_numeric
    )
    if (
        metric_conf < 0.3
        and not name_looks_like_identifier
        and not METRIC_NAME_RE.search(col_name)
        and not uniqueness_blocks_metric
        and not is_pre_aggregated_name
    ):
        if is_continuous_numeric:
            metric_conf += 0.40
            metric_evidence.append(
                {
                    "source": "type_heuristic",
                    "tier": "continuous_numeric",
                    "col_type": col_type,
                }
            )
        elif is_integer_numeric and approx_ndv is not None and approx_ndv >= 10:
            metric_conf += 0.40
            metric_evidence.append(
                {
                    "source": "type_heuristic",
                    "tier": "integer_numeric_ndv10+",
                    "col_type": col_type,
                }
            )

    # Dirty-STRING-numeric guard: a STRING-typed column that history_sql
    # happens to aggregate over (``AVG(crp)``) is NOT safe to suggest as
    # metric if the underlying values don't reliably cast to a number.
    # MaxCompute's ``CAST(... AS DOUBLE)`` is permissive (returns NULL on
    # parse failure) so the agent gets a silent partial result on dirty
    # data. The build phase emits ``COUNT(CAST(c AS DOUBLE))`` for STRING
    # columns and stores ``cast_rate``; if the column is overwhelmingly
    # non-numeric, suppress the metric suggestion so the attribute
    # fallback wins. The 0.99 threshold matches the uniqueness gate used
    # elsewhere — a metric column should be "essentially all-numeric".
    cast_rate = stats.get("cast_rate")
    metric_demoted_evidence: dict[str, Any] | None = None
    if metric_conf > 0.3 and "STRING" in col_type and cast_rate is not None and cast_rate < 0.99:
        metric_demoted_evidence = {
            "source": "profile_stats",
            "cast_rate": cast_rate,
            "tier": "dirty_string_numeric",
            "demoted_from": "measure",
        }
        metric_conf = 0.0

    if metric_conf > best_conf and metric_conf > 0.3:
        # Subtype priority: history-SQL aggregate function > name-heuristic
        # default (SUM, since name tokens like ``amount``/``total`` imply
        # additivity) > type-heuristic default (AVG, the safest aggregate
        # for an unknown continuous quantity — SUM assumes additivity that
        # ratios, concentrations, and rates lack).
        subtype = None
        for key in aggregate_counts:
            if f"({col_key})" in key:
                subtype = key.split("(")[0]
                break
        if subtype is None:
            name_boosted = any(
                e.get("source") == "name_heuristic" and e.get("pattern") == "metric_name"
                for e in metric_evidence
            )
            subtype = "SUM" if name_boosted else "AVG"
        best_role = "measure"
        best_subtype = subtype
        best_conf = min(metric_conf, _WORKLOAD_CAP)
        evidence = metric_evidence

    # --- Dimension ---
    dim_evidence: list[dict[str, Any]] = []
    dim_conf = 0.0

    if col_key in group_by_counts:
        dim_conf += 0.45
        dim_evidence.append({"source": "history_sql", "group_by_count": group_by_counts[col_key]})

    if col_key in where_counts:
        dim_conf += 0.20
        dim_evidence.append({"source": "history_sql", "where_count": where_counts[col_key]})

    # Name pattern: _type, _status, _label etc. are strong dimension signals.
    if DIM_NAME_RE.search(col_name):
        dim_conf += 0.20
        dim_evidence.append({"source": "name_heuristic", "pattern": "dim_suffix"})

    # Time-dimension name boost. Temporal columns (``date``,
    # ``releasedate``, ``created_at`` etc.) inherently have unbounded
    # cardinality — every new day adds a distinct value — so they
    # routinely exceed the NDV-tier ceiling of 100 even on small
    # tables. Without a name-only signal they fall through to
    # ``attribute``, demoting columns that the human reader (and the
    # ``mcs package apply`` agent reviewing suggestions) would
    # immediately recognize as time dimensions.
    #
    # Concrete regression motivating this branch — an entity-catalog schema:
    #   - ``rulings.date`` (STRING, ndv 110/87769) suggested as attribute
    #   - ``sets.releasedate`` (STRING, ndv 347/551) suggested as attribute
    #   - ``cards.originalreleasedate`` (STRING, ndv 370/56822) suggested as attribute
    # The boost of 0.35 alone (clears the > 0.3 gate) matches the
    # NDV-tier "tiny" boost so the column lands as a low-confidence
    # ``dimension/time`` suggestion the agent then reviews against
    # workload and type evidence. The stricter ``TIME_DIM_NAME_RE``
    # (vs the looser ``TIME_NAME_RE`` used in the identifier
    # carve-out) excludes ambiguous bare ``time$`` suffix to keep
    # duration columns (``laptime``, ``fastestlaptime``) classified
    # as attribute, not time dimension.
    if TIME_DIM_NAME_RE.search(col_name):
        dim_conf += 0.35
        dim_evidence.append({"source": "name_heuristic", "pattern": "time_dim_suffix"})

    # Content-driven temporal signal. The build phase's
    # ``_date_format_hint`` inspects ``sample_values_json`` and tags a
    # STRING column ``str-date`` / ``str-datetime`` when ≥ 50% of the
    # sampled values match the ISO date/datetime shape. This is stronger
    # evidence than name regex — the column literally STORES dates,
    # regardless of what the schema designer named it. Concrete
    # regression observed in recent build sessions: a clinical-history
    # style table has a STRING ``description`` column whose stored values
    # are ISO dates (``"1995-04-13"`` etc.) — content-detected as
    # ``str-date`` but ``description`` matches no temporal regex, so the
    # column landed at ``attribute/fallback``. The 0.40 boost clears the
    # 0.3 gate by itself, so format_hint alone can promote a column to
    # ``dimension/time`` even with no other signal. ``_dimension_subtype``
    # below reads ``format_hint`` to set the subtype to ``time`` even
    # when no temporal name pattern matches.
    format_hint = stats.get("format_hint")
    if format_hint in ("str-date", "str-datetime"):
        dim_conf += 0.40
        dim_evidence.append({"source": "profile_stats", "format_hint": format_hint})

    # Low cardinality from profiling: tiered by NDV magnitude.
    # Constant-value columns (approx_ndv <= 1, i.e. a single distinct
    # non-null value or all-null) are excluded from the NDV-tier boost
    # — a column that never varies cannot discriminate rows in
    # ``GROUP BY`` or ``WHERE``, so promoting it to dimension on
    # low-cardinality alone is a false positive. Explicit workload
    # evidence (``group_by_counts`` / ``where_counts``) and name
    # patterns above still apply, so a user who genuinely groups by a
    # constant column would still get the suggestion — but the silent
    # NDV-only promotion path is closed. Concrete regression observed
    # in build sessions: a session-properties table had a ``u_pro``
    # STRING column whose every sampled value was the literal ``"-"``
    # placeholder (NDV=1); the NDV-tier ``tiny`` boost alone
    # (+0.35 > 0.3 gate) classified it as ``dimension/categorical``,
    # which the agent then promoted into the table's
    # ``dimensions: [..., u_pro, ...]`` annotation list and the LLM
    # downstream wasted JSON tokens on a non-informative column.
    is_string_type = any(t in col_type for t in ("STRING", "VARCHAR", "CHAR"))
    if approx_ndv is not None and approx_ndv > 1 and row_count > 0:
        for max_ndv, boost, tier, max_ratio, string_only in _NDV_DIMENSION_TIERS:
            if approx_ndv > max_ndv:
                continue
            if string_only and not is_string_type:
                break
            if max_ratio is not None and (approx_ndv / row_count) >= max_ratio:
                break
            dim_conf += boost
            dim_evidence.append({"source": "profile_stats", "approx_ndv": approx_ndv, "tier": tier})
            break

    if dim_conf > best_conf and dim_conf > 0.3:
        subtype = _dimension_subtype(col_name, col_type, approx_ndv, format_hint)
        best_role = "dimension"
        best_subtype = subtype
        best_conf = min(dim_conf, _WORKLOAD_CAP)
        evidence = dim_evidence

    # --- Attribute (fallback for columns with no strong role) ---
    if best_role is None:
        best_role = "attribute"
        best_subtype = None
        best_conf = _NAME_ONLY_CONFIDENCE
        evidence = [{"source": "name_heuristic", "pattern": "fallback"}]

    # Pre-aggregated name surface: when the column name itself signals
    # an already-aggregated value AND the classifier landed on attribute
    # (i.e. workload/profile signals didn't override the name guard),
    # boost the suggestion's confidence above the markdown render
    # threshold (``_SUGGESTION_MIN_CONFIDENCE = 0.5``) and attach
    # explicit evidence so the agent reading ``annotation_suggestions``
    # in the per-table markdown sees the "do NOT re-aggregate" hint
    # during ``mcs package apply``. Without the boost the row gets
    # dropped from the markdown (silent name_heuristic/fallback @ 0.35)
    # and the agent has no signal that the column is anything other
    # than a generic numeric measurement.
    if is_pre_aggregated_name and best_role == "attribute":
        best_conf = 0.65
        evidence = [
            {
                "source": "name_heuristic",
                "pattern": "pre_aggregated",
                "note": (
                    "name prefix indicates value is already an aggregate; "
                    "SELECT directly, do NOT re-aggregate with SUM/AVG/COUNT"
                ),
            }
        ]

    # Surface the metric-demotion trail on whatever role finally won, so
    # the agent reading ``annotation_suggestions`` sees why we did NOT
    # suggest metric for a column that history_sql aggregated over.
    if metric_demoted_evidence is not None:
        evidence = [*evidence, metric_demoted_evidence]

    return best_role, best_subtype, best_conf, evidence


def _dimension_subtype(
    col_name: str,
    col_type: str,
    approx_ndv: int | None,
    format_hint: str | None = None,
) -> str:
    """Determine dimension subtype.

    Priority order:
      1. ``format_hint`` — content-detected ``str-date`` / ``str-datetime``
         is the strongest temporal signal (the column literally STORES
         dates per sample inspection, regardless of name).
      2. TIME_NAME_RE (the broader pattern used for the identifier
         carve-out, matching ``date|time|created|updated|ds|pt``) OR
         TIME_DIM_NAME_RE (the stricter dim-boost pattern that adds the
         ``_at$`` suffix common in event-timestamp columns like
         ``created_at`` / ``updated_at``). The union covers all common
         temporal naming shapes; bare ``time$`` matches TIME_NAME_RE
         which is intentional here — if the column has already cleared
         the dim confidence gate via workload evidence (e.g. is in
         ``GROUP BY``), classifying it as ``time`` is more useful than
         ``ordinal``.
      3. STRING + NDV within the large-tier ceiling (500) → categorical.
         The 500 ceiling matches the ``large`` ``_NDV_DIMENSION_TIERS``
         entry — the only path that can promote a STRING column with
         NDV > 30 to dimension already requires ratio < 0.20, which
         means each value covers ≥ 5 rows and the column is
         categorical (diagnosis codes, country names, department
         codes) rather than ordinal.
      4. Default → ordinal.
    """
    if format_hint in ("str-date", "str-datetime"):
        return "time"
    if TIME_NAME_RE.search(col_name) or TIME_DIM_NAME_RE.search(col_name):
        return "time"
    if "STRING" in col_type and approx_ndv is not None and approx_ndv <= 500:
        return "categorical"
    return "ordinal"
