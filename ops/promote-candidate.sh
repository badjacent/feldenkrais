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
# Two targets share this build step, each with its own checkout on the Mini:
#   web       the analyzer web app and capture server  (feldenkrais-candidate)
#   analysis  the dedicated analysis worker            (feldenkrais-analysis)
#
#   ops/promote-candidate.sh                          # promote HEAD to the web target
#   ops/promote-candidate.sh --target analysis        # promote HEAD to the worker
#   ops/promote-candidate.sh --rev <ref>              # promote a specific revision
#   ops/promote-candidate.sh --status                 # what is deployed right now
#   ops/promote-candidate.sh --rollback               # return to the previous revision
#   ops/promote-candidate.sh --target analysis --restart   # also stop a pass in progress
#
# A promotion never takes the target down on failure. Running processes hold
# their code in memory, so a bad checkout or a failed import restores the
# previous revision before anything is restarted.
#
# The analysis worker is not interrupted by default: a pass already running
# finishes on the code it started with and the next pass (at most five minutes
# later) uses the new revision. --restart stops the running pass instead; its
# in-flight steps are abandoned and become runnable again.
set -euo pipefail

MINI_HOST=${MINI_HOST:-locals-mac-mini.tailed6642.ts.net}
MINI_USER=${MINI_USER:-hermes}
MINI_KEY=${MINI_KEY:-$HOME/.ssh/id_ed25519_codex_mini}
CANDIDATE_ROOT=${CANDIDATE_ROOT:-/Users/hermes/src/feldenkrais-candidate}
ANALYSIS_ROOT=${ANALYSIS_ROOT:-/Users/hermes/src/feldenkrais-analysis}
PRIVATE_URL=${PRIVATE_URL:-git@github.com:badjacent/feldenkrais-private.git}
ANALYZER_URL="http://${MINI_HOST}:8000/analysis"
CAPTURE_URL="http://${MINI_HOST}:7890/health"

root=${0:A:h:h}
cd "$root"

rev=HEAD
skip_tests=0
allow_test_failures=0
restart_worker=0
target=web
mode=promote

while (( $# )); do
  case $1 in
    --rev) rev=$2; shift 2;;
    --target) target=$2; shift 2;;
    --restart) restart_worker=1; shift;;
    --skip-tests) skip_tests=1; shift;;
    --allow-test-failures) allow_test_failures=1; shift;;
    --status) mode=status; shift;;
    --rollback) mode=rollback; shift;;
    -h|--help) sed -n '2,32p' "$0"; exit 0;;
    *) print -u2 "unknown argument: $1"; exit 2;;
  esac
done

case $target in
  web)      TARGET_ROOT=$CANDIDATE_ROOT; RELEASE_FILE=var/candidate-release.json;;
  analysis) TARGET_ROOT=$ANALYSIS_ROOT;  RELEASE_FILE=var/analysis-release.json;;
  *) print -u2 "unknown target: $target (web or analysis)"; exit 2;;
esac

mini() { ssh -i "$MINI_KEY" -o IdentitiesOnly=yes -o ConnectTimeout=10 "${MINI_USER}@${MINI_HOST}" "$@"; }
step() { print -- "\n── $1"; }
die()  { print -u2 -- "\nFAILED: $1"; exit 1; }

health() {
  if [[ $target == analysis ]]; then
    local loaded
    # A system daemon on a converted Mini (ops/mini/install-launch-daemons.sh),
    # otherwise a launch agent in hermes's login session.
    loaded=$(mini "launchctl print system/com.feldenkrais.analysis 2>/dev/null \
      | awk '\$1 == \"state\" || (\$1 == \"last\" && \$2 == \"exit\") { printf \"%s \", \$0 }' \
      | grep . || launchctl list | grep -w com.feldenkrais.analysis" || true)
    print -- "analysis worker job: ${loaded:-not loaded}"
    [[ -n $loaded ]]
    return
  fi
  local analyzer capture
  analyzer=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$ANALYZER_URL" || true)
  capture=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$CAPTURE_URL" || true)
  print -- "analyzer $ANALYZER_URL -> ${analyzer}"
  print -- "capture  $CAPTURE_URL -> ${capture}"
  [[ $analyzer == 200 && $capture == 200 ]]
}

if [[ $mode == status ]]; then
  mini "cat ${TARGET_ROOT}/${RELEASE_FILE} 2>/dev/null || echo '{}'"
  print --
  health || true
  exit 0
fi

