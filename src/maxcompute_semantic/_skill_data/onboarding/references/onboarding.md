# Onboarding — Install & Configure

> **Loaded on demand** — SKILL.md loads this when auth/setup/onboard intent detected, or when an auth error occurs.

## Install CLI

```bash
pip install maxcompute-semantic
# or: uv pip install maxcompute-semantic
```

Verify: `mcs --version`

## Configure Auth

### Option A: Environment variables (CI / transient)

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=<your_ak_id>
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=<your_ak_secret>
export MAXCOMPUTE_ENDPOINT=https://service.<region>.maxcompute.aliyun.com/api
export MAXCOMPUTE_PROJECT=<your_project>
```

`mcs sql execute` automatically falls back to env vars when no profile exists.

### Option B: Persistent profile (recommended)

Two paths share the underlying CLI; pick whichever fits the caller:

**TTY users — interactive wizard:**

```bash
mcs profile create
```

Walks 8 steps: alias → credential discovery → endpoint → auth method → credentials → advanced → sources → submit & test.

**Agents — Agent Wizard Flow:**

When the caller has no TTY (every agent in `claude --print` mode, every CI runner, every scripted bootstrap), follow the 8-step flow below. Each step maps 1-1 to the TTY wizard's same-numbered step but uses agent-callable verbs and conversational prompts in place of `iterfzf` pickers.

### Agent Wizard Flow

The agent collects answers step-by-step and assembles them into a single `mcs profile create --from-spec` call at Step 7. Spec template comes from `mcs profile spec-template`.

#### Step 1 — Profile name (alias)

Ask the user. Validate the name is not already in `mcs profile list`. Populate spec field `name`.

#### Step 1.5 — Credential discovery & reuse

```bash
mcs -f json profile suggest-creds --exclude-name <NAME>
```

Returns `{"existing_mcs": [...], "external": [...]}`. Secrets are never serialized in this envelope.

- `existing_mcs[]` entries: `name`, `auth_kind` (`ak` / `ncs` / `process`), `endpoint`, `compute_project`, `sources_count`, `display`.
- `external[]` entries: `source` (`maxc` / `odpscmd`), `path`, `auth_kind`, `endpoint`, `compute_project`, `display`.

If both arrays are empty → continue to Step 2.

If non-empty → present the candidates to the user and ask whether to reuse one or configure manually:

- **Reuse an existing mcs profile** (entry from `existing_mcs[]`): run `mcs profile show <picked-name> --format yaml` to fetch a round-trippable spec. The output is wrapped in the standard `{status, data: {...}}` envelope — extract the inner `data` object before passing to `--from-spec` (e.g. parse the JSON/YAML and pull `data`, or `yq .data`). Env-var refs (`${env:VAR}`) pass through verbatim; literal AKs are redacted to `***REDACTED***`. The agent **MUST** prompt the user for fresh AK values and replace every `***REDACTED***` placeholder before submission — the loader does not reject the placeholder string, but a profile with `***REDACTED***` will fail at the post-create `SELECT 1` with an opaque ODPS auth error. Edit the spec (rename, replace placeholders, etc.) and skip to Step 6.
- **Reuse an external config** (entry from `external[]`): run `mcs profile import-creds --source <source> --config-path <path> --alias <NAME> --no-test` (existing verb). If the entry has `auth_kind=process`, this first run may stop and print the non-ncs `ProcessAuth` command it found; show that exact command to the user, ask whether they trust the helper, and rerun with `--trust-process-command` only after explicit approval. The canonical `auth_kind=ncs` helper is the standard known process-auth helper. Then skip to Step 6 to add sources via `mcs profile update` afterwards.
- **Skip** → continue to Step 2.

#### Step 2 — Endpoint

```bash
mcs -f json profile endpoint-presets
```

Returns `{"public_region_template": "https://service.<region>.maxcompute.aliyun.com/api", "common_regions": [...], "internal": [{"label": ..., "url": ...}]}`.

Ask the user: **Public cloud, internal (Alibaba intranet), or custom URL?**

- **Public**: ask for the region (suggest from `common_regions`); synthesize URL by substituting into `public_region_template`. Populate spec field `endpoint`.
- **Internal**: present the `internal[]` list as a numbered choice. Use the picked entry's `url`. Populate spec field `endpoint`.
- **Custom**: ask for the URL verbatim. Populate spec field `endpoint`.

#### Step 3 — Auth method

Default mapping (mirrors the TTY wizard):

| Endpoint kind | Branch label | Spec `auth.type` |
|---------------|--------------|------------------|
| internal | `ncs` | `process` |
| custom | `process` | `process` |
| public | `ak` | `ak` |

Surface the default; let the user override. The "branch label" picks which Step 4 sub-flow to follow; the "Spec `auth.type`" value is what gets written into the spec at submit time. Valid `auth.type` values are only `ak` and `process` — `ncs` is a *classifier label* (used in `suggest-creds` envelopes), never a spec value.

#### Step 4 — Credentials

**For `ak`:** Ask env-var-reference vs literal:

- **Env-var (recommended)**: collect AK_ID / AK_SECRET env-var names. Spec fields: `auth.type = "ak"`, `auth.access_key_id = "${env:<NAME>}"`, `auth.access_key_secret = "${env:<NAME>}"`.
- **Literal**: collect the values directly. Spec fields: `auth.type = "ak"`, `auth.access_key_id` / `auth.access_key_secret` set to the literals.

**For `ncs`:**

```bash
mcs -f json profile list-ncs-identities
```

Returns `{"available": true|false, "identities": [...], "reason": "..."}`.

- `available=true`: present the `identities[]` list (label format `"{buc_account_name} ({buc_user_type})"`); user picks one. Build the spec block: `auth.type = "process"`, `auth.command = "ncs create credential odpsuser --buc-user-id <picked.buc_user_id> -o template -t odpscmd"` (and optionally `auth.timeout`).
- `available=false`: the `ncs` CLI isn't installed. `ncs` is an internal credential helper (see <https://authx.io.alibaba-inc.com>). If not available, suggest AK auth instead. If the user has access to ncs, ask for `employee_id` and build the spec block: `auth.type = "process"`, `auth.command = "ncs create credential odpsuser --employee-id <ID> -o template -t odpscmd"` (and optionally `auth.timeout`). The wizard prints the same hint when it detects the missing binary.

**For `process`:** Ask for the command (must return STS AssumeRole JSON on stdout) + timeout (default 60s). Spec fields: `auth.type = "process"`, `auth.command`, `auth.timeout` (default 60s, range 1–600).

#### Step 4.5 — Compute project

The default MaxCompute project the agent runs SQL against (separate from the per-source projects in Step 6).

The profile doesn't exist on disk yet, so the in-wizard helpers can't auto-discover this — **ask the user directly** for the home compute project name. Populate spec field `compute_project`.

If the user has the standard ODPS env-var quartet (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` / `MAXCOMPUTE_ENDPOINT` / `MAXCOMPUTE_PROJECT`) already exported and wants to enumerate first, the agent can call `mcs -f json meta list-projects` (env-var fallback path — no `--profile` flag), but this is optional; asking the user is the simpler and more reliable path.

