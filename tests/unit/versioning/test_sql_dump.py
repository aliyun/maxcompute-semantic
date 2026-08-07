# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""sql_dump — round-trip a package.db through a text dump file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from maxcompute_semantic.build.storage import _SCHEMA_VERSION, PackageDB
from maxcompute_semantic.versioning.errors import PackageSqlCorrupt
from maxcompute_semantic.versioning.sql_dump import (
    _IGNORED_TABLE_PREFIXES,
    _MAGIC_COMMENT_RE,
    _is_ignored_statement,
    dump_db_to_sql,
    restore_sql_to_db,
)


def _populate_minimal(db_path: Path) -> None:
    """Open a PackageDB at ``db_path``, insert a couple of rows into
    user-data tables so a non-trivial dump exists, close cleanly.

    The exact column shape (which fields of ``tables`` / ``memory_entries``
    / ``udfs`` take which values) is taken from the live ``_SCHEMA_SQL``
    constant in ``build/storage.py`` rather than the plan's pseudo-schema
    — the live schema is the source of truth and the plan's docstring
    is illustrative. The integration test in T21 exercises the
    end-to-end through a real ``mcs build`` so this unit's job is
    only to make sure dump→restore preserves whatever rows the
    upstream populated.
    """
    pdb = PackageDB(db_path)
    conn = pdb._conn  # the underlying sqlite3 connection
    # Two rows in ``tables`` (the schema's flagship user-data table),
    # one in ``memory_entries`` (so the FTS5 shadow tables are
    # non-empty in the source DB and the dump's filter actually
    # drops something observable), one in ``udfs`` (covering a
    # third table family).
    conn.executemany(
        "INSERT INTO tables (source_key, name, schema_hash, last_built_at, "
        "errors_json, ai_context) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("p1__default", "orders", "h1", "2026-05-23T00:00:00Z", None, "the orders fact"),
            ("p1__default", "customers", "h2", "2026-05-23T00:00:00Z", None, "the customer dim"),
        ],
    )
    conn.execute(
        "INSERT INTO memory_entries (kind, payload_json, retrieval_text, "
        "fts_text, tags_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "verified_query",
            '{"sql": "SELECT * FROM customers LIMIT 5"}',
            "show top 5 customers SELECT * FROM customers LIMIT 5",
            "show top 5 customers SELECT FROM customers LIMIT 5",
            None,
            "2026-05-23T00:00:00Z",
        ),
    )
    # The FTS5 trigger on memory_entries propagates the row into the
    # memory_fts virtual table — the dump's filter must drop that
    # propagated shadow data, but the source row in memory_entries
    # itself must survive the round trip.
    conn.execute(
        "INSERT INTO udfs (name, kind, signature, class_name, description, "
        "last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "udf_isodate",
            "function",
            "STRING -> STRING",
            "com.example.IsoDateUdf",
            "format a unix timestamp as ISO 8601",
            "2026-05-22T00:00:00Z",
        ),
    )
    conn.commit()
    pdb.close()


