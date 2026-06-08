---
name: udf
description: Use when listing, showing, searching, creating, testing, or removing MaxCompute UDFs and UDF resources.
---

# mcs UDF workflow

Use this workflow for MaxCompute UDF lifecycle work.

## Commands

```bash
mcs udf list
mcs udf search KEYWORD
mcs udf show NAME
mcs udf create NAME --inline-python script.py
mcs udf test NAME --args '1, "abc"'
mcs udf remove NAME
mcs udf resource list
mcs udf resource show NAME
mcs udf resource remove NAME
```

`mcs udf test --args` accepts SQL literals only: numeric values, `NULL`,
`TRUE`/`FALSE`, and quoted strings. Do not pass identifiers, function calls,
expressions, or semicolon-separated fragments as test arguments.

For ordinary SQL querying, do not manage UDFs unless the question requires
a function that is absent or broken.

See `references/udf.md`.
