#!/bin/zsh
# Development tier: the analyzer on this laptop, with autoreload.
#
# Reloads on edits under src/kb and bundled resources. Bound to localhost, where the
# app applies no login gate and keeps its pipeline controls enabled.
#
#   ops/dev/run-analyzer.sh            # http://localhost:8000
#   DEV_WEB_PORT=8001 ops/dev/run-analyzer.sh
#
set -euo pipefail

root=${0:A:h:h:h}
cd "$root"
set -a
[[ -f .env ]] && source .env
set +a

host=${DEV_WEB_HOST:-127.0.0.1}
port=${DEV_WEB_PORT:-8000}
python=${DEV_PYTHON:-$root/.venv/bin/python}

if [[ ! -x $python ]]; then
  print -u2 "no interpreter at $python"
  exit 1
fi
if ! $python -c "import uvicorn" 2>/dev/null; then
  print -u2 "uvicorn missing. Install with:"
  print -u2 "  uv sync --extra web --extra worker"
  exit 1
fi

reload_args=(--reload --reload-dir "$root/src/kb")
for extra in "$root/data"; do
  [[ -d $extra ]] && reload_args+=(--reload-dir "$extra")
done

cd src/kb
exec $python -m uvicorn web:app --host "$host" --port "$port" $reload_args
