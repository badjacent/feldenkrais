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

## Install without enabling

From `/Users/hermes/src/feldenkrais`:

```sh
export PATH=/opt/homebrew/bin:$PATH
UV_PROJECT_ENVIRONMENT=.venv-mini uv sync --extra mini
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

Use a dedicated S3 principal restricted to reading the archive prefix. Prefer a
dedicated database role restricted to the tables/functions required by the
workers after the initial smoke test establishes the exact grants.

## Manual analysis cutover

First stop the laptop's bulk analysis loop. The Mini CLI worker has a PostgreSQL
advisory lease that prevents two CLI workers from overlapping, but the legacy
web-process loop does not yet participate in that lease.

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

## Disable

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.analysis.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.feldenkrais.transcription.plist
```

Disabling launchd does not delete or rewrite queued work.
