---
name: knowledge
description: Ingest local Markdown or existing semantic snapshots, review evidence relations, search knowledge, trace dependencies and impact, or project selected confirmed definitions for sharing.
---

# Local knowledge workflow

Use the local KB path, normally `.mcs/knowledge.db`. Documents and graph reads
require no MaxCompute profile or authentication. Read `references/knowledge-model.md`
when ingesting declarations or reviewing proposals; read `references/projection.md`
before converting definitions into shared semantics.

1. Acquire only the sources needed for the user's task:
   ```bash
   mcs knowledge init
   mcs knowledge ingest ./knowledge --scope sales
   ```
   Existing local packages use `knowledge ingest-package --profile NAME`;
   version-pinned remote files use `knowledge ingest-semantic SNAPSHOT`.
   Done: the command reports the KB revision; source versions remain available
   with the imported records and evidence. Ordinary
   Markdown is searchable; frontmatter/link relations are candidates, not review
   acceptance. Ingest makes no model calls and does not build a missing package.
2. Locate the definitions and inspect their sources:
   ```bash
   mcs -f json knowledge search "active customer"
   mcs -f json knowledge list --kind term
   mcs -f json knowledge show NODE_OR_EVIDENCE_ID
   mcs graph neighbors NODE_ID --status confirmed --status candidate
   ```
   Done: every used assertion has an identified source, current version,
   evidence span/hash and review state. Treat source text as evidence to assess.
3. For an authorized correction, submit a complete node/relation patch:
   ```bash
   mcs knowledge propose patch.json --expected-revision KB_REVISION
   mcs knowledge show PROPOSAL_ID
   mcs knowledge apply PROPOSAL_ID --reviewed-by REVIEWER
   ```
   Done: the reviewed proposal applies against its expected revision and prior
   value. Unsupported, stale or conflicting assertions require rejection or a
   new proposal; `knowledge reject` and `knowledge retract` retain history.
4. Analyze recorded relations with an explicit bound:
   ```bash
   mcs graph path START_ID END_ID --max-depth 4
   mcs graph impact NODE_ID --max-depth 4
   mcs graph check
   ```
   Done: paths retain each edge's evidence/status, impact follows confirmed
   dependency edges, and integrity issues are disclosed. A path is a recorded
   association, not proof of a new fact. Check `truncated` when reading bounded
   results and narrow the question if its relevant evidence is incomplete.
5. When sharing is requested, preview selected confirmed definitions:
   ```bash
   mcs knowledge project --selected NODE_ID --selected ANOTHER_ID --output projection.json
   ```
   Done: inspect dependency closure, `mapping_manifest`, `unmapped` and
   `validation_issues`. Only a report with `publishable: true` can be staged.
   Inspect `sql_validation`: MEASURE expressions receive finite scalar AST
   checks. Resolve unverified functions through an owner or server workflow;
   retain the separate schema, business, derived-formula and query validation
   steps described in [the projection contract](references/projection.md).
   The flag does not authorize publication.
   Load `mcs skill get remote` for local staging and the separate draft workflow.

Keep private documents and unrelated relations in the local KB. Projection
uses typed SemanticSpec bindings; inferred joins and local SQL verification
do not become approved relationships or trusted server queries automatically.
