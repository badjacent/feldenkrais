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
export FF_CAPTURE_HOST=${FF_CAPTURE_HOST:-100.85.14.17}
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
