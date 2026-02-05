Scrape and stage videos from Feldenkrais Manhattan training site.

## What this does

Logs into feldenkraismanhattantraining.com, finds YouTube video URLs from lesson pages, and stages them for the transcription pipeline.

## Authentication

Uses `FM_EMAIL` and `FM_PASSWORD` from `.env`. These are also set as GitHub secrets. If login fails, ask the user to verify their credentials.

## How to run

There are two tools depending on what the user wants:

### 1. Just scrape video URLs (no staging)

```bash
cd /Users/michaelhollander/github/feldenkrais
uv run python src/kb/feldenkrais_manhattan_scraper.py --urls-only
```

This outputs YouTube URLs to `feldenkrais_manhattan_videos.json`. Useful for seeing what's on the site.

### 2. Full pipeline: scrape + stage to S3

```bash
cd /Users/michaelhollander/github/feldenkrais
uv run python src/kb/fm_loader.py
```

Common flags:
- `--scrape-only` — just check what's available, don't download/stage
- `--download-missing` — download videos not found locally via yt-dlp
- `--local-dir /path/to/videos` — use local video files instead of downloading
- `--limit N` — only process N videos
- `--skip-metadata` — skip YouTube metadata fetch (faster)

### Typical workflow

```bash
# 1. Check what's new (no downloads)
uv run python src/kb/fm_loader.py --scrape-only

# 2. Process new videos (download from YouTube)
uv run python src/kb/fm_loader.py --download-missing --limit 5

# 3. Or process with local files
uv run python src/kb/fm_loader.py --local-dir /path/to/videos
```

## After staging

The loader Lambda automatically picks up files from `staging/`. The transcription Lambda then processes them. Check status:

```bash
aws lambda invoke --function-name feldenkrais-loader-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /tmp/out.json && cat /tmp/out.json
```

## Environment

Requires these env vars (loaded from `.env`):
- `FM_EMAIL` — login email
- `FM_PASSWORD` — login password
- `KB_BUCKET` — S3 bucket name
- `DATABASE_URL` — PostgreSQL connection string

## Common issues

- **"Login failed"**: Check FM_EMAIL and FM_PASSWORD in `.env`
- **"unauthorized"**: Account may have expired or been deactivated
- **yt-dlp errors**: Update with `uv pip install --upgrade yt-dlp`

## User input

$ARGUMENTS
