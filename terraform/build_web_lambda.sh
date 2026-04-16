#!/usr/bin/env bash
# Build the web Lambda deployment package.
# Creates lambda_web.zip with src/kb/ code + data files + docs.
#
# Usage: cd terraform && ./build_web_lambda.sh

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUT="$SCRIPT_DIR/lambda_web.zip"
STAGING="$SCRIPT_DIR/_web_staging"

echo "Building web Lambda package..."

# Clean staging
rm -rf "$STAGING" "$OUT"
mkdir -p "$STAGING"

# --- Source code from src/kb/ ---
# Copy all .py files preserving directory structure
cd "$PROJECT_DIR/src/kb"
find . -name '*.py' -not -path './__pycache__/*' -not -path '*/ingest/*' \
    -not -path '*/search/*' -not -path '*/ops/*' -not -path '*/extract_map/*' \
    | while read -r f; do
    mkdir -p "$STAGING/$(dirname "$f")"
    cp "$f" "$STAGING/$f"
done
cd "$SCRIPT_DIR"

# --- Data files (prompts, schemas) from public data/ ---
# Single source of truth: everything the Lambda needs is in public data/
mkdir -p "$STAGING/data"
for f in "$PROJECT_DIR"/data/pass_0*.md "$PROJECT_DIR"/data/pass_0*.schema.json \
         "$PROJECT_DIR"/data/pass_0*.json "$PROJECT_DIR"/data/pass_a.schema.json \
         "$PROJECT_DIR"/data/frameworks.json; do
    [ -f "$f" ] && cp "$f" "$STAGING/data/"
done

# --- Framework prompts (v1) — all under data/frameworks/v1/ ---
mkdir -p "$STAGING/data/frameworks/v1"
for f in "$PROJECT_DIR"/data/frameworks/v1/*; do
    [ -f "$f" ] && cp "$f" "$STAGING/data/frameworks/v1/"
done

# --- Docs ---
mkdir -p "$STAGING/docs"
for f in "$PROJECT_DIR"/docs/*.md; do
    [ -f "$f" ] && cp "$f" "$STAGING/docs/"
done
# Interiority booklet
if [ -d "$PROJECT_DIR/docs/interiority" ]; then
    mkdir -p "$STAGING/docs/interiority"
    cp "$PROJECT_DIR/docs/interiority/index.html" "$STAGING/docs/interiority/"
fi

# --- Build zip ---
cd "$STAGING"
zip -r "$OUT" . -x '__pycache__/*' '*.pyc'
cd "$SCRIPT_DIR"

# Clean staging
rm -rf "$STAGING"

FILE_COUNT=$(unzip -l "$OUT" | tail -1 | awk '{print $2}')
SIZE=$(ls -lh "$OUT" | awk '{print $5}')
echo "Created $OUT ($SIZE, $FILE_COUNT files)"
