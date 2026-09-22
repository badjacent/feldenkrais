#!/bin/zsh
# Promote a committed revision from development to the candidate tier.
#
#   development  this laptop, autoreload        ops/dev/run-analyzer.sh
#   candidate    the Mini, over the tailnet     this script
#   production   Lambda behind aionyourside.net terraform apply
#
# The capture server has no production tier: the candidate is its only
# deployment, and the Chrome extension points at it.
#
#   ops/promote-candidate.sh                 # promote HEAD
#   ops/promote-candidate.sh --rev <ref>     # promote a specific revision
#   ops/promote-candidate.sh --status        # what is deployed right now
#   ops/promote-candidate.sh --rollback      # return to the previous revision
#
# A promotion never takes the candidate down on failure. The running processes
# hold their code in memory, so a bad checkout or a failed import restores the
# previous revision before anything is restarted.
set -euo pipefail

MINI_HOST=${MINI_HOST:-100.85.14.17}
MINI_USER=${MINI_USER:-hermes}
MINI_KEY=${MINI_KEY:-$HOME/.ssh/id_ed25519_codex_mini}
CANDIDATE_ROOT=${CANDIDATE_ROOT:-/Users/hermes/src/feldenkrais-candidate}
PRIVATE_URL=${PRIVATE_URL:-git@github.com:badjacent/feldenkrais-private.git}
ANALYZER_URL="http://${MINI_HOST}:8000/analysis"
CAPTURE_URL="http://${MINI_HOST}:7890/health"

root=${0:A:h:h}
cd "$root"

rev=HEAD
skip_tests=0
allow_test_failures=0
mode=promote

while (( $# )); do
  case $1 in
    --rev) rev=$2; shift 2;;
    --skip-tests) skip_tests=1; shift;;
    --allow-test-failures) allow_test_failures=1; shift;;
    --status) mode=status; shift;;
    --rollback) mode=rollback; shift;;
    -h|--help) sed -n '2,25p' "$0"; exit 0;;
    *) print -u2 "unknown argument: $1"; exit 2;;
  esac
done

mini() { ssh -i "$MINI_KEY" -o IdentitiesOnly=yes -o ConnectTimeout=10 "${MINI_USER}@${MINI_HOST}" "$@"; }
step() { print -- "\n── $1"; }
die()  { print -u2 -- "\nFAILED: $1"; exit 1; }

health() {
  local analyzer capture
  analyzer=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$ANALYZER_URL" || true)
  capture=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$CAPTURE_URL" || true)
  print -- "analyzer $ANALYZER_URL -> ${analyzer}"
  print -- "capture  $CAPTURE_URL -> ${capture}"
  [[ $analyzer == 200 && $capture == 200 ]]
}

if [[ $mode == status ]]; then
  mini "cat ${CANDIDATE_ROOT}/var/candidate-release.json 2>/dev/null || echo '{}'"
  print --
  health || true
  exit 0
fi

if [[ $mode == rollback ]]; then
  rev=$(mini "cd ${CANDIDATE_ROOT} && python3 -c \"import json,sys; print(json.load(open('var/candidate-release.json'))['previous_revision'])\" 2>/dev/null" || true)
  [[ -n $rev ]] || die "no previous revision recorded on the Mini"
  print -- "rolling back to ${rev}"
  skip_tests=1
fi

# ── Preflight ───────────────────────────────────────────────────────────────
step "Preflight"
git fetch -q origin || die "could not fetch origin"
sha=$(git rev-parse --verify --quiet "${rev}^{commit}") || die "no such revision: ${rev}"

explicit_rev=0
[[ $rev != HEAD ]] && explicit_rev=1

if ! git diff --quiet HEAD -- || ! git -C private diff --quiet HEAD --; then
  if (( explicit_rev )); then
    print -- "warning: working tree has uncommitted changes; they are not part of ${sha:0:8}"
  else
    print -u2 -- "\nUncommitted changes to tracked files. A promotion ships committed code only."
    git status --short -- ':!:*.pyc' | grep '^ *M' | head -10
    git -C private status --short | grep '^ *M' | sed 's|^|private/|' | head -10
    die "commit or stash first, or pass --rev <ref> to promote something else"
  fi
fi

if [[ -z $(git branch -r --contains "$sha" 2>/dev/null) ]]; then
  die "${sha:0:8} is not on any remote branch. Push it first."
fi

private_sha=$(git rev-parse --verify --quiet "${sha}:private") || die "cannot read the private submodule pointer at ${sha:0:8}"
git -C private fetch -q origin || true
if [[ -z $(git -C private branch -r --contains "$private_sha" 2>/dev/null) ]]; then
  die "private submodule ${private_sha:0:8} is not pushed. Push the private repo first."
fi