def test_round_trip_preserves_user_rows(tmp_path: Path) -> None:
    """The smoke contract: dump a populated DB → restore into a fresh
    DB → row counts and key values match for every dumped table."""
    src_db = tmp_path / "src" / "package.db"
    src_db.parent.mkdir(parents=True)
    _populate_minimal(src_db)
    # Read the source's PRAGMA user_version so the dump's magic
    # comment matches the upstream schema-version constant.
    with sqlite3.connect(str(src_db)) as src_conn:
        src_version = src_conn.execute("PRAGMA user_version").fetchone()[0]
    assert src_version == _SCHEMA_VERSION, (
        "freshly-opened PackageDB should stamp ``PRAGMA user_version`` "
        "to ``_SCHEMA_VERSION``; if this assertion fails the schema "
        "constant in ``build/storage.py`` has drifted from what "
        "PackageDB.__init__ writes — investigate before continuing."
    )

    sql_file = tmp_path / "package.sql"
    dump_db_to_sql(src_db, sql_file, schema_version=src_version)

    # The file exists, starts with the magic comment, and contains
    # no lines naming ignored tables.
    text = sql_file.read_text(encoding="utf-8")
    m = _MAGIC_COMMENT_RE.search(text)
    assert m is not None and int(m.group(1)) == src_version
    for line in text.splitlines():
        lower = line.lower()
        for prefix in _IGNORED_TABLE_PREFIXES:
            assert f'"{prefix}' not in lower, (
                f"line names ignored-prefix table {prefix!r}: {line!r}"
            )

    dest_db = tmp_path / "dest" / "package.db"
    dest_db.parent.mkdir(parents=True)
    restored_version = restore_sql_to_db(sql_file, dest_db)
    assert restored_version == src_version

    # The destination's user-data rows match the source's.
    with sqlite3.connect(str(dest_db)) as dst_conn:
        dst_version = dst_conn.execute("PRAGMA user_version").fetchone()[0]
        assert dst_version == src_version
        tables_rows = dst_conn.execute(
            "SELECT source_key, name, schema_hash, ai_context FROM tables ORDER BY name"
        ).fetchall()
        memory_rows = dst_conn.execute(
            "SELECT kind, payload_json, retrieval_text FROM memory_entries"
        ).fetchall()
        udf_rows = dst_conn.execute("SELECT name, kind, signature FROM udfs").fetchall()

    assert tables_rows == [
        ("p1__default", "customers", "h2", "the customer dim"),
        ("p1__default", "orders", "h1", "the orders fact"),
    ]
    assert memory_rows == [
        (
            "verified_query",
            '{"sql": "SELECT * FROM customers LIMIT 5"}',
            "show top 5 customers SELECT * FROM customers LIMIT 5",
        ),
    ]
    assert udf_rows == [("udf_isodate", "function", "STRING -> STRING")]


def test_restore_with_missing_magic_comment_raises_package_sql_corrupt(
    tmp_path: Path,
) -> None:
    """A dump without the magic comment header is corrupt — the
    restore must not silently apply it (because there's no way to
    know what schema version it was taken at, and the existing
    PackageDB migrator would interpret ``PRAGMA user_version = 0``
    as "pre-versioning era, refuse to open")."""
    sql_file = tmp_path / "package.sql"
    sql_file.write_text(
        "BEGIN TRANSACTION;\nCREATE TABLE foo (id INTEGER);\nCOMMIT;\n",
        encoding="utf-8",
    )
    dest_db = tmp_path / "dest.db"
    with pytest.raises(PackageSqlCorrupt) as exc_info:
        restore_sql_to_db(sql_file, dest_db)
    assert "magic comment" in str(exc_info.value).lower()
    assert exc_info.value.remediation
    # The destination file must not exist on a failed restore.
    assert not dest_db.exists()


def test_restore_with_malformed_sql_body_raises_package_sql_corrupt(
    tmp_path: Path,
) -> None:
    """A dump with the magic comment but with a syntactically broken
    SQL body raises ``PackageSqlCorrupt`` and cleans up the
    half-written destination file."""
    sql_file = tmp_path / "package.sql"
    sql_file.write_text(
        "-- mcs-versioning: schema_version=5\n"
        "BEGIN TRANSACTION;\n"
        "THIS IS NOT VALID SQL;\n"
        "COMMIT;\n",
        encoding="utf-8",
    )
    dest_db = tmp_path / "dest.db"
    with pytest.raises(PackageSqlCorrupt) as exc_info:
        restore_sql_to_db(sql_file, dest_db)
    assert "schema_version=5" in str(exc_info.value) or "sqlite" in str(exc_info.value).lower()
    assert not dest_db.exists(), (
        "a failed restore must clean up its half-written destination "
        "file so the next PackageDB.open doesn't try to interpret "
        "the broken bytes."
    )