if [[ $mode == rollback ]]; then
  rev=$(mini "cd ${TARGET_ROOT} && python3 -c \"import json,sys; print(json.load(open('${RELEASE_FILE}'))['previous_revision'])\" 2>/dev/null" || true)
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
  # Disconnected from the database: importing web runs initialize(), which
  # would otherwise execute schema DDL against whatever DATABASE_URL names.
  if DATABASE_URL= .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider > /tmp/promote-tests.log 2>&1; then
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
step "Ship ${target} to ${MINI_USER}@${MINI_HOST}:${TARGET_ROOT}"
ship_output=$(mini "env TARGET=${target} RESTART=${restart_worker} SHA=${sha} PRIVATE_SHA=${private_sha} ROOT=${TARGET_ROOT} PRIVATE_URL=${PRIVATE_URL} /bin/zsh -s" <<'REMOTE'
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

# Dependencies, runners, import checks and restart differ by target.
if [[ $TARGET == web ]]; then
  /opt/homebrew/bin/uv pip install -q --python .venv-web/bin/python \
    -r terraform/lambda_web_requirements.txt uvicorn boto3 requests pyyaml yt-dlp python-docx
  runners=(run-candidate-analyzer.sh run-candidate-capture.sh)
  checks=(
    "src/kb|.venv-web/bin/python|import web; assert web.app; import print_docx"
    "private/products/ff_capture|.venv-web/bin/python|import server; assert server.main"
  )
else
  UV_PROJECT_ENVIRONMENT=.venv-mini /opt/homebrew/bin/uv sync -q --frozen \
    --python /opt/homebrew/bin/python3.12 --extra mini
  runners=(run-analysis-worker.sh)
  checks=("src/kb|.venv-mini/bin/python|import pipeline.analysis as p; assert p.run_bounded_worker")
fi

# Stable runners live outside the tree so promotion and rollback cannot strand
# the launchd jobs. Refresh them from whatever the promoted revision carries.
for f in $runners; do
  [[ -f ops/mini/$f ]] && install -m 755 "ops/mini/$f" ~/ops/$f
done

# Verify the artifact in its own environment before restarting anything.
set -a
source .env
set +a
for check in $checks; do
  dir=${check%%|*}; rest=${check#*|}; python=${rest%%|*}; code=${rest#*|}
  if ! (cd "$dir" && "$ROOT/$python" -c "$code" >/dev/null 2>/tmp/candidate-import.log); then
    print -u2 -- "import check failed in $dir:"
    tail -5 /tmp/candidate-import.log >&2
    restore
    exit 3
  fi
done

# (Re)start a job. On the Mini the jobs are system launch daemons that run as
# hermes (ops/mini/install-launch-daemons.sh). hermes can read those but not
# kickstart them; it can stop its own process instead, and launchd starts a
# KeepAlive job again at once. An interval job picks up the new code on its
# next scheduled run. A job still installed as a launch agent (a machine not yet
# converted) is loaded or kickstarted in the login session, as before.
start_job() {
  local label=$1 restart=$2 domain="gui/$(id -u)"
  if launchctl print "system/$label" >/dev/null 2>&1; then
    local pid
    pid=$(launchctl print "system/$label" | awk '$1 == "pid" { print $3; exit }')
    if [[ $restart == 1 && -n $pid ]]; then
      kill "$pid"
    fi
  elif launchctl print "$domain/$label" >/dev/null 2>&1; then
    if [[ $restart == 1 ]]; then
      launchctl kickstart -k "$domain/$label"
    else
      launchctl kickstart "$domain/$label" 2>/dev/null || true
    fi
  else
    launchctl bootstrap "$domain" ~/Library/LaunchAgents/$label.plist
  fi
}

if [[ $TARGET == web ]]; then
  start_job com.feldenkrais.analyzer-web 1
  start_job com.feldenkrais.ff-capture 1
else
  # Without --restart a running pass keeps its code; otherwise a pass starts now.
  start_job com.feldenkrais.analysis "$RESTART"
fi

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
  print -u2 -- "  ops/promote-candidate.sh --target ${target} --rollback"
  exit 1
fi

# ── Record ──────────────────────────────────────────────────────────────────
mini "cd ${TARGET_ROOT} && cat > ${RELEASE_FILE}" <<JSON
{
  "target": "${target}",
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
if [[ $target == web ]]; then
  print -- "analyzer $ANALYZER_URL"
  print -- "capture  $CAPTURE_URL"
else
  print -- "worker   com.feldenkrais.analysis in ${TARGET_ROOT}"
fi
