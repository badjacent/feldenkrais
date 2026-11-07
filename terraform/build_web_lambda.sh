#!/usr/bin/env bash
# Build an application-only Lambda artifact. Does not deploy.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/package_web.py"
