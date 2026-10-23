#!/bin/zsh
set -euo pipefail

# The dedicated analysis worker. Installed to /Users/hermes/ops/ by
# ops/promote-candidate.sh --target analysis, which sets ANALYSIS_ROOT to the
# promoted checkout; run in-tree it uses its own checkout.
repo_root=${ANALYSIS_ROOT:-${0:A:h:h:h}}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/log
if [[ "${ANALYSIS_ENABLED:-0}" != "1" ]]; then
  print -r -- '{"status":"disabled","reason":"ANALYSIS_ENABLED is not 1"}'
  exit 0
fi
# The worker switches each model profile off once it has nothing to run, and
# removes this file when none is left on. Without it, exit before touching the
# database, so an idle worker lets Neon suspend. Switching a profile on in the
# analyzer on this machine (or `touch` by hand) creates it again.
export ANALYSIS_WAKE_FILE=${ANALYSIS_WAKE_FILE:-$HOME/ops/analysis-active}
if [[ ! -e $ANALYSIS_WAKE_FILE ]]; then
  print -r -- '{"status":"asleep","reason":"no model profile is switched on; switch one on in /analysis to wake the worker"}'
  exit 0
fi
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
# Overall concurrency. Each model profile is further held to its own
# max_concurrency, and only profiles switched on in the analysis page run.
default_workers=8
exec /usr/bin/caffeinate -i /usr/bin/nice -n 10 \
  .venv-mini/bin/python -m kb.pipeline.analysis run \
  --max-steps "${ANALYSIS_MAX_STEPS:-100}" \
  --max-seconds "${ANALYSIS_MAX_SECONDS:-3300}" \
  --workers "${ANALYSIS_WORKERS:-$default_workers}" \
  --step-timeout "${ANALYSIS_STEP_TIMEOUT:-600}"