The reuse path in Step 1.5 inherits `compute_project` from the picked profile and skips this step.

##### Profile design — dev vs prod

In standard-mode DataWorks workspaces the typical shape is:

- **`compute_project`** = the **dev** project (often `*_dev`) — where SQL execution lands and the AK has read+write. Treat it as a write sandbox.
- **`sources[].project`** = the **prod** project (the same name without `_dev`) — where the real business data lives. Personal AKs typically have read-only access here.

**Don't add dev as a second source.** `mcs build` runs join inference across all sources as one semantic space — adding both `acme_dev` and `acme` produces phantom cross-environment relationships (`dev.fact_orders.user_id ↔ prod.dim_user.id`) that fail at execution and pollute the package. Configure exactly one source — usually prod — and let `compute_project` be dev.

`mcs sql {execute,cost,explain}` parses the SQL and auto-routes the default project to the source that owns the referenced bare names — so a single-source dev/prod profile reads from prod without anyone passing `--project`. Cross-source SQL keeps `compute_project` as the default and lets the engine route each FQN. Pass `--project <other>` only to override.

#### Step 5 — Advanced (optional)

Only if the user mentions cost gating or tagging:

- `cost_thresholds.confirm_cny` (default 10.0)
- `cost_thresholds.blocked_cny` (default 100.0)
- `tags` (list of strings)

