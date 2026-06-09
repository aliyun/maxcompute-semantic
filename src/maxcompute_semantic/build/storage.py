"""PackageDB — SQLite truth source for package build data.

Schema version 5 (0.4.0a13) replaces the hand-rolled ``bm25_index``
table with an FTS5 virtual table ``memory_fts`` in external-content
mode against ``memory_entries``. The ``vec_index`` virtual table
(sqlite-vec vec0) added in v4 remains alongside ``memory_fts`` for
hybrid FTS5 + vector retrieval via Reciprocal Rank Fusion.

Schema version 4 (0.4.0a12) added the ``vec_index`` vec0 virtual
table alongside the now-removed ``bm25_index``.

Schema version 3 (0.4.0a4) is **per-source-keyed**: every ``tables``
and ``joins`` row carries the ``source_key`` of the ``DataSource``
it came from. The version pin lives in SQLite's ``PRAGMA user_version``
slot so old packages are detected at open time and migrated or
rejected with ``RebuildRequiredError``.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from maxcompute_semantic.build.errors import RebuildRequiredError

# Bump when ``_SCHEMA_SQL`` changes shape. Stored in the SQLite
# header via ``PRAGMA user_version`` — fresh DBs land at 0 and we
# stamp them, existing DBs are checked at open and rejected if the
# version doesn't match.
_SCHEMA_VERSION = 13

_TRUTHY = {"1", "true", "yes", "on"}


def _auto_vector_enabled() -> bool:
    return os.environ.get("MCS_AUTO_VECTOR", "").strip().lower() in _TRUTHY


# Shorthand → canonical role mapping. Agents (especially smaller
# models) reach for SQL vocabulary like ``pk`` / ``fk`` / ``dim`` /
# ``measure`` before they reach for the OSI-aligned canonical names.
# Normalizing here lets the first attempt stick instead of forcing
# the agent into a tight-budget retry that drops the
# ``description:`` field to fit.
_ROLE_ALIASES = {
    "dim": "dimension",
    # "measure" is now the canonical column-level role — no alias needed.
    "fact": "measure",
    "attr": "attribute",
    "descriptive": "attribute",
    "id": "identifier",
    "pk": "identifier",
    "fk": "identifier",
    "primary_key": "identifier",
    "foreign_key": "identifier",
    "unique_key": "identifier",
    "reference": "identifier",
    "date": "dimension",
    "time": "dimension",
    "timestamp": "dimension",
    "datetime": "dimension",
    "temporal": "dimension",
    # General data-modeling vocabulary the agent reaches for before
    # the OSI canonical names. ``categorical`` also auto-fills
    # ``dim_type=categorical`` via ``_ROLE_IMPLIES_DIM_TYPE`` below so
    # the rule-2 check passes without an explicit dim_type.
    "categorical": "dimension",
    "numeric": "measure",
    "numerical": "measure",
    "numeric_measurable": "measure",
    "measurable": "measure",
    "quantitative": "measure",
    "free_text": "attribute",
    "text": "attribute",
    "string": "attribute",
    # Kimball star-schema vocabulary: a ``context`` column is a
    # descriptive/payload value that travels with a fact row but
    # carries no analytic role of its own — same shape as our
    # ``attribute`` canonical.
    "context": "attribute",
    # Column-name-as-role shorthand the agent reaches for when a
    # column's name itself describes its semantic kind (a column
    # called ``name`` / ``url`` / ``description`` / ``location``
    # really is just a payload string with no analytic role — the
    # ``attribute`` canonical). Same for ``const``/``constant`` (a
    # column that carries a constant value) and ``value`` (the
    # value side of an EAV pair, used as a generic payload).
    "name": "attribute",
    "url": "attribute",
    "description": "attribute",
    "location": "attribute",
    "const": "attribute",
    "constant": "attribute",
    "value": "attribute",
    # ``category`` is the singular/short form of ``categorical`` —
    # also auto-fills ``dim_type=categorical`` via
    # ``_ROLE_IMPLIES_DIM_TYPE`` below. ``status`` is universally
    # a categorical filter/grouper column (order status, account
    # status, etc.) — same auto-fill. ``code`` / ``type`` follow
    # the same shape — country code, currency code, type code,
    # enum-style typeid columns — universally categorical
    # filter/grouper columns. The agent reaches for ``role: code``
    # as a catch-all for short categorical identifiers; this lets
    # the first attempt stick.
    "category": "dimension",
    "status": "dimension",
    "code": "dimension",
    "type": "dimension",
    "enum": "dimension",
    "flag": "dimension",
    "boolean": "dimension",
    "bool": "dimension",
    # ``entity_id`` is industry-standard data-modeling vocabulary
    # for "the natural identifier of an entity row" — the EAV
    # model's E. Maps to ``identifier`` with no id_type auto-fill,
    # leaving the column's id_type slot empty (which the rule-4
    # soft-drop below absorbs). Don't auto-fill to ``primary``:
    # in some schemas (audit logs, polymorphic associations) an
    # ``entity_id`` column is a foreign reference rather than a
    # local PK, so guessing wrong loses the join-target signal.
    "entity_id": "identifier",
    # ML / data-science vocabulary the agent reaches for when the
    # table looks like a training dataset (one row per observation,
    # with a known outcome column). ``target`` / ``label`` /
    # ``outcome`` / ``response`` are all standard names for the
    # column an ML model predicts; ``feature`` / ``predictor`` /
    # ``independent`` / ``dependent`` are the model's input
    # columns. None of these carry an analytic role in the
    # dimension/metric/identifier OSI taxonomy — they're payload
    # columns whose value is what the row "is about", which maps
    # cleanly to ``attribute``. Storing them this way preserves
    # the SQL-gen signal (the column is annotated, not anonymous)
    # without forcing the agent to invent a non-existent canonical.
    "target": "attribute",
    "label": "attribute",
    "outcome": "attribute",
    "response": "attribute",
    "feature": "attribute",
    "predictor": "attribute",
    "dependent": "attribute",
    "independent": "attribute",
}
# Subset of role shorthand that also pins ``id_type``. Only fills the
# slot when the caller didn't pass an explicit ``id_type`` — explicit
# always wins.
_ROLE_IMPLIES_ID_TYPE = {
    "pk": "primary",
    "fk": "foreign",
    "primary_key": "primary",
    "foreign_key": "foreign",
    "unique_key": "unique",
    "reference": "foreign",
}
# Subset of role shorthand that also pins ``dim_type``. Same explicit-
# wins rule as ``_ROLE_IMPLIES_ID_TYPE``.
_ROLE_IMPLIES_DIM_TYPE = {
    "date": "time",
    "time": "time",
    "timestamp": "time",
    "datetime": "time",
    "temporal": "time",
    "categorical": "categorical",
    "category": "categorical",
    "status": "categorical",
    "code": "categorical",
    "type": "categorical",
    "enum": "categorical",
    "flag": "categorical",
    "boolean": "categorical",
    "bool": "categorical",
}
_ID_TYPE_ALIASES = {
    "pk": "primary",
    "fk": "foreign",
    "primary_key": "primary",
    "foreign_key": "foreign",
    "unique_key": "unique",
}
_DIM_TYPE_ALIASES = {
    "cat": "categorical",
    "category": "categorical",
    "date": "time",
    "datetime": "time",
    "timestamp": "time",
}
# Natural-English aggregator names the agent reaches for before the
# canonical SQL verb. ``average`` / ``mean`` are universally used in
# data-modeling and analytics prose; ``total`` is the most common
# narrative substitute for ``SUM`` (a metric called "total revenue"
# really means SUM(revenue)); ``minimum`` / ``maximum`` are the
# unabbreviated forms of ``MIN`` / ``MAX``; ``cnt`` / ``n`` /
# ``row_count`` / ``total_count`` are the natural shorthand for
# ``COUNT``; ``distinct_count`` / ``unique_count`` / ``nunique`` are
# the pandas / stats vocabulary for ``COUNT_DISTINCT``.
#
# This map is consulted by ``_normalize_annotation_aliases`` so that
# both an explicit ``--agg average`` flag and the ``subtype: average``
# routing path (annotate.py's column-payload coercion) land on the
# canonical verb before storage validates the agg against
# ``VALID_AGGS`` in rule-3.
_AGG_ALIASES = {
    "average": "AVG",
    "mean": "AVG",
    "avg": "AVG",
    "sum": "SUM",
    "total": "SUM",
    "sum_total": "SUM",
    "count": "COUNT",
    "cnt": "COUNT",
    "n": "COUNT",
    "row_count": "COUNT",
    "total_count": "COUNT",
    "num": "COUNT",
    "min": "MIN",
    "minimum": "MIN",
    "min_value": "MIN",
    "max": "MAX",
    "maximum": "MAX",
    "max_value": "MAX",
    "count_distinct": "COUNT_DISTINCT",
    "distinct_count": "COUNT_DISTINCT",
    "unique_count": "COUNT_DISTINCT",
    "nunique": "COUNT_DISTINCT",
    "distinct": "COUNT_DISTINCT",
}
_CANONICAL_ROLES = {"dimension", "measure", "identifier", "attribute"}

# Names that meant a column-level annotation in v9 but now refer to a
# different layer entirely. Caught *before* alias resolution so the
# error message can guide the agent to the right verb (top-level
# `mcs metric add` for named business measures, `--role measure` for
# the column-property tagging that v9 called "metric").
_LAYER_MISTAKE_ROLES = {"metric"}
_CANONICAL_ID_TYPES = {"primary", "foreign", "unique"}
_CANONICAL_DIM_TYPES = {"categorical", "time", "ordinal"}
_CANONICAL_AGGS = {"SUM", "COUNT", "AVG", "MAX", "MIN", "COUNT_DISTINCT"}


def _resolve_role(raw: str | None) -> str | None:
    """Normalize a user-supplied role string to its canonical form."""
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in _LAYER_MISTAKE_ROLES:
        from maxcompute_semantic.errors.annotate import AnnotateValidationError

        raise AnnotateValidationError(
            f"role {raw!r} is no longer a column-level annotation in mcs "
            f"0.12+; it is now a top-level entity.",
            remediation=(
                "to tag this column as aggregable, use "
                "--role measure --agg <SUM|COUNT|AVG|MAX|MIN|COUNT_DISTINCT>. "
                "to define a named business measure, use "
                '`mcs metric add NAME --expression "..."`'
            ),
        )
        return None  # unreachable; satisfies type-checker
    return _ROLE_ALIASES.get(lowered, lowered)


def _normalize_annotation_aliases(
    role: str | None,
    dim_type: str | None,
    id_type: str | None,
    agg: str | None = None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Map common shorthand to canonical enum values before validation.

    Lowercases canonical values too (``Dimension`` → ``dimension``).
    Unknown values pass through unchanged and will be caught by the
    validation matrix downstream.

    Sub-flag auto-fill only fires when the caller didn't pass an
    explicit value for that slot — explicit input always wins.

    ``agg`` is normalized via ``_AGG_ALIASES`` (case-insensitive) so
    natural-English aggregator names like ``average`` / ``mean`` /
    ``total`` / ``minimum`` land on the canonical SQL verb
    (``AVG`` / ``SUM`` / ``MIN``) before rule-3 validates against
    ``_CANONICAL_AGGS``. Already-canonical inputs (``AVG``) pass
    through unchanged; unknown inputs (``BOGUS``) pass through and
    surface as rule-3 violations downstream.
    """
    norm_role = role
    norm_dim_type = dim_type
    norm_id_type = id_type
    norm_agg = agg

    if role is not None:
        # ``_resolve_role`` handles the layer-mistake guard (e.g. raising
        # a pointer error for v9-era ``role: metric``) plus pure alias
        # resolution. The per-alias id_type / dim_type auto-fill stays
        # inline because it needs the original alias key to look up the
        # implied sub-type.
        resolved = _resolve_role(role)
        rl = role.strip().lower()
        if rl in _ROLE_ALIASES:
            norm_role = resolved
            if id_type is None and rl in _ROLE_IMPLIES_ID_TYPE:
                norm_id_type = _ROLE_IMPLIES_ID_TYPE[rl]
            if dim_type is None and rl in _ROLE_IMPLIES_DIM_TYPE:
                norm_dim_type = _ROLE_IMPLIES_DIM_TYPE[rl]
        elif rl in _CANONICAL_ROLES:
            norm_role = rl

    if norm_id_type is not None:
        it = norm_id_type.strip().lower()
        if it in _ID_TYPE_ALIASES:
            norm_id_type = _ID_TYPE_ALIASES[it]
        elif it in _CANONICAL_ID_TYPES:
            norm_id_type = it

    if norm_dim_type is not None:
        dt = norm_dim_type.strip().lower()
        if dt in _DIM_TYPE_ALIASES:
            norm_dim_type = _DIM_TYPE_ALIASES[dt]
        elif dt in _CANONICAL_DIM_TYPES:
            norm_dim_type = dt

    if norm_agg is not None:
        ag = norm_agg.strip().lower()
        if ag in _AGG_ALIASES:
            norm_agg = _AGG_ALIASES[ag]
        elif ag.upper() in _CANONICAL_AGGS:
            norm_agg = ag.upper()

    return norm_role, norm_dim_type, norm_id_type, norm_agg


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tables (
  id              INTEGER PRIMARY KEY,
  source_key      TEXT NOT NULL,
  name            TEXT NOT NULL,
  schema_hash     TEXT NOT NULL,
  last_built_at   TEXT NOT NULL,
  errors_json     TEXT,
  ai_context      TEXT DEFAULT NULL,
  table_type      TEXT DEFAULT NULL,
  build_complete  INTEGER NOT NULL DEFAULT 0,
  data_modified_at TEXT DEFAULT NULL,
  last_sampled_at  TEXT DEFAULT NULL,
  UNIQUE(source_key, name)
);

