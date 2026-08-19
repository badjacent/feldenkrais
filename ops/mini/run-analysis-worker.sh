#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/log
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/caffeinate -i /usr/bin/nice -n 10 \
  .venv-mini/bin/python -m kb.pipeline.analysis run \
  --max-steps "${ANALYSIS_MAX_STEPS:-100}" \
  --max-seconds "${ANALYSIS_MAX_SECONDS:-3300}" \
  --workers "${ANALYSIS_WORKERS:-2}" \
  --step-timeout "${ANALYSIS_STEP_TIMEOUT:-600}"
