# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""sqlite-vec extension loading and virtual-table management.

Handles the full lifecycle: load extension, create vec0 table,
insert/query vectors. Gracefully degrades when the extension
cannot be loaded (old SQLite, blocked extension loading, missing
sqlite-vec package).
"""

from __future__ import annotations

import sqlite3
import struct

_VEC_TABLE_NAME = "vec_index"
_VEC_DIM = 384  # matches all-MiniLM-L6-v2


def serialize_f32(vector: list[float]) -> bytes:
    """Serialize a float list to the binary format sqlite-vec expects.

    Uses little-endian float32 encoding matching
    ``sqlite_vec.serialize_float32()`` but without requiring the
    sqlite-vec package at serialization time.
    """
    return struct.pack(f"<{len(vector)}f", *vector)


def load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Attempt to load sqlite-vec into the connection.

    Returns True if successful, False if loading failed or sqlite-vec
    is not installed. Failure is non-fatal — the caller falls back
    to BM25-only mode.
    """
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except (ImportError, sqlite3.OperationalError, RuntimeError):
        return False


def vec_extension_loaded(conn: sqlite3.Connection) -> bool:
    """Check whether sqlite-vec extension is already loaded."""
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        return False


def create_vec_table(conn: sqlite3.Connection) -> bool:
    """Create the vec_index virtual table if sqlite-vec is loaded.

    Returns True if the table was created, False if vec extension
    is unavailable.
    """
    if not vec_extension_loaded(conn) and not load_vec_extension(conn):
        return False
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_VEC_TABLE_NAME} "
            f"USING vec0(embedding float[{_VEC_DIM}])"
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def vec_table_exists(conn: sqlite3.Connection) -> bool:
    """Check whether the vec_index virtual table exists."""
    if not vec_extension_loaded(conn):
        return False
    try:
        conn.execute(f"SELECT count(*) FROM {_VEC_TABLE_NAME} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def insert_vector(conn: sqlite3.Connection, rowid: int, embedding: list[float]) -> None:
    """Insert a vector into vec_index linked to the given rowid."""
    conn.execute(
        f"INSERT INTO {_VEC_TABLE_NAME}(rowid, embedding) VALUES (?, ?)",
        (rowid, serialize_f32(embedding)),
    )
    conn.commit()


def delete_vector(conn: sqlite3.Connection, rowid: int) -> None:
    """Delete a vector from vec_index for the given rowid.

    vec0 virtual tables do not support CASCADE — this must be called
    explicitly before deleting from memory_entries.
    """
    if not vec_table_exists(conn):
        return
    conn.execute(f"DELETE FROM {_VEC_TABLE_NAME} WHERE rowid=?", (rowid,))
    conn.commit()


def clear_all_vectors(conn: sqlite3.Connection) -> int:
    """Delete all vectors from vec_index.

    Returns the number of rows deleted, or -1 if vec_index is unavailable.
    """
    if not vec_table_exists(conn):
        return -1
    try:
        count = conn.execute(f"SELECT count(*) FROM {_VEC_TABLE_NAME}").fetchone()[0]
        # vec0 virtual tables don't support DELETE without WHERE, so
        # delete rowids individually.
        rowids = conn.execute(f"SELECT rowid FROM {_VEC_TABLE_NAME}").fetchall()
        for row in rowids:
            conn.execute(f"DELETE FROM {_VEC_TABLE_NAME} WHERE rowid=?", (row[0],))
        conn.commit()
        return int(count)
    except sqlite3.OperationalError:
        return -1


def query_vectors(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Return (rowid, distance) pairs for the top-K nearest vectors.

    Distance is cosine distance (0 = identical for normalized vectors).
    Returns an empty list if vec_index is unavailable.
    """
    if not vec_table_exists(conn):
        return []
    try:
        rows = conn.execute(
            f"SELECT rowid, distance FROM {_VEC_TABLE_NAME} "
            f"WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (serialize_f32(query_embedding), top_k),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
    except sqlite3.OperationalError:
        return []
