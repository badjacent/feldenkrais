#!/bin/zsh
# Candidate tier: the analyzer web app, served from the promoted checkout on the
# Mini. Installed to /Users/hermes/ops/ by ops/promote-candidate.sh, so that the
# launchd job keeps working across any promotion or rollback of the tree itself.
#
# Development is the laptop (ops/dev/run-analyzer.sh, with autoreload).
# Production is the Lambda behind aionyourside.net.
set -euo pipefail

root=${CANDIDATE_ROOT:-/Users/hermes/src/feldenkrais-candidate}
# The Mini's current Tailscale address (100.64.0.0/10), read from its interfaces:
# the Tailscale app belongs to another account, so its CLI is not usable here.
# Never fall back to every interface: tailnet membership is the access control.
tailnet_address() {
  ifconfig | awk '/inet 100\./ { split($2, a, "."); if (a[2] >= 64 && a[2] <= 127) { print $2; exit } }'
}
host=${CANDIDATE_WEB_HOST:-$(tailnet_address)}
[[ -n $host ]] || { print -u2 "no tailnet address on this machine; not starting"; exit 1; }
port=${CANDIDATE_WEB_PORT:-8000}

cd "$root"
set -a
source .env
set +a

# The web app never runs analysis. The dedicated worker (com.feldenkrais.analysis)
# does, from its own promoted checkout. Switching a profile on here creates the
# worker's wake file (see run-analysis-worker.sh), the manual gate restart.
export ANALYSIS_WAKE_FILE=${ANALYSIS_WAKE_FILE:-$HOME/ops/analysis-active}

cd src/kb
# Full, superuser access without login, as on localhost. The server binds only
# to the tailnet address, so tailnet membership is the access control.
export FULL_ACCESS_WITHOUT_LOGIN=1

exec "$root/.venv-web/bin/python" -m uvicorn web:app \
  --host "$host" --port "$port" --no-access-log
