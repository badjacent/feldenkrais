#!/bin/zsh
# Candidate tier: the analyzer web app, served from the promoted checkout on the
# Mini. Installed to /Users/hermes/ops/ by ops/promote-candidate.sh, so that the
# launchd job keeps working across any promotion or rollback of the tree itself.
#
# Development is the laptop (ops/dev/run-analyzer.sh, with autoreload).
# Production is the Lambda behind aionyourside.net.
set -euo pipefail

root=${CANDIDATE_ROOT:-/Users/hermes/src/feldenkrais-candidate}
host=${CANDIDATE_WEB_HOST:-100.85.14.17}
port=${CANDIDATE_WEB_PORT:-8000}

cd "$root"
set -a
source .env
set +a

# Analysis execution belongs to the worker checkout. The candidate web tree is
# intentionally lightweight and does not carry the worker virtualenv.
export ANALYSIS_WORKER_PYTHON=${ANALYSIS_WORKER_PYTHON:-/Users/hermes/src/feldenkrais/.venv-mini/bin/python}
export ANALYSIS_WORKER_CWD=${ANALYSIS_WORKER_CWD:-/Users/hermes/src/feldenkrais/src/kb}
export ANALYSIS_QUEUE_ENABLED=${ANALYSIS_QUEUE_ENABLED:-1}
export ANALYSIS_WORKER_CONCURRENCY=${ANALYSIS_WORKER_CONCURRENCY:-8}
export ANALYSIS_STEP_TIMEOUT=${ANALYSIS_STEP_TIMEOUT:-900}

cd src/kb
exec "$root/.venv-web/bin/python" -m uvicorn web:app \
  --host "$host" --port "$port" --no-access-log
