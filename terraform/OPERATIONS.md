# Operations Guide

## Prerequisites

All `terraform apply` commands require environment variables from `.env`. Load them first:
```bash
cd terraform
export $(grep -v '^#' ../.env | xargs)
```

**Important:** `source .env` does NOT export variables. You must use the `export` command above, or the Groq/DB/API keys will be empty and Lambdas will be silently skipped or misconfigured.

## Scheduled Lambda Triggers

All Lambda functions can be triggered on a schedule via EventBridge. These schedules can be enabled/disabled via the `enable_scheduled_triggers` variable.

### Current Schedules (when enabled)
- **Loader**: Every 1 minute - processes files in staging/
- **Transcription**: Every 1 minute - creates batches and processes results
- **YouTube**: Every 5 minutes - processes YouTube queue

### Disable All Schedules
```bash
cd terraform
terraform apply -var="enable_scheduled_triggers=false" \
  -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

### Re-enable All Schedules
```bash
cd terraform
terraform apply -var="enable_scheduled_triggers=true" \
  -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

Or simply omit the variable (defaults to true):
```bash
cd terraform
terraform apply -var="environment=prod" \
  -var="groq_api_key=$GROQ_API_KEY" \
  -var="database_url=$DATABASE_URL"
```

## Manual Lambda Invocation

Lambdas can still be invoked manually when schedules are disabled:

### Loader
```bash
aws lambda invoke --function-name feldenkrais-loader-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /tmp/out.json
```

### Transcription
```bash
# Process completed batches and create new ones
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "create_batch"}' /tmp/out.json

# Retry failed jobs
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "retry_failed"}' /tmp/out.json

# Process specific batch results
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "process_results", "batch_id": "batch_xxx"}' /tmp/out.json
```

### YouTube
```bash
# Process queue
aws lambda invoke --function-name feldenkrais-youtube-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "process", "limit": 5}' /tmp/out.json

# Add URL to queue
aws lambda invoke --function-name feldenkrais-youtube-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action": "add", "url": "https://youtube.com/watch?v=xxx"}' /tmp/out.json
```

## FM Loader (Feldenkrais Manhattan)

Local CLI tool to scrape and stage videos from the Feldenkrais Manhattan training site.

### Setup
Requires environment variables (or `.env` file):
- `FM_EMAIL` - Login email
- `FM_PASSWORD` - Login password
- `KB_BUCKET` - S3 bucket name
- `DATABASE_URL` - PostgreSQL connection string

### Usage

**Check for new videos (no upload):**
```bash
uv run python src/kb/fm_loader.py --scrape-only
```

**Process new videos (download from YouTube):**
```bash
uv run python src/kb/fm_loader.py --download-missing
```

**Process with local files (if available):**
```bash
uv run python src/kb/fm_loader.py --local-dir /path/to/videos
```

**Skip YouTube metadata fetch (faster):**
```bash
uv run python src/kb/fm_loader.py --download-missing --skip-metadata
```

**Limit number of videos to process:**
```bash
uv run python src/kb/fm_loader.py --download-missing --limit 5
```

### How It Works
1. Logs into Feldenkrais Manhattan site
2. Scrapes course page for lesson URLs (caches youtube IDs in database)
3. Skips already-completed lessons
4. For pending lessons:
   - Uses local files if `--local-dir` provided
   - Downloads via yt-dlp if `--download-missing`
5. Stages files to S3 `staging/` folder with metadata sidecar
6. Loader Lambda picks up staged files and creates documents

## Web Lambda (Lesson Analyzer)

Public web interface for analyzing Feldenkrais lessons.

### First-Time Setup

1. Build the dependencies layer:
```bash
cd terraform
./build_web_layer.sh
```

2. Deploy with API keys:
```bash
terraform apply \
  -var="environment=prod" \
  -var="google_api_key=$GOOGLE_API_KEY" \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="openai_api_key=$OPENAI_API_KEY" \
  -var="database_url=$DATABASE_URL" \
  -var="groq_api_key=$GROQ_API_KEY"
```

3. Get the public URL:
```bash
terraform output web_function_url
```

### Updating the Web App

After modifying `web.py`, `llm.py`, `schemas.py`, or data files:
```bash
cd terraform
terraform apply -var="environment=prod" \
  -var="google_api_key=$GOOGLE_API_KEY" \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="openai_api_key=$OPENAI_API_KEY" \
  -var="database_url=$DATABASE_URL" \
  -var="groq_api_key=$GROQ_API_KEY"
```

