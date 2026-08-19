#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/run var/log
lock_dir=var/run/atlas-intake-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  print "Atlas intake worker already active"
  exit 0
fi
trap 'rmdir "$lock_dir"' EXIT INT TERM

catalog=${ATLAS_CATALOG_PATH:-/Users/hermes/src/feldenkrais-index/catalog/resources.json}
if [[ ! -r "$catalog" ]]; then
  print -u2 "Atlas catalog is not readable: $catalog"
  exit 1
fi

export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
/usr/bin/nice -n 10 .venv-mini/bin/python ops/import_atlas_lessons.py \
  --catalog "$catalog" \
  --apply \
  --limit "${ATLAS_INTAKE_BATCH_LIMIT:-20}"
