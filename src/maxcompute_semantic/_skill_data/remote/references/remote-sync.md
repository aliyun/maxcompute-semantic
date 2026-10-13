# Remote source and write contracts

Connection commands are `remote add/list/show/login/remove`. Add requires
`--mcp-url` and `--region`; `--token-env` names an environment variable rather
than supplying its value. Login uses native MCP OAuth; `--no-browser` prints
the URL for manual opening. Neither route reads a MaxCompute AK profile.

Manual login retains the loopback callback: the browser must reach the CLI
host's registered callback port. Forward that connection when the CLI runs in
a remote workspace. `--no-browser` does not select device-code authentication.

Credentials are stored separately with private file permissions. A per-alias
lock covers re-reading the current cache, SDK refresh and persistence, and
waits at most ten seconds. It is released before the original MCP request, so
it does not serialize business tool calls. `RemoteAuthBusy` is a contention
result, not successful authentication.

After contention or a failed refresh, start a fresh connection so it reloads
the current cache, then use a read-only probe to check authentication and the
bound identity. Another caller may already have refreshed successfully. If
authentication still fails, report it and request explicit `remote login`;
ordinary calls do not open a browser. Do not infer that tokens were revoked or
declare success from a cache file alone. A write with an uncertain outcome
still requires remote read-back before any retry.

A workspace binds to remote alias/URL, namespace, routing Region, exact name
and `semantic_spec_id`. Reusing a name for another object or account is an
identity change. Use a separate workspace for a different source.

## Reading

`package pull` defaults to PUBLISHED and all seven complete sections. Repeat
`--section` to select a smaller read; `--revision` selects an immutable
PUBLISHED revision. Draft and suggestion reads must remain at one revision
throughout the read. Missing bodies, truncation and size/hash mismatches are
errors. Divergent local edits survive a failed pull.

`package export-semantic --output FILE` exports the verified remote **base**,
not locally edited sections. It requires one source/revision across the
tracked sections, and includes the identity needed by `knowledge ingest-semantic`.
Using stdout in JSON mode adds the normal command envelope; `--output` writes
the raw snapshot directly.

## Local staging

The native sections are `dataReferences`, `semanticModel`, `instructions`,
`metricDefinitions`, `verifiedQueries`, `glossary` and `benchmarks`.

```bash
mcs package edit-section glossary --file glossary.json --workspace .mcs-remote
mcs package stage-projection --file projection.json --workspace .mcs-remote
```

Pull each complete target section first. Normal edits merge entries by their
native key: glossary `term`, metric/query `name`, and each semanticModel
member's `name`. Unrelated entries and native fields remain. `--remove KEY`
is an explicit removal; `--replace` intentionally replaces the whole local
section, including deletions. Direct dataReferences edits require `--replace`
and 1–20 references. Stage-projection atomically merges a report with
`publishable: true`, no `unmapped`, and no `validation_issues`; it performs no
remote writes and does not establish business correctness or SQL validity.

## Updating an existing draft

Diff compares base, local and current USER_DRAFT. Review every changed section,
including removals and unrelated entries, before approving its `plan_hash`.
Push defaults to preview. `--apply --plan-hash HASH` recomputes the plan,
checks its identity/revision and hash, then writes complete sections with
`expected_draft_revision_id`. Only matching identity, returned revision and
complete read-back advance local base state. Push never publishes.

First-draft writes return `SemanticFirstDraftUnsupported`: the server API has
no atomic object-ID guard for that path. Create and review the draft using the
server workflow, then pull it before applying local changes. CAS conflicts
require a fresh read and another review, not an automatic retry with old input.

## Publishing

Pull USER_DRAFT without `--section` so all seven sections, including explicit
absence snapshots, are recorded at one exact revision. Review their content;
local staged changes must already be synchronized. Publish requires that
revision plus `--confirm` and reads back the publication's source revision and
every section. An uncertain transport outcome is inspected, not blindly retried.

`mcs -f json ...` emits the standard status/data or status/error envelope.
Preview success means a plan was produced: check `ready`, `conflicts` and
`blocked_reason` before considering a write. A successful backend response is
also distinct from caller review and claim-level factual acceptance.