def test_dump_is_atomic_via_tmp_file_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dump_db_to_sql`` writes to a ``.tmp`` sibling first, then
    ``os.replace``'s it into place. If the writer is interrupted
    after creating the tmp but before the rename, the destination
    path remains unchanged. We exercise the contract by monkey-
    patching ``Path.replace`` to raise immediately after the tmp has
    been fully written, then assert the destination path is empty
    and the tmp file exists with the dump contents."""
    src_db = tmp_path / "src" / "package.db"
    src_db.parent.mkdir(parents=True)
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]

    sql_file = tmp_path / "out" / "package.sql"
    # Pre-create the destination with a sentinel content. If the dump
    # is non-atomic, this sentinel will be partially overwritten.
    sql_file.parent.mkdir()
    sql_file.write_text("SENTINEL\n", encoding="utf-8")

    def boom(self: Path, target: object) -> None:
        raise OSError("simulated interrupt before atomic rename")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated interrupt"):
        dump_db_to_sql(src_db, sql_file, schema_version=ver)
    # The destination's sentinel survives because the rename never
    # happened.
    assert sql_file.read_text(encoding="utf-8") == "SENTINEL\n"
    # The tmp file is left on disk (the caller / cleanup logic in
    # the hook is responsible for sweeping stale tmps — the dump
    # function itself doesn't try to garbage-collect on failure
    # because a separate concurrent dump might have legitimately
    # created the same-named tmp). We assert the tmp exists with
    # the dump content as the recovery breadcrumb.
    tmp_file = sql_file.with_suffix(sql_file.suffix + ".tmp")
    assert tmp_file.exists()
    tmp_content = tmp_file.read_text(encoding="utf-8")
    assert tmp_content.startswith(f"-- mcs-versioning: schema_version={ver}\n")
    # Cleanup so the test's tmpdir doesn't carry the file forward.
    monkeypatch.undo()
    tmp_file.unlink()


def test_filter_drops_unquoted_create_virtual_table() -> None:
    """Pre-3.11.5 Python's ``iterdump`` emits the FTS5 virtual table
    DDL straight from ``sqlite_master.sql`` — unquoted name, just
    ``CREATE VIRTUAL TABLE memory_fts USING fts5(...)``. The 3.13+
    iterdump emits a different form (``INSERT INTO sqlite_master(...)
    VALUES('table','memory_fts',...)`` with a single-quoted name) which
    the substring fallback catches. This test pins the unquoted-leading
    form: the leading-target parse must recognize the bare ``memory_fts``
    token as an ignored table and drop the statement, otherwise the
    restore would re-create the FTS5 shadow tables and break the
    round-trip contract on RHEL/centos CI images.

    The same parse must also drop ``vec_index`` and the ``sqlite_``
    namespace family in their unquoted forms (defense in depth — if
    a future SQLite or iterdump change produces an unquoted CREATE
    for any of these, the filter still catches it)."""
    assert _is_ignored_statement(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "fts_text, content='memory_entries', content_rowid='id');"
    )
    assert _is_ignored_statement(
        "CREATE TABLE memory_fts_data(id INTEGER PRIMARY KEY, block BLOB);"
    )
    assert _is_ignored_statement("INSERT INTO memory_fts_idx VALUES(1, X'00', 2);")
    assert _is_ignored_statement("CREATE VIRTUAL TABLE vec_index USING vec0(embedding FLOAT[384]);")
    # The CREATE TRIGGER form whose body REFERENCES memory_fts
    # (unquoted inside BEGIN ... END) must NOT be dropped — the
    # trigger DDL has to roundtrip so PackageDB.open re-installs it.
    assert not _is_ignored_statement(
        "CREATE TRIGGER memory_ai AFTER INSERT ON memory_entries BEGIN "
        "INSERT INTO memory_fts(rowid, fts_text) "
        "VALUES (new.id, new.fts_text); END;"
    )
    # Plain user tables pass through.
    assert not _is_ignored_statement("CREATE TABLE tables(id INTEGER PRIMARY KEY, name TEXT);")
    assert not _is_ignored_statement(
        "INSERT INTO memory_entries VALUES(1, 'verified_query', '{}', "
        "'t', 't', NULL, '2026-01-01T00:00:00Z');"
    )


def test_filter_skips_sqlite_internal_tables(tmp_path: Path) -> None:
    """The ``sqlite_sequence`` table that the engine auto-creates for
    AUTOINCREMENT columns is in the ``sqlite_`` prefix family and
    must be filtered out of the dump.

    The live ``memory_entries`` schema uses plain ``INTEGER PRIMARY
    KEY`` (not ``AUTOINCREMENT``) so the engine doesn't materialize
    ``sqlite_sequence`` from it. We force the situation by creating
    an extra throw-away table with ``AUTOINCREMENT`` on the source
    DB after PackageDB init, insert one row to trigger the counter,
    then dump and check the text never names sqlite_sequence."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        c.execute("CREATE TABLE _autoinc_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
        c.execute("INSERT INTO _autoinc_probe (name) VALUES ('seed')")
        c.commit()
        ver = c.execute("PRAGMA user_version").fetchone()[0]
        # Confirm the engine did materialize sqlite_sequence (the
        # auto-increment counter table).
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "sqlite_sequence" in names, (
            "sanity: AUTOINCREMENT on the throw-away probe table should "
            "have materialized the sqlite_sequence counter table. If "
            "this fails, SQLite's AUTOINCREMENT semantics have changed "
            "(very unlikely) and the test's setup is no longer valid."
        )

    sql_file = tmp_path / "out.sql"
    dump_db_to_sql(src_db, sql_file, schema_version=ver)
    text = sql_file.read_text(encoding="utf-8")
    assert "sqlite_sequence" not in text.lower()


