#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/log
if [[ "${ANALYSIS_ENABLED:-0}" != "1" ]]; then
  print -r -- '{"status":"disabled","reason":"ANALYSIS_ENABLED is not 1"}'
  exit 0
fi
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
default_workers=8
exec /usr/bin/caffeinate -i /usr/bin/nice -n 10 \
  .venv-mini/bin/python -m kb.pipeline.analysis run \
  --max-steps "${ANALYSIS_MAX_STEPS:-100}" \
  --max-seconds "${ANALYSIS_MAX_SECONDS:-3300}" \
  --workers "${ANALYSIS_WORKERS:-$default_workers}" \
  --step-timeout "${ANALYSIS_STEP_TIMEOUT:-600}"
