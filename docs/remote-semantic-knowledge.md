# Shared SemanticSpec and local knowledge

`mcs` can read shared SemanticSpec through MCP and maintain a separate local
evidence graph. The remote workspace holds native semantic JSON; the knowledge
database holds documents, definitions, recorded relations and their sources.
An explicit projection connects the two.

| Work | Local artifact | Authentication |
| --- | --- | --- |
| Query/build an existing MaxCompute profile | Profile semantic package | Profile's MaxCompute auth |
| Read/update SemanticSpec through MCP | `.mcs-remote/state.json` workspace | Separate MCP OAuth or bearer environment reference |
| Search documents and analyze relations | `.mcs/knowledge.db` | None |
| Import an existing package into knowledge | Same knowledge database | Read local profile/package metadata only |

These workflows make no model calls and do not execute SQL. Run a build or SQL
execution only as its own authorized workflow. Local metric expressions are
preserved as content; this bridge does not compile them into server metrics.

## Read a shared definition

Use an existing SemanticSpec, its exact name, and the gateway URL and Region
provided by its owner. Set `MCP_URL` and `MCP_REGION` to those non-secret values.

```bash
mcs remote add team --mcp-url "$MCP_URL" --region "$MCP_REGION"
mcs remote login team
mcs package bind-remote team --name sales --workspace .mcs-remote
mcs package pull --source PUBLISHED --workspace .mcs-remote
mcs package export-semantic --output sales.snapshot.json --workspace .mcs-remote
mcs knowledge ingest-semantic sales.snapshot.json
```

| Stage | Done when | Next step |
| --- | --- | --- |
| Connection | `remote login` returns `authenticated: true` and discovered tools | Bind the exact object |
| Binding | URL, namespace, Region and SemanticSpec object ID are recorded | Pull complete sections |
| Pull | All selected bodies and integrity metadata share the returned revision | Inspect or export their verified base |
| Export | Snapshot has one source/revision and complete identity metadata | Import that file into knowledge |
| Knowledge import | KB revision/source evidence are recorded | Search and inspect the definitions |

`remote login --no-browser` prints the OAuth authorization URL for manual
opening. If an authorized bearer is already supplied in an environment
variable, use `remote add ... --token-env VARIABLE_NAME` instead. The value is
not a CLI argument or connection-file field. MCP auth is independent of the AK
profile used by existing MaxCompute query/build commands.

Manual browser login still uses the registered loopback callback. The browser
must reach the CLI host's callback port; a CLI running over SSH or in a remote
workspace needs that connection forwarded. `--no-browser` does not change the
flow to device-code authentication.

Pull defaults to all seven section types: dataReferences, semanticModel,
instructions, metricDefinitions, verifiedQueries, glossary and benchmarks.
Repeat `--section` for a narrower read. `--revision REVISION` pins an immutable
PUBLISHED revision. Missing bodies, truncation, mixed revisions or inconsistent
hash/size metadata fail the read. Export writes the verified remote **base**;
unpublished local edits remain separate.

## Acquire and review local knowledge

The KB works without a MaxCompute profile. `--kb FILE` selects another local
database on either the group or an individual command.

```bash
mcs knowledge init
mcs knowledge ingest ./knowledge --scope sales
mcs -f json knowledge search "active customer"
mcs -f json knowledge list --kind term --status candidate
mcs -f json knowledge show NODE_ID
```

Ingest reads UTF-8 Markdown and safe YAML frontmatter. Ordinary text is
searchable; structured nodes use `mcs.nodes` and relations use `mcs.relations`.
Links become candidate mentions. It does not use a model to infer an ontology.
Read [the declaration and evidence format](../src/maxcompute_semantic/_skill_data/knowledge/references/knowledge-model.md)
before adding structured declarations.

To import an already built local package:

```bash
mcs knowledge ingest-package --profile existing-profile
```

This reads local metadata and query memory without resolving credentials or
building a missing package. Imported joins remain candidates regardless of
their inference confidence. Remote PUBLISHED imports confirm the identity of
published definitions; USER_DRAFT and SYSTEM_SUGGESTIONS remain candidates.
Published source evidence establishes where a definition came from, rather
than independently proving its business meaning.

Review a candidate by reading its node, cited evidence and source version, then
prepare a complete node or edge patch preserving its identity and references:

```bash
mcs knowledge propose patch.json --expected-revision KB_REVISION
mcs knowledge show PROPOSAL_ID
mcs knowledge apply PROPOSAL_ID --reviewed-by REVIEWER
```

