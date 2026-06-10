# MaxCompute (ODPS) SQL Grammar

ANTLR4 grammar files for the MaxCompute SQL dialect, used as the
reference oracle for testing the SQLGlot MaxCompute dialect.

## Source

Grammar files in `odps/` are from the public Apache-2.0-licensed repository:

- **Repository:** <https://github.com/aliyun/aliyun-odps-java-sdk>
- **Path:** `odps-sdk/odps-sdk-core/src/main/java/com/aliyun/odps/sqa/commandapi/antlr/sql/`
- **License:** Apache License 2.0

## Usage

Generate the Python parser from the grammar files:

```bash
./grammar/generate.sh
```

This will:
1. Download the ANTLR 4.13.2 jar (if not cached)
2. Apply Python-compatibility patches to the grammar
3. Generate Python lexer/parser into `grammar/generated/`

### Prerequisites

- Java 11+ (`java -version`)
- Python `antlr4-python3-runtime` package: `uv pip install antlr4-python3-runtime==4.13.2`

## Directory structure

```
grammar/
  README.md           ← this file
  generate.sh         ← one-shot generation script
  odps/
    OdpsLexer.g4      ← original grammar (do not edit)
    OdpsParser.g4     ← original grammar (do not edit)
  generated/          ← output (committed; regenerate via generate.sh)
```

## Updating the grammar

To update to a newer grammar version:

1. Download new `.g4` files from the public repo into `odps/`
2. Run `./grammar/generate.sh`
3. Run `pytest tests/` to check for regressions
