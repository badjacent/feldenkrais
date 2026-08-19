#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a

mkdir -p var/run var/log
lock_dir=var/run/youtube-ingest-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  print "YouTube ingest worker already active"
  exit 0
fi
trap 'rmdir "$lock_dir"' EXIT INT TERM

export PYTHONPATH="$repo_root/src/kb${PYTHONPATH:+:$PYTHONPATH}"
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  .venv-mini/bin/python -m ingest.youtube process \
  --limit "${YOUTUBE_INGEST_BATCH_LIMIT:-5}"
