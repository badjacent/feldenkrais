#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -a
source .env
set +a

mkdir -p var/run var/log
lock_dir=var/run/lite-enrichment-worker.lock
if ! mkdir "$lock_dir" 2>/dev/null; then
  lock_pid=""
  [[ -r "$lock_dir/pid" ]] && lock_pid=$(<"$lock_dir/pid")
  if [[ "$lock_pid" == <-> ]] && kill -0 "$lock_pid" 2>/dev/null; then
    print "lite enrichment worker already active (pid $lock_pid)"
    exit 0
  fi
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || exit 1
  mkdir "$lock_dir"
fi
print $$ > "$lock_dir/pid"
trap 'rm -f "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || true' EXIT INT TERM

cd src/kb
/usr/bin/caffeinate -i /usr/bin/nice -n 19 \
  ../../.venv-mini/bin/python -m ingest.lite_enrich run \
  --limit "${LITE_ENRICHMENT_BATCH_LIMIT:-50}" --apply
