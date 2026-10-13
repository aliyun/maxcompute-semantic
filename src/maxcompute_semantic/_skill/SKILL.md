---
name: maxcompute-semantic
description: 'Use for MaxCompute SQL, schema and local semantic packages, query memory, UDFs, local document knowledge and evidence relations, or shared SemanticSpec through MCP with the mcs CLI.'
---

# maxcompute-semantic

This installed file is a discovery stub. Before using `mcs` for real work,
load the runtime workflow that matches the task:

| Intent | Load |
| --- | --- |
| Answer a data question / write SQL / inspect schema for a query | `mcs skill get query` |
| Query when `mcs show` reports no semantic package | `mcs skill get query` |
| Build / refresh / onboard a semantic package | `mcs skill get build` |
| Review / apply annotation suggestions, semantic-package proposals, or enrich semantics | `mcs skill get enrich` |
| Create, link, edit, or diagnose profiles | `mcs skill get onboarding` |
| Record or recall verified / failed SQL | `mcs skill get memory` |
| Ingest local documents / review knowledge relations / trace dependencies or impact / project selected knowledge | `mcs skill get knowledge` |
| Connect MCP with OAuth / pull shared SemanticSpec / review, update or publish its exact revision | `mcs skill get remote` |
| Create or manage MaxCompute UDFs | `mcs skill get udf` |
| File an upstream bug / issue against this skill | `mcs skill get report-issue` |

## Query-flow build ban

When answering a data question, producing SQL, inspecting schema for a query,
or recovering from a query error, never run `mcs build` or `mcs package propose`.
If `mcs show` or `mcs status` reports no build data / no semantic package,
load `mcs skill get query` and use its cold-start workflow with live
`mcs meta` commands. `mcs sql review` still runs syntax / dialect checks
without a package; use those issues, but expect semantic hints and coverage
to be skipped.

Run `mcs build` only when the user explicitly asks to build, refresh,
onboard, or maintain a semantic profile.

## Runtime skill commands

```bash
mcs skill catalog          # list available runtime workflows
mcs skill get query        # load the query workflow
mcs skill get query --full # include references
```

The knowledge workflow operates locally without MaxCompute authentication.
The remote workflow has its own MCP authentication and revision-bound writes.
