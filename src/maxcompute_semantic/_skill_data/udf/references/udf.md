# UDF reference — create / test / remove

> **Loaded on demand** — SKILL.md loads this when UDF intent detected.

## Important rule

UDF write operations (create / remove) **must be confirmed by the user first**.
These are destructive operations and must not run silently.

All UDF commands support `--project P` (target MaxCompute project) and `--profile X` (explicit profile override).

## Command quick reference

### Read side

| Command | Purpose |
| --- | --- |
| `mcs udf list [--project P] [--profile X]` | List all UDFs |
| `mcs udf show <name> [--project P] [--profile X]` | Show one UDF's details |
| `mcs udf search <keyword> [--project P] [--profile X]` | Keyword search across UDFs |

### Write side (requires user confirmation)

| Command | Purpose |
| --- | --- |
| `mcs udf create <name> --inline-python script.py [--description "…"]` | Create an inline Python UDF (currently the only supported mode; for jar/resource-attach UDFs use `mcs sql execute --allow-write 'CREATE FUNCTION …'`) |
| `mcs udf test <name> --args '1, "abc"'` | Test a UDF (via SELECT call); args must be SQL literals only |
| `mcs udf remove <name> [--delete-resources]` | Remove a UDF + optionally its associated resources |

### Resource management

| Command | Purpose |
| --- | --- |
| `mcs udf resource list [--project P] [--profile X]` | List all resources |
| `mcs udf resource show <name> [--project P] [--profile X]` | Show resource details |
| `mcs udf resource remove <name> [--project P] [--profile X]` | Remove a resource |

## Inline Python mode

`mcs udf create` currently **only supports** `--inline-python` (MC 3.x native;
no separate resource-package upload required). The script file defines a single
Python class, and the entry point defaults to the file base name. For UDFs that
need a jar or external resource, go through the SQL channel directly with
`mcs sql execute --allow-write 'CREATE FUNCTION …'` (one of the rare legitimate
uses of `--allow-write` — the agent must still confirm the write with the user
first per the rule above, and the cost gate still applies).

## Testing

`mcs udf test` constructs `SELECT <name>(args)` and runs it via `mcs sql execute`.
`--args` is deliberately strict: only numeric literals, `NULL`, `TRUE`/`FALSE`,
single-quoted strings with doubled apostrophes, and double-quoted strings are
accepted. Bare identifiers, expressions, function calls, semicolons, empty
arguments, and unterminated quotes are rejected before SQL construction.
Returns the result row(s) plus execution status.

## Removal

`mcs udf remove` removes only the function definition by default.
`--delete-resources` also removes the associated resource files.