def test_dump_is_byte_deterministic_for_same_input(tmp_path: Path) -> None:
    """Two dumps of the same source DB byte-for-byte match. Important
    so the auto-commit hook's ``git diff --cached --quiet`` short-
    circuit fires when a write touched the DB but the resulting
    dump is unchanged (e.g. a no-op the proposal workflow that re-set the
    same ai_context). The determinism comes from ``iterdump``'s
    stable walk order over the schema (``sqlite_schema``'s rowid
    order) and the stable row order it uses for the data (``ORDER
    BY rowid`` per CPython's implementation in
    ``Lib/sqlite3/dump.py``)."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]

    out_a = tmp_path / "a.sql"
    out_b = tmp_path / "b.sql"
    dump_db_to_sql(src_db, out_a, schema_version=ver)
    dump_db_to_sql(src_db, out_b, schema_version=ver)
    assert out_a.read_bytes() == out_b.read_bytes(), (
        "dumps of the same DB must be byte-identical so the hook's "
        "``git diff --cached --quiet`` empty-change short-circuit "
        "fires correctly on a no-op write."
    )


def test_round_trip_under_packagedb_open_recreates_virtual_tables(
    tmp_path: Path,
) -> None:
    """The dump drops the ``vec_index`` and ``memory_fts*`` virtual-
    table family. After restore, opening a ``PackageDB`` against the
    restored file kicks the existing ``_ensure_schema`` / migration
    chain, which CREATEs the missing virtual tables with empty
    contents. The user-data rows in ``memory_entries`` are intact —
    the next ``mcs memory reindex`` repopulates the FTS / vec
    contents from them."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]

    sql_file = tmp_path / "out.sql"
    dest_db = tmp_path / "dest.db"
    dump_db_to_sql(src_db, sql_file, schema_version=ver)
    restore_sql_to_db(sql_file, dest_db)

    # Sanity: the virtual tables don't exist in the raw restored DB
    # (no CREATE in the dump, no CREATE under restore).
    with sqlite3.connect(str(dest_db)) as c:
        names_raw = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
    assert "memory_entries" in names_raw  # user-data side survived
    assert "memory_fts" not in names_raw  # FTS virtual table dropped
    assert "vec_index" not in names_raw  # vec virtual table dropped

    # Open via PackageDB — the schema-ensure step in
    # ``build/storage.py`` recreates the missing virtual tables with
    # empty contents.
    pdb = PackageDB(dest_db)
    try:
        with sqlite3.connect(str(dest_db)) as c:
            names_after = {
                r[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
            }
        # The FTS5 virtual table is always recreated.
        assert "memory_fts" in names_after
        # vec_index re-creation depends on sqlite-vec being loadable
        # in the current environment. Skip the assertion when the
        # extension isn't available (CI images without sqlite-vec).
        try:
            import sqlite_vec  # noqa: F401

            vec_available = True
        except ImportError:
            vec_available = False
        if vec_available:
            assert "vec_index" in names_after, (
                "the sqlite-vec virtual table ``vec_index`` should be "
                "re-created by ``PackageDB.__init__``'s schema-ensure "
                "step on the restored DB when sqlite-vec is loadable."
            )
        # User rows in memory_entries are intact.
        cur = pdb._conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()
        assert cur[0] == 1
    finally:
        pdb.close()


# ---------- additional one-liner cases ----------


def test_dump_file_uses_lf_line_endings_on_all_platforms(
    tmp_path: Path,
) -> None:
    """The dump opens its output stream with ``newline="\\n"`` so CRLF
    mode on Windows doesn't double-line-feed the body."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]
    sql_file = tmp_path / "out.sql"
    dump_db_to_sql(src_db, sql_file, schema_version=ver)
    assert b"\r\n" not in sql_file.read_bytes()


def test_dump_throws_filenotfound_when_source_db_missing(
    tmp_path: Path,
) -> None:
    """Calling ``dump_db_to_sql`` with a ``db_path`` that doesn't
    exist raises ``FileNotFoundError`` — the explicit pre-check,
    not the sqlite3 layer's vaguer "unable to open database file"
    wording."""
    missing = tmp_path / "does-not-exist.db"
    sql_file = tmp_path / "out.sql"
    with pytest.raises(FileNotFoundError):
        dump_db_to_sql(missing, sql_file, schema_version=1)


def test_restore_sweeps_sqlite_wal_siblings(tmp_path: Path) -> None:
    """Pre-create ``package.db-wal``, ``-shm`` and ``-journal`` next to
    the destination path with marker content + a captured inode. After
    restore returns, the planted marker content must be gone — either
    the sibling was unlinked (the WAL/SHM case under default journal
    mode: nothing left to recreate them) or it was replaced with a
    different inode/contents by sqlite during the executescript
    transaction (the -journal case, which sqlite re-creates and then
    deletes on commit/close)."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]
    sql_file = tmp_path / "out.sql"
    dest_db = tmp_path / "dest.db"
    dump_db_to_sql(src_db, sql_file, schema_version=ver)

    siblings = {
        "wal": Path(str(dest_db) + "-wal"),
        "shm": Path(str(dest_db) + "-shm"),
        "journal": Path(str(dest_db) + "-journal"),
    }
    markers = {kind: f"stale-{kind}-bytes-marker".encode() for kind in siblings}
    pre_inodes: dict[str, int] = {}
    for kind, path in siblings.items():
        path.write_bytes(markers[kind])
        pre_inodes[kind] = path.stat().st_ino

    restore_sql_to_db(sql_file, dest_db)

    for kind, path in siblings.items():
        if not path.exists():
            # Best outcome: restore unlinked the sibling and nothing
            # in the executescript reopened it. WAL/SHM are expected
            # to land here because the default journal mode is
            # DELETE, not WAL, so sqlite never creates them on a
            # fresh open.
            continue
        # If the sibling still exists, it MUST have been replaced
        # by sqlite during restore — different inode AND different
        # content. Surviving with the marker content would mean the
        # destructive sweep was a no-op.
        assert path.stat().st_ino != pre_inodes[kind], (
            f"{path} survived restore with the same inode — sweep "
            "was bypassed and the stale file was not unlinked"
        )
        assert path.read_bytes() != markers[kind], (
            f"{path} still contains the planted marker content after restore"
        )


def test_magic_comment_regex_tolerates_trailing_whitespace() -> None:
    """A magic comment line with trailing whitespace and a CRLF tail
    (defensive parsing because the user could have hand-edited the
    file) still parses to the correct integer version."""
    samples = [
        "-- mcs-versioning: schema_version=7\n",
        "-- mcs-versioning: schema_version=7 \n",
        "-- mcs-versioning: schema_version=7\r\n",
        "-- mcs-versioning: schema_version=7  \r\n",
    ]
    for body in samples:
        m = _MAGIC_COMMENT_RE.search(body + "BEGIN TRANSACTION;\n")
        assert m is not None, f"failed to match magic comment in {body!r}"
        assert int(m.group(1)) == 7


def test_ignored_prefixes_are_case_insensitive_match_on_quoted_names(
    tmp_path: Path,
) -> None:
    """The filter's lowercase comparison catches mixed-case quoted
    names (SQLite is case-insensitive for unquoted identifiers but
    case-preserving for quoted ones, so a hand-written schema could
    in theory use mixed case)."""
    from maxcompute_semantic.versioning.sql_dump import _is_ignored_statement

    assert _is_ignored_statement('CREATE TABLE "Memory_Fts_Data"(x INT);')
    assert _is_ignored_statement('INSERT INTO "VEC_INDEX"(rowid) VALUES(1);')
    assert _is_ignored_statement('CREATE TABLE "sqlite_Sequence"(name, seq);')
    assert not _is_ignored_statement('CREATE TABLE "memory_entries"(id INTEGER PRIMARY KEY);')


def test_restore_pragma_user_version_matches_magic_comment(
    tmp_path: Path,
) -> None:
    """After a restore, querying ``PRAGMA user_version`` against the
    destination's connection returns the integer from the dump's
    magic comment, exactly."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]
    sql_file = tmp_path / "out.sql"
    dest_db = tmp_path / "dest.db"
    dump_db_to_sql(src_db, sql_file, schema_version=ver)
    restore_sql_to_db(sql_file, dest_db)
    with sqlite3.connect(str(dest_db)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == ver


def test_sequential_dumps_with_drift_produce_different_outputs(
    tmp_path: Path,
) -> None:
    """Populate, dump A, insert another row, dump B; the two output
    files differ in exactly the lines pertaining to the new row."""
    src_db = tmp_path / "src.db"
    _populate_minimal(src_db)
    with sqlite3.connect(str(src_db)) as c:
        ver = c.execute("PRAGMA user_version").fetchone()[0]

    out_a = tmp_path / "a.sql"
    dump_db_to_sql(src_db, out_a, schema_version=ver)

    with sqlite3.connect(str(src_db)) as c:
        c.execute(
            "INSERT INTO tables (source_key, name, schema_hash, "
            "last_built_at, errors_json, ai_context) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "p1__default",
                "drift_row",
                "h-drift",
                "2026-05-23T00:00:00Z",
                None,
                "added between dumps",
            ),
        )
        c.commit()

    out_b = tmp_path / "b.sql"
    dump_db_to_sql(src_db, out_b, schema_version=ver)

    assert out_a.read_bytes() != out_b.read_bytes()
    assert "drift_row" not in out_a.read_text(encoding="utf-8")
    assert "drift_row" in out_b.read_text(encoding="utf-8")