#### Step 6a — Scenario (the package's purpose)

Before picking tables, ask the user — **in detail** — what this profile is
for: the business questions they expect to answer, the domain, the kinds of
analysis, and the metrics / time grain they care about. Encourage specifics
(domain entities, key metrics, time grain); a vague answer makes the
recommendation in Step 6b weak.

Record the answer into spec field `description`. This becomes the semantic
package's description (shown in `mcs profile show`, written into the package
`_overview.md` by `mcs build`), and it drives the table recommendation below.

#### Step 6b — Sources (scenario-driven table recommendation)

For each source the user wants:

```bash
mcs -f json meta list-projects                              # if needed
mcs -f json meta list-schemas --project <P>                 # if 3-level
mcs -f json meta list-tables --project <P> [--schema <S>]   # name list only
```

`list-tables` returns table **names** only (comments are not included — they
cost a per-table round-trip). Recommend candidates like this:

1. **Rank the names against the scenario `description`** from Step 6a using
   your own judgment. Produce a shortlist of the tables most likely relevant.
2. **Enrich the shortlist as needed (your call).** If the shortlist is small
   and/or the names are opaque (`t_001`, `dwd_xxx_df`), run
   `mcs -f json meta describe-table --project <P> [--schema <S>] <TABLE>` on
   the shortlist to read each table's comment and key columns and tighten the
   recommendation. If the names are already self-explanatory or the shortlist
   is large, present names with a one-line rationale and describe on demand.
   When `describe-table` returns an empty comment, fall back to the column
   names as the relevance signal — many tables have no comment.
3. **Present the candidates to the user with brief reasons**, and explicitly
   invite them to add or remove tables. Offer to widen ("show more names") or
   narrow.
4. Finalize the user's selection as an enumerated list and populate
   `sources[].project`, `sources[].schema`, `sources[].tables`.

**MUST NOT** guess table names from prior knowledge or naming conventions —
recommend only from the live `list-tables` result. **MUST NOT** pass
`tables: []` (CLI rejects with `InvalidProfile`). Use `tables: "*"` only when
the user explicitly confirms wholesale and the project is small.

**If you lack list permission:** when `list-projects` / `list-schemas` /
`list-tables` returns a permission error, you cannot enumerate — fall back to
asking the user for an explicit table list. Step 6a (scenario capture) still
applies; `description` is independent of catalog permission.

#### Step 7 — Submit & test

```bash
mcs profile create --from-spec '<JSON>'
```

The CLI auto-runs `SELECT 1` against `compute_project`. If it fails, surface the error to the user and ask whether to save anyway (pass `--no-test` only when explicitly approved).

#### Step 8 — Identity confirm

```bash
mcs profile whoami <NAME>            # live RAM principal probe
mcs link bind <NAME>                 # if user wants this cwd bound
```

### Non-Interactive Submission

When all answers are pre-determined (eval harnesses, IaC templates), skip the Agent Wizard Flow and submit a complete spec directly:

```bash
mcs profile create --from-file @profile.yaml
mcs profile create --from-spec '{"name": "...", ...}'   # inline JSON also accepted
```

For AK auth, prefer `${env:ALIBABA_CLOUD_ACCESS_KEY_ID}` and `${env:ALIBABA_CLOUD_ACCESS_KEY_SECRET}` references, or ProcessAuth/ncs on internal endpoints. If a script must create a literal-AK profile, pass `--ak-literal --ak-id ...` and pipe exactly one secret line with `--ak-secret-stdin`; avoid `--ak-secret` unless you intentionally accept shell-history exposure.

Run `mcs profile spec-template` for a fillable yaml template.

### Identity check

```bash
mcs profile whoami NAME             # live ODPS whoami probe → RAM principal
mcs profile whoami                  # same, for the active profile (no flags — takes a bare positional NAME)
mcs -q profile whoami               # quiet mode: prints just the identity string for shell pipelining
mcs profile show NAME               # static config (no network call)
mcs profile show NAME --format yaml # round-trippable spec (also accepts json / plain)
```

Live probe, never cached. To verify end-to-end auth, run any real
command (`mcs sql execute "select 1" --profile NAME`).

### Health check / diagnosis

