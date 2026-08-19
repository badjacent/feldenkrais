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

## Install without enabling

From `/Users/hermes/src/feldenkrais`:

```sh
export PATH=/opt/homebrew/bin:$PATH
UV_PROJECT_ENVIRONMENT=.venv-mini uv sync --python /opt/homebrew/bin/python3.12 \
  --extra mini --group dev
mkdir -p var/log var/run ~/Library/LaunchAgents
cp ops/mini/launchd/*.plist ~/Library/LaunchAgents/
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

Use a dedicated S3 principal restricted to reading the archive prefix. Prefer a
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

The local worker retries its own failed claims up to
`TRANSCRIPTION_MAX_RETRIES` and may reclaim a `pending` or `processing` claim
only after `TRANSCRIPTION_STALE_MINUTES`. Each launch is bounded by
`TRANSCRIPTION_MAX_SECONDS`. Inspect shared queue health without doing work:

```sh
cd src/kb
../../.venv-mini/bin/python -m ingest.local_transcribe status
```

## Disable

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.analysis.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.transcription.plist
```

Disabling launchd does not delete or rewrite queued work.
