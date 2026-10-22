# Mac Mini workers

The Mini is the unattended execution host. The laptop remains the development,
review, and control surface.

## Boundaries

- PostgreSQL/Neon owns analysis gates, transcription claims, results, and errors.
- S3 owns archived media.
- `launchd` owns process lifecycle; it does not own work state.
- Hermes may inspect, explain, and manually trigger workers, but workers do not
  depend on a Hermes process remaining alive.
- The crawler remains in the separate `feldenkrais-index` checkout and retains
  its discovery queue.

The queues remain domain-specific. A future operational status projection may
join their run IDs and heartbeats, but it must not replace their authoritative
tables.

## The analysis worker sleeps when there is nothing to do

At the end of each pass the worker switches off every model profile that has
nothing left to run; it never switches one on. When no profile is left on it
removes `~/ops/analysis-active`, and from then on its scheduled runs print
`{"status":"asleep"}` and exit without touching the database, so Neon can
suspend. To start analysis again, switch a profile on in `/analysis` on the Mini
(or re-run a step there): that recreates the file and the next pass, within five
minutes, picks the work up. By hand: `touch ~/ops/analysis-active` after
switching the profile on.

## Jobs are system launch daemons

Since 2026-10-21 every `com.feldenkrais.*` job is a system launch daemon in
`/Library/LaunchDaemons/` that runs as `hermes`, so the Mini's servers and
workers start at boot with nobody logged in. (As launch agents they started only
inside a hermes login session, so after the 2026-10-19 reboot nothing ran for two
days.) `install-launch-daemons.sh` did the conversion; the old agent plists are
in `~hermes/Library/LaunchAgents/retired-to-daemons/`.

The plist files under `ops/mini/launchd/` and the agent commands in the sections
below describe each job's program and schedule. On the Mini, install a new or
changed job by placing its agent plist in `~/Library/LaunchAgents/` and running
`sudo /Users/hermes/ops/install-launch-daemons.sh`; do not `launchctl bootstrap
gui/...` it there.

As hermes, without sudo:

- Status: `launchctl print system/com.feldenkrais.<job> | head -20`
- Restart a server (`analyzer-web`, `ff-capture`, `operations-dashboard`): stop
  its process, `kill <pid>`, and launchd starts it again.
- An interval job cannot be started early without sudo
  (`sudo launchctl kickstart system/com.feldenkrais.<job>`); otherwise it runs on
  its schedule.

Tailscale is the `tailscaled` system daemon (Homebrew formula), installed the
same day, so the tailnet is also up at boot without a login; the Tailscale app it
replaced ran only inside `localmini`'s session. The servers bind to the tailnet
address and exit if it is not up yet; launchd retries them, so they start once
Tailscale connects. If the address changes while they run, restart them (kill
their processes) so they bind to the new one.

## Atlas lesson intake

The Atlas catalog feeds newly discovered free media into durable private ingest
queues. YouTube resources use the existing YouTube queue; website, SoundCloud,
and podcast media use `atlas_media_queue`. The command is deliberately dry-run
by default and deduplicates against both documents and queued work:

```sh
set -a; source .env; set +a
PYTHONPATH=src .venv-mini/bin/python ops/import_atlas_lessons.py \
  --catalog /Users/hermes/src/feldenkrais-index/catalog/resources.json
```

After database, S3, FFmpeg, and one-document transcription smoke tests pass,
add a bounded batch with `--apply --limit 20`. The YouTube ingest worker archives
media and creates transcription work; the transcription and analysis workers
then consume their own durable queues. Re-running intake is safe. No transcript,
media, or private analysis is copied into the public Atlas repository.

