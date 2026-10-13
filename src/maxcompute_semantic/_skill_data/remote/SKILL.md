---
name: remote
description: Connect MCP with OAuth and read, compare, stage, update or publish shared SemanticSpec revisions independently of MaxCompute profiles.
---

# Shared SemanticSpec workflow

Use an existing SemanticSpec and the user's supplied gateway URL, Region and
name. Keep the MCP connection separate from a MaxCompute AK profile. Read
`references/remote-sync.md` before changing local sections or a remote draft.

1. Configure and authenticate the connection:
   ```bash
   mcs remote add team --mcp-url "$MCP_URL" --region "$MCP_REGION"
   mcs remote login team
   ```
   Done: login reports `authenticated: true` and tools are discoverable.
   If the user already provides an authorized bearer environment variable,
   configure `--token-env VARIABLE_NAME` instead. Keep its value out of output.
2. Bind a dedicated workspace and read a complete source:
   ```bash
   mcs package bind-remote team --name sales --workspace .mcs-remote
   mcs package pull --source PUBLISHED --workspace .mcs-remote
   ```
   Done: the binding carries endpoint, namespace, Region and object ID; the
   selected sections share the returned revision and verified content hashes.
3. Inspect or export the verified base:
   ```bash
   mcs package export-semantic --output sales.snapshot.json --workspace .mcs-remote
   ```
   Done: the snapshot has one source revision and native section content.
   For local document/relation analysis, load `mcs skill get knowledge` and
   ingest that snapshot. Export keeps unpublished local edits separate.
4. When an update is authorized, stage reviewed native JSON with
   `package edit-section`, or a complete knowledge report with
   `package stage-projection --file REPORT`. Inspect the complete result:
   ```bash
   mcs -f json package diff --workspace .mcs-remote
   mcs -f json package push --dry-run --workspace .mcs-remote
   ```
   Done: `ready` is true, `conflicts` is empty, and the exact `changes`,
   `writes` and `plan_hash` have been reviewed. Staging has `remote_written: false`.
   A missing USER_DRAFT is blocked: bootstrap it on the server, then reread.
5. Apply only the authorized current plan:
   ```bash
   mcs package push --apply --plan-hash 'sha256:REVIEWED_HASH' --workspace .mcs-remote
   ```
   Done: the returned draft revision has matching complete read-back;
   `published` remains false. Changed identity, revision or plan ends this step.
6. Publish only when the user has explicitly authorized that revision. Pull
   and review all seven USER_DRAFT sections, then:
   ```bash
   mcs package pull --source USER_DRAFT --workspace .mcs-remote
   mcs package publish --revision 'REVIEWED_DRAFT_REVISION' --confirm --workspace .mcs-remote
   ```
   Done: `published: true`, `published_revision_id` and `source_revision_id`
   establish the read-back result. An unknown outcome requires inspection;
   never turn it into a success claim or retry publication automatically.

Reading or analyzing semantics authorizes neither SQL execution nor remote
writes. Run build, draft writes, publication and model calls only in their
explicitly authorized workflows. This workflow itself performs no model calls.
