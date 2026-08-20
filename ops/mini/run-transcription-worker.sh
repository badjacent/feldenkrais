#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -a
source .env
set +a

mkdir -p var/run var/log
lock_dir=var/run/transcription-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  print "transcription worker already active"
  exit 0
fi
trap 'rmdir "$lock_dir"' EXIT INT TERM

cd src/kb
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  ../../.venv-mini/bin/python -m ingest.local_transcribe run \
  --limit "${TRANSCRIPTION_BATCH_LIMIT:-20}" \
  --max-seconds "${TRANSCRIPTION_MAX_SECONDS:-3300}" \
  --max-retries "${TRANSCRIPTION_MAX_RETRIES:-3}" \
  --stale-minutes "${TRANSCRIPTION_STALE_MINUTES:-360}"
