# Profile version history (git-backed)

Every profile's data directory is a per-profile git repository. Every
write command (`mcs build`, `mcs package apply/reject`, `mcs memory *`,
`mcs udf *`, `mcs profile import`) ends with an auto-commit hook that
snapshots the resulting state. Eight verbs surface the resulting
history, branching, and rollback flows.

## Read-side verbs

| Verb | What it does |
|---|---|
| `mcs profile log [--profile X] [-n N] [--grep <regex>]` | List recent commits on the profile's repo (newest first). One commit per write command (or per `recover: …` rollup of pre-existing changes). `--grep` filters subject lines by POSIX-extended regex (same syntax as `git log --grep --extended-regexp`); useful when the history outgrows the `-n` window. |
| `mcs profile log-show <ref> [--profile X]` | Show one commit's metadata, message, and per-file diff stat. `<ref>` is a short SHA, a full SHA, `HEAD`, `HEAD~N`, or any rev-parseable name. |
| `mcs profile diff <ref-a> <ref-b> [--profile X]` | Diff two commits' tracked files (annotation markdown, joins, package.sql dump). |
| `mcs profile show <name>` | Trailer adds `📜 Version  <short-sha> (<subject>)` on main; `🌿 Forks  <name>, …` when forks exist; `🌿 Parent  <name> @ <sha>` on fork. JSON envelope adds `version` / `forks` (main) or `parent` / `anchor` (fork). |

## Mutation-side verbs

| Verb | What it does |
|---|---|
| `mcs profile reset --to <ref> [--profile X] [--yes]` | Roll the profile back to `<ref>`. Restores `package.sql` (the git-tracked twin of `package.db`) into the live DB via reindex, then `git reset --hard <ref>` on the working tree. Use this when a bad `mcs package apply` damaged the semantic layer. |
| `mcs profile fork <new-name> --from <ref> [--profile X]` | Create a `kind=fork` profile whose data dir is a `git worktree --detach` on the parent at `<ref>`. The fork is read-only (every write verb refuses with a "fork-kind profile is read-only" remediation). Use forks for A/B comparison of two profile versions against the same NL2SQL eval set. |
| `mcs profile fork-list [--profile X]` | Enumerate every fork of the resolved profile (or every fork in `profiles.yaml` when no profile is resolved). Self-heals ghost forks (yaml row points at a worktree dir that was hand-deleted — sweeps the row via `git worktree prune` + `unregister_fork`) on every invocation. |
| `mcs profile fork-remove <fork> [--force] [--yes]` | Tear down a `kind=fork` profile: `git worktree remove <path>` first (which also sweeps the parent's `.git/worktrees/<short>/` admin entry), then `unregister_fork(name)` drops the yaml row. `--force` passes through to `git worktree remove --force` for dirty-worktree cases. Ghost-fork and double-orphan (parent yaml gone too) self-heal arms land on exit-zero rails. |
| `mcs profile enable-versioning [--profile X] [--yes]` | Inaugurate the git repo on a profile that pre-dates the per-profile versioning layer (no `.git/` under its data dir). Idempotent — re-running on an already-versioned profile no-ops with an exit-zero "already versioned" banner. |

`mcs profile remove <name>` is fork-aware: on a `kind=main` profile
with live forks it refuses with a remediation pointing at
`mcs profile fork-remove`; on a `kind=fork` profile it delegates
through the same worktree-remove + unregister path as
`mcs profile fork-remove`.

## Opting out: `MCS_NO_VERSIONING`

The auto-init + auto-commit layer is gated by the `MCS_NO_VERSIONING`
env var. Truthy values (case-insensitive: `1`, `true`, `yes`, `on`)
short-circuit both `profile create`'s repo-init step and every write
command's commit hook. The writes themselves still happen; only the
commit is suppressed. This belongs to the same eval-mode-opt-out family
as `MCS_NO_HISTORY` (which suppresses the build miner's history-mining
phase) — the two env vars are typically set together when running the
NL2SQL benchmark, since both leak prior-run information into the
agent's view of the profile.

The canonical contract for the env knob's precedence and semantics
lives in the spec at
`docs/superpowers/specs/2026-05-23-mcs-profile-git-versioning-design.md`
under "MCS_NO_VERSIONING semantics".

## Worked example: rollback after a bad proposal batch

```bash
# An agent applies proposals and corrupts the semantic layer.
mcs package apply 10 --profile prod
mcs package apply 11 --profile prod
mcs package apply 12 --profile prod

# Inspect history.
mcs profile log --profile prod -n 5

# Diff HEAD against the previous commit to confirm the regression.
mcs profile diff HEAD~1 HEAD --profile prod

# Roll back to before the bad batch.
mcs profile reset --to HEAD~1 --profile prod --yes
```

## Worked example: A/B fork against the same eval set

```bash
# Fork the profile at the current HEAD.
mcs profile fork prod@baseline --from HEAD --profile prod

# Run the enrichment experiment on the main profile.
mcs package propose --from-stdin --profile prod <<'EOF'
tables:
  - table: orders
    ai_context: "Each row is one customer order event."
EOF
mcs package apply 1 --profile prod

# Now run the eval against both arms — the fork still sees the
# pre-experiment annotations; the main profile sees the new ones.
mcs profile fork-list --profile prod   # confirm the fork is healthy
```