After that manual smoke test, `run-atlas-intake-worker.sh` performs the same
idempotent operation in bounded batches. It reads the sibling Atlas catalog via
`ATLAS_CATALOG_PATH` and adds URLs only; PostgreSQL remains the durable queue.
Install but do not enable its launchd definition until all downstream smoke
tests have passed:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.atlas-intake.plist
launchctl kickstart gui/$(id -u)/com.feldenkrais.atlas-intake
```

The non-YouTube media worker asks yt-dlp for an audio-only rendition, uploads
only that audio plus a loader sidecar to S3, and never archives source video:

```sh
PYTHONPATH=src/kb .venv-mini/bin/python -m ingest.atlas_media status
ATLAS_MEDIA_BATCH_LIMIT=1 ops/mini/run-atlas-media-worker.sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.atlas-media.plist
```

## Public Atlas projection

`run-atlas-projection-worker.sh` exports only the reviewed, versioned public
projection after analysis has produced structured concepts. It acquires the
Atlas crawler lock, requires a clean `main` checkout, pulls fast-forward-only,
validates every projected URL against the current free lesson catalog, and runs
an approved site build before committing and pushing only
`catalog/chooser-analysis.json`. If a crawl is active, it changes nothing and a
later scheduled run can retry.

Install its plist with the others, but do not bootstrap it until the complete
one-document intake, transcription, and analysis smoke test has passed. Run the
first projection manually and inspect its commit before enabling it:

```sh
ops/mini/run-atlas-projection-worker.sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.atlas-projection.plist
```

## Three tiers

| Tier | Where | What runs it | Reload |
|---|---|---|---|
| development | this laptop | `ops/dev/run-analyzer.sh` | autoreload on edits |
| candidate | the Mini, tailnet only | `ops/promote-candidate.sh` | promoted revisions only |
| production | Lambda, `aionyourside.net` | `terraform apply` | deploys only |

The capture server for the Chrome extension has no production tier. The
candidate is its only deployment, and the extension points at it.

Candidate addresses, reachable from the owner's devices and from nothing on the
LAN or the internet:

| Service | Address | launchd job |
|---|---|---|
| Analyzer web app | `http://locals-mac-mini.tailed6642.ts.net:8000` | `com.feldenkrais.analyzer-web` |
| Chrome capture server | `http://locals-mac-mini.tailed6642.ts.net:7890` | `com.feldenkrais.ff-capture` |

### Layout on the Mini

The candidate has its own checkout at `/Users/hermes/src/feldenkrais-candidate`,
separate from the worker checkout, so a promotion cannot disturb transcription,
analysis, or the crawler. It carries its own `.venv-web` (the worker environment
has no web framework) and symlinks the worker `.env` rather than holding a second
copy of the secrets.

The runner scripts live at `/Users/hermes/ops/`, outside the deployed trees,
so that promoting or rolling back a tree can never strand the launchd jobs.
`ops/promote-candidate.sh` reinstalls them from whatever revision it promotes.

### The analysis worker

Analysis runs in one dedicated launchd job, `com.feldenkrais.analysis`, from its
own checkout at `/Users/hermes/src/feldenkrais-analysis` with its own
`.venv-mini`. It is promoted with the same build step, `--target analysis`. The
web app never runs analysis.

Each pass drains what is runnable and exits; launchd starts the next pass up to
five minutes later. Work is claimed as (document, model profile, step): a step
runs when its group's gate is open for that profile and the profile is switched
on at `/analysis`. Each profile is held to its own `max_concurrency`, within
eight workers overall. See `docs/analysis.md` for profiles.

`ANALYSIS_ENABLED=1` in the job's environment is the machine-level kill switch;
the per-model switches are the everyday control.

### Promoting

From the laptop, on committed and pushed code:

```sh
ops/promote-candidate.sh                      # promote HEAD to the web app
ops/promote-candidate.sh --target analysis    # promote HEAD to the analysis worker
ops/promote-candidate.sh --rev <ref>          # promote a specific revision
ops/promote-candidate.sh --status             # what is deployed right now
ops/promote-candidate.sh --rollback           # return to the previous revision
```

`--target analysis` does not interrupt a pass already running: it finishes on
its own code and the next pass uses the new revision. Add `--restart` to stop
it instead; its in-flight steps become runnable again.

The build step refuses to ship uncommitted work, refuses a revision that is not
on a remote, checks that the private submodule pointer is pushed too, and runs
the unit suite. Pass `--allow-test-failures` to proceed anyway, which the release
record then shows.

On the Mini it checks out the revision, updates the submodule to the pointer that
revision carries, installs dependencies, and imports both applications in the
deployed environment *before* restarting anything. A failure at any of those
points restores the previous revision, and the running services never stop,
because they hold their code in memory. After the restart it polls both health
endpoints, and writes `var/candidate-release.json` recording the revision, the
one it replaced, and the test result.

Off-Lambda the web app applies no login gate and keeps its pipeline controls
enabled, which is why the bind address is the tailnet interface and never
`0.0.0.0`. Under Lambda the analysis pipeline answers 404: it is visible only
on the laptop and the Mini.

### The Chrome extension

Loaded unpacked on the laptop, pointed at the candidate through `SERVER_URL` in
`background.js` and `popup.js`, with matching entries in the manifest's
`host_permissions`. Changing either file needs a reload at `chrome://extensions`.

## Install without enabling

From `/Users/hermes/src/feldenkrais`:

```sh
export PATH=/opt/homebrew/bin:$PATH
UV_PROJECT_ENVIRONMENT=.venv-mini uv sync --python /opt/homebrew/bin/python3.12 \
  --extra mini --group dev
mkdir -p var/log var/run ~/Library/LaunchAgents
cp ops/mini/launchd/*.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.feldenkrais.atlas-projection.plist
plutil -lint ~/Library/LaunchAgents/com.feldenkrais.atlas-intake.plist
plutil -lint ~/Library/LaunchAgents/com.feldenkrais.youtube-ingest.plist
plutil -lint ~/Library/LaunchAgents/com.feldenkrais.analysis.plist
plutil -lint ~/Library/LaunchAgents/com.feldenkrais.transcription.plist
```

