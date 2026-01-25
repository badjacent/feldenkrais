#!/bin/bash
# Build Python dependencies layer for Lambda
# Run this before terraform apply when lambda_requirements.txt changes

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building Python dependencies layer..."

# Clean up
rm -rf python lambda_deps.zip

# Install dependencies for Lambda (Linux x86_64)
pip install \
    -r lambda_requirements.txt \
    -t python \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    --python-version 3.12

# Create zip
zip -r lambda_deps.zip python

# Clean up
rm -rf python

echo "Created lambda_deps.zip"
ls -lh lambda_deps.zip