working_private=$(git -C private rev-parse HEAD)
if [[ $working_private != $private_sha ]]; then
  print -- "note: your private checkout is at ${working_private:0:8}, the promotion carries ${private_sha:0:8}"
fi

print -- "promoting $(git log --oneline -1 "$sha")"
print -- "private   $(git -C private log --oneline -1 "$private_sha" 2>/dev/null || print "$private_sha")"

# ── Build ───────────────────────────────────────────────────────────────────
step "Build"
tests="skipped"
if (( skip_tests )); then
  print -- "tests skipped"
else
  if .venv/bin/python -m pytest tests/unit -q > /tmp/promote-tests.log 2>&1; then
    tests=$(tail -1 /tmp/promote-tests.log)
    print -- "$tests"
  else
    tests=$(tail -1 /tmp/promote-tests.log)
    print -- "$tests"
    grep '^FAILED' /tmp/promote-tests.log | head -10 || true
    (( allow_test_failures )) || die "unit tests failed (see /tmp/promote-tests.log, or pass --allow-test-failures)"
    print -- "continuing despite failures (--allow-test-failures)"
  fi
fi

# ── Ship ────────────────────────────────────────────────────────────────────
step "Ship to ${MINI_USER}@${MINI_HOST}"
ship_output=$(mini "env SHA=${sha} PRIVATE_SHA=${private_sha} ROOT=${CANDIDATE_ROOT} PRIVATE_URL=${PRIVATE_URL} /bin/zsh -s" <<'REMOTE'
set -euo pipefail
cd "$ROOT"
mkdir -p ~/ops var/log
previous=$(git rev-parse HEAD)

restore() {
  git checkout -q -f --detach "$previous" || true
  git submodule update --init --force -q || true
  print -- "RESTORED=${previous}"
}

git fetch -q origin
git checkout -q -f --detach "$SHA"
git clean -qfd -e .venv-web -e var -e .env
git config submodule.private.url "$PRIVATE_URL"
git submodule update --init --force -q

# Dependencies for the deployed revision.
/opt/homebrew/bin/uv pip install -q --python .venv-web/bin/python \
  -r terraform/lambda_web_requirements.txt uvicorn boto3 requests pyyaml yt-dlp

# Stable runners live outside the tree so promotion and rollback cannot strand
# the launchd jobs. Refresh them from whatever the promoted revision carries.
for f in run-candidate-analyzer.sh run-candidate-capture.sh; do
  [[ -f ops/mini/$f ]] && install -m 755 "ops/mini/$f" ~/ops/$f
done

# Verify the artifact in its own environment before restarting anything.
set -a
source .env
set +a
if ! (cd src/kb && "$ROOT/.venv-web/bin/python" -c "import web; assert web.app" >/dev/null 2>/tmp/candidate-import.log); then
  print -u2 -- "import check failed:"
  tail -5 /tmp/candidate-import.log >&2
  restore
  exit 3
fi
if ! (cd private/products/ff_capture && "$ROOT/.venv-web/bin/python" -c "import server; assert server.main" >/dev/null 2>/tmp/candidate-import.log); then
  print -u2 -- "capture server import check failed:"
  tail -5 /tmp/candidate-import.log >&2
  restore
  exit 3
fi

launchctl kickstart -k "gui/$(id -u)/com.feldenkrais.analyzer-web"
launchctl kickstart -k "gui/$(id -u)/com.feldenkrais.ff-capture"

print -- "PREVIOUS=${previous}"
REMOTE
) || die "ship failed on the Mini; the previous revision was restored and the services were left running"
print -- "$ship_output"
previous=$(print -- "$ship_output" | sed -n 's/^PREVIOUS=//p')

# ── Verify ──────────────────────────────────────────────────────────────────
step "Verify"
ok=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  sleep 4
  if health > /tmp/promote-health.log 2>&1; then ok=1; break; fi
done
cat /tmp/promote-health.log
if (( ! ok )); then
  print -u2 -- "\nthe candidate did not come up. Roll back with:"
  print -u2 -- "  ops/promote-candidate.sh --rollback"
  exit 1
fi

# ── Record ──────────────────────────────────────────────────────────────────
mini "cd ${CANDIDATE_ROOT} && cat > var/candidate-release.json" <<JSON
{
  "revision": "${sha}",
  "private_revision": "${private_sha}",
  "previous_revision": "${previous}",
  "subject": "$(git log --format=%s -1 "$sha" | sed 's/"/\\"/g')",
  "promoted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "promoted_from": "$(hostname -s)",
  "tests": "${tests}"
}
JSON

step "Promoted"
print -- "revision ${sha:0:8}  ($(git log --format=%s -1 "$sha"))"
print -- "previous ${previous:0:8}"
print -- "analyzer $ANALYZER_URL"
print -- "capture  $CAPTURE_URL"
