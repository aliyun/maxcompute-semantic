# Memory — Query Experience Recall & Recording

> **Loaded on demand** — SKILL.md loads this when "record feedback" / "verify SQL" / "recall experience" intent detected. Memory recall is OPTIONAL — only use when uncertain about table/column patterns.

## Commands

All memory commands support `--profile X` (explicit profile override)
and `--project P` (target MaxCompute project). Neither flag
disambiguates source within a multi-source profile — `--profile` picks
which profile is active, `--project` selects the MaxCompute project to
operate against.

For source-within-profile disambiguation on `verify`, pass FQN
`project.schema.table` in `--tables` (the only disambiguation surface —
there is no `--source` flag). `fail`, `note`, `recall`, `list`, `show`,
`remove`, `clear`, and `reindex` operate on the whole memory store
regardless of source.

| Command | Use |
| --- | --- |
| `mcs memory verify --question Q --sql '<SQL>' --tables T1,T2 [--evidence E]` | Record a verified (successful) SQL with source-aware table refs (use FQN `project.schema.table` for ambiguous bare names in multi-source profiles). `--evidence` is an optional free-form hint that accompanied the question. |
| `mcs memory fail --question Q --sql '<SQL>' [--error-code C] [--error-msg M] [--remediation R]` | Record a failed SQL + error classification. `--error-code` auto-detects from `--error-msg` when omitted. |
| `mcs memory note '<TEXT>' [--tags a,b]` | Write a free-form domain knowledge note. `TEXT` is positional, not `--text`. |
| `mcs memory recall '<keyword>' [--kind verified_query,failed_query] [--top-K 10] [--no-vector]` | Hybrid FTS5 + optional vector search, top-K results (default 5). `--kind` filters by entry kind; `--no-vector` skips the embedding lookup for FTS5-only search. |
| `mcs memory list` | List all entries |
| `mcs memory show <id>` | Show single entry detail |
| `mcs memory remove <id>` | Delete an entry |
| `mcs memory clear [--kind K] [--before ISO_DATE]` | Clear user-written entries only (verified_query, failed_query, user_note). `--kind` targets one kind; `--before` only deletes entries created before a given ISO date. |
| `mcs memory clear --include-generated` | Also delete generated entries (package_doc, sample_sql). |
| `mcs memory reindex [--vectors]` | Rebuild FTS5 index; add `--vectors` to rebuild vector embeddings (requires `maxcompute-semantic[vec]`). |

## Write Timing

1. **Query succeeds and user confirms correct** → `mcs memory verify --tables ...`
2. **Query fails** → `mcs memory fail` (include error code and fix)
3. **User shares domain knowledge** → `mcs memory note`

For verified queries, always include `--tables`. In multi-source
profiles, pass FQN refs (`project.schema.table`) when bare table names
are ambiguous. Do not record a query as verified just because it
executed; wait for user confirmation that the result answers the
question.

## Recall Timing (OPTIONAL)

Use `mcs memory recall "<keyword>"` when uncertain about:
- Which table/columns match a concept
- Known error patterns for a query shape
- Domain constraints the user previously mentioned

Do NOT recall before every query — only when you need hints.

## Retention / Generated Entries

- `mcs memory verify`, `fail`, and `note` append user-written entries.
- `mcs build` rebuilds generated `package_doc` entries from schema/UDF metadata.
- When history mining is enabled, `mcs build` rebuilds generated `sample_sql` pattern entries per source.
- `sample_sql` entries are mined from successful historical SQL tasks. They are useful hints, not proof that the query answers a user's current question.
- `sample_sql.frequency` counts mined queries with the same normalized SQL shape, so `WHERE id = 10` and `WHERE id = 20` group together.
- `sample_sql.verified_count` counts matching `mcs memory verify` entries. Only this field represents strict user confirmation.
- `sample_sql.confidence` is one of `user_verified`, `mined_high`, `mined_medium`, or `mined_low`.
- `mcs memory clear` without `--kind` clears only user-written entries. Pass `--include-generated` or an explicit generated `--kind` to delete `package_doc` / `sample_sql`.

## Package Docs

`mcs build` auto-generates `package_doc` entries from schema/UDF metadata.
`mcs memory recall` can retrieve these (no manual writing needed).

## Bulk Import: Generate Questions From SQL

When the user has a backlog of raw SQL queries (a `.sql` file, a query log,
a notebook export) and wants them seeded into memory, the agent — not the
`mcs` CLI — generates the natural-language questions, then writes each
entry via `mcs memory verify`.

Why agent-side: `mcs` has no LLM dependency. The reverse-generation step is
LLM work; keeping it out of the CLI keeps `mcs` deployable in environments
where only MaxCompute credentials exist.

### Workflow

1. **Read the SQL backlog.** One SQL per logical query; multi-statement
   files split on `;` boundaries first.

2. **For each SQL, generate 1–3 paraphrase questions.** Aim for at least
   one literal-style ("show me X grouped by Y") and one casual-style
   ("how's X doing this month") per SQL. The paraphrase diversity is what
   makes BM25 retrieval robust to phrasing drift at recall time.

3. **For each (sql, question) pair, record:**

   ```bash
   mcs memory verify --question '<paraphrase>' --sql '<SQL>' --tables <T1,T2>
   ```

   The `--tables` list comes from parsing the SQL's FROM/JOIN clauses; if
   a profile is multi-source, use FQN form (`project.schema.table`).

4. **Spot-check.** Run `mcs memory recall '<one of the paraphrases>'` and
   confirm the just-imported entry surfaces in the top-3.

### Failure modes

- **Same SQL, three near-identical paraphrases** — wastes index space and
  inflates recall numbers without measuring real robustness. Reject and
  regenerate with explicit style diversity.
- **Paraphrase contains a literal that's not in the SQL** — the entry
  will mis-route at recall time. Drop the entry.
- **SQL references a table not in the current profile** — record anyway
  if the user confirms; FTS5 indexes the text regardless of profile
  membership.