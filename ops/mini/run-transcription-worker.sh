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
  lock_pid=""
  [[ -r "$lock_dir/pid" ]] && lock_pid=$(<"$lock_dir/pid")
  if [[ "$lock_pid" == <-> ]] && kill -0 "$lock_pid" 2>/dev/null; then
    print "transcription worker already active (pid $lock_pid)"
    exit 0
  fi
  print "removing stale transcription worker lock"
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || {
    print -u2 "cannot safely remove transcription worker lock: $lock_dir"
    exit 1
  }
  mkdir "$lock_dir"
fi
print $$ > "$lock_dir/pid"
trap 'rm -f "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true' EXIT INT TERM

cd src/kb
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  ../../.venv-mini/bin/python -m ingest.local_transcribe run \
  --limit "${TRANSCRIPTION_BATCH_LIMIT:-20}" \
  --max-seconds "${TRANSCRIPTION_MAX_SECONDS:-3300}" \
  --max-retries "${TRANSCRIPTION_MAX_RETRIES:-3}" \
  --stale-minutes "${TRANSCRIPTION_STALE_MINUTES:-360}"
