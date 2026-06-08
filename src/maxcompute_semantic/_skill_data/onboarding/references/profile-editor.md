# Profile editor (`mcs profile update`)

Single verb for editing any profile field — `compute_project`,
`endpoint`, `auth`, `cost_thresholds`, `tags`, `sources`. Two paths:

- **Interactive (humans)**: drop into a multi-level file-browser-style
  picker. Drill into a section, edit, ↩ Back, ✓ Save and exit at top
  to commit, ✗ Cancel to discard.
- **Non-interactive (agents / scripts)**: full-replace via a
  complete-profile YAML or JSON spec. Read the current state with
  `mcs profile show <name> --format json`, mutate locally, write back
  with `mcs profile update <name> --from-spec '<json>'`.

## Interactive path (humans only)

```bash
mcs profile update <name>
```

Opens a multi-level picker — top-level lists `compute_project`,
`endpoint`, `auth`, `cost_thresholds`, `tags`, `sources` plus `✓ Save`
/ `✗ Cancel`. Picking a section drills into a sub-prompt. Sources
goes one level deeper — list current sources, "+ Add new source" (full
project → schema → tables → columns drill-down via questionary), pick
an existing source to drill into table-level edits.

Auth-test runs at top-level Save **only when the auth section was
edited**. Field changes in cost thresholds / tags / sources commit
without re-testing auth.

Cancel at top-level returns the original profile unchanged. Cancel from
a sub-section (Back) goes up one level without losing the other section
edits.

## Non-interactive path (agents / scripts)

```bash
# GET current profile state as round-trippable JSON
mcs profile show <name> --format json > profile.json

# Mutate profile.json locally (edit tags, add a source, etc.)

# PUT back via update --from-spec
mcs profile update <name> --from-spec "$(cat profile.json)"
# or
mcs profile update <name> --from-file @profile.json
```

The JSON shape matches the on-disk `profiles.yaml` per-profile block
plus a top-level `name` field. The same loader powers both
`update --from-spec` and the on-disk yaml deserializer, so any field
the yaml accepts is valid in `--from-spec`.

### Auth secret handling

The `show --format json` output redacts AK secrets that are stored as
**literal values** to `***REDACTED***`. Env-var references
(`${env:VAR}`) pass through unchanged — they're not secrets, just
references the resolver expands at use time.

`update --from-spec` honors the `***REDACTED***` marker: when the spec's
`auth.access_key_id` or `auth.access_key_secret` is the literal
`***REDACTED***`, the loader substitutes the existing profile's stored
value. This makes the GET-mutate-PUT loop work without the agent ever
seeing the secret.

`create --from-spec` does **not** accept the marker — there's no
existing profile to substitute from. Use real credentials or env-refs
when creating a new profile.

### Spec shape

```yaml
name: myprofile          # required, must equal the PROFILE arg
compute_project: acme
endpoint: https://service.cn-shanghai.maxcompute.aliyun.com/api
auth:
  type: ak               # or "process"
  access_key_id: ${env:MY_AK_ID}
  access_key_secret: ${env:MY_AK_SECRET}
cost_thresholds:
  confirm_cny: 10.0
  blocked_cny: 100.0
tags: [team-a]
sources:
  - project: acme
    schema: s1
    tables:
      - name: orders
        columns_exclude: [password, ssn]
      - name: users        # bare-string for unscoped tables
  - project: prod
    schema: analytics
    tables: '*'
```

`tables` accepts either:
- the wildcard string `'*'` — picks up all tables in the schema,
  including future ones (locked once enumerated below);
- a list mixing bare strings (unscoped) and `{name, columns_exclude}` /
  `{name, columns}` mappings for column-scoped tables.

`columns_exclude` is a blacklist — agent doesn't see those columns in
the package metadata. `columns` is a whitelist — agent sees only those.
The two are mutually exclusive per table; pick one or neither.

> **Column scope is an agent-VIEW filter, not access control**.
> Server-side data access is gated by MaxCompute's table-level GRANT
> and (optionally) LabelSecurity for per-column ACL. The picker /
> spec only controls what the agent sees in the local profile metadata.

