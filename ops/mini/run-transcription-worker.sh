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
python=../../.venv-mini/bin/python

# Sleep unless there is work. The loader Lambda writes an S3 marker when it
# creates a document that needs transcribing (ingest/transcription_wake.py);
# without it, exit before touching the database so Neon can suspend. Set
# TRANSCRIPTION_ALWAYS_POLL=1 to skip the check. If S3 itself cannot be read,
# poll as before rather than miss work.
if [[ "${TRANSCRIPTION_ALWAYS_POLL:-0}" != 1 ]]; then
  set +e
  $python -m ingest.transcription_wake take
  wake_status=$?
  set -e
  if [[ $wake_status -eq 3 ]]; then
    print -r -- '{"status":"asleep","reason":"no transcription marker in S3 (control/transcription/pending)"}'
    exit 0
  fi
  [[ $wake_status -eq 0 ]] || print -u2 "transcription wake check failed (status $wake_status); polling anyway"
fi

set +e
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  $python -m ingest.local_transcribe run \
  --limit "${TRANSCRIPTION_BATCH_LIMIT:-20}" \
  --max-seconds "${TRANSCRIPTION_MAX_SECONDS:-3300}" \
  --document-timeout-seconds "${TRANSCRIPTION_DOCUMENT_TIMEOUT_SECONDS:-1800}" \
  --max-retries "${TRANSCRIPTION_MAX_RETRIES:-3}" \
  --stale-minutes "${TRANSCRIPTION_STALE_MINUTES:-360}"
worker_status=$?
set -e
if [[ $worker_status -eq 0 ]]; then
  /bin/launchctl kickstart "gui/$(id -u)/com.feldenkrais.lite-enrichment" 2>/dev/null || true
else
  # A crash can leave a job claimed; run again so stale-claim recovery happens.
  $python -m ingest.transcription_wake arm || true
fi
exit $worker_status
