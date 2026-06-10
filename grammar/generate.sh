#!/usr/bin/env bash
# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0
#
# Generate Python lexer/parser from the MaxCompute ANTLR4 grammar.
#
# Usage:
#   ./grammar/generate.sh            # generate from local .g4 files
#   ./grammar/generate.sh --fetch    # download latest .g4 from GitHub first
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GRAMMAR_DIR="$SCRIPT_DIR/odps"
OUTPUT_DIR="$SCRIPT_DIR/generated"
CACHE_DIR="$SCRIPT_DIR/.cache"

ANTLR_VERSION="4.13.2"
ANTLR_JAR="$CACHE_DIR/antlr-${ANTLR_VERSION}-complete.jar"
ANTLR_URL="https://www.antlr.org/download/antlr-${ANTLR_VERSION}-complete.jar"

SOURCE_REPO="aliyun/aliyun-odps-java-sdk"
SOURCE_PATH="odps-sdk/odps-sdk-core/src/main/java/com/aliyun/odps/sqa/commandapi/antlr/sql"
SOURCE_BRANCH="master"

# ── Helpers ──────────────────────────────────────────────────────────

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

check_java() {
    command -v java >/dev/null 2>&1 || die "Java is required. Install JDK 11+."
}

download_antlr_jar() {
    if [[ -f "$ANTLR_JAR" ]]; then
        return
    fi
    info "Downloading ANTLR ${ANTLR_VERSION} jar..."
    mkdir -p "$CACHE_DIR"
    curl -fsSL "$ANTLR_URL" -o "$ANTLR_JAR"
}

fetch_grammar() {
    info "Fetching grammar from github.com/${SOURCE_REPO}..."
    local base_url="https://raw.githubusercontent.com/${SOURCE_REPO}/${SOURCE_BRANCH}/${SOURCE_PATH}"
    mkdir -p "$GRAMMAR_DIR"
    curl -fsSL "${base_url}/OdpsLexer.g4"  -o "$GRAMMAR_DIR/OdpsLexer.g4"
    curl -fsSL "${base_url}/OdpsParser.g4" -o "$GRAMMAR_DIR/OdpsParser.g4"
    info "Downloaded OdpsLexer.g4 ($(wc -l < "$GRAMMAR_DIR/OdpsLexer.g4") lines) and OdpsParser.g4 ($(wc -l < "$GRAMMAR_DIR/OdpsParser.g4") lines)"
}

patch_grammar() {
    info "Patching grammar for Python target..."
    local tmp_dir
    tmp_dir=$(mktemp -d)
    trap "rm -rf '$tmp_dir'" EXIT

    # Copy originals
    cp "$GRAMMAR_DIR/OdpsLexer.g4"  "$tmp_dir/OdpsLexer.g4"
    cp "$GRAMMAR_DIR/OdpsParser.g4" "$tmp_dir/OdpsParser.g4"

    # Remove Java-specific contextSuperClass
    sed -i.bak 's/contextSuperClass=OdpsParserRuleContext;//' "$tmp_dir/OdpsParser.g4"

    # Replace Java 'boolean' type annotations with Python 'bool',
    # and Java boolean literals 'true'/'false' with Python 'True'/'False'
    # in rule parameter default values and rule invocations.
    sed -i.bak 's/\[boolean /[bool /g' "$tmp_dir/OdpsParser.g4"
    # Fix bare 'true'/'false' in rule arguments and parameter passing.
    # Patterns: [true] → [True], [false] → [False], (true) → (True), (false) → (False)
    sed -i.bak 's/\[true\]/[True]/g; s/\[false\]/[False]/g; s/(true)/(True)/g; s/(false)/(False)/g' "$tmp_dir/OdpsParser.g4"

    # Remove @header/@members blocks with Java imports (if present)
    # The public grammar currently has none, but guard against future additions.
    sed -i.bak '/@header/,/^}/d' "$tmp_dir/OdpsParser.g4" 2>/dev/null || true
    sed -i.bak '/@members/,/^}/d' "$tmp_dir/OdpsParser.g4" 2>/dev/null || true
    sed -i.bak '/@header/,/^}/d' "$tmp_dir/OdpsLexer.g4"  2>/dev/null || true
    sed -i.bak '/@members/,/^}/d' "$tmp_dir/OdpsLexer.g4"  2>/dev/null || true

    rm -f "$tmp_dir"/*.bak

    echo "$tmp_dir"
    # Note: caller must use the returned path before EXIT trap fires
    trap - EXIT
    echo "$tmp_dir"
}

generate_python() {
    local patched_dir="$1"

    info "Generating Python parser..."
    rm -rf "$OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"

    java -jar "$ANTLR_JAR" \
        -Dlanguage=Python3 \
        -visitor \
        -o "$OUTPUT_DIR" \
        "$patched_dir/OdpsLexer.g4" \
        "$patched_dir/OdpsParser.g4"

    # ANTLR may nest output; flatten if needed
    if [[ -d "$OUTPUT_DIR/$patched_dir" ]]; then
        mv "$OUTPUT_DIR/$patched_dir"/*.py "$OUTPUT_DIR/" 2>/dev/null || true
        mv "$OUTPUT_DIR/$patched_dir"/*.interp "$OUTPUT_DIR/" 2>/dev/null || true
        mv "$OUTPUT_DIR/$patched_dir"/*.tokens "$OUTPUT_DIR/" 2>/dev/null || true
        # Clean nested dirs
        rm -rf "$OUTPUT_DIR/$(echo "$patched_dir" | cut -d/ -f2)"
    fi

    # Create __init__.py
    cat > "$OUTPUT_DIR/__init__.py" << 'PYEOF'
"""Auto-generated ANTLR4 parser for MaxCompute (ODPS) SQL.

Do not edit — regenerate via ``grammar/generate.sh``.
"""
PYEOF

    # Clean up intermediate files we don't need
    rm -f "$OUTPUT_DIR"/*.interp "$OUTPUT_DIR"/*.tokens

    # Clean up patched temp dir
    rm -rf "$patched_dir"

    local py_count
    py_count=$(find "$OUTPUT_DIR" -name "*.py" | wc -l | tr -d ' ')
    info "Generated ${py_count} Python files in grammar/generated/"
}

verify() {
    info "Verifying generated parser loads..."
    local py
    if [[ -f ".venv/bin/python3" ]]; then
        py=".venv/bin/python3"
    else
        py="python3"
    fi
    PYTHONPATH="$OUTPUT_DIR" "$py" -c "
from OdpsLexer import OdpsLexer
from OdpsParser import OdpsParser
print('  OdpsLexer and OdpsParser loaded successfully')
" || die "Generated parser failed to load. Ensure antlr4-python3-runtime is installed."
}

# ── Main ─────────────────────────────────────────────────────────────

main() {
    cd "$SCRIPT_DIR/.."

    check_java

    if [[ "${1:-}" == "--fetch" ]]; then
        fetch_grammar
    fi

    [[ -f "$GRAMMAR_DIR/OdpsParser.g4" ]] || die "Grammar files not found in $GRAMMAR_DIR. Run with --fetch to download."
    [[ -f "$GRAMMAR_DIR/OdpsLexer.g4"  ]] || die "Grammar files not found in $GRAMMAR_DIR. Run with --fetch to download."

    download_antlr_jar

    local patched_dir
    # patch_grammar prints the path twice (before and after trap change)
    # take the last line
    patched_dir=$(patch_grammar | tail -1)

    generate_python "$patched_dir"
    verify

    info "Done. Generated parser is in grammar/generated/"
}

main "$@"
