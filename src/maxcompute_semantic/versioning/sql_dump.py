# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Textual round-trip for the committed slice of ``package.db``.

The committed slice = every table whose name doesn't match the
ignored-prefix tuple ``("sqlite_", "vec_index", "memory_fts")``.
Implementation is stdlib-only — ``sqlite3.Connection.iterdump()``
gives us a sequence of SQL-statement strings, we filter the lines
that name an ignored table, prepend a magic comment carrying the
schema version, and write the result to ``package.sql``.

Restore is the inverse: read the magic comment, open the
destination ``package.db`` (truncating any existing file via
``unlink(missing_ok=True)``), and run ``executescript()`` on the
rest of the file. The post-restore ``PRAGMA user_version`` is set
to the schema version read from the magic comment so the existing
``PackageDB`` schema-migration chain in ``build/storage.py`` knows
whether to migrate the schema forward.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from maxcompute_semantic.versioning.errors import PackageSqlCorrupt

_IGNORED_TABLE_PREFIXES: tuple[str, ...] = (
    "sqlite_",
    "vec_index",
    "memory_fts",
)
"""Table-name prefixes whose ``CREATE TABLE`` and ``INSERT INTO``
statements are dropped from the dump. ``sqlite_*`` is the engine's
internal namespace. ``vec_index*`` is the sqlite-vec extension's
virtual-table family (the ``vec0`` index over float arrays — re-
derivable by re-embedding the source text). ``memory_fts*`` is the
FTS5 virtual-table family for the memory_entries full-text-search
index (the contentless table plus its data/idx/docsize/config
shadow tables — re-derivable by re-tokenizing the source text)."""


_MAGIC_COMMENT_RE = re.compile(r"^-- mcs-versioning: schema_version=(\d+)\s*$", re.MULTILINE)

_LEADING_TARGET_RE = re.compile(
    r"^\s*(?:"
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?"
    r"|INSERT(?:\s+OR\s+\w+)?\s+INTO"
    r"|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\S+\s+ON"
    r")\s+(?:\"([^\"]+)\"|'([^']+)'|(\w+))",
    re.IGNORECASE,
)


def _is_ignored_statement(line: str) -> bool:
    """``True`` if the ``iterdump`` line targets an ignored table.

    Two checks compose the filter:

    1. **Leading-target parse.** Match the statement's opening
       keyword (``CREATE TABLE`` / ``CREATE VIRTUAL TABLE`` /
       ``INSERT INTO`` / ``CREATE INDEX … ON``) and pull out the
       target table name — quoted or unquoted. If that name starts
       with an ignored prefix, the statement is dropped. This is the
       form pre-3.11.5 Python's ``iterdump`` emits for virtual
       tables: ``CREATE VIRTUAL TABLE memory_fts USING fts5(...)``
       straight from ``sqlite_master.sql`` with the table name
       unquoted. The 3.13+ ``iterdump`` uses the ``INSERT INTO
       sqlite_master(…) VALUES('table','memory_fts',…,'CREATE
       VIRTUAL TABLE …')`` form instead, where ``'memory_fts'``
       inside the VALUES tuple is single-quoted and the substring
       check below catches it — but the leading-target parse on
       that line sees ``INSERT INTO sqlite_master`` and lets it
       through, so we need the substring check too.
    2. **Quoted-substring scan** (the legacy belt-and-suspenders
       fallback). Catches the 3.13+ sqlite_master-insert variant
       and any shadow-table ``CREATE TABLE 'memory_fts_data'`` /
       ``INSERT INTO "memory_fts_idx"`` rows.

    Triggers that *reference* an ignored table inside their body
    are NOT affected by either check: the leading parse sees
    ``CREATE TRIGGER`` (not in the keyword set) and the substring
    scan sees the unquoted body token (``INSERT INTO memory_fts(...)``
    with no surrounding quote). That fallthrough is intentional and
    correct — the trigger statements roundtrip through the dump and
    are re-played by ``executescript`` during restore;
    ``PackageDB.open`` on the restored file re-creates the FTS5
    virtual table itself before the triggers ever fire."""
    m = _LEADING_TARGET_RE.match(line)
    if m is not None:
        name = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        if any(name.startswith(prefix) for prefix in _IGNORED_TABLE_PREFIXES):
            return True
    lower = line.lower()
    # ``BEGIN TRANSACTION;`` / ``COMMIT;`` / ``DELETE FROM
    # "sqlite_sequence";`` boilerplate that ``iterdump`` emits passes
    # through here unchanged — the DELETE on sqlite_sequence falls
    # into the sqlite_ branch of the substring check below; BEGIN /
    # COMMIT never match the quoted-prefix tokens.
    return any(f'"{prefix}' in lower or f"'{prefix}" in lower for prefix in _IGNORED_TABLE_PREFIXES)


