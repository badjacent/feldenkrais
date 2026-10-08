#!/bin/zsh
set -euo pipefail
cd /Users/hermes/src/feldenkrais
set -a
source .env
set +a
# The Mini's current Tailscale address (100.64.0.0/10), read from its interfaces:
# the Tailscale app belongs to another account, so its CLI is not usable here.
# Never fall back to every interface: tailnet membership is the access control.
tailnet_address() {
  ifconfig | awk '/inet 100\./ { split($2, a, "."); if (a[2] >= 64 && a[2] <= 127) { print $2; exit } }'
}
export OPERATIONS_DASHBOARD_HOST=${OPERATIONS_DASHBOARD_HOST:-$(tailnet_address)}
[[ -n $OPERATIONS_DASHBOARD_HOST ]] || { print -u2 "no tailnet address on this machine; not starting"; exit 1; }
exec .venv-mini/bin/python ops/mini/operations_dashboard.py
