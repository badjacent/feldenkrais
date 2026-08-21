#!/bin/zsh
set -euo pipefail
cd /Users/hermes/src/feldenkrais
set -a
source .env
set +a
export OPERATIONS_DASHBOARD_HOST=100.85.14.17
exec .venv-mini/bin/python ops/mini/operations_dashboard.py
