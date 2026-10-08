#!/bin/zsh
# Candidate tier: the Chrome extension's capture server. This server has no
# production tier; the candidate on the Mini is the only deployment, and the
# extension points at it.
#
# Installed to /Users/hermes/ops/ by ops/promote-candidate.sh so the launchd job
# survives promotion and rollback of the tree.
set -euo pipefail

root=${CANDIDATE_ROOT:-/Users/hermes/src/feldenkrais-candidate}

cd "$root"
set -a
source .env
set +a

export PATH="$root/.venv-web/bin:/opt/homebrew/bin:$PATH"
# The Mini's current Tailscale address (100.64.0.0/10), read from its interfaces:
# the Tailscale app belongs to another account, so its CLI is not usable here.
# Never fall back to every interface: tailnet membership is the access control.
tailnet_address() {
  ifconfig | awk '/inet 100\./ { split($2, a, "."); if (a[2] >= 64 && a[2] <= 127) { print $2; exit } }'
}
export FF_CAPTURE_HOST=${FF_CAPTURE_HOST:-$(tailnet_address)}
[[ -n $FF_CAPTURE_HOST ]] || { print -u2 "no tailnet address on this machine; not starting"; exit 1; }
export FF_CAPTURE_PORT=${FF_CAPTURE_PORT:-7890}

# server.py hardcodes localhost:7890. Override the module globals at import time
# rather than editing the file, so the deployed tree matches its revision exactly.
exec "$root/.venv-web/bin/python" -c '
import os, sys
sys.path.insert(0, "private/products/ff_capture")
import server
server.HOST = os.environ["FF_CAPTURE_HOST"]
server.PORT = int(os.environ["FF_CAPTURE_PORT"])
server.main()
'
