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

# The web app never runs analysis. The dedicated worker (com.feldenkrais.analysis)
# does, from its own promoted checkout.

cd src/kb
# Full, superuser access without login, as on localhost. The server binds only
# to the tailnet address, so tailnet membership is the access control.
export FULL_ACCESS_WITHOUT_LOGIN=1

exec "$root/.venv-web/bin/python" -m uvicorn web:app \
  --host "$host" --port "$port" --no-access-log
