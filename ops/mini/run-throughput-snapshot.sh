#!/bin/zsh
set -euo pipefail
repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a
exec .venv-mini/bin/python ops/mini/collect-throughput-snapshot.py
