# S63 implementation: remote SemanticSpec and local evidence graph

Requirement: Aone 87614020. Base: d09554d. Worktree:
`/data/jiexian.hc/projects/maxcompute-semantic-s63-20261013`.

Development and checks run locally. The preferred remote host rejected the
current operator's command execution permission; no remote state was changed.

## Public interfaces and invariants

The agreed acceptance surface is the CLI and exported services called by that
CLI. Tests exercise commands, local files, a standard MCP server, and observable
remote side effects. No real credentials are needed for hermetic tests.

- `remote`: independent connection configuration, native MCP OAuth, optional
  environment bearer reference, standard SDK transport, bounded calls and safe
  errors. Credentials never enter package or graph exports.
- `package`: remote binding, complete section snapshots, base/local/remote
  comparison, approved-plan-bound Draft CAS, independent exact-revision publish
  and verified readback. Failures never silently advance the sync base.
- `knowledge`: local documents, source revisions, typed nodes/claims/edges and
  evidence, proposal review, import/export and scoped retrieval. No MaxCompute
  profile is required for local knowledge.
- `graph`: bounded neighbors/path/impact queries and consistency checks.
  Candidate extraction is distinct from confirmed facts and stale evidence.
- Projection: preserve native SemanticSpec data, project selected confirmed
  knowledge with dependencies, and report unmapped content. Generic business
  relations cannot become executable joins without an explicit physical binding.
- Historical SQL: exact identity preserves literal and quoted identifier
  semantics; unparseable history cannot become a physical attribution via text
  matching. Existing runtime parser machinery owns SQL semantics.

## Vertical slices

1. Connection/configuration and local standard-MCP read handshake.
2. Bind/pull/diff and identity/complete-body validation.
3. Approved Draft push, conflicts, uncertain outcomes and exact publish readback.
4. Markdown/structured graph lifecycle and evidence-grounded analysis.
5. Package/SemanticSpec graph import and selected semantic projection.
6. CLI registration, workflow skill, user docs and package/install regression.

## Verification

Each slice starts with a failing public behavior test. Run focused tests after
each slice, then all non-live tests, coverage, ruff, mypy, lock consistency and
wheel-only install smoke. Exercise JSON and YAML output, permission errors,
conflicts, missing/oversized evidence, stale versions and uncertain publishing.

Real MCP acceptance uses the documented OAuth/caller-OBO path. Catalog trust
token checks, if used, are recorded separately and never called an OAuth pass.
No production deployment or automatic knowledge publication is part of coding.