## Discovery before adding sources (agents)

When adding sources to an existing profile, drive table selection the same way
as onboarding Step 6b (scenario-driven recommendation):

1. Check the profile's existing `description` (`mcs profile show <name>
   --format json` → `.data.description`). If set, reuse it as the scenario; if
   empty, ask the user for the scenario and include `description` in the PUT.
2. `mcs -f json meta list-tables --project <P> [--schema <S>]` returns names
   only. Rank them against the scenario, **excluding tables already present in
   the profile**, and recommend additions.
3. Enrich the shortlist with `mcs -f json meta describe-table ... <TABLE>` only
   when names are opaque or the shortlist is small (your call); fall back to
   column names when a table's comment is empty.
4. Present candidates with reasons; let the user adjust; append the chosen
   tables to the spec's `sources` list and PUT back.

If you lack list permission, fall back to asking the user for explicit table
names. The scenario / `description` capture still applies.

To pick a project + schema before adding a source, the agent uses these
list commands and its platform's ask-user mechanism:

```bash
# 1. Enumerate projects accessible by the AK
mcs meta list-projects                # JSON: {projects: [...]}

# 2. Enumerate schemas in a project
mcs meta list-schemas --project <P>   # JSON: {schemas: [...]}

# 3. Enumerate tables in a schema
mcs meta list-tables --project <P> --schema <S>

# 4. Describe a table's columns
mcs meta describe-table --project <P> --schema <S> <table>
```

These four LIST commands provide discovery **hints**, not prerequisites.
Some AKs have SELECT on objects they can't enumerate (the GRANT layer is
separate from the catalog LIST layer). If any discovery step fails with a
permission error, write the table or column names directly into the spec
JSON — the spec loader doesn't validate names against the catalog. Same
escape hatch the human interactive flow offers (manual table/column name
entry via `📝 Add table by name` / `<other:>` sentinel).

After drilling, the agent assembles a complete-profile spec locally and
PUTs it back via `mcs profile update <name> --from-spec '<json>'`.

## Common patterns

### Add one source

```bash
# Read current state
spec="$(mcs profile show myprofile --format json | jq '.data')"

# Build a new source dict
new_source='{"project":"acme","schema":"orders","tables":"*"}'

# Append to the spec's sources list and PUT back
echo "$spec" | jq --argjson s "$new_source" '.sources += [$s]' \
  | mcs profile update myprofile --from-file /dev/stdin
```

### Remove a source

```bash
# Filter out the (project, schema) pair you want gone
mcs profile show myprofile --format json | jq '.data | .sources |= map(select(.project != "old_proj" or .schema != "old_schema"))' \
  | mcs profile update myprofile --from-file /dev/stdin
```

### Edit cost thresholds

```bash
mcs profile show myprofile --format json | jq '.data | .cost_thresholds.blocked_cny = 200' \
  | mcs profile update myprofile --from-file /dev/stdin
```

> **Stdin form**: pipe with `--from-file /dev/stdin`. The `--from-spec -`
> form looks like a Unix stdin idiom but is treated as a literal yaml token
> by the loader and errors out (`spec must be a yaml mapping (got list)`).
> Use `--from-spec` only for genuinely inline string specs; for piping the
> output of another command, always go through `--from-file /dev/stdin`.

> **PUT semantics**: every `--from-spec` call replaces the entire
> profile with the given spec. To make incremental changes, GET first,
> mutate locally, PUT the result. The spec must include all unchanged
> fields too — omitting a field doesn't preserve it; it deletes it
> (or resets to default for fields with defaults).

> **Batch multiple mutations into one GET-mutate-PUT**: when adding a
> source AND changing cost thresholds AND editing tags, do one
> `mcs profile show`, apply all three jq filters in sequence, then one
> `mcs profile update`. Three serial GET-mutate-PUT round-trips wastes
> two reads and risks lost-update if anything else writes the profile
> between calls.
