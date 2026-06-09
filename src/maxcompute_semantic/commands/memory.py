"""mcs memory subcommand group -- structured memory CRUD + hybrid retrieval.

Write commands (verify, fail, note) create memory entries.
Read/manage commands (recall, list, show, remove, clear, reindex)
query and maintain the memory store.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click

from maxcompute_semantic._internal.paths import profile_data_dir
from maxcompute_semantic.auth.context import ProfileContext
from maxcompute_semantic.build.storage import PackageDB
from maxcompute_semantic.commands._profile_command import profile_command
from maxcompute_semantic.memory.errors import MemoryNotFoundError
from maxcompute_semantic.memory.hybrid import HybridSearcher
from maxcompute_semantic.memory.sql_pattern import redact_projection_columns
from maxcompute_semantic.versioning import ACTION_MEMORY_PREFIX


def _truncate(s: str, n: int) -> str:
    """Inline shortener for the question_text portion of the memory
    write-verb commit summaries. Spec at the plan's T8 table calls
    for an ``…``-ellipsed form when the text exceeds ``n`` chars;
    the verify / fail summaries feed into ``mcs profile log``
    output where excessively long questions would line-wrap.
    """
    return s if len(s) <= n else s[: n - 1] + "…"


def _redact_sample_sql_payload(payload: dict) -> dict:
    """Strip raw SELECT projection from non-user_verified ``sample_sql`` payloads.

    Mined SQL exposes access shape (FROM / WHERE / JOIN) the agent can
    reuse, but the SELECT list is question-specific — copying it
    verbatim answers the wrong question. The literal show / markdown
    surfaces already filter to user_verified-only; this helper keeps
    ``mcs memory recall`` and ``mcs memory show`` consistent so the
    agent can never trip the same wire from a different verb.
    """
    confidence = payload.get("confidence", "mined_low")
    if confidence == "user_verified":
        return payload
    redacted = dict(payload)
    for key in ("sql", "representative_sql", "canonical_sql"):
        value = redacted.get(key)
        if isinstance(value, str) and value:
            redacted[key] = redact_projection_columns(value)
    rep_sqls = redacted.get("representative_sqls")
    if isinstance(rep_sqls, list):
        redacted["representative_sqls"] = [
            redact_projection_columns(item) if isinstance(item, str) and item else item
            for item in rep_sqls
        ]
    return redacted


USER_MEMORY_KINDS = {"verified_query", "failed_query", "user_note"}
GENERATED_MEMORY_KINDS = {"package_doc", "sample_sql"}


# Error classification patterns for failed-query memory entries.
_ERROR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TABLE_NOT_FOUND", re.compile(r"table\s+.*not\s+found", re.I)),
    ("TABLE_NOT_FOUND", re.compile(r"ODPS-0420111", re.I)),
    ("COLUMN_NOT_FOUND", re.compile(r"column\s+.*not\s+found", re.I)),
    ("COLUMN_NOT_FOUND", re.compile(r"cannot\s+resolve\s+column", re.I)),
    ("SYNTAX_ERROR", re.compile(r"syntax\s+error", re.I)),
    ("SYNTAX_ERROR", re.compile(r"ODPS-0130161", re.I)),
    ("PARTITION_NOT_FOUND", re.compile(r"partition\s+not\s+found", re.I)),
    ("PARTITION_NOT_FOUND", re.compile(r"ODPS-0420061", re.I)),
    ("TYPE_MISMATCH", re.compile(r"type\s+mismatch", re.I)),
    ("TYPE_MISMATCH", re.compile(r"cannot\s+(?:implicitly\s+)?cast", re.I)),
    ("PERMISSION_DENIED", re.compile(r"permission\s+denied", re.I)),
    ("PERMISSION_DENIED", re.compile(r"ODPS-0420095", re.I)),
    ("FULL_SCAN_BLOCKED", re.compile(r"full\s+scan\s+not\s+allowed", re.I)),
    ("FULL_SCAN_BLOCKED", re.compile(r"ODPS-0421065", re.I)),
    ("CROSS_JOIN_ERROR", re.compile(r"cross\s+join\s+.*not\s+supported", re.I)),
]


def _classify_error(error_msg: str) -> str:
    """Auto-classify an error message into a stable code."""
    for code, pat in _ERROR_PATTERNS:
        if pat.search(error_msg or ""):
            return code
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Click group + write subcommands
# ---------------------------------------------------------------------------


@click.group(name="memory")
def memory_group() -> None:
    """Structured memory: write, recall, and manage memory entries."""


@profile_command(
    memory_group,
    "verify",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.option("--question", required=True, help="natural-language question")
@click.option("--sql", required=True, help="verified SQL statement")
@click.option(
    "--tables",
    default="",
    help="comma-separated table references; use FQN ``proj.schema.table`` "
    "for ambiguous bare names in multi-source profiles",
)
@click.option("--evidence", default="", help="optional free-form evidence hint")
def verify_cmd(
    pctx: ProfileContext,
    question: str,
    sql: str,
    tables: str,
    evidence: str,
) -> None:
    """Record a verified query into memory."""
    from maxcompute_semantic.commands._table_resolve import resolve_table_to_source

    raw_refs = [t.strip() for t in tables.split(",") if t.strip()]

    db = pctx.open_db()
    try:
        # Resolve every table ref to its (source_key, table_name) pair so
        # downstream recall is source-aware. Bare names that don't
        # disambiguate uniquely error out with a remediation hint.
        qualified = [resolve_table_to_source(raw, db, profile=pctx.profile) for raw in raw_refs]

        table_refs = [{"source_key": sk, "table": t} for sk, t in qualified]
        payload = {
            "question": question,
            "sql": sql,
            "table_refs": table_refs,
            "evidence_text": evidence,
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        # Prefix retrieval_text table tokens with their source_key so
        # BM25 retrieval can disambiguate between same-named tables in
        # different sources.
        tables_token = ",".join(f"{sk}:{t}" for sk, t in qualified)
        retrieval_text = f"Q: {question}\nSQL: {sql}\nTables: {tables_token}\nEvidence: {evidence}"

        id_ = db.upsert_memory("verified_query", payload_json, retrieval_text)

        from maxcompute_semantic.memory.sql_pattern import analyze_sql_pattern

        pattern = analyze_sql_pattern(sql)
        updated_sample_patterns = 0
        for sk, table_name in qualified:
            if db.mark_sample_sql_verified(sk, table_name, pattern.shape_key):
                updated_sample_patterns += 1
        pctx.success(
            {
                "id": id_,
                "kind": "verified_query",
                "profile": pctx.profile.name,
                "updated_sample_patterns": updated_sample_patterns,
            },
            commit_summary=f"verify {id_} ({_truncate(question, 40)!r})",
        )
    finally:
        db.close()


@profile_command(
    memory_group,
    "fail",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.option("--question", required=True, help="natural-language question")
@click.option("--sql", required=True, help="failed SQL attempt")
@click.option("--error-code", default="", help="classified error code (auto-detected if omitted)")
@click.option("--error-msg", default="", help="raw error message")
@click.option("--remediation", default="", help="suggested fix")
def fail_cmd(
    pctx: ProfileContext,
    question: str,
    sql: str,
    error_code: str,
    error_msg: str,
    remediation: str,
) -> None:
    """Record a failed query attempt into memory."""
    # Auto-classify error_code from error_msg if not provided
    if not error_code and error_msg:
        error_code = _classify_error(error_msg)
    elif not error_code:
        error_code = "UNKNOWN"

    payload = {
        "question": question,
        "sql_attempt": sql,
        "error_code": error_code,
        "error_msg": error_msg,
        "remediation": remediation,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    retrieval_text = (
        f"Q: {question}\nSQL: {sql}\nError: {error_code} -- {error_msg}\nFix: {remediation}"
    )

    db = pctx.open_db()
    try:
        id_ = db.upsert_memory("failed_query", payload_json, retrieval_text)
        pctx.success(
            {
                "id": id_,
                "kind": "failed_query",
                "error_code": error_code,
                "profile": pctx.profile.name,
            },
            commit_summary=f"fail {id_} ({_truncate(question, 40)!r})",
        )
    finally:
        db.close()


@profile_command(
    memory_group,
    "note",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.argument("text")
@click.option("--tags", default="", help="comma-separated tags")
def note_cmd(pctx: ProfileContext, text: str, tags: str) -> None:
    """Record a free-form user note into memory."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    tags_json = json.dumps(tag_list, ensure_ascii=False) if tag_list else None
    payload = {"text": text, "tags": tag_list}
    payload_json = json.dumps(payload, ensure_ascii=False)
    retrieval_text = text

    db = pctx.open_db()
    try:
        id_ = db.upsert_memory("user_note", payload_json, retrieval_text, tags_json=tags_json)
        pctx.success(
            {"id": id_, "kind": "user_note", "profile": pctx.profile.name},
            commit_summary=f"note {id_}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Read / manage subcommands
# ---------------------------------------------------------------------------


@profile_command(
    memory_group,
    "recall",
    accepts_schema=False,
)
@click.argument("query")
@click.option(
    "--kind",
    default="",
    help="comma-separated kind filter (e.g. verified_query,failed_query)",
)
@click.option("--top-K", default=5, type=int, help="number of results to return")
@click.option("--no-vector", is_flag=True, help="FTS5-only search (skip vector retrieval)")
def recall_cmd(
    pctx: ProfileContext,
    query: str,
    kind: str,
    top_k: int,
    no_vector: bool,
) -> None:
    """Hybrid retrieval: search memory entries by query text."""
    r = pctx.renderer
    kind_filter = [k.strip() for k in kind.split(",") if k.strip()] if kind else None

    db = pctx.open_db()
    try:
        searcher = HybridSearcher(db)
        results = searcher.search(query, kind_filter=kind_filter, top_k=top_k, no_vector=no_vector)

        for res in results:
            if res.get("kind") == "sample_sql":
                try:
                    payload = json.loads(res.get("payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    continue
                payload = _redact_sample_sql_payload(payload)
                res["payload_json"] = json.dumps(payload, ensure_ascii=False)

        if r.is_envelope:
            r.success({"results": results})
        elif r.quiet:
            for res in results:
                r.quiet_essential(res, "id")
        else:
            if not results:
                r.success({"count": 0, "results": []})
                return
            lines: list[str] = []
            for i, res in enumerate(results, 1):
                lines.append(
                    f"[{i}] score={res['score']} kind={res['kind']} "
                    f"(id={res['id']}, created {res.get('created_at', 'unknown')})"
                )
                if res["kind"] == "sample_sql":
                    try:
                        payload = json.loads(res.get("payload_json", "{}"))
                        table = payload.get("table", "?")
                        sql = payload.get("sql", "")
                        lines.append(f"    table={table}")
                        for sql_line in sql.splitlines():
                            lines.append(f"    {sql_line}")
                    except (json.JSONDecodeError, TypeError):
                        lines.append(f"    {res.get('retrieval_text', '')}")
                else:
                    snippet = res.get("retrieval_text", "")
                    lines.append(f"    {snippet.split(chr(10))[0]}")
            click.echo_via_pager("\n".join(lines))
    finally:
        db.close()


@profile_command(
    memory_group,
    "list",
    accepts_schema=False,
)
@click.option("--kind", default="", help="comma-separated kind filter")
@click.option("--limit", default=50, type=int, help="max entries to return")
def list_cmd(
    pctx: ProfileContext,
    kind: str,
    limit: int,
) -> None:
    """List memory entries."""
    r = pctx.renderer
    kind_filter = kind if kind.strip() else None

    db = pctx.open_db()
    try:
        entries = db.list_memories(kind=kind_filter, limit=limit)

        if r.is_envelope:
            r.success({"entries": entries})
        elif r.quiet:
            for entry in entries:
                r.quiet_essential(entry, "id")
        else:
            if not entries:
                click.echo("No memory entries found.")
                return
            for entry in entries:
                payload = json.loads(entry.get("payload_json", "{}"))
                question = payload.get("question", payload.get("text", ""))
                click.echo(
                    f"[id={entry['id']}] kind={entry['kind']} "
                    f"| created {entry.get('created_at', 'unknown')} "
                    f"| {question}"
                )
    finally:
        db.close()


@profile_command(
    memory_group,
    "show",
    accepts_schema=False,
)
@click.argument("id", type=int)
def show_cmd(
    pctx: ProfileContext,
    id: int,
) -> None:
    """Show a single memory entry in detail."""
    r = pctx.renderer

    db = pctx.open_db()
    try:
        entry = db.get_memory(id)
        if entry is None:
            raise MemoryNotFoundError(f"Memory entry {id} not found")

        if entry.get("kind") == "sample_sql":
            try:
                payload = json.loads(entry.get("payload_json", "{}"))
                redacted = _redact_sample_sql_payload(payload)
                entry["payload_json"] = json.dumps(redacted, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass

        if r.is_envelope:
            r.success({"entry": entry})
        elif r.quiet:
            r.quiet_essential(entry, "id")
        else:
            click.echo(f"ID:        {entry['id']}")
            click.echo(f"Kind:      {entry['kind']}")
            click.echo(f"Created:   {entry.get('created_at', 'unknown')}")
            payload = json.loads(entry.get("payload_json", "{}"))
            for key, value in payload.items():
                click.echo(f"{key}:      {value}")
            if entry.get("tags_json"):
                tags = json.loads(entry["tags_json"])
                click.echo(f"Tags:      {', '.join(tags)}")
            if entry.get("retrieval_text"):
                click.echo(f"Retrieval: {entry['retrieval_text']}")
    finally:
        db.close()


@profile_command(
    memory_group,
    "remove",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.argument("id", type=int)
def remove_cmd(
    pctx: ProfileContext,
    id: int,
) -> None:
    """Delete a memory entry."""
    db = pctx.open_db()
    try:
        result = db.remove_memory(id)
        if not result:
            raise MemoryNotFoundError(f"Memory entry {id} not found")

        pctx.success({"removed": id}, commit_summary=f"remove {id}")
    finally:
        db.close()


@profile_command(
    memory_group,
    "clear",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.option("--kind", default="", help="only clear entries of this kind")
@click.option("--before", default="", help="only clear entries created before this ISO date")
@click.option(
    "--include-generated",
    is_flag=True,
    help="also delete generated package_doc/sample_sql entries",
)
@click.option("--yes", "-y", is_flag=True, help="skip confirmation prompt")
def clear_cmd(
    pctx: ProfileContext,
    kind: str,
    before: str,
    include_generated: bool,
    yes: bool,
) -> None:
    """Bulk delete memory entries.

    By default only clears user-written entries (verified_query,
    failed_query, user_note). Pass ``--include-generated`` to also
    delete package_doc and sample_sql entries, or use ``--kind``
    to target a specific kind. Pass ``--yes`` / ``-y`` to skip the
    confirmation prompt — required for non-interactive callers.
    """
    kind_filter = kind if kind.strip() else None
    before_filter = before if before.strip() else None

    if not yes:
        if kind_filter:
            scope = f"kind={kind_filter!r}"
        elif include_generated:
            scope = "all entries (including generated)"
        else:
            scope = "user-written entries (verified_query, failed_query, user_note)"
        if before_filter:
            scope += f" created before {before_filter}"
        confirmed = click.confirm(
            f"clear {scope} from profile '{pctx.profile.name}'?",
            default=False,
        )
        if not confirmed:
            click.echo("aborted")
            return

    db = pctx.open_db()
    try:
        if kind_filter:
            count = db.clear_memories(kind=kind_filter, before=before_filter)
        elif include_generated:
            count = db.clear_memories(before=before_filter)
        else:
            count = 0
            for user_kind in sorted(USER_MEMORY_KINDS):
                count += db.clear_memories(kind=user_kind, before=before_filter)
        pctx.success(
            {"cleared": count, "include_generated": include_generated},
            commit_summary=f"clear ({count} entries)",
        )
    finally:
        db.close()


def run_reindex(db_path: Path, *, vectors: bool = True) -> tuple[int, int]:
    """Rebuild memory_fts (and optionally vec_index) on the package
    DB at ``db_path``.

    Lifted out of ``reindex_cmd``'s body so non-CLI callers — most
    notably the ``mcs profile reset`` rebuild flow in T13 — can
    repopulate the FTS / vec virtual tables after restoring a
    ``package.sql`` dump without going through the click runner.

    Returns ``(fts_rebuilt_count, vec_rebuilt_count)``. The vec
    component is ``-1`` when ``vectors=False`` was passed, or when
    the vec extension isn't available on the running interpreter
    (matches ``PackageDB.reindex_vectors``'s own
    not-installed signal).
    """
    db = PackageDB(db_path)
    try:
        fts_count = db.reindex_memory_fts()
        vec_count = -1
        if vectors:
            vec_count = db.reindex_vectors()
        return fts_count, vec_count
    finally:
        db.close()


@profile_command(
    memory_group,
    "reindex",
    action=ACTION_MEMORY_PREFIX,
    accepts_schema=False,
)
@click.option("--vectors", is_flag=True, help="also rebuild vector embeddings")
def reindex_cmd(
    pctx: ProfileContext,
    vectors: bool,
) -> None:
    """Rebuild the memory_fts index (and optionally vector embeddings) from all memory entries."""
    db_path = profile_data_dir(pctx.profile) / "package.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fts_count, vec_count = run_reindex(db_path, vectors=vectors)
    pctx.success(
        {"fts_reindexed": fts_count, "vectors_reindexed": vec_count},
        commit_summary=f"reindex ({fts_count}, {vec_count})",
    )
