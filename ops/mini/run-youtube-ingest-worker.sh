#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
batch_limit_override=${YOUTUBE_INGEST_BATCH_LIMIT:-}
stale_minutes_override=${YOUTUBE_STALE_PROCESSING_MINUTES:-}
set -a
source .env
set +a
[[ -n "$batch_limit_override" ]] && export YOUTUBE_INGEST_BATCH_LIMIT="$batch_limit_override"
[[ -n "$stale_minutes_override" ]] && export YOUTUBE_STALE_PROCESSING_MINUTES="$stale_minutes_override"

mkdir -p var/run var/log
lock_dir=var/run/youtube-ingest-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  lock_pid=""
  [[ -r "$lock_dir/pid" ]] && lock_pid=$(<"$lock_dir/pid")
  if [[ "$lock_pid" == <-> ]] && kill -0 "$lock_pid" 2>/dev/null; then
    print "YouTube ingest worker already active (pid $lock_pid)"
    exit 0
  fi
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || exit 1
  mkdir "$lock_dir"
fi
print $$ > "$lock_dir/pid"
cleanup() {
  local owned_pid=""
  [[ -r "$lock_dir/pid" ]] && owned_pid=$(<"$lock_dir/pid")
  if [[ "$owned_pid" == "$$" ]]; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$repo_root/src/kb${PYTHONPATH:+:$PYTHONPATH}"
set +e
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  .venv-mini/bin/python -m ingest.youtube process \
  --limit "${YOUTUBE_INGEST_BATCH_LIMIT:-5}" \
  --origin feldenkrais_atlas
worker_status=$?
set -e
cleanup
exit "$worker_status"