Done means the reviewed proposal applied against its captured revision and
prior value. A conflict needs a fresh read and new review. `knowledge reject`
retains a rejected proposal; `knowledge retract RELATION_ID --reason TEXT`
withdraws an assertion while retaining its history. Source changes can mark
dependent items stale; those items need current evidence before projection.

## Analyze relationships

```bash
mcs graph neighbors NODE_ID --status confirmed --status candidate
mcs graph path START_ID END_ID --max-depth 4 --limit 100
mcs graph impact NODE_ID --max-depth 4
mcs graph check
```

Neighbors and paths retain each relation's state and evidence. Impact follows
confirmed dependency relations in reverse to find dependents. Inspect the
returned bound/truncation indicators; a bounded result is complete only for
the stated scope. `graph check` returns issues and a blocking `ok` result;
isolated nodes are informational. A path is a recorded association, not a
proof of another fact or of causal influence.

`knowledge export graph.json` exports KB identity, revisions, sources,
evidence and review history. `knowledge import graph.json` restores a complete
graph into an empty KB or merges an explicit fragment. Export is local; check
the file's documents and private content before sharing it with another party.

## Stage selected knowledge for a shared draft

Choose IDs from `knowledge list/show` after review:

```bash
mcs knowledge project --selected NODE_ID --selected ANOTHER_ID --output projection.json
mcs package stage-projection --file projection.json --workspace .mcs-remote
mcs -f json package diff --workspace .mcs-remote
```

The projection report names the original KB revision, selected IDs, dependency
closure, mapping manifest, unmapped items, validation issues and native
sections. `publishable: true` means that structural/evidence checks and the
finite MEASURE scalar-expression checks passed. The existing MaxCompute parser
requires one scalar expression and rejects aggregates, windows, subqueries,
query clauses and multiple statements. An unclassified function remains an
explicit validation issue until its scalar behavior is established by an owner
or server workflow. Raw expressions remain unchanged.
`sql_validation` lists validated measure IDs and unchecked areas: physical
columns/types, business semantics, runtime function behavior, CALCULATED_METRIC
formula grammar, named-filter expressions and verified-query SQL. The flag is
neither complete SQL validation, server acceptance nor authorization to write.
Stage-projection requires that flag and empty issue/unmapped lists, and merges
the sections locally into a workspace whose complete target sections have
already been pulled. Its result has `remote_written: false`.

Glossary terms map their definition and aliases directly. Table-level entities
require an explicit physical baseTable and logical binding. Metrics and joins
require server-native typed bindings; a local `SUM(amount)` is not enough to
infer aggregation, grain or a table. General business edges and private
extensions stay unmapped. Local verified SQL does not become server-trusted
SQL automatically. See [the projection contract](../src/maxcompute_semantic/_skill_data/knowledge/references/projection.md).

For hand-authored section JSON, `package edit-section glossary --file FILE`
merges entries by native key. `--remove KEY` expresses an explicit removal;
`--replace` intentionally replaces a whole local section, including deletions.
Unrelated entries and native fields survive normal merging. Section-level
server writes are always complete replacements, so their complete diff matters.

## Apply and publish separately

```bash
mcs -f json package push --dry-run --workspace .mcs-remote
```

Review `changes`, `writes`, `conflicts`, `ready` and `plan_hash`. A successful
preview only means a plan was produced. After the exact plan is authorized:

```bash
mcs package push --apply --plan-hash 'sha256:REVIEWED_HASH' --workspace .mcs-remote
```

Apply recomputes and checks the plan and current draft revision, writes complete
sections, and checks identity, returned revision and complete read-back before
advancing local state. The result includes `revision_id`, `remote_written` and
`published: false`. A changed plan, identity or revision requires another read
and review.

**First-draft updates are currently blocked.** The server API lacks an atomic
object-ID guard for that write path. Bootstrap and review a USER_DRAFT through
the server workflow first, then pull it. The CLI does not create an object or
initialize its first draft as a synchronization shortcut.

Publication is a separate authorized action:

```bash
mcs package pull --source USER_DRAFT --workspace .mcs-remote
mcs package publish --revision 'REVIEWED_DRAFT_REVISION' --confirm --workspace .mcs-remote
```

Pull and review all seven sections at the same exact USER_DRAFT revision before
publication, including sections recorded as absent. Local edits must already
be synchronized. Publication completion requires `published: true`, an exact
`published_revision_id`, matching `source_revision_id`, and complete content
read-back. An uncertain outcome calls for inspection, not an automatic publish
retry. Full write and recovery details are in
[the remote contract](../src/maxcompute_semantic/_skill_data/remote/references/remote-sync.md).

Agent workflows are discoverable through `mcs skill catalog`,
`mcs skill get knowledge --full`, and `mcs skill get remote --full`.