Do not bootstrap either job until the corresponding manual smoke test succeeds.

## Required private environment

The repository `.env` must be mode `0600` and contain the production values used
by the existing application, including:

- `DATABASE_URL` for Neon
- the configured analysis and embedding provider credentials
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION`
- `KB_BUCKET=feldenkrais-prod`
- `LOCAL_WHISPER_MODEL=mlx-community/whisper-large-v3-turbo`

Start from `ops/mini/worker.env.example`, copy it to `.env`, fill values over a
trusted SSH session, and run `chmod 600 .env`. The example contains no secrets.
Then run `ops/mini/check-readiness.sh transcription`; it checks permissions,
the transcription-specific values, database connectivity, S3 access, FFmpeg,
Python/MLX, Apple Silicon, and every plist without printing credentials or
changing durable state. This mode does not require an analysis-provider key,
so local speech-to-text can be certified independently. Run
`ops/mini/check-readiness.sh full` before enabling the analysis and projection
workers; full mode additionally requires an analysis-provider credential.

Use a dedicated S3 principal restricted to listing the bucket and reading and
writing the archive prefix (ingest writes; transcription reads). Prefer a
dedicated database role restricted to the tables/functions required by the
workers after the initial smoke test establishes the exact grants.

## Manual analysis cutover

Analysis is owner-approval gated. Merely installing or bootstrapping the plist
does not authorize work: `run-analysis-worker.sh` exits successfully without
claiming queue items unless `ANALYSIS_ENABLED=1`. Keep it unset or `0` until the
owner explicitly approves an analysis run. Approval should name the intended
cohort or budget; transcription approval does not imply analysis approval.

The Mini CLI worker and the web-triggered worker share a PostgreSQL advisory
lease, preventing analysis execution from overlapping across hosts. Stop any
older deployed worker that predates this lease before the first cutover test.

```sh
set -a; source .env; set +a
PYTHONPATH=src .venv-mini/bin/python -m kb.pipeline.analysis status
PYTHONPATH=src .venv-mini/bin/python -m kb.pipeline.analysis run --dry-run
PYTHONPATH=src .venv-mini/bin/python -m kb.pipeline.analysis run \
  --max-steps 1 --max-seconds 900 --workers 1
```

After inspecting that result, run `ops/mini/run-analysis-worker.sh` manually.
Only then enable scheduling:

```sh
export ANALYSIS_ENABLED=1
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.analysis.plist
launchctl kickstart gui/$(id -u)/com.feldenkrais.analysis
```

## Manual transcription cutover

Install FFmpeg and the MLX dependencies, prefetch the configured model, then
follow `private/ops/mini_transcribe/SETUP.md` for the dry run and one-document
live test. After those checks, run `ops/mini/run-transcription-worker.sh`
manually before bootstrapping its plist.

The readiness command for this cutover is:

```sh
ops/mini/check-readiness.sh transcription
```

The local worker retries its own failed claims up to
`TRANSCRIPTION_MAX_RETRIES` and may reclaim a `pending` or `processing` claim
only after `TRANSCRIPTION_STALE_MINUTES`. Each launch is bounded by
`TRANSCRIPTION_MAX_SECONDS`. Inspect shared queue health without doing work:

```sh
cd src/kb
../../.venv-mini/bin/python -m ingest.local_transcribe status
```

## Manual YouTube ingest cutover

This worker consumes the URLs added by `import_atlas_lessons.py`, downloads
audio with yt-dlp, archives it in S3, and creates the durable transcription
work consumed by the local MLX worker. It is bounded by
`YOUTUBE_INGEST_BATCH_LIMIT` (default 5) and has a host-local lock. The database
worker lease also prevents overlap with the existing Lambda consumer.

After the Atlas intake dry run, queue exactly one URL, then run:

```sh
set -a; source .env; set +a
PYTHONPATH=src/kb .venv-mini/bin/python -m ingest.youtube status
YOUTUBE_INGEST_BATCH_LIMIT=1 ops/mini/run-youtube-ingest-worker.sh
PYTHONPATH=src/kb .venv-mini/bin/python -m ingest.youtube status
```

Inspect the S3 object, document row, and queued transcription before enabling:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.youtube-ingest.plist
launchctl kickstart gui/$(id -u)/com.feldenkrais.youtube-ingest
```

## Disable

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.analysis.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.transcription.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.youtube-ingest.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.atlas-intake.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.atlas-projection.plist
```

Disabling launchd does not delete or rewrite queued work.
