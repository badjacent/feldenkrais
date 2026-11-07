#!/bin/zsh
set -euo pipefail

# Temporary, manually invoked MacBook backlog helper. It transcribes only and
# never starts the lesson-analysis queue.
repo_root=${0:A:h:h:h}
cd "$repo_root"
set -a
source .env
set +a
export WORKER_INITIALIZE_DATABASE=0

python_bin="${TRANSCRIPTION_HELPER_PYTHON:-$repo_root/.venv/bin/python}"
[[ -x "$python_bin" ]] || {
  print -u2 "missing transcription Python: $python_bin"
  exit 1
}

if [[ "${1:-}" == "status" ]]; then
  cd src/kb
  exec "$python_bin" -m ingest.local_transcribe status
fi

mkdir -p var/run var/log
lock_dir=var/run/macbook-transcription-helper.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  print -u2 "MacBook transcription helper is already active"
  exit 1
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT INT TERM

args=(
  run
  --limit "${TRANSCRIPTION_HELPER_LIMIT:-1}"
  --max-seconds "${TRANSCRIPTION_HELPER_MAX_SECONDS:-900}"
  --max-retries "${TRANSCRIPTION_MAX_RETRIES:-3}"
  --stale-minutes "${TRANSCRIPTION_STALE_MINUTES:-360}"
)
if [[ -n "${TRANSCRIPTION_HELPER_LANGUAGE:-}" ]]; then
  args+=(--language "$TRANSCRIPTION_HELPER_LANGUAGE")
fi

cd src/kb
exec /usr/bin/caffeinate -i /usr/bin/nice -n 15 \
  "$python_bin" -m ingest.local_transcribe "${args[@]}"