def dump_db_to_sql(
    db_path: Path,
    sql_path: Path,
    *,
    schema_version: int,
) -> None:
    """Dump the committed-slice tables of ``db_path`` to a text file
    at ``sql_path`` with the magic-comment header carrying
    ``schema_version``.

    ``schema_version`` is the integer the source DB's ``PRAGMA
    user_version`` reads back as. The hook (T5) computes it via
    ``conn.execute("PRAGMA user_version").fetchone()[0]`` against an
    open ``PackageDB``-managed connection right before calling this
    function; if the source DB doesn't exist yet (the very first
    ``build`` of a brand-new profile), the caller passes the current
    ``_SCHEMA_VERSION`` constant from ``build/storage.py`` instead.

    The output is utf-8, LF line endings (the SQLite ``iterdump``
    output is ASCII for the keywords and the row values are
    SQL-string-quoted, so non-ASCII bytes only appear inside the
    quoted literal column-text values where they're properly
    escaped by SQLite's quoter into ``X'<hex>'`` blob literals).
    The dump is deterministic *given* a deterministic source DB —
    ``iterdump`` walks ``sqlite_schema`` in rowid order and the row
    sets in ``ORDER BY rowid`` (per the CPython implementation
    documented in stdlib ``Lib/sqlite3/dump.py``), so two builds
    that produce the same logical content produce byte-identical
    dumps modulo any non-deterministic floats that the ``memory``
    embedding floats would carry — those live in the
    ``vec_index`` shadow tables that we explicitly drop.

    The file is written atomically via a temp file in the same
    directory + ``os.replace`` so a crash mid-write doesn't leave a
    half-written ``package.sql`` for the next mcs to consume.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"source ``package.db`` does not exist at {db_path!r}")
    # Atomic write: tmp in same dir, rename over.
    tmp_path = sql_path.with_suffix(sql_path.suffix + ".tmp")
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(str(db_path)) as conn,
        tmp_path.open("w", encoding="utf-8", newline="\n") as fout,
    ):
        fout.write(f"-- mcs-versioning: schema_version={schema_version}\n")
        for stmt in conn.iterdump():
            if _is_ignored_statement(stmt):
                continue
            fout.write(stmt)
            # ``iterdump`` yields statements *without* a trailing
            # newline. Emit a newline ourselves so the output is
            # line-oriented and human-readable in ``git diff``.
            if not stmt.endswith("\n"):
                fout.write("\n")
    # ``os.replace`` is atomic on POSIX and on Windows. We use it via
    # ``Path.replace`` which is the Pathlib wrapper.
    tmp_path.replace(sql_path)


def restore_sql_to_db(sql_path: Path, db_path: Path) -> int:
    """Rebuild ``db_path`` from the text dump at ``sql_path``. Returns
    the schema version read from the magic comment so the caller can
    cross-check it against the current ``_SCHEMA_VERSION`` in
    ``build/storage.py`` (and run a migration via the existing
    chain if they don't match — the caller, ``mcs profile reset``,
    handles that by opening a ``PackageDB`` against the file
    immediately after this returns, which kicks the existing migrator
    in ``storage.py:_check_version_and_migrate``).

    Any existing ``db_path`` is overwritten — the dump is the
    authoritative state of the world. The dump's contents are
    executed via ``sqlite3.Connection.executescript()`` which wraps
    the whole thing in a single implicit transaction (the dump
    starts with ``BEGIN TRANSACTION;`` and ends with ``COMMIT;`` so
    the ``executescript`` matches that pairing).

    Failures during the parse / execute phase raise
    ``PackageSqlCorrupt`` with the dump file path in the message,
    so the user can identify which profile's history was the
    casualty and ``mcs profile reset --to <previous-sha>`` to the
    previous good dump.
    """
    sql_text = sql_path.read_text(encoding="utf-8")
    m = _MAGIC_COMMENT_RE.search(sql_text)
    if m is None:
        raise PackageSqlCorrupt(
            f"package.sql at {sql_path} is missing the "
            f"``-- mcs-versioning: schema_version=N`` magic comment header.",
            remediation=(
                "the file was either written by a pre-versioning-era mcs "
                "(no longer supported) or has been corrupted on disk. "
                "If the parent git repo's history is intact, "
                f"``cd {sql_path.parent} && git show HEAD:package.sql > package.sql`` "
                "restores the most recently committed dump."
            ),
        )
    schema_version = int(m.group(1))

    # Wipe any existing DB so the restore starts from a clean slate.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    # Also wipe the SQLite WAL siblings if they exist.
    for sibling_suffix in ("-journal", "-wal", "-shm"):
        sibling = Path(str(db_path) + sibling_suffix)
        sibling.unlink(missing_ok=True)

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.executescript(sql_text)
            # Stamp the schema version so the existing PackageDB
            # migrator (which reads ``PRAGMA user_version`` at open
            # time) sees the dump's version, not zero. The dump's
            # ``iterdump`` output doesn't include the user_version
            # pragma — it's a connection-level setting, not a SQL
            # statement that appears in the schema. We set it
            # explicitly here.
            conn.execute(f"PRAGMA user_version = {schema_version}")
            conn.commit()
    except sqlite3.DatabaseError as e:
        # Clean up the half-written file so the next ``open`` of
        # ``PackageDB`` doesn't try to interpret it.
        db_path.unlink(missing_ok=True)
        raise PackageSqlCorrupt(
            f"package.sql at {sql_path} failed to load into a fresh sqlite database",
            remediation=(
                f"sqlite reported: {e}. The dump's magic comment "
                f"declared schema_version={schema_version}; "
                "the current mcs's ``build/storage.py:_SCHEMA_VERSION`` "
                "may be incompatible if the dump was produced by a "
                "newer mcs. Inspect ``git log -- package.sql`` to "
                "find the commit that introduced the breakage and "
                "``mcs profile reset --to <previous-sha>``."
            ),
        ) from e

    return schema_version
