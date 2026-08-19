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

## Atlas lesson intake

The Atlas catalog can feed newly discovered free YouTube lessons into the
existing private ingest queue. The command is deliberately dry-run by default
and deduplicates by YouTube video ID against both documents and queued work:

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
