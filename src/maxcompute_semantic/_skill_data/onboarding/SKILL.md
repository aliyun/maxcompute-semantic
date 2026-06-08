---
name: onboarding
description: Use when creating, linking, editing, importing/exporting, diagnosing, or version-recovering MaxCompute semantic profiles.
---

# mcs onboarding and profile workflow

Use this workflow for setup and profile maintenance, not for answering a
single data question.

## Common commands

```bash
mcs -f json doctor
mcs profile create
mcs link bind PROFILE
mcs profile show PROFILE --format json
mcs profile update PROFILE --from-spec '<json>'
mcs profile export PROFILE
mcs profile import PATH
```

For profile history and recovery:

```bash
mcs profile log --profile PROFILE
mcs profile log-show REF --profile PROFILE
mcs profile diff REF_A REF_B --profile PROFILE
mcs profile reset --to REF --profile PROFILE
mcs profile fork FORK_NAME --from REF --profile PROFILE
```

See `references/onboarding.md`, `references/profile-editor.md`, and
`references/profile-history.md`.