CREATE TABLE IF NOT EXISTS columns (
  table_id        INTEGER REFERENCES tables(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  type            TEXT NOT NULL,
  comment         TEXT,
  is_partition    INTEGER DEFAULT 0,
  sample_values_json TEXT,
  is_enum         INTEGER DEFAULT 0,
  null_ratio      REAL,
  distinct_count  INTEGER,
  semantic_role        TEXT DEFAULT NULL,
  dim_type             TEXT DEFAULT NULL,
  agg                  TEXT DEFAULT NULL,
  id_type              TEXT DEFAULT NULL,
  references_target    TEXT DEFAULT NULL,
  semantic_description TEXT DEFAULT NULL,
  row_count            INTEGER DEFAULT NULL,
  approx_ndv          INTEGER DEFAULT NULL,
  uniqueness_ratio    REAL DEFAULT NULL,
  cast_rate           REAL DEFAULT NULL,
  profile_scope       TEXT DEFAULT NULL,
  profile_method      TEXT DEFAULT NULL,
  profile_confidence  REAL DEFAULT NULL,
  PRIMARY KEY (table_id, name)
);

CREATE TABLE IF NOT EXISTS joins (
  id                INTEGER PRIMARY KEY,
  left_source_key   TEXT NOT NULL,
  left_table        TEXT NOT NULL,
  left_col          TEXT NOT NULL,
  right_source_key  TEXT NOT NULL,
  right_table       TEXT NOT NULL,
  right_col         TEXT NOT NULL,
  kind              TEXT NOT NULL,
  confidence        REAL NOT NULL,
  cardinality       TEXT
);

CREATE TABLE IF NOT EXISTS udfs (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  kind            TEXT NOT NULL,
  signature       TEXT,
  class_name      TEXT,
  description     TEXT,
  created_locally INTEGER DEFAULT 0,
  last_seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tables_source_name ON tables(source_key, name);
CREATE INDEX IF NOT EXISTS idx_tables_hash ON tables(schema_hash);
CREATE INDEX IF NOT EXISTS idx_columns_table ON columns(table_id);
CREATE INDEX IF NOT EXISTS idx_joins_left ON joins(left_source_key, left_table);
CREATE INDEX IF NOT EXISTS idx_joins_right ON joins(right_source_key, right_table);

CREATE TABLE IF NOT EXISTS join_candidates (
  id                  INTEGER PRIMARY KEY,
  left_source_key     TEXT NOT NULL,
  left_table          TEXT NOT NULL,
  left_col            TEXT NOT NULL,
  right_source_key    TEXT NOT NULL,
  right_table         TEXT NOT NULL,
  right_col           TEXT NOT NULL,
  confidence          REAL NOT NULL,
  status              TEXT NOT NULL DEFAULT 'suggested',
  evidence_json       TEXT NOT NULL,
  conflict_group      TEXT,
  coverage_ratio      REAL,
  right_uniqueness_ratio REAL,
  cardinality         TEXT,
  updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_join_candidates_left
  ON join_candidates(left_source_key, left_table);
CREATE INDEX IF NOT EXISTS idx_join_candidates_right
  ON join_candidates(right_source_key, right_table);

CREATE TABLE IF NOT EXISTS annotation_suggestions (
  id                  INTEGER PRIMARY KEY,
  source_key          TEXT NOT NULL,
  table_name          TEXT NOT NULL,
  column_name         TEXT NOT NULL,
  suggested_role      TEXT NOT NULL,
  suggested_subtype   TEXT,
  confidence          REAL NOT NULL,
  evidence_json       TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'suggested',
  updated_at          TEXT NOT NULL,
  UNIQUE(source_key, table_name, column_name, suggested_role)
);

CREATE INDEX IF NOT EXISTS idx_annotation_suggestions_table
  ON annotation_suggestions(source_key, table_name);

CREATE TABLE IF NOT EXISTS memory_entries (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  retrieval_text  TEXT NOT NULL,
  fts_text        TEXT,
  tags_json       TEXT,
  created_at      TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  fts_text,
  content='memory_entries',
  content_rowid='id',
  tokenize='unicode61'
);

-- external-content FTS5: the 'delete' op + old.fts_text removes
-- the index row (a plain DELETE FROM memory_fts WHERE rowid=... is
-- a no-op against an external-content table); AU emits delete-then-
-- insert to handle in-place fts_text changes.
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_entries BEGIN
  INSERT INTO memory_fts(rowid, fts_text) VALUES (new.id, new.fts_text);
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, fts_text)
    VALUES('delete', old.id, old.fts_text);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_entries BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, fts_text)
    VALUES('delete', old.id, old.fts_text);
  INSERT INTO memory_fts(rowid, fts_text) VALUES (new.id, new.fts_text);
END;

-- v7: persistent package-scope settings. Generic key-value store for
-- build-time flags that need to survive across CLI invocations.
CREATE TABLE IF NOT EXISTS package_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  expression      TEXT NOT NULL,
  description     TEXT DEFAULT NULL,
  ai_context      TEXT DEFAULT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);

