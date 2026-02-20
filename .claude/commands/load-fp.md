Scrape and stage audio lessons from feldenkraisproject.com.

## What this does

Downloads audio from feldenkraisproject.com lesson pages and stages them to S3 for the transcription pipeline to pick up.

## Authentication

Cookies are at `~/.feldenkrais_cookies.json`. Before running the scraper, ALWAYS ask the user to paste fresh cookies from their browser. Use AskUserQuestion:

> "Paste your feldenkraisproject.com cookies from browser DevTools (Storage → Cookies). Select the wordpress_logged_in_* and wordpress_sec_* rows and paste them."

The pasted text will be tab-separated columns from DevTools, like:
```
wordpress_logged_in_abc123    user%40email.com%7C...    feldenkraisproject.com    /    Session    197 B    ✓    ✓
wordpress_sec_abc123    user%40email.com%7C...    feldenkraisproject.com    /wp-content/plugins    Session    191 B    ✓    ✓
```

Parse this by splitting each line on tabs — column 0 is the cookie name, column 1 is the value. Extract only rows whose name starts with `wordpress_logged_in_` or `wordpress_sec_`. Write the result as JSON to `~/.feldenkrais_cookies.json`:

```json
{
  "wordpress_logged_in_abc123": "user%40email.com%7C...",
  "wordpress_sec_abc123": "user%40email.com%7C..."
}
```

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
