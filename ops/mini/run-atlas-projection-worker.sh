#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/log var/run
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/caffeinate -i /usr/bin/nice -n 10 \
  .venv-mini/bin/python ops/sync_atlas_projection.py \
  --atlas "${ATLAS_REPO_PATH:-/Users/hermes/src/feldenkrais-index}"
