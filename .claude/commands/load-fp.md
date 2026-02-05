Scrape and stage audio lessons from feldenkraisproject.com.

## What this does

Downloads audio from feldenkraisproject.com lesson pages and stages them to S3 for the transcription pipeline to pick up.

## Authentication

Cookies are at `~/.feldenkrais_cookies.json`. If scraping fails with a paywall or auth error, the cookies have probably expired. Tell the user to:

1. Log in at feldenkraisproject.com in Safari
2. Dev Tools (Cmd+Opt+I) → Storage → Cookies
3. Copy `wordpress_logged_in_*` and `wordpress_sec_*` values
4. Update `~/.feldenkrais_cookies.json`

## How to run

The user will provide one or more feldenkraisproject.com URLs. Use this script:

```bash
# Dry run first (checks auth, extracts audio URL, no download):
cd /Users/michaelhollander/github/feldenkrais
uv run python src/kb/feldenkrais_project_scraper.py --dry-run "URL"

# Full run (downloads and stages to S3):
uv run python src/kb/feldenkrais_project_scraper.py --source-id 4 --type-id 2 "URL"
```

- `--source-id 4` = FELDENKRAIS_PROJECT (from document_source table)
- `--type-id 2` = AUDIO (from document_type table)
- `--tags "tag1,tag2"` = optional comma-separated tags
- `--bucket BUCKET` = override KB_BUCKET env var

For multiple URLs, run them sequentially.

## After staging

Once staged, the loader Lambda will automatically pick up files from `staging/` and create documents. The transcription Lambda will then process them. You can check status with:

```bash
aws lambda invoke --function-name feldenkrais-loader-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /tmp/out.json && cat /tmp/out.json
```

## Common issues

- **"No audio URL found"**: Cookies expired or insufficient access level. Update cookies.
- **"Cookies file not found"**: Create `~/.feldenkrais_cookies.json` with the wordpress cookies.
- **"Paywall detected"**: Same as expired cookies — re-authenticate.

## User input

$ARGUMENTS
