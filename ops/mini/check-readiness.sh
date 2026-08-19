#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
cd "$repo_root"

if [[ ! -f .env ]]; then
  print -u2 "not ready: $repo_root/.env is absent"
  exit 1
fi
mode=$(/usr/bin/stat -f '%Lp' .env)
if [[ "$mode" != "600" ]]; then
  print -u2 "not ready: .env mode is $mode (required: 600)"
  exit 1
fi
set -a
source .env
set +a

required=(DATABASE_URL KB_BUCKET AWS_DEFAULT_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY LOCAL_WHISPER_MODEL)
missing=()
for name in $required; do
  if [[ -z "${(P)name:-}" ]]; then
    missing+=("$name")
  fi
done
if (( ${#missing} )); then
  print -u2 "not ready: missing ${missing[*]}"
  exit 1
fi
if [[ -z "${GEMINI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
  print -u2 "not ready: no configured analysis provider credential"
  exit 1
fi

for executable in /opt/homebrew/bin/ffmpeg .venv-mini/bin/python; do
  if [[ ! -x "$executable" ]]; then
    print -u2 "not ready: executable absent: $executable"
    exit 1
  fi
done

export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
.venv-mini/bin/python - <<'PY'
import os
import boto3
import psycopg2

with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)

boto3.client("s3", region_name=os.environ["AWS_DEFAULT_REGION"]).head_bucket(
    Bucket=os.environ["KB_BUCKET"]
)
PY

for plist in ops/mini/launchd/*.plist; do
  plutil -lint "$plist" >/dev/null
done

print "ready: database, S3, FFmpeg, Python, and launchd definitions passed"
print "next: run the documented one-lesson smoke test; workers remain disabled"