CREATE TABLE IF NOT EXISTS semantic_proposals (
  id              INTEGER PRIMARY KEY,
  proposal_key    TEXT NOT NULL UNIQUE,
  target_type     TEXT NOT NULL,
  target_ref      TEXT NOT NULL,
  operation       TEXT NOT NULL,
  patch_json      TEXT NOT NULL,
  confidence      REAL NOT NULL DEFAULT 0,
  evidence_json   TEXT NOT NULL,
  provenance      TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'suggested',
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  reviewed_by     TEXT,
  reviewed_at     TEXT,
  applied_at      TEXT,
  validation_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_semantic_proposals_status
  ON semantic_proposals(status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_semantic_proposals_target
  ON semantic_proposals(target_type, target_ref);
"""

# Annotation-dependent index — must be created AFTER the upgrade ALTER
# adds ``semantic_role`` to pre-0.4.0a5 DBs, because the CREATE INDEX
# references that column. On fresh DBs the column already exists so
# this runs harmlessly right after executescript.
_ANNOTATION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_columns_role ON columns(semantic_role);
"""


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Migrate from schema v3 to v4: add vec_index virtual table.

    The vec0 virtual table is only created if sqlite-vec can be
    loaded. If it cannot, the migration still succeeds (version
    stamps to 4) but the vec_index table does not exist — vector
    search will be unavailable until the user installs [vec] extras
    and reindexes.
    """
    from maxcompute_semantic.memory.vec_ext import create_vec_table

    create_vec_table(conn)


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Migrate from schema v4 to v5: drop bm25_index, add memory_fts.

    Adds the ``fts_text`` column to ``memory_entries``, creates the
    ``memory_fts`` FTS5 virtual table (external-content) plus the three
    sync triggers, then backfills ``fts_text`` for every existing row
    via :class:`MemoryTokenizer`. The triggers auto-sync the backfilled
    rows into the FTS5 shadow index. Finally drops the obsolete
    ``bm25_index`` table. ``vec_index``, when present, is left untouched.

    When migrating from v3 (which lacks ``memory_entries`` entirely)
    the column-add and backfill are skipped — the subsequent
    ``_SCHEMA_SQL`` re-run creates ``memory_entries`` with
    ``fts_text`` already present.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_entries'"
    ).fetchone()
    if table_exists:
        existing = [
            r[0]
            for r in conn.execute("SELECT name FROM pragma_table_info('memory_entries')").fetchall()
        ]
        if "fts_text" not in existing:
            conn.execute("ALTER TABLE memory_entries ADD COLUMN fts_text TEXT")

        # Backfill fts_text BEFORE creating the FTS5 table + triggers.
        # If triggers existed first, the UPDATE on each row would fire
        # memory_au's `'delete'` op against an uninitialized shadow
        # index — that corrupts external-content FTS5.
        from maxcompute_semantic.memory.tokenizer import MemoryTokenizer

        tok = MemoryTokenizer()
        rows = conn.execute(
            "SELECT id, retrieval_text FROM memory_entries WHERE fts_text IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE memory_entries SET fts_text=? WHERE id=?",
                (tok.tokenize_for_index(row[1]), row[0]),
            )

        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
              fts_text,
              content='memory_entries',
              content_rowid='id',
              tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_entries BEGIN
              INSERT INTO memory_fts(rowid, fts_text) VALUES (new.id, new.fts_text);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_entries BEGIN
              INSERT INTO memory_fts(memory_fts, rowid, fts_text)
                VALUES('delete', old.id, old.fts_text);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_entries BEGIN
              INSERT INTO memory_fts(memory_fts, rowid, fts_text)
                VALUES('delete', old.id, old.fts_text);
              INSERT INTO memory_fts(rowid, fts_text) VALUES (new.id, new.fts_text);
            END;
            """
        )
        # Populate the FTS5 shadow index from the now-backfilled content
        # table in one shot.
        conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")

    conn.execute("DROP TABLE IF EXISTS bm25_index")
    conn.commit()


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Migrate from schema v5 to v6: add profile columns + join_candidates + annotation_suggestions.

    Adds six typed nullable columns to ``columns`` for profiling evidence,
    creates ``join_candidates`` and ``annotation_suggestions`` tables with
    indexes. Profile columns are left NULL — they are populated by the
    profiling phase during the next ``mcs build``.
    """
    existing = [
        r[0] for r in conn.execute("SELECT name FROM pragma_table_info('columns')").fetchall()
    ]
    profile_cols = {
        "row_count": "INTEGER DEFAULT NULL",
        "approx_ndv": "INTEGER DEFAULT NULL",
        "uniqueness_ratio": "REAL DEFAULT NULL",
        "profile_scope": "TEXT DEFAULT NULL",
        "profile_method": "TEXT DEFAULT NULL",
        "profile_confidence": "REAL DEFAULT NULL",
    }
    for col, col_type in profile_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE columns ADD COLUMN {col} {col_type}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS join_candidates (
          id                  INTEGER PRIMARY KEY,
          left_source_key     TEXT NOT NULL,
          left_table          TEXT NOT NULL,
          left_col            TEXT NOT NULL,
          right_source_key    TEXT NOT NULL,
          right_table         TEXT NOT NULL,
          right_col           TEXT NOT NULL,
          confidence          REAL NOT NULL,
          status              TEXT NOT NULL DEFAULT 'suggested',
          evidence_json       TEXT NOT NULL,
          conflict_group      TEXT,
          coverage_ratio      REAL,
          right_uniqueness_ratio REAL,
          cardinality         TEXT,
          updated_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_join_candidates_left
          ON join_candidates(left_source_key, left_table);
        CREATE INDEX IF NOT EXISTS idx_join_candidates_right
          ON join_candidates(right_source_key, right_table);
        CREATE TABLE IF NOT EXISTS annotation_suggestions (
          id                  INTEGER PRIMARY KEY,
          source_key          TEXT NOT NULL,
          table_name          TEXT NOT NULL,
          column_name         TEXT NOT NULL,
          suggested_role      TEXT NOT NULL,
          suggested_subtype   TEXT,
          confidence          REAL NOT NULL,
          evidence_json       TEXT NOT NULL,
          status              TEXT NOT NULL DEFAULT 'suggested',
          updated_at          TEXT NOT NULL,
          UNIQUE(source_key, table_name, column_name, suggested_role)
        );
        CREATE INDEX IF NOT EXISTS idx_annotation_suggestions_table
          ON annotation_suggestions(source_key, table_name);
        """
    )
    conn.commit()


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Migrate from schema v6 to v7: add ``package_settings`` table.

    Pure additive — no existing row reshape, no backfill. The new
    table is empty after migration; callers stamp keys into it on
    demand.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS package_settings (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Migrate from schema v7 to v8: add ``cast_rate`` column to ``columns``.

    Surfaces the STRING-numeric cast success rate measured by
    ``build_column_profile_sql``. NULL on existing rows until the next
    ``mcs build`` populates them, and NULL on rows whose underlying type
    isn't STRING (no numeric-cast probe is emitted in that case).
    """
    existing = [
        r[0] for r in conn.execute("SELECT name FROM pragma_table_info('columns')").fetchall()
    ]
    if "cast_rate" not in existing:
        conn.execute("ALTER TABLE columns ADD COLUMN cast_rate REAL DEFAULT NULL")
    conn.commit()


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Migrate from schema v8 to v9: add ``table_type`` column to ``tables``.

    Carries the pyodps ``Table.type.value`` ("MANAGED_TABLE",
    "VIRTUAL_VIEW", "MATERIALIZED_VIEW", "EXTERNAL_TABLE",
    "OBJECT_TABLE") so the build pipeline can skip per-row scanning
    for views (which re-execute their underlying SQL, often a
    multi-table JOIN that times out the per-call cost gate at 120s).

    Existing rows stay NULL — the next ``mcs build --refresh`` will
    populate them via ``phase_describe_table``. NULL is interpreted
    as "treat as table" by the pipeline gate (conservative — never
    silently changes behavior on upgrade).
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tables'"
    ).fetchone()
    if table_exists:
        existing = [r[1] for r in conn.execute("PRAGMA table_info(tables)").fetchall()]
        if "table_type" not in existing:
            conn.execute("ALTER TABLE tables ADD COLUMN table_type TEXT DEFAULT NULL")
    conn.commit()


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Migrate v9 → v10: rename column-level role 'metric' → 'measure',
    normalize matching generated suggestions, and add the top-level
    ``metrics`` table.

    Per ADR-0001 this is a hard cut — no alias window. Existing rows
    with ``semantic_role = 'metric'`` and generated suggestions with
    ``suggested_role = 'metric'`` are rewritten to ``'measure'``.
    Per ADR-0002 the new ``metrics`` table is profile-global (UNIQUE(name),
    no ``source_key`` column).
    """
    # Old (v3/v4) DBs may not have ``semantic_role`` on ``columns`` —
    # the column is added by the post-migration annotation ALTER pass
    # in ``PackageDB.__init__``. If it's missing here, there's nothing
    # to rewrite and the UPDATE would raise OperationalError.
    column_names = {r[1] for r in conn.execute("PRAGMA table_info(columns)").fetchall()}
    if "semantic_role" in column_names:
        conn.execute("UPDATE columns SET semantic_role = 'measure' WHERE semantic_role = 'metric'")
    suggestion_table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation_suggestions'"
    ).fetchone()
    if suggestion_table_exists:
        suggestion_column_names = {
            r[1] for r in conn.execute("PRAGMA table_info(annotation_suggestions)").fetchall()
        }
        if "suggested_role" in suggestion_column_names:
            if {
                "source_key",
                "table_name",
                "column_name",
                "suggested_role",
            } <= suggestion_column_names:
                conn.execute(
                    """
                    DELETE FROM annotation_suggestions
                    WHERE suggested_role = 'metric'
                      AND EXISTS (
                        SELECT 1
                        FROM annotation_suggestions AS existing
                        WHERE existing.source_key =
                              annotation_suggestions.source_key
                          AND existing.table_name =
                              annotation_suggestions.table_name
                          AND existing.column_name =
                              annotation_suggestions.column_name
                          AND existing.suggested_role = 'measure'
                      )
                    """
                )
            conn.execute(
                "UPDATE annotation_suggestions SET suggested_role = 'measure' "
                "WHERE suggested_role = 'metric'"
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
          id              INTEGER PRIMARY KEY,
          name            TEXT NOT NULL UNIQUE,
          expression      TEXT NOT NULL,
          description     TEXT DEFAULT NULL,
          ai_context      TEXT DEFAULT NULL,
          created_at      TEXT NOT NULL,
          updated_at      TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name)")
    conn.commit()


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Migrate v10 → v11: add ``build_complete`` flag to ``tables``.

    Tracks whether a table finished sampling/profiling (1) or was only
    described (0). ``mcs build --refresh`` reads this to resume an
    interrupted build: a table whose ``schema_hash`` is unchanged but
    whose ``build_complete`` is 0 (described but never sampled, because
    a prior build was interrupted between the describe and sampling
    phases) is re-sampled instead of being skipped as "unchanged".

    Existing rows are backfilled to 1 — packages built before v11 were
    fully built under the old all-or-nothing logic, so a refresh must
    not suddenly re-sample every table on first upgrade.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tables'"
    ).fetchone()
    if table_exists:
        existing = [r[1] for r in conn.execute("PRAGMA table_info(tables)").fetchall()]
        if "build_complete" not in existing:
            conn.execute("ALTER TABLE tables ADD COLUMN build_complete INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE tables SET build_complete = 1")
    conn.commit()


def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Migrate v11 → v12: add data-freshness columns to ``tables``.

    ``data_modified_at`` records the table's ``last_data_modified_time``
    as observed at the last sample; ``last_sampled_at`` records when that
    sample ran. ``mcs build --refresh`` compares the live modification
    time against ``data_modified_at`` to detect data changes (new rows on
    an unchanged schema) and re-samples — throttled by ``last_sampled_at``
    so a busy table isn't re-sampled on every refresh.

    Existing rows are left NULL: there's no reliable baseline for a
    pre-v12 package. The build pipeline treats that missing baseline as a
    one-time re-sample trigger when a live modification time is available.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tables'"
    ).fetchone()
    if table_exists:
        existing = [r[1] for r in conn.execute("PRAGMA table_info(tables)").fetchall()]
        if "data_modified_at" not in existing:
            conn.execute("ALTER TABLE tables ADD COLUMN data_modified_at TEXT DEFAULT NULL")
        if "last_sampled_at" not in existing:
            conn.execute("ALTER TABLE tables ADD COLUMN last_sampled_at TEXT DEFAULT NULL")
    conn.commit()


