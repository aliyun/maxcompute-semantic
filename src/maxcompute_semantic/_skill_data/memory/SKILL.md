---
name: memory
description: Use when recording, recalling, listing, showing, removing, clearing, or running reindex for verified/failed SQL and domain notes.
---

# mcs memory workflow

Use memory after a query succeeds or fails, or when past verified SQL can
help answer a new question.

## Commands

```bash
mcs memory recall "keyword"
mcs memory verify --question Q --sql '<SQL>' --tables T1,T2
mcs memory fail --question Q --sql '<SQL>' --error-msg '<msg>' --remediation '<fix>'
mcs memory note 'domain note'
mcs memory list
mcs memory show ID
mcs memory remove ID
mcs memory reindex
```

For multi-source profiles, pass FQN table names in `--tables` when a bare
table name is ambiguous.

See `references/memory.md`.