If dependencies changed (new packages in `lambda_web_requirements.txt`), rebuild the layer first:
```bash
./build_web_layer.sh
```

### Framework Versions

Analysis prompts live in versioned directories under `data/frameworks/`:
```
data/
  frameworks.json              ← pointer: {"current_version": "v1"}
  frameworks/
    v1/                        ← prompt files for v1
      pass_a.md, pass_b.md, vl1-5.md, frameworks.json
    v2/                        ← prompt files for v2 (if exists)
      ...
  pass_a.schema.json           ← shared schemas (unversioned)
  pass_b.schema.json
  pass_b_narrative.schema.json
  pass_b_table.schema.json
```

**To create a new version:**
1. Copy the current version directory:
   ```bash
   cp -r data/frameworks/v1 data/frameworks/v2
   ```
2. Edit the prompt files in `data/frameworks/v2/`
3. The web UI has a "Framework" dropdown to select which version to analyze with

**To change the default version:**
Edit `data/frameworks.json`:
```json
{"current_version": "v2"}
```

**Cache behavior:**
- v1 uses existing cache keys (no version suffix) — backwards compatible
- v2+ adds `:fv2` etc. to cache keys, so results cache separately
- Switching versions never invalidates existing cached analyses

**Deploying new versions to Lambda:**
1. Copy files to `terraform/data/frameworks/` to match
2. Add new source blocks in `main.tf` for the new version's files
3. Redeploy

### Adding New Lesson Files

1. Add `.txt` files to `data/lessons/`
2. Add them to the `archive_file "web_lambda"` block in `main.tf`
3. Redeploy

### Custom Domain

The domain `aionyourside.net` is configured via Route 53 + ACM + API Gateway:
- ACM certificate with DNS validation (auto-created Route 53 CNAME records)
- API Gateway custom domain name (regional endpoint)
- Route 53 A alias record pointing to the API Gateway

Gated behind `var.domain_name`. To deploy:
```bash
terraform apply \
  -var="domain_name=aionyourside.net" \
  -var="environment=prod" \
  -var="google_api_key=$GOOGLE_API_KEY" \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="openai_api_key=$OPENAI_API_KEY" \
  -var="database_url=$DATABASE_URL" \
  -var="groq_api_key=$GROQ_API_KEY"
```

To skip custom domain setup, omit the domain_name variable or set it to empty string.

**Note:** The domain must have completed ICANN email verification (check the email sent during Route 53 registration).

## Continuous Deployment (CD)

All Lambda functions and infrastructure auto-deploy on push to `master` via GitHub Actions.

### How It Works

```
push to master
  → GitHub Actions (.github/workflows/deploy.yml)
    → OIDC → assume AWS role (no stored AWS keys)
    → Build dependency layers (cached by requirements hash)
    → terraform apply (state in S3 + DynamoDB lock)
```

- **Auth**: GitHub OIDC federation — no long-lived AWS credentials stored in GitHub
- **State**: S3 bucket `feldenkrais-terraform-state` with DynamoDB locking (`feldenkrais-terraform-lock`)
- **Secrets**: API keys passed via `TF_VAR_` environment variables from GitHub Secrets

### GitHub Secrets Required

Set in repo settings (`badjacent/feldenkrais` → Settings → Secrets → Actions):

| Secret | Description |
|--------|-------------|
| `AWS_ROLE_ARN` | ARN of the `feldenkrais-github-deploy` IAM role |
| `GROQ_API_KEY` | Groq API key for transcription |
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `GOOGLE_API_KEY` | Google/Gemini API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |

### Bootstrap (One-Time Setup)

The CI/CD prerequisites (S3 bucket, DynamoDB table, OIDC provider, IAM role) are managed in `terraform/ci/`:

```bash
cd terraform/ci
terraform init
terraform apply
```

After bootstrap, migrate existing state to S3:
```bash
cd terraform
terraform init -migrate-state
```

### Dependency Layer Caching

The workflow caches `lambda_deps.zip` and `lambda_web_deps.zip` based on the hash of their respective requirements files. Layers only rebuild when requirements change.

### Manual Deploy

If you need to deploy manually (e.g., to test a branch), the same commands still work:
```bash
cd terraform
export $(grep -v '^#' ../.env | xargs)
terraform apply -var="environment=prod" -var="domain_name=aionyourside.net"
```