```bash
mcs doctor             # profile, auth, connectivity, tier, package, skill install
mcs doctor --offline   # local config/package/skill checks only
mcs -f json doctor     # agent-friendly envelope
```

Read-only diagnostic. Run when profile / binding / auth / package /
skill-install state is unclear. Online mode adds `SELECT 1` + tier
probes; `--offline` skips network.

## Profile management

The CLI verbs that manage saved profiles on disk:

```bash
mcs profile list                              # all configured profiles, one per row
mcs profile show <name>                       # render one profile's static config (secrets redacted)
mcs profile show <name> --format yaml         # round-trippable spec (also accepts json / plain)
mcs profile show                              # same, but for the active profile per the chain below
mcs profile whoami <name>                     # live ODPS-side identity probe (the RAM principal)
mcs profile whoami                            # same, for the active profile
mcs -q profile whoami                         # quiet form — bare identity string for shell pipelining
mcs profile update <name>                     # interactive editor for a profile's full spec
mcs profile update <name> --from-file @<p>    # non-interactive full-replace (yaml or json)
mcs profile remove <name> --yes [--purge]     # delete the on-disk entry; add --purge to also wipe the per-profile data dir (idempotent: removing a nonexistent name exits 0)
mcs profile import-creds [--source maxc|odpscmd|auto] [--config-path PATH] [--alias NAME] [--no-test] [--trust-process-command]
                                              # bootstrap from existing odpscmd / maxc-cli configs (auto = scan both default locations)
mcs profile spec-template                     # print the fillable yaml template the --from-file form consumes
mcs profile export <name> [--export-name <new-name>] -o <bundle>   # bundle a profile for transfer; --export-name rewrites the bundle's internal name
mcs profile import <bundle> [--name <local-name>] [--package-path <dir>]  # unpack a bundle; --name overrides on collision, --package-path picks the data dir
```

Active-profile resolution order (highest priority first):

1. `--profile NAME` flag
2. `MCS_PROFILE` env var (per-shell)
3. cwd-link binding via `mcs link bind <NAME>` (per-directory, persists across shells)
4. ODPS env-var quartet (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `_SECRET` / `MAXCOMPUTE_ENDPOINT` / `MAXCOMPUTE_PROJECT`) — wrapped into an unnamed in-memory Profile

```bash
mcs link bind <name>          # bind cwd to a profile
mcs link status               # show current binding
mcs link status -v            # also dump the bound profile's source details
mcs link unlink               # clear binding
export MCS_PROFILE=<name>     # per-shell override
```

### Editing a profile

`mcs profile update <name>` is the single edit verb (interactive picker
or `--from-file @spec.yaml` for full-replace PUT). See
[`profile-editor.md`](profile-editor.md) for the editor workflow and
the agent GET-mutate-PUT pattern.

## Manage Skill Installation

```bash
# Install
mcs skill install                # local, default platform (agents)
mcs skill install -g             # global, default platform (agents)
mcs skill install --all          # install to every agent platform
mcs skill install -p claude-code # specific platform
mcs skill list                   # show all platforms + install status

# Inspect
mcs skill path                   # print where the skill is (or would be) installed
mcs skill diff                   # verify the install symlink points at the current package _skill/

# Update / remove
mcs skill update                 # re-link to the current package (run after a package upgrade)
mcs skill uninstall              # remove the deployed symlink (errors if not installed)
```

## Profile Data Location

`<XDG_DATA_HOME>/maxcompute-semantic/data/<profile_name>/`

`<XDG_DATA_HOME>` follows the XDG Base Directory spec: it defaults to
`~/.local/share` on Linux and `~/Library/Application Support` on
macOS, so on most machines the resolved path is
`~/.local/share/maxcompute-semantic/data/<profile_name>/` (Linux) or
`~/Library/Application Support/maxcompute-semantic/data/<profile_name>/`
(macOS). Override with:

- `MCS_DATA_DIR` — data root (default `<XDG_DATA_HOME>/maxcompute-semantic/data`)
- `MCS_PROFILES_DIR` — overrides the inner `data/` segment specifically (historical name from when the dir was called `profiles/`)
- `MCS_CONFIG_DIR` — config root for `profiles.yaml` and `link.json` (default `~/.config/maxcompute-semantic`, always XDG-config-home not XDG-data-home)
