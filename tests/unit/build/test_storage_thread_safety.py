"""Concurrent access regression tests for PackageDB.

Justifies the single-connection + threading.RLock design used to make
BuildPipeline._run_full safe under --parallel N.
"""

from __future__ import annotations

import threading
from pathlib import Path

from maxcompute_semantic.build.storage import PackageDB


def _seed_table(db: PackageDB, source_key: str, name: str) -> int:
    """Insert a table row and return its id."""
    return db.upsert_table(
        source_key=source_key,
        name=name,
        schema_hash="hash-" + name,
        errors_json=None,
        table_type="MANAGED_TABLE",
    )


def test_concurrent_upsert_columns_no_programming_error(tmp_path: Path) -> None:
    """Two threads calling upsert_columns on different tables must not
    raise sqlite3.ProgrammingError and all writes must land."""
    db = PackageDB(tmp_path / "pkg.db")
    table_ids = [_seed_table(db, "src__default", f"t{i}") for i in range(2)]

    errors: list[BaseException] = []

    def writer(table_id: int, prefix: str) -> None:
        try:
            for n in range(20):
                db.upsert_columns(
                    table_id,
                    [
                        {
                            "name": f"{prefix}_col_{n}",
                            "type": "STRING",
                            "comment": "",
                            "is_partition": 0,
                        }
                    ],
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(table_ids[0], "a")),
        threading.Thread(target=writer, args=(table_ids[1], "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"workers raised: {errors}"
    # upsert_columns deletes stale rows on each call; only the last write's
    # column survives per table — that's the contract we want preserved.
    cols_a = db.get_columns(table_ids[0])
    cols_b = db.get_columns(table_ids[1])
    assert len(cols_a) == 1 and cols_a[0]["name"] == "a_col_19"
    assert len(cols_b) == 1 and cols_b[0]["name"] == "b_col_19"


def test_concurrent_reader_and_writer(tmp_path: Path) -> None:
    """A reader thread issuing get_table while a writer issues upsert_table
    must not raise; reader sees either old or new value, never crashes."""
    db = PackageDB(tmp_path / "pkg.db")
    _seed_table(db, "src__default", "t0")

    errors: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                row = db.get_table("src__default", "t0")
                assert row is not None
        except BaseException as exc:
            errors.append(exc)

    def writer() -> None:
        try:
            for _ in range(50):
                db.upsert_table(
                    source_key="src__default",
                    name="t0",
                    schema_hash="hash-x",
                    errors_json=None,
                    table_type="MANAGED_TABLE",
                )
        finally:
            stop.set()

    rt = threading.Thread(target=reader)
    wt = threading.Thread(target=writer)
    rt.start()
    wt.start()
    wt.join()
    rt.join()

    assert errors == [], f"workers raised: {errors}"
