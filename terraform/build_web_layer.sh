#!/bin/bash
# Build Python dependencies layer for Web Lambda
# Run this before terraform apply when lambda_web_requirements.txt changes

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building Web Lambda dependencies layer..."

# Clean up
rm -rf python_web lambda_web_deps.zip

# Install dependencies for Lambda (Linux x86_64)
python3 -m pip install \
    -r lambda_web_requirements.txt \
    -t python_web \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    --python-version 3.12 \
    --implementation cp

# Rename to python (Lambda layer expects this)
mv python_web python

# Create zip
zip -r lambda_web_deps.zip python

# Clean up
rm -rf python

echo "Created lambda_web_deps.zip"
ls -lh lambda_web_deps.zip
