#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -a
source .env
set +a

mkdir -p var/run var/log
lock_dir=var/run/atlas-media-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  print "Atlas media worker already active"
  exit 0
fi
cleanup() { rmdir "$lock_dir" 2>/dev/null || true }
trap cleanup EXIT INT TERM

export PYTHONPATH="$repo_root/src/kb${PYTHONPATH:+:$PYTHONPATH}"
set +e
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  .venv-mini/bin/python -m ingest.atlas_media run \
  --limit "${ATLAS_MEDIA_BATCH_LIMIT:-2}"
worker_status=$?
set -e
cleanup
exit "$worker_status"
