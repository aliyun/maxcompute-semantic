# S63 validation record

Requirement: [Aone 87614020](https://project.aone.alibaba-inc.com/coco/projects/2155299/workitems/87614020).
Source base: `d09554d8df1dfe9c06ded046451cdb99fc81dce3`.
Implementation branch: `feat/mcs-semantic-knowledge-s63`.

## Environment

Work and checks run in the isolated local worktree. The preferred remote host
rejected the current operator's execution permission. Existing user checkouts,
shared Gateway processes, deployments and business SemanticSpecs were preserved.

## Behavior covered by automated tests

- Standard MCP HTTP discovery and calls, native OAuth discovery/DCR/PKCE,
  loopback callbacks, token expiry and refresh across client processes, and
  sanitization of public errors. Ordinary reads never initiate browser login.
- Complete native section reads, identity/version/hash/size checks, immutable
  Published reads, mixed Draft revision rejection, three-way conflict handling,
  approved-plan checking, full-section CAS updates, and exact publish readback.
  Failed or uncertain operations do not silently advance synchronization state.
- Independent Markdown/structured knowledge, stable identities, source version
  and evidence spans, proposal review with revision/before-value checks,
  rejection/retraction, stale evidence, consistent graph reads, and bounded
  paths/dependency impact.
- Package and SemanticSpec import, dependency-closed projection, unmapped
  content, candidate/confirmed boundaries, physical relationship bindings,
  metric mappings, and preservation of unrelated native fields.
  MEASURE expressions use the existing parser for finite scalar AST checks;
  unknown function behavior and business/type correctness remain explicit gaps.
  Identical source reimports preserve local review, and complete native source
  snapshots survive graph round trips. Dependency checks cover the whole graph.
- Registered CLI staging/export/push/publish flow with an external Gateway
  double; JSON/YAML errors; preservation of source packages and active SQLite
  databases when exporting artifacts.
- Historical SQL literal/quoted-identifier identity and parser-derived physical
  references. Unparseable history remains unresolved rather than gaining a
  guessed table attribution.

## Local checks

Final Python 3.10.22 complete non-live suite: **3850 passed, 25 skipped**, with
**91%** total coverage and **94%** changed-line coverage, including untracked
files. The unchanged skips cover known grammar fallback cases, the Windows
self-replace case and optional vector dependencies. The earlier Python 3.12.15
complete suite also passed before the final review fixes.
Ruff, mypy (145 source files), lock consistency and diff whitespace checks pass.

Clean wheel installations using only declared runtime dependencies pass on
Python **3.10.22** and **3.14.8**. Both exercise CLI imports/help, local knowledge
import/search/projection/check, remote configuration and the bundled knowledge
and remote skills. No business backend or model is contacted by these smokes.

Reproduce the core local gate from this repository:

```bash
uv sync --locked --extra dev --python 3.10
uv run pytest tests/ -m 'not live' -n 8 --dist loadscope --maxfail=1 -q \
  --cov=maxcompute_semantic --cov-report=xml --cov-fail-under=90
uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=80
uv run ruff check src/ tests/
uv run mypy src/
uv lock --check
git diff --check
```

## Cross-SDK interoperability

Go MCP SDK v1.7 and Python MCP SDK 2.3 synthetic JSON/SSE cases exercise actual
wire transport. Seventeen protocol cases and six native-section cases pass.
They exposed a real field-order issue: Gateway hashes native Go JSON, while
structured content may reorder object members. The client checks both channels
for matching values/types/array order and retains JSON text order. Contradictory
channels fail, including JSON boolean versus integer values.

The real Gateway path can reorder both channels. Synchronization therefore
also restores the pinned Catalog DTO declaration order without changing values
or dropping fields, and still requires the declared size and SHA-256 to match.
The checked-in synthetic Go golden covers nested types and unknown map members:
`tests/unit/remote/catalog-sdk-native-json.generate.go` records the source blob.

These checks establish protocol behavior, not acceptance of real business
definitions. Local reproduction evidence is in
`/tmp/mcs-s63-go-python-interop/`; no real bearer values enter those artifacts.

## OAuth credential concurrency

A local synthetic authorization server exposed an actual cache race: two
successful refreshes completing in reverse order could overwrite the latest
stored refresh token. With strict single-use refresh, the losing caller instead
returned `RemoteAuthRequired`; it did not delete the other caller's successful
cache. Evidence is recorded in
`/tmp/mcs-s63-refresh-concurrency-audit.results.json`, using only synthetic
credentials. This does not establish Aliyun's production reuse behavior or
production credential loss.

The client now uses a bounded private per-alias credential lock around fresh
cache reads, SDK refresh and persistence. Valid cached credentials release the
lock before the original MCP request; business tool calls are not serialized
by this lock. Contention times out within ten seconds as `RemoteAuthBusy`, and
failure or cancellation releases the owned lock. Repeated 401 authorization and
403 scope escalation reopen the registered loopback callback safely. Malformed
MCP responses and OAuth errors cannot echo credential values through SDK logs.

## Live acceptance and remaining boundaries

Read-only MCP initialization, capability discovery, health and SemanticSpec
listing passed through the existing local Gateway with real Aliyun OAuth and
caller-bound OBO. The complete read-only chain passed: bind, revision-pinned
seven-section pull (five present, two absent), verified native snapshot, graph
import/check/export/reimport, source-field preservation, and remote diff.
The imported graph contains 101 nodes, 75 unique edges and 118 evidence records;
nine identical duplicate relation payloads were safely coalesced. Graph checks
passed with only informational isolated-node findings. The seven-section diff
has no conflicts. Remote mutations were **zero**; temporary native/SQLite
artifacts were deleted and no business content or credentials entered the
repository. Sanitized evidence is checked in at
`docs/superpowers/plans/2026-10-13-s63-live-read.evidence.json`.

That local profile reaches production MaxCompute FE/Catalog; it establishes
real authenticated read behavior, not isolated test-backend write acceptance.
The Shanghai `dev`/`pre` entries in the Gateway repository's
`test/mcp/test-env/.mcp.json` target the shared test cluster. Its operations
documentation and local OAuth configuration route `dev`/`pre` to the test
FE/Catalog backend. No currently usable OAuth session for that test target has
been established in this validation run. Endpoint-specific issuer/audience
binding prevents substituting the existing production-backed login.

The reproducible native login entry for a scheduled test session is:

```bash
mcs remote add s63-test-dev \
  --mcp-url https://mcp-pre-dev.cn-shanghai.maxcompute.aliyun-inc.com/mcp \
  --region cn-shanghai
mcs remote login s63-test-dev
```

Complete that flow as the authorized test account, then verify the resolved
caller/namespace and the target's SemanticSpec tools before selecting an asset.
Use a dedicated test-only SemanticSpec and an existing reviewed USER_DRAFT,
independent of business objects. After that setup, exercise the complete
bind/pull/local-edit/diff/approved-plan push/read-back/publication lifecycle and
its permission, stale-revision and uncertain-outcome cases. Discovery of a
server or possession of an OAuth registration alone does not satisfy this
gate. The isolated object's readiness and those real write checks remain open;
no test object, login or credential was created during this read-only audit.

Real Draft mutation/publication, a new native OAuth first login, and the fixed
business-question comparison remain acceptance work. Synthetic write tests and
coverage do not substitute for those checks. No production publication or
deployment has been performed.

First Draft initialization is blocked by the client because that server path
has no atomic object-ID guard. Existing Draft updates use server revision CAS
and client identity checks; they do not claim an independent atomic spec-ID
condition. Projection validates its mapping/evidence structure; publication
still requires the business definitions and native SQL to be reviewed.