def _migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Migrate from schema v12 to v13: add semantic proposal queue."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_proposals (
          id              INTEGER PRIMARY KEY,
          proposal_key    TEXT NOT NULL UNIQUE,
          target_type     TEXT NOT NULL,
          target_ref      TEXT NOT NULL,
          operation       TEXT NOT NULL,
          patch_json      TEXT NOT NULL,
          confidence      REAL NOT NULL DEFAULT 0,
          evidence_json   TEXT NOT NULL,
          provenance      TEXT NOT NULL,
          status          TEXT NOT NULL DEFAULT 'suggested',
          created_by      TEXT NOT NULL,
          created_at      TEXT NOT NULL,
          reviewed_by     TEXT,
          reviewed_at     TEXT,
          applied_at      TEXT,
          validation_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_proposals_status
          ON semantic_proposals(status, confidence DESC);
        CREATE INDEX IF NOT EXISTS idx_semantic_proposals_target
          ON semantic_proposals(target_type, target_ref);
        """
    )
    conn.commit()


# Schema migration chain.  Each entry maps (from_version, to_version)
# to a callable that receives the sqlite3 connection and applies the
# schema changes needed to reach ``to_version``.  The migrator walks
# from the on-disk ``user_version`` to ``_SCHEMA_VERSION`` one step at
# a time.  Gaps in the chain mean the migration is unsupported and the
# caller must handle a ``RebuildRequiredError``.
_MIGRATIONS: dict[tuple[int, int], object] = {
    (3, 4): _migrate_v3_to_v4,
    (4, 5): _migrate_v4_to_v5,
    (5, 6): _migrate_v5_to_v6,
    (6, 7): _migrate_v6_to_v7,
    (7, 8): _migrate_v7_to_v8,
    (8, 9): _migrate_v8_to_v9,
    (9, 10): _migrate_v9_to_v10,
    (10, 11): _migrate_v10_to_v11,
    (11, 12): _migrate_v11_to_v12,
    (12, 13): _migrate_v12_to_v13,
}


def _executescript_or_rebuild(conn: sqlite3.Connection, script: str, path: Path) -> None:
    """Run executescript; translate FTS5-unavailable OperationalError
    into RebuildRequiredError with a remediation hint."""
    try:
        conn.executescript(script)
    except sqlite3.OperationalError as e:
        if "no such module: fts5" in str(e).lower():
            raise RebuildRequiredError(
                f"package at {path} requires SQLite FTS5, but this "
                f"Python's sqlite3 was built without it.",
                remediation=(
                    "install a Python whose sqlite3 has FTS5 enabled "
                    "(the official python.org and Homebrew builds do; "
                    "some minimal/Alpine images don't), or rebuild "
                    "sqlite3 with --enable-fts5"
                ),
            ) from e
        raise


class PackageDB:
    """Thin SQLite wrapper for package build storage."""

    def __init__(self, path: Path) -> None:
        self._path = path
        # Fresh-vs-existing check needs to know whether the file already
        # has data before we run ``executescript`` (which would create
        # the v3 tables on top of a v2 file and confuse downstream
        # queries).
        is_fresh = not path.exists() or path.stat().st_size == 0
        # check_same_thread=False + self._lock together let mcs build
        # --parallel N share one PackageDB across a ThreadPoolExecutor.
        # WAL gives multi-reader concurrency at the SQLite layer; the
        # RLock serializes every public method body so we never
        # interleave a half-committed write with another statement on
        # the same connection. RLock (not Lock) guards against any
        # public-to-public self-call inside this class.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if is_fresh:
            _executescript_or_rebuild(self._conn, _SCHEMA_SQL, path)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()
        else:
            self._check_version_and_migrate(path)
            # Re-run the CREATE TABLE IF NOT EXISTS block — it's a
            # no-op on a v3 DB and protects against partially-written
            # files where some tables exist and others don't.
            _executescript_or_rebuild(self._conn, _SCHEMA_SQL, path)
            self._conn.commit()

        # In-place upgrade: add annotation columns if missing (v3 DBs
        # created before 0.4.0a5 lack these columns). The pragma_table_info
        # probe-and-conditional-ALTER pattern ensures idempotency.
        _annotation_upgrade_cols = {
            "tables": ["ai_context"],
            "memory_entries": ["fts_text"],
            "columns": [
                "semantic_role",
                "dim_type",
                "agg",
                "id_type",
                "references_target",
                "semantic_description",
                "row_count",
                "approx_ndv",
                "uniqueness_ratio",
                "profile_scope",
                "profile_method",
                "profile_confidence",
            ],
        }
        for table, expected_cols in _annotation_upgrade_cols.items():
            existing = [
                r[0]
                for r in self._conn.execute(
                    f"SELECT name FROM pragma_table_info('{table}')"
                ).fetchall()
            ]
            for col in expected_cols:
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT NULL")
            self._conn.commit()

        # In-place upgrade: add typed profile columns if missing (v5 DBs
        # created before profiling support lack these columns). These
        # need specific types, not the generic TEXT from the annotation
        # upgrade above.
        _profile_upgrade_cols = {
            "columns": {
                "row_count": "INTEGER DEFAULT NULL",
                "approx_ndv": "INTEGER DEFAULT NULL",
                "uniqueness_ratio": "REAL DEFAULT NULL",
                "cast_rate": "REAL DEFAULT NULL",
                "profile_scope": "TEXT DEFAULT NULL",
                "profile_method": "TEXT DEFAULT NULL",
                "profile_confidence": "REAL DEFAULT NULL",
            },
        }
        for table, typed_cols in _profile_upgrade_cols.items():
            existing = [
                r[0]
                for r in self._conn.execute(
                    f"SELECT name FROM pragma_table_info('{table}')"
                ).fetchall()
            ]
            for col, col_type in typed_cols.items():
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            self._conn.commit()

        # Annotation-dependent index — must come after the upgrade ALTER
        # so that ``semantic_role`` exists in the ``columns`` table.
        self._conn.executescript(_ANNOTATION_INDEX_SQL)
        self._conn.commit()

        # Load sqlite-vec extension and create vec_index virtual table
        # for hybrid FTS5 + vector retrieval. This is optional — if
        # sqlite-vec is not installed or extension loading fails, we
        # proceed without vector search capability.
        from maxcompute_semantic.memory.vec_ext import create_vec_table, load_vec_extension

        if load_vec_extension(self._conn):
            create_vec_table(self._conn)

    def _check_version_and_migrate(self, path: Path) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == _SCHEMA_VERSION:
            return
        if version > _SCHEMA_VERSION:
            raise RebuildRequiredError(
                f"package at {path} was built by a newer mcs version "
                f"(user_version={version}, current mcs expects "
                f"{_SCHEMA_VERSION}). Downgrade is not supported.",
                remediation=f"either upgrade mcs to match user_version={version}, "
                f"or remove {path} and rebuild with the current version",
            )
        if version == 0:
            raise RebuildRequiredError(
                f"package at {path} has no version stamp "
                f"(user_version=0). This file was created by a "
                f"pre-versioning-era mcs or is corrupted.",
                remediation=f"remove {path} and re-run `mcs build`",
            )
        # version < _SCHEMA_VERSION and > 0: try migration chain.
        migrated = self._run_migrations(version, path)
        if not migrated:
            raise RebuildRequiredError(
                f"package at {path} was built with an older PackageDB "
                f"format (user_version={version}, expected "
                f"{_SCHEMA_VERSION}). Automatic migration from v{version} "
                f"to v{_SCHEMA_VERSION} is not supported.",
                remediation=f"remove {path} and re-run `mcs build`",
            )

    def _run_migrations(self, from_version: int, path: Path) -> bool:
        """Apply migration chain from ``from_version`` to ``_SCHEMA_VERSION``.

        Returns True if all migrations were applied successfully, False
        if a migration step is missing (the gap is unsupported).
        """
        v = from_version
        while v < _SCHEMA_VERSION:
            next_v = v + 1
            fn = _MIGRATIONS.get((v, next_v))
            if fn is None:
                return False
            fn(self._conn)
            self._conn.execute(f"PRAGMA user_version = {next_v}")
            self._conn.commit()
            v = next_v
        return True

    def get_setting(self, key: str) -> str | None:
        """Read a row from ``package_settings``; ``None`` if not present."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM package_settings WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str | None) -> None:
        """Upsert (``value`` not None) or delete (``value`` is None) a row."""
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM package_settings WHERE key=?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO package_settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self._conn.commit()

    def get_inference_logic_version(self) -> int:
        """Return the ``inference_logic_version`` stamp, or 0 if unset.

        A pre-feature profile has no row in ``package_settings`` →
        returns 0, which sorts below any future
        :data:`~maxcompute_semantic.build._logic_version.INFERENCE_LOGIC_VERSION`
        and triggers an offline re-derive on the next
        ``mcs build --refresh``. A row that fails to parse as an integer
        is treated the same (defensive: a hand-edited DB shouldn't
        crash the refresh path).
        """
        with self._lock:
            raw = self.get_setting("inference_logic_version")
            if raw is None:
                return 0
            try:
                return int(raw)
            except ValueError:
                return 0

    def set_inference_logic_version(self, value: int) -> None:
        """Stamp the current inference-logic version into ``package_settings``."""
        with self._lock:
            self.set_setting("inference_logic_version", str(value))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __del__(self) -> None:
        # Close the underlying sqlite3 connection on garbage collection.
        # Without this, tests that create a ``PackageDB`` and let it fall
        # out of scope without an explicit ``close()`` leave the conn
        # open, which sqlite GC then warns about ("ResourceWarning:
        # unclosed database"). The warning gets captured by Click's
        # ``CliRunner`` into ``result.output`` and breaks downstream
        # JSON-parsing assertions.
        with contextlib.suppress(Exception):
            self._conn.close()

    def upsert_table(
        self,
        source_key: str,
        name: str,
        schema_hash: str,
        errors_json: str | None = None,
        table_type: str | None = None,
    ) -> int:
        """Insert or update a tables row. ``table_type`` is the pyodps
        ``Table.type.value`` (e.g. ``"MANAGED_TABLE"``, ``"VIRTUAL_VIEW"``).
        When ``table_type`` is None on an update, the existing column value
        is preserved — refresh paths that only record an error envelope
        must not blank out the type captured by a prior successful describe.
        """
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT id FROM tables WHERE source_key=? AND name=?",
                (source_key, name),
            ).fetchone()
            if row:
                if table_type is None:
                    # Preserve existing table_type on update.
                    self._conn.execute(
                        "UPDATE tables SET schema_hash=?, last_built_at=?, errors_json=? "
                        "WHERE id=?",
                        (schema_hash, now, errors_json, row[0]),
                    )
                else:
                    self._conn.execute(
                        "UPDATE tables SET schema_hash=?, last_built_at=?, errors_json=?, "
                        "table_type=? WHERE id=?",
                        (schema_hash, now, errors_json, table_type, row[0]),
                    )
                self._conn.commit()
                return row[0]
            cur = self._conn.execute(
                "INSERT INTO tables "
                "(source_key, name, schema_hash, last_built_at, errors_json, table_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_key, name, schema_hash, now, errors_json, table_type),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_table(self, source_key: str, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tables WHERE source_key=? AND name=? COLLATE NOCASE",
                (source_key, name),
            ).fetchone()
            return dict(row) if row else None

    def get_schema_hash(self, source_key: str, name: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT schema_hash FROM tables WHERE source_key=? AND name=?",
                (source_key, name),
            ).fetchone()
            return row[0] if row else None

    def list_tables(self, source_key: str | None = None) -> list[dict]:
        """Return all rows; if ``source_key`` is given, restrict to
        that source only. Used by the build pipeline's refresh diff
        (which needs per-source ``existing_names``) and by status /
        memory consumers (which want everything).
        """
        with self._lock:
            if source_key is None:
                rows = self._conn.execute(
                    "SELECT * FROM tables ORDER BY source_key, name"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tables WHERE source_key=? ORDER BY name",
                    (source_key,),
                ).fetchall()
            return [dict(r) for r in rows]

    def find_table_by_name(self, name: str) -> list[dict]:
        """Return every row matching ``name``, regardless of source.
        Used by memory bare-name disambiguation (auto-
        resolve when unique, error when ambiguous). Case-insensitive
        to mirror MaxCompute identifier semantics — agents that copy
        a table name from external docs in a non-canonical case still
        resolve to the same row pyodps inserted from MaxCompute's
        catalog (which canonicalizes to lowercase).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tables WHERE name=? COLLATE NOCASE ORDER BY source_key",
                (name,),
            ).fetchall()
            return [dict(r) for r in rows]

    def lookup_source_key(self, project: str, schema: str, table: str) -> str | None:
        """Resolve a (project, schema, table) triple to its source_key.

        When *schema* is ``None`` or ``"default"``, matches any source_key
        whose project matches and whose schema part is ``"default"`` (the
        2-level canonical schema). Returns ``None`` when no match is found.
        """
        with self._lock:
            if schema is None:
                schema = "default"
            row = self._conn.execute(
                "SELECT source_key FROM tables WHERE name=? COLLATE NOCASE AND source_key LIKE ?",
                (table, f"{project}__{schema}"),
            ).fetchone()
            return row["source_key"] if row else None

    def upsert_columns(self, table_id: int, columns: list[dict[str, Any]]) -> None:
        """Refresh schema-derived column rows for *table_id*.

        Drops rows whose names no longer appear in *columns* (handles
        DROP COLUMN), inserts new rows, and updates only the fields the
        caller actually provided.

        Schema fields (``type`` / ``comment`` / ``is_partition``) are
        always written. Sample-phase fields (``sample_values_json`` /
        ``is_enum`` / ``null_ratio`` / ``distinct_count``) are only
        written when the caller's col dict contains the matching key —
        ``phase_column_sampling`` always provides them (and may set
        ``sample_values_json`` to ``None`` to clear stale samples),
        while ``phase_describe_table`` omits them so a refresh-path
        schema-hash check never clobbers data the sampling phase
        wrote. Annotation fields (``semantic_role`` / ``dim_type`` /
        ``agg`` / ``id_type`` / ``references_target`` /
        ``semantic_description``) and profile-phase fields
        (``row_count`` / ``approx_ndv`` / ``uniqueness_ratio`` /
        ``cast_rate`` / ``profile_*``) are never touched here — they
        live on different writers entirely.

        The earlier shape (DELETE+INSERT, then unconditional ON
        CONFLICT updates) clobbered cross-phase data on every refresh
        round-trip; see CHANGELOG 0.10.9.
        """
        with self._lock:
            new_names = {col["name"] for col in columns}
            existing = self._conn.execute(
                "SELECT name FROM columns WHERE table_id=?", (table_id,)
            ).fetchall()
            stale = [r["name"] for r in existing if r["name"] not in new_names]
            if stale:
                placeholders = ",".join("?" * len(stale))
                self._conn.execute(
                    f"DELETE FROM columns WHERE table_id=? AND name IN ({placeholders})",  # noqa: S608
                    (table_id, *stale),
                )

            sample_keys = ("sample_values_json", "is_enum", "null_ratio", "distinct_count")
            for col in columns:
                insert_cols = ["table_id", "name", "type", "comment", "is_partition"]
                insert_vals: list[Any] = [
                    table_id,
                    col["name"],
                    col["type"],
                    col.get("comment", ""),
                    col.get("is_partition", 0),
                ]
                update_parts = [
                    "type = excluded.type",
                    "comment = excluded.comment",
                    "is_partition = excluded.is_partition",
                ]
                for k in sample_keys:
                    if k in col:
                        insert_cols.append(k)
                        insert_vals.append(col[k])
                        update_parts.append(f"{k} = excluded.{k}")

                cols_sql = ", ".join(insert_cols)
                placeholders = ", ".join("?" for _ in insert_vals)
                update_sql = ", ".join(update_parts)
                self._conn.execute(
                    f"INSERT INTO columns ({cols_sql}) VALUES ({placeholders}) "  # noqa: S608
                    f"ON CONFLICT(table_id, name) DO UPDATE SET {update_sql}",
                    insert_vals,
                )
            self._conn.commit()

    def get_columns(self, table_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM columns WHERE table_id=?", (table_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_columns_bulk(self, table_ids: list[int]) -> dict[int, list[dict]]:
        """Fetch columns for many tables in one query, grouped by table_id.

        Avoids the N+1 pattern of calling ``get_columns`` in a loop.
        Returns an empty dict when *table_ids* is empty; missing ids
        get an empty list in the result so callers can use ``.get(tid, [])``
        without checking membership.
        """
        with self._lock:
            if not table_ids:
                return {}
            placeholders = ",".join("?" * len(table_ids))
            rows = self._conn.execute(
                f"SELECT * FROM columns WHERE table_id IN ({placeholders})",  # noqa: S608
                table_ids,
            ).fetchall()
            by_tid: dict[int, list[dict]] = {tid: [] for tid in table_ids}
            for r in rows:
                d = dict(r)
                by_tid[d["table_id"]].append(d)
            return by_tid

    def upsert_join(
        self,
        left_source_key: str,
        left_table: str,
        left_col: str,
        right_source_key: str,
        right_table: str,
        right_col: str,
        kind: str,
        confidence: float,
        cardinality: str | None = None,
    ) -> None:
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM joins WHERE "
                "left_source_key=? AND left_table=? AND left_col=? AND "
                "right_source_key=? AND right_table=? AND right_col=? AND kind=?",
                (
                    left_source_key,
                    left_table,
                    left_col,
                    right_source_key,
                    right_table,
                    right_col,
                    kind,
                ),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE joins SET confidence=?, cardinality=? WHERE id=?",
                    (confidence, cardinality, existing[0]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO joins "
                    "(left_source_key, left_table, left_col, "
                    "right_source_key, right_table, right_col, "
                    "kind, confidence, cardinality) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        left_source_key,
                        left_table,
                        left_col,
                        right_source_key,
                        right_table,
                        right_col,
                        kind,
                        confidence,
                        cardinality,
                    ),
                )
            self._conn.commit()

    def list_joins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM joins ORDER BY left_source_key, left_table, "
                "right_source_key, right_table"
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_udf(
        self,
        name: str,
        kind: str,
        signature: str | None = None,
        class_name: str | None = None,
        description: str | None = None,
        last_seen_at: str | None = None,
    ) -> None:
        with self._lock:
            now = last_seen_at or datetime.now(timezone.utc).isoformat()
            existing = self._conn.execute("SELECT id FROM udfs WHERE name=?", (name,)).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE udfs SET kind=?, signature=?, "
                    "class_name=?, description=?, last_seen_at=? WHERE id=?",
                    (kind, signature, class_name, description, now, existing[0]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO udfs "
                    "(name, kind, signature, class_name, description, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, kind, signature, class_name, description, now),
                )
            self._conn.commit()

    def list_udfs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM udfs ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    # ── metrics (top-level) ─────────────────────────────────────────

    def add_metric(
        self,
        *,
        name: str,
        expression: str,
        description: str | None = None,
        ai_context: str | None = None,
    ) -> int:
        """Insert a new top-level metric. Raises ``MetricExistsError`` on
        UNIQUE(name) collision. Returns the new row id.
        """
        from maxcompute_semantic.errors.build import MetricExistsError

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO metrics(name, expression, description, "
                    "ai_context, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, expression, description, ai_context, now, now),
                )
                self._conn.commit()
                rowid = cur.lastrowid
                assert rowid is not None
                return rowid
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc):
                    raise MetricExistsError(name) from exc
                from maxcompute_semantic.mc_client.errors import McsError

                raise McsError(
                    f"metric insert failed: {exc}",
                    remediation="check the metric name and expression",
                ) from exc

    def list_metrics(self) -> list[dict[str, Any]]:
        """Return all metrics in this profile, sorted by name."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, expression, description, ai_context, "
                "created_at, updated_at FROM metrics ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_metric(self, name: str) -> dict[str, Any] | None:
        """Return one metric row by name, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, expression, description, ai_context, "
                "created_at, updated_at FROM metrics WHERE name = ?",
                (name,),
            ).fetchone()
            return dict(row) if row else None

    def update_metric(
        self,
        name: str,
        *,
        expression: str | None = None,
        description: str | None = None,
        ai_context: str | None = None,
    ) -> None:
        """Partial-update a metric. Only non-None fields are written.
        Raises ``MetricNotFoundError`` if no row matches ``name``.
        """
        from maxcompute_semantic.errors.build import MetricNotFoundError

        sets: list[str] = []
        params: list[Any] = []
        if expression is not None:
            sets.append("expression = ?")
            params.append(expression)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if ai_context is not None:
            sets.append("ai_context = ?")
            params.append(ai_context)
        if not sets:
            if self.get_metric(name) is None:
                raise MetricNotFoundError(name)
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sets.append("updated_at = ?")
        params.append(now)
        params.append(name)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE metrics SET {', '.join(sets)} WHERE name = ?",
                params,
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise MetricNotFoundError(name)

    def remove_metric(self, name: str) -> None:
        """Delete a metric by name. Raises ``MetricNotFoundError`` if no
        row matched.
        """
        from maxcompute_semantic.errors.build import MetricNotFoundError

        with self._lock:
            cur = self._conn.execute("DELETE FROM metrics WHERE name = ?", (name,))
            self._conn.commit()
            if cur.rowcount == 0:
                raise MetricNotFoundError(name)

    def delete_table(self, source_key: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM tables WHERE source_key=? AND name=?",
                (source_key, name),
            )
            self._conn.commit()

    def mark_build_complete(self, source_key: str, table_names: Iterable[str]) -> None:
        """Mark tables as fully built (sampled/profiled). Sets
        ``build_complete=1`` so a subsequent ``mcs build --refresh`` skips
        them as "unchanged" rather than treating them as an interrupted-build
        remnant to resume.

        Does NOT touch ``last_sampled_at`` or ``data_modified_at`` — callers
        that actually sampled table data should use :meth:`record_sampled`
        instead so the freshness baseline only advances after successful
        sampling/profiling. This batch form is for cases with no sample
        (e.g. ``--no-sampling`` or skipped views)."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for name in table_names:
                self._conn.execute(
                    "UPDATE tables SET last_built_at=?, build_complete=1 "
                    "WHERE source_key=? AND name=?",
                    (now, source_key, name),
                )
            self._conn.commit()

    def record_sampled(
        self,
        source_key: str,
        name: str,
        data_modified_at: str | None,
    ) -> None:
        """Mark a single table sampled and advance its data-change baseline.

        Sets ``build_complete=1``, stamps ``last_built_at`` /
        ``last_sampled_at`` to now, and records ``data_modified_at`` (the
        table's live ``last_data_modified_time`` observed during this
        build's describe). The next ``--refresh`` compares the then-live
        modification time against this stored value to decide whether the
        data changed since we last sampled."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE tables SET last_built_at=?, build_complete=1, "
                "last_sampled_at=?, data_modified_at=? "
                "WHERE source_key=? AND name=?",
                (now, now, data_modified_at, source_key, name),
            )
            self._conn.commit()

    def mark_build_incomplete(self, source_key: str, table_names: Iterable[str]) -> None:
        """Reset ``build_complete`` to 0 for the given tables. Used by the
        refresh path when a changed table is about to be re-sampled: if
        the re-sample is interrupted, the next refresh sees an
        incomplete table and resumes it instead of skipping."""
        with self._lock:
            for name in table_names:
                self._conn.execute(
                    "UPDATE tables SET build_complete=0 WHERE source_key=? AND name=?",
                    (source_key, name),
                )
            self._conn.commit()

    def upsert_memory(
        self,
        kind: str,
        payload_json: str,
        retrieval_text: str,
        tags_json: str | None = None,
    ) -> int:
        """Insert a memory entry; fts_text populated synchronously,
        memory_ai trigger pushes into memory_fts. Vector indexing still
        runs via _index_vector (best-effort, no-op if vec deps absent).
        """
        with self._lock:
            from maxcompute_semantic.memory.tokenizer import MemoryTokenizer

            now = datetime.now(timezone.utc).isoformat()
            fts_text = MemoryTokenizer().tokenize_for_index(retrieval_text)
            cur = self._conn.execute(
                "INSERT INTO memory_entries "
                "(kind, payload_json, retrieval_text, fts_text, tags_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, payload_json, retrieval_text, fts_text, tags_json, now),
            )
            memory_id = cur.lastrowid
            self._conn.commit()
            if _auto_vector_enabled():
                self._index_vector(memory_id, retrieval_text)
            return memory_id

    def get_memory(self, id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM memory_entries WHERE id=?", (id,)).fetchone()
            return dict(row) if row else None

    def list_memories(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            if kind:
                rows = self._conn.execute(
                    "SELECT * FROM memory_entries WHERE kind=? ORDER BY id DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memory_entries ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def remove_memory(self, id: int) -> bool:
        """Delete a memory entry; memory_fts row removed via memory_ad trigger.
        vec_index rows are explicitly deleted (vec0 has no CASCADE)."""
        with self._lock:
            row = self._conn.execute("SELECT id FROM memory_entries WHERE id=?", (id,)).fetchone()
            if not row:
                return False
            from maxcompute_semantic.memory.vec_ext import delete_vector

            delete_vector(self._conn, id)
            self._conn.execute("DELETE FROM memory_entries WHERE id=?", (id,))
            self._conn.commit()
            return True

    def clear_memories(self, kind: str | None = None, before: str | None = None) -> int:
        """Bulk delete memory entries; memory_fts rows removed via memory_ad trigger.
        vec_index rows are explicitly deleted (vec0 has no CASCADE)."""
        with self._lock:
            from maxcompute_semantic.memory.vec_ext import delete_vector

            # Collect rowids that will be deleted so we can also remove
            # their vec_index entries before the memory_entries DELETE.
            if kind and before:
                ids_to_delete = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT id FROM memory_entries WHERE kind=? AND created_at<?",
                        (kind, before),
                    ).fetchall()
                ]
                result = self._conn.execute(
                    "SELECT COUNT(*) FROM memory_entries WHERE kind=? AND created_at<?",
                    (kind, before),
                ).fetchone()
                count = result[0]
                self._conn.execute(
                    "DELETE FROM memory_entries WHERE kind=? AND created_at<?",
                    (kind, before),
                )
            elif kind:
                ids_to_delete = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT id FROM memory_entries WHERE kind=?", (kind,)
                    ).fetchall()
                ]
                result = self._conn.execute(
                    "SELECT COUNT(*) FROM memory_entries WHERE kind=?", (kind,)
                ).fetchone()
                count = result[0]
                self._conn.execute("DELETE FROM memory_entries WHERE kind=?", (kind,))
            elif before:
                ids_to_delete = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT id FROM memory_entries WHERE created_at<?", (before,)
                    ).fetchall()
                ]
                result = self._conn.execute(
                    "SELECT COUNT(*) FROM memory_entries WHERE created_at<?", (before,)
                ).fetchone()
                count = result[0]
                self._conn.execute("DELETE FROM memory_entries WHERE created_at<?", (before,))
            else:
                ids_to_delete = [
                    r[0] for r in self._conn.execute("SELECT id FROM memory_entries").fetchall()
                ]
                result = self._conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()
                count = result[0]
                self._conn.execute("DELETE FROM memory_entries")
            for mid in ids_to_delete:
                delete_vector(self._conn, mid)
            self._conn.commit()
            return count

    def list_sample_sqls(
        self,
        *,
        source_key: str | None = None,
        table: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return sample_sql memory entries filtered by payload fields.

        Filtering is done in Python to avoid relying on SQLite JSON1 being
        enabled in every runtime where mcs runs.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_entries WHERE kind='sample_sql' ORDER BY id DESC"
            ).fetchall()

            result: list[dict] = []
            for row in rows:
                item = dict(row)
                try:
                    payload = json.loads(item["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if source_key is not None and payload.get("source_key") != source_key:
                    continue
                if table is not None and payload.get("table") != table:
                    continue
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break
            return result

    def clear_sample_sqls_for_source(self, source_key: str) -> int:
        """Delete generated sample_sql entries for one source only."""
        with self._lock:
            rows = self.list_sample_sqls(source_key=source_key)
            for row in rows:
                self.remove_memory(row["id"])
            return len(rows)

    def sample_sql_table_names_for_source(self, source_key: str) -> set[str]:
        """Return table names that currently have sample_sql rows for a source."""
        with self._lock:
            names: set[str] = set()
            for row in self.list_sample_sqls(source_key=source_key):
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                table_name = payload.get("table")
                if isinstance(table_name, str) and table_name:
                    names.add(table_name)
            return names

    def verified_shape_counts_for_source(self, source_key: str) -> dict[tuple[str, str], int]:
        """Return {(table, shape_key): verified_count} from verified_query entries."""
        with self._lock:
            from maxcompute_semantic.memory.sql_pattern import analyze_sql_pattern

            counts: dict[tuple[str, str], int] = {}
            for row in self.list_memories(kind="verified_query", limit=10000):
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                sql = payload.get("sql")
                table_refs = payload.get("table_refs") or []
                if not isinstance(sql, str):
                    continue
                pattern = analyze_sql_pattern(sql)
                for ref in table_refs:
                    if not isinstance(ref, dict):
                        continue
                    if ref.get("source_key") != source_key:
                        continue
                    table = ref.get("table")
                    if isinstance(table, str) and table:
                        key = (table, pattern.shape_key)
                        counts[key] = counts.get(key, 0) + 1
            return counts

    def mark_sample_sql_verified(self, source_key: str, table: str, shape_key: str) -> bool:
        """Increment verified_count for the current matching sample_sql pattern."""
        with self._lock:
            from maxcompute_semantic.memory.sample_sql import (
                confidence_for_counts,
                sample_sql_retrieval_text,
            )
            from maxcompute_semantic.memory.tokenizer import MemoryTokenizer

            for row in self.list_sample_sqls(source_key=source_key, table=table):
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if payload.get("shape_key") != shape_key:
                    continue
                verified_count = int(payload.get("verified_count") or 0) + 1
                frequency = int(payload.get("frequency") or 1)
                payload["verified_count"] = verified_count
                payload["confidence"] = confidence_for_counts(
                    frequency=frequency,
                    verified_count=verified_count,
                )
                payload_json = json.dumps(payload, ensure_ascii=False)
                retrieval_text = sample_sql_retrieval_text(payload)
                fts_text = MemoryTokenizer().tokenize_for_index(retrieval_text)
                self._conn.execute(
                    "UPDATE memory_entries SET payload_json=?, retrieval_text=?, fts_text=? "
                    "WHERE id=?",
                    (payload_json, retrieval_text, fts_text, row["id"]),
                )
                self._conn.commit()
                return True
            return False

    def reindex_memory_fts(self) -> int:
        """Rebuild memory_fts from memory_entries.

        Re-tokenizes retrieval_text for every row and updates fts_text,
        then issues an FTS5 'rebuild' command. The 'rebuild' is issued
        AFTER the UPDATEs so the index reflects the freshly tokenized
        text; we run an initial 'rebuild' first to ensure the index is
        in a sane state (memory_au triggers raise "database disk image
        is malformed" if the FTS index is missing rows the trigger
        tries to delete — that happens after a 'delete-all' wipe or
        any other index-only corruption).
        """
        with self._lock:
            from maxcompute_semantic.memory.tokenizer import MemoryTokenizer

            # Pre-rebuild restores any index rows missing relative to the
            # content table so subsequent UPDATEs' memory_au triggers can
            # find the old row to delete.
            self._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            tok = MemoryTokenizer()
            rows = self._conn.execute("SELECT id, retrieval_text FROM memory_entries").fetchall()
            for row in rows:
                ft = tok.tokenize_for_index(row[1])
                self._conn.execute("UPDATE memory_entries SET fts_text=? WHERE id=?", (ft, row[0]))
            # Final rebuild guarantees the FTS index matches the just-updated
            # fts_text values, even if any trigger was somehow bypassed.
            self._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            self._conn.commit()
            return len(rows)

    def reindex_vectors(self) -> int:
        """Rebuild all vec_index embeddings from memory_entries.retrieval_text.

        Returns the count of entries that were successfully re-embedded.
        Returns -1 if vector search is unavailable (no sentence-transformers
        or sqlite-vec extension).
        """
        with self._lock:
            from maxcompute_semantic.memory.embedding import embed_batch, is_available
            from maxcompute_semantic.memory.vec_ext import (
                delete_vector,
                insert_vector,
                vec_table_exists,
            )

            if not is_available() or not vec_table_exists(self._conn):
                return -1

            # Delete all existing vectors.
            rows = self._conn.execute("SELECT id FROM memory_entries").fetchall()
            for row in rows:
                delete_vector(self._conn, row[0])

            # Batch-embed all retrieval texts.
            texts = self._conn.execute("SELECT id, retrieval_text FROM memory_entries").fetchall()
            if not texts:
                return 0

            ids = [row[0] for row in texts]
            texts_list = [row[1] for row in texts]
            embeddings = embed_batch(texts_list)
            if embeddings is None:
                return -1

            for memory_id, embedding in zip(ids, embeddings, strict=True):
                insert_vector(self._conn, memory_id, embedding)

            return len(ids)

    def _index_vector(self, memory_id: int, retrieval_text: str) -> None:
        """Compute embedding and insert into vec_index if available."""
        from maxcompute_semantic.memory.embedding import embed
        from maxcompute_semantic.memory.vec_ext import insert_vector, vec_table_exists

        embedding = embed(retrieval_text)
        if embedding is None:
            return  # sentence-transformers not installed, skip silently
        if not vec_table_exists(self._conn):
            return  # vec_index table doesn't exist, skip silently
        try:
            insert_vector(self._conn, memory_id, embedding)
        except sqlite3.OperationalError:
            return  # vec extension not loaded or table issue, skip silently

    # --- Annotation setter/getter methods (§4) ---

    def annotation_coverage(self, *, per_table: bool = False) -> dict:
        """Compute annotation-coverage rollup counters from current SQLite state.

        Top-level counters cover every ``tables`` row regardless of source.
        When ``per_table=True``, the ``per_table`` field is nested by
        ``source_key`` → ``table_name`` so same-named tables under different
        sources are addressed independently:

            {
              "tables_total": int, "tables_with_ai_context": int, ...,
              "per_table": {
                "<source_key>": {
                  "<table_name>": {"columns_total": int, ..., "tristate": str},
                },
              },
            }
        """
        with self._lock:
            tables_total = self._conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0]
            tables_with_ai_context = self._conn.execute(
                "SELECT COUNT(*) FROM tables WHERE ai_context IS NOT NULL AND ai_context != ''"
            ).fetchone()[0]
            tables_with_any_column_role = self._conn.execute(
                "SELECT COUNT(DISTINCT table_id) FROM columns WHERE semantic_role IS NOT NULL"
            ).fetchone()[0]
            columns_total = self._conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0]
            columns_with_role = self._conn.execute(
                "SELECT COUNT(*) FROM columns WHERE semantic_role IS NOT NULL"
            ).fetchone()[0]
            result: dict = {
                "tables_total": tables_total,
                "tables_with_ai_context": tables_with_ai_context,
                "tables_with_any_column_role": tables_with_any_column_role,
                "columns_total": columns_total,
                "columns_with_role": columns_with_role,
            }
            if per_table:
                per_table_data: dict[str, dict[str, dict]] = {}
                for t in self.list_tables():
                    sk = t["source_key"]
                    name = t["name"]
                    has_ai = bool(t.get("ai_context"))
                    total_cols = self._conn.execute(
                        "SELECT COUNT(*) FROM columns WHERE table_id=?", (t["id"],)
                    ).fetchone()[0]
                    annotated_cols = self._conn.execute(
                        "SELECT COUNT(*) FROM columns "
                        "WHERE table_id=? AND semantic_role IS NOT NULL",
                        (t["id"],),
                    ).fetchone()[0]
                    described_cols = self._conn.execute(
                        "SELECT COUNT(*) FROM columns "
                        "WHERE table_id=? AND semantic_description IS NOT NULL "
                        "AND semantic_description != ''",
                        (t["id"],),
                    ).fetchone()[0]
                    if annotated_cols == 0 and not has_ai:
                        tristate = "no"
                    elif annotated_cols == total_cols and has_ai:
                        tristate = "yes"
                    else:
                        tristate = f"partial({annotated_cols}/{total_cols})"
                    per_table_data.setdefault(sk, {})[name] = {
                        "columns_total": total_cols,
                        "columns_annotated": annotated_cols,
                        "columns_with_description": described_cols,
                        "has_ai_context": has_ai,
                        "tristate": tristate,
                    }
                result["per_table"] = per_table_data
            return result

    def _suggest_close_names(self, target: str, candidates: Iterable[str]) -> list[str]:
        """Return up to 3 names from ``candidates`` closest to ``target``.

        Used to enrich ``AnnotateNotFoundError`` remediations with explicit
        candidate names instead of forcing the agent to guess a typo fix
        from a generic "check spelling" hint. Case-insensitive matching
        because the same NOCASE collator applies to the lookup itself.
        """
        target_lc = target.strip().lower()
        # Build a lowercase→canonical map; difflib compares lowercased
        # forms but we return the canonical case the catalog holds.
        pool = list(candidates)
        if not pool:
            return []
        lc_map: dict[str, str] = {}
        for name in pool:
            lc_map.setdefault(name.lower(), name)
        matches = get_close_matches(target_lc, list(lc_map.keys()), n=3, cutoff=0.6)
        return [lc_map[m] for m in matches]

    def _did_you_mean_clause(self, suggestions: list[str]) -> str:
        """Format a suggestion list for prepending to a remediation string."""
        if not suggestions:
            return ""
        return f"did you mean {', '.join(repr(s) for s in suggestions)}? Or "

    def _resolve_table_id(self, source_key: str, table_name: str) -> int:
        """Resolve ``(source_key, table_name)`` to the ``tables.id`` PK.
        Raises ``AnnotateNotFoundError(scope="table")`` if no matching row.
        Single source of truth for the annotation setters/getters that
        used to look up by ``name`` alone — broken for multi-source profiles
        where two sources can carry same-named tables.
        """
        from maxcompute_semantic.errors.annotate import AnnotateNotFoundError

        # Case-insensitive match on ``name`` mirrors MaxCompute's
        # identifier semantics: two tables differing only in case can't
        # coexist, so callers passing a non-canonical case (e.g. an
        # agent that copied the name from external docs or training-data
        # priors) should still resolve to the row pyodps inserted from
        # the catalog's canonical lowercase. ``source_key`` is internal
        # and constructed deterministically, so it stays exact-match.
        row = self._conn.execute(
            "SELECT id FROM tables WHERE source_key=? AND name=? COLLATE NOCASE",
            (source_key, table_name),
        ).fetchone()
        if not row:
            sibling_rows = self._conn.execute(
                "SELECT name FROM tables WHERE source_key=?", (source_key,)
            ).fetchall()
            siblings = [r["name"] for r in sibling_rows]
            suggestions = self._suggest_close_names(table_name, siblings)
            hint = self._did_you_mean_clause(suggestions)
            raise AnnotateNotFoundError(
                f"table {table_name!r} not found in source {source_key!r}",
                remediation=f"{hint}run `mcs build --refresh` or check spelling",
                scope="table",
            )
        return int(row["id"])

    def table_exists(self, source_key: str, table_name: str) -> bool:
        """Check whether a table row exists under ``(source_key, name)``.
        Case-insensitive on ``name`` for the same reason as
        :meth:`_resolve_table_id`.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM tables WHERE source_key=? AND name=? COLLATE NOCASE",
                (source_key, table_name),
            ).fetchone()
            return row is not None

    def set_table_ai_context(
        self, source_key: str, table_name: str, ai_context: str | None
    ) -> None:
        """Write the OSI Dataset-level business-context string for the
        ``(source_key, name)`` table. Empty string normalizes to None per
        §1 rule 8. Raises ``AnnotateNotFoundError(scope="table")`` when
        the row doesn't exist under this source.
        """
        with self._lock:
            value = None if (ai_context is None or ai_context == "") else ai_context
            tid = self._resolve_table_id(source_key, table_name)
            self._conn.execute("UPDATE tables SET ai_context=? WHERE id=?", (value, tid))
            self._conn.commit()

    def get_table_ai_context(self, source_key: str, table_name: str) -> str | None:
        """Read the ai_context for the ``(source_key, name)`` table.
        Raises ``AnnotateNotFoundError(scope="table")`` when missing.
        """
        with self._lock:
            tid = self._resolve_table_id(source_key, table_name)
            row = self._conn.execute("SELECT ai_context FROM tables WHERE id=?", (tid,)).fetchone()
            return row[0] if row else None

    def set_column_semantics(
        self,
        source_key: str,
        table_name: str,
        column_name: str,
        *,
        role: str | None,
        dim_type: str | None = None,
        agg: str | None = None,
        id_type: str | None = None,
        references_target: str | None = None,
        semantic_description: str | None = None,
    ) -> None:
        """Write the per-column annotation tuple for ``(source_key, table_name, column_name)``.
        Validates §1 rules 1-7. Raises ``AnnotateNotFoundError`` for missing
        table/column, ``AnnotateValidationError`` for rule violations.

        Rule-7 target-table lookup is also source-scoped: a foreign-key
        reference resolves only against tables in the same ``source_key``.
        Cross-source FK references are out of scope for §1.
        """
        with self._lock:
            from maxcompute_semantic.errors.annotate import (
                AnnotateNotFoundError,
                AnnotateValidationError,
            )

            role, dim_type, id_type, agg = _normalize_annotation_aliases(
                role, dim_type, id_type, agg
            )
            semantic_description = (
                None if semantic_description in (None, "") else semantic_description
            )
            tid = self._resolve_table_id(source_key, table_name)
            # Case-insensitive match on ``columns.name`` for the same
            # reason as ``_resolve_table_id``: MaxCompute identifiers are
            # case-insensitive, the catalog canonicalizes to lowercase, but
            # agents that copy column names from external schema docs
            # (CSV-import conventions, vendor warehouses, training-data
            # priors) often pass an upper- or mixed-case form. Without this
            # collator the UPDATE WHERE clause below silently misses the
            # canonical row and the column goes unannotated. Re-bind
            # ``column_name`` to the canonical case the storage holds, so
            # the subsequent UPDATE (which stays exact-match) lands on the
            # right row.
            c_row = self._conn.execute(
                "SELECT name FROM columns WHERE table_id=? AND name=? COLLATE NOCASE",
                (tid, column_name),
            ).fetchone()
            if not c_row:
                sibling_rows = self._conn.execute(
                    "SELECT name FROM columns WHERE table_id=?", (tid,)
                ).fetchall()
                siblings = [r["name"] for r in sibling_rows]
                suggestions = self._suggest_close_names(column_name, siblings)
                hint = self._did_you_mean_clause(suggestions)
                raise AnnotateNotFoundError(
                    f"column {column_name!r} not found on table {table_name!r} "
                    f"in source {source_key!r}",
                    remediation=f"{hint}run `mcs build --refresh` or check spelling",
                    scope="column",
                )
            column_name = c_row["name"]
            # §1 rule 1: role in valid set
            VALID_ROLES = {"dimension", "measure", "identifier", "attribute"}
            if role is not None and role not in VALID_ROLES:
                raise AnnotateValidationError(
                    f"role must be one of {sorted(VALID_ROLES)} or null, got {role!r}",
                    remediation="set role to a valid value",
                    code_subkey="rule-1",
                )
            # §1 rule 2: dim_type iff role==dimension
            if role == "dimension":
                if dim_type is None:
                    raise AnnotateValidationError(
                        "role=dimension requires dim_type",
                        remediation="set --dim-type to categorical, time, or ordinal",
                        code_subkey="rule-2",
                    )
                if dim_type not in {"categorical", "time", "ordinal"}:
                    raise AnnotateValidationError(
                        f"dim_type must be categorical/time/ordinal, got {dim_type!r}",
                        remediation="set --dim-type to categorical, time, or ordinal",
                        code_subkey="rule-2",
                    )
            elif dim_type is not None:
                raise AnnotateValidationError(
                    f"dim_type is only valid with role=dimension, got role={role!r}",
                    remediation="remove --dim-type or set --role dimension",
                    code_subkey="rule-2",
                )
            # §1 rule 3: agg iff role==measure
            VALID_AGGS = {"SUM", "COUNT", "AVG", "MAX", "MIN", "COUNT_DISTINCT"}
            if role == "measure":
                if agg is None:
                    # Softened: agents often write ``role: measure`` (or the
                    # alias ``role: numeric`` / ``role: quantitative``) without
                    # picking an agg, expecting the default to be obvious.
                    # Demote to ``attribute`` rather than losing the whole
                    # column annotation — the column's description /
                    # semantic_description still land, and the column type
                    # carries enough signal for SQL gen to pick an
                    # aggregation. Mirrors the rule-5 soft-drop of
                    # ``id_type=foreign`` without a references target.
                    role = "attribute"
                elif agg not in VALID_AGGS:
                    raise AnnotateValidationError(
                        f"agg must be in {sorted(VALID_AGGS)}, got {agg!r}",
                        remediation="set --agg to a valid aggregation",
                        code_subkey="rule-3",
                    )
            elif agg is not None:
                # Softened: when the caller (typically an agent annotating
                # ``role: identifier, agg: COUNT`` or ``role: attribute,
                # agg: COUNT_DISTINCT``) lands on a non-measure role with
                # an agg set, drop ``agg`` instead of failing the whole
                # column. The role / dim_type / id_type / description
                # metadata is still useful for SQL generation; the agg
                # slot is only consumed by the measure-rollup paths and is
                # harmless when absent on a non-measure column. Mirrors
                # the rule-5 soft-drop precedent below.
                agg = None
            # §1 rule 4: id_type iff role==identifier
            VALID_ID_TYPES = {"primary", "foreign", "unique"}
            if role == "identifier":
                if id_type is None:
                    # Softened: agents reaching for ``role: identifier``
                    # via the ``entity_id`` alias (or directly with intent
                    # "this is the entity's key") often omit id_type
                    # because the choice isn't obvious from the column
                    # alone — a column called ``entity_id`` could be a
                    # local PK or a polymorphic FK. Land the identifier
                    # role with id_type=None instead of losing the whole
                    # column annotation; SQL gen still benefits from the
                    # identifier signal, and the build's join_candidates
                    # layer infers FK relationships from data co-occurrence
                    # independently. Mirrors the rule-5 soft-drop precedent
                    # below.
                    pass
                elif id_type not in VALID_ID_TYPES:
                    raise AnnotateValidationError(
                        f"id_type must be in {sorted(VALID_ID_TYPES)}, got {id_type!r}",
                        remediation="set --id-type to primary, foreign, or unique",
                        code_subkey="rule-4",
                    )
            elif id_type is not None:
                raise AnnotateValidationError(
                    f"id_type is only valid with role=identifier, got role={role!r}",
                    remediation="remove --id-type or set --role identifier",
                    code_subkey="rule-4",
                )
            # §1 rule 5: references_target iff id_type==foreign.
            #
            # Softened: when the caller (typically an agent's ``role:
            # foreign_key`` alias) lands on ``id_type=foreign`` without a
            # references target, we demote to a generic identifier
            # (``id_type=NULL``) instead of failing the whole column. The
            # build's ``join_candidates`` layer already infers FK
            # relationships independently from data co-occurrence, so the
            # column still gets useful identifier-role metadata without
            # blocking annotation just because the agent didn't repeat the
            # join target. The explicit canonical shape (``role:
            # identifier, id_type: foreign, references: T.col``) still
            # works as before.
            if id_type == "foreign" and references_target is None:
                id_type = None
            elif id_type != "foreign" and references_target is not None:
                raise AnnotateValidationError(
                    "references is only valid with id_type=foreign",
                    remediation="remove --references or set --id-type foreign",
                    code_subkey="rule-5",
                )
            # §1 rule 6: references_target shape
            if references_target is not None:
                parts = references_target.split(".", 1)
                if len(parts) != 2:
                    raise AnnotateValidationError(
                        f"references must be 'TABLE.COLUMN', got {references_target!r}",
                        remediation="use the dotted-pair form TABLE.COLUMN",
                        code_subkey="rule-6",
                    )
                # §1 rule 7: target table must exist in the same source
                target_table = parts[0]
                if not self.table_exists(source_key, target_table):
                    raise AnnotateValidationError(
                        f"target table {target_table!r} not found in source {source_key!r}",
                        remediation="check spelling or run `mcs build --refresh`",
                        code_subkey="rule-7",
                    )
            self._conn.execute(
                "UPDATE columns SET semantic_role=?, dim_type=?, agg=?, "
                "id_type=?, references_target=?, semantic_description=? "
                "WHERE table_id=? AND name=?",
                (
                    role,
                    dim_type,
                    agg,
                    id_type,
                    references_target,
                    semantic_description,
                    tid,
                    column_name,
                ),
            )
            self._conn.commit()

    def get_column_semantics(
        self, source_key: str, table_name: str, column_name: str
    ) -> dict | None:
        """Read the annotation tuple for ``(source_key, table_name, column_name)``.
        Returns ``None`` if no matching column row. Does NOT raise — used by
        read-only CLI paths that should noop on missing rows. Case-insensitive
        on ``table_name`` / ``column_name`` so a write that came in via
        :meth:`set_column_semantics` with a non-canonical case is readable
        back by callers using either the original case or the canonical one.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT c.semantic_role, c.dim_type, c.agg, c.id_type, "
                "c.references_target, c.semantic_description "
                "FROM columns c JOIN tables t ON c.table_id=t.id "
                "WHERE t.source_key=? AND t.name=? COLLATE NOCASE "
                "AND c.name=? COLLATE NOCASE",
                (source_key, table_name, column_name),
            ).fetchone()
            if not row:
                return None
            return {
                "semantic_role": row[0],
                "dim_type": row[1],
                "agg": row[2],
                "id_type": row[3],
                "references_target": row[4],
                "semantic_description": row[5],
            }

    # ---- Column profiling ----

    def update_column_profile(
        self,
        source_key: str,
        table_name: str,
        column_name: str,
        *,
        row_count: int | None = None,
        approx_ndv: int | None = None,
        uniqueness_ratio: float | None = None,
        is_enum: int | None = None,
        null_ratio: float | None = None,
        cast_rate: float | None = None,
        profile_scope: str | None = None,
        profile_method: str | None = None,
        profile_confidence: float | None = None,
    ) -> None:
        """Update profile fields on a single column row.

        Separate from ``upsert_columns()`` (which only handles schema
        describe data) — profile fields are written by the profiling
        phase after sampling.
        """
        with self._lock:
            tid = self._resolve_table_id(source_key, table_name)
            parts: list[str] = []
            values: list[Any] = []
            if row_count is not None:
                parts.append("row_count=?")
                values.append(row_count)
            if approx_ndv is not None:
                parts.append("approx_ndv=?")
                values.append(approx_ndv)
            if uniqueness_ratio is not None:
                parts.append("uniqueness_ratio=?")
                values.append(uniqueness_ratio)
            if is_enum is not None:
                parts.append("is_enum=?")
                values.append(is_enum)
            if null_ratio is not None:
                parts.append("null_ratio=?")
                values.append(null_ratio)
            if cast_rate is not None:
                parts.append("cast_rate=?")
                values.append(cast_rate)
            if profile_scope is not None:
                parts.append("profile_scope=?")
                values.append(profile_scope)
            if profile_method is not None:
                parts.append("profile_method=?")
                values.append(profile_method)
            if profile_confidence is not None:
                parts.append("profile_confidence=?")
                values.append(profile_confidence)
            if not parts:
                return
            values.append(tid)
            values.append(column_name)
            self._conn.execute(
                f"UPDATE columns SET {', '.join(parts)} WHERE table_id=? AND name=?",
                values,
            )
            self._conn.commit()

    def update_columns_profile_batch(
        self,
        source_key: str,
        table_name: str,
        profile_fields: list[dict],
    ) -> None:
        """Update profile fields on multiple columns in one batch.

        Each dict must have ``name`` plus any profile fields.
        """
        with self._lock:
            for pf in profile_fields:
                self.update_column_profile(
                    source_key,
                    table_name,
                    pf["name"],
                    row_count=pf.get("row_count"),
                    approx_ndv=pf.get("approx_ndv"),
                    uniqueness_ratio=pf.get("uniqueness_ratio"),
                    is_enum=pf.get("is_enum"),
                    null_ratio=pf.get("null_ratio"),
                    cast_rate=pf.get("cast_rate"),
                    profile_scope=pf.get("profile_scope"),
                    profile_method=pf.get("profile_method"),
                    profile_confidence=pf.get("profile_confidence"),
                )

    # ---- Join candidates ----

    def clear_join_candidates(self) -> int:
        """Delete all generated join candidates and return the deleted row count."""
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM join_candidates").fetchone()[0]
            self._conn.execute("DELETE FROM join_candidates")
            self._conn.commit()
            return count

    def upsert_join_candidate(
        self,
        *,
        left_source_key: str,
        left_table: str,
        left_col: str,
        right_source_key: str,
        right_table: str,
        right_col: str,
        confidence: float,
        evidence: list[dict[str, Any]],
        conflict_group: str | None = None,
        coverage_ratio: float | None = None,
        right_uniqueness_ratio: float | None = None,
        cardinality: str | None = None,
        status: str = "suggested",
    ) -> None:
        """Insert or update one generated join candidate."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            evidence_json = json.dumps(evidence, ensure_ascii=False)
            existing = self._conn.execute(
                "SELECT id FROM join_candidates WHERE "
                "left_source_key=? AND left_table=? AND left_col=? AND "
                "right_source_key=? AND right_table=? AND right_col=?",
                (left_source_key, left_table, left_col, right_source_key, right_table, right_col),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE join_candidates SET confidence=?, status=?, evidence_json=?, "
                    "conflict_group=?, coverage_ratio=?, right_uniqueness_ratio=?, "
                    "cardinality=?, updated_at=? WHERE id=?",
                    (
                        confidence,
                        status,
                        evidence_json,
                        conflict_group,
                        coverage_ratio,
                        right_uniqueness_ratio,
                        cardinality,
                        now,
                        existing[0],
                    ),
                )
            else:
                self._conn.execute(
                    "INSERT INTO join_candidates "
                    "(left_source_key, left_table, left_col, right_source_key, right_table, "
                    "right_col, confidence, status, evidence_json, conflict_group, "
                    "coverage_ratio, right_uniqueness_ratio, cardinality, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        left_source_key,
                        left_table,
                        left_col,
                        right_source_key,
                        right_table,
                        right_col,
                        confidence,
                        status,
                        evidence_json,
                        conflict_group,
                        coverage_ratio,
                        right_uniqueness_ratio,
                        cardinality,
                        now,
                    ),
                )
            self._conn.commit()

    def list_join_candidates(
        self,
        *,
        left_source_key: str | None = None,
        left_table: str | None = None,
        right_source_key: str | None = None,
        right_table: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return candidates ordered by confidence descending."""
        with self._lock:
            clauses: list[str] = []
            params: list[Any] = []
            if left_source_key is not None:
                clauses.append("left_source_key=?")
                params.append(left_source_key)
            if left_table is not None:
                clauses.append("left_table=?")
                params.append(left_table)
            if right_source_key is not None:
                clauses.append("right_source_key=?")
                params.append(right_source_key)
            if right_table is not None:
                clauses.append("right_table=?")
                params.append(right_table)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            sql = f"SELECT * FROM join_candidates{where} ORDER BY confidence DESC"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                item["evidence"] = json.loads(item.get("evidence_json", "[]"))
                result.append(item)
            return result

    # ---- Semantic proposals ----

    def upsert_semantic_proposal(
        self,
        *,
        proposal_key: str,
        target_type: str,
        target_ref: str,
        operation: str,
        patch: dict[str, Any],
        confidence: float,
        evidence: list[dict[str, Any]],
        provenance: str,
        created_by: str,
        status: str = "suggested",
        reopen_rejected: bool = False,
    ) -> int:
        """Insert or update one semantic proposal and return its id."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            patch_json = json.dumps(patch, ensure_ascii=False, sort_keys=True)
            evidence_json = json.dumps(evidence, ensure_ascii=False)
            row = self._conn.execute(
                "SELECT id, status FROM semantic_proposals WHERE proposal_key=?",
                (proposal_key,),
            ).fetchone()
            if row:
                pid = int(row["id"])
                existing_status = row["status"]
                can_reopen = reopen_rejected and existing_status == "rejected"
                if existing_status != "suggested" and not can_reopen:
                    return pid
                if can_reopen:
                    self._conn.execute(
                        "UPDATE semantic_proposals SET target_type=?, target_ref=?, "
                        "operation=?, patch_json=?, confidence=?, evidence_json=?, "
                        "provenance=?, status=?, created_by=?, reviewed_by=NULL, "
                        "reviewed_at=NULL, applied_at=NULL, validation_json=NULL WHERE id=?",
                        (
                            target_type,
                            target_ref,
                            operation,
                            patch_json,
                            confidence,
                            evidence_json,
                            provenance,
                            status,
                            created_by,
                            pid,
                        ),
                    )
                else:
                    self._conn.execute(
                        "UPDATE semantic_proposals SET target_type=?, target_ref=?, "
                        "operation=?, patch_json=?, confidence=?, evidence_json=?, "
                        "provenance=?, status=?, created_by=? WHERE id=?",
                        (
                            target_type,
                            target_ref,
                            operation,
                            patch_json,
                            confidence,
                            evidence_json,
                            provenance,
                            status,
                            created_by,
                            pid,
                        ),
                    )
            else:
                cur = self._conn.execute(
                    "INSERT INTO semantic_proposals "
                    "(proposal_key, target_type, target_ref, operation, patch_json, "
                    "confidence, evidence_json, provenance, status, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        proposal_key,
                        target_type,
                        target_ref,
                        operation,
                        patch_json,
                        confidence,
                        evidence_json,
                        provenance,
                        status,
                        created_by,
                        now,
                    ),
                )
                pid = int(cur.lastrowid)
            self._conn.commit()
            return pid

    def get_semantic_proposal(self, proposal_id: int) -> dict | None:
        """Return one semantic proposal row by id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM semantic_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_semantic_proposals(
        self,
        *,
        status: str | None = None,
        target_type: str | None = None,
        limit: int | None = 50,
    ) -> list[dict]:
        """Return semantic proposals ordered by status, confidence, id."""
        with self._lock:
            clauses: list[str] = []
            params: list[Any] = []
            if status is not None:
                clauses.append("status=?")
                params.append(status)
            if target_type is not None:
                clauses.append("target_type=?")
                params.append(target_type)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            sql = (
                f"SELECT * FROM semantic_proposals{where} ORDER BY status, confidence DESC, id DESC"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def update_semantic_proposal_status(
        self,
        proposal_id: int,
        *,
        status: str,
        reviewed_by: str | None = None,
        validation: dict[str, Any] | None = None,
    ) -> bool:
        """Update proposal status and review metadata."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM semantic_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if row is None:
                return False
            now = datetime.now(timezone.utc).isoformat()
            validation_json = (
                json.dumps(validation, ensure_ascii=False, sort_keys=True)
                if validation is not None
                else None
            )
            applied_at = now if status == "applied" else None
            self._conn.execute(
                "UPDATE semantic_proposals SET status=?, reviewed_by=?, "
                "reviewed_at=?, applied_at=?, validation_json=? WHERE id=?",
                (status, reviewed_by, now, applied_at, validation_json, proposal_id),
            )
            self._conn.commit()
            return True

    # ---- Annotation suggestions ----

    def count_annotation_suggestions(self) -> int:
        """Return total number of annotation suggestions."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM annotation_suggestions"
            ).fetchone()
            return row[0] if row else 0

    def clear_annotation_suggestions(self, source_key: str | None = None) -> int:
        """Delete generated annotation suggestions and return the deleted row count."""
        with self._lock:
            if source_key is not None:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM annotation_suggestions WHERE source_key=?",
                    (source_key,),
                ).fetchone()[0]
                self._conn.execute(
                    "DELETE FROM annotation_suggestions WHERE source_key=?", (source_key,)
                )
            else:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM annotation_suggestions"
                ).fetchone()[0]
                self._conn.execute("DELETE FROM annotation_suggestions")
            self._conn.commit()
            return count

    def upsert_annotation_suggestion(
        self,
        *,
        source_key: str,
        table_name: str,
        column_name: str,
        suggested_role: str,
        suggested_subtype: str | None = None,
        confidence: float,
        evidence: list[dict[str, Any]],
        status: str = "suggested",
    ) -> None:
        """Insert or update one generated annotation suggestion."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            evidence_json = json.dumps(evidence, ensure_ascii=False)
            existing = self._conn.execute(
                "SELECT id FROM annotation_suggestions WHERE "
                "source_key=? AND table_name=? AND column_name=? AND suggested_role=?",
                (source_key, table_name, column_name, suggested_role),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE annotation_suggestions SET suggested_subtype=?, "
                    "confidence=?, evidence_json=?, status=?, updated_at=? WHERE id=?",
                    (suggested_subtype, confidence, evidence_json, status, now, existing[0]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO annotation_suggestions "
                    "(source_key, table_name, column_name, suggested_role, "
                    "suggested_subtype, confidence, evidence_json, status, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_key,
                        table_name,
                        column_name,
                        suggested_role,
                        suggested_subtype,
                        confidence,
                        evidence_json,
                        status,
                        now,
                    ),
                )
            self._conn.commit()

    def list_annotation_suggestions(
        self,
        *,
        source_key: str | None = None,
        table_name: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return suggestions ordered by table, confidence descending, and column."""
        with self._lock:
            clauses: list[str] = []
            params: list[Any] = []
            if source_key is not None:
                clauses.append("source_key=?")
                params.append(source_key)
            if table_name is not None:
                clauses.append("table_name=?")
                params.append(table_name)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            sql = (
                f"SELECT * FROM annotation_suggestions{where} "
                "ORDER BY table_name, confidence DESC, column_name"
            )
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                item["evidence"] = json.loads(item.get("evidence_json", "[]"))
                result.append(item)
            return result
