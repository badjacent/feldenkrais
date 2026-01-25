# Feldenkrais Knowledge Base

A serverless system for scraping, storing, and transcribing audio/video content.

## Architecture

```
                                    +------------------+
                                    |   Web Page       |
                                    +--------+---------+
                                             |
                                             v
+------------------+              +----------+---------+
|  Manual Invoke   | -----------> |  Scraper Lambda    |
+------------------+              |  - Discovers media |
                                  |  - Downloads files |
                                  +----------+---------+
                                             |
                                             v
                                  +----------+---------+
                                  |  S3: staging/      |
                                  |  - media files     |
                                  |  - sidecar JSON    |
                                  +----------+---------+
                                             |
                                             | (S3 Event Trigger)
                                             v
                                  +----------+---------+
                                  |  Loader Lambda     |
                                  |  - Validates files |
                                  |  - Creates records |
                                  |  - Moves to archive|
                                  +----------+---------+
                                             |
                            +----------------+----------------+
                            |                                 |
                            v                                 v
                 +----------+---------+            +----------+---------+
                 |  S3: archive/      |            |  S3: db/           |
                 |  - UUID-named files|            |  - knowledge.db    |
                 +--------------------+            +----------+---------+
                                                              |
                                                              |
+------------------+                               +----------+---------+
|  EventBridge     | ----------------------------> | Transcription      |
|  (every minute)  |                               | Lambda             |
+------------------+                               |  - Chunks audio    |
                                                   |  - Calls Groq API  |
                                                   |  - Saves to DB     |
                                                   +--------------------+
```

## Lambdas

| Lambda | Trigger | Description |
|--------|---------|-------------|
| `feldenkrais-scraper-{env}` | Manual | Scrapes URLs for media, stages to S3 |
| `feldenkrais-loader-{env}` | S3 staging/ upload | Processes files, creates DB records |
| `feldenkrais-transcription-{env}` | Scheduled (1 min) | Transcribes audio using Groq Whisper |

## Usage

### Scrape a page

```bash
# Dry run (preview without downloading)
aws lambda invoke --function-name feldenkrais-scraper-prod \
  --payload '{"url":"https://example.com/page","dry_run":true}' \
  --cli-binary-format raw-in-base64-out /tmp/result.json

# Actually scrape
aws lambda invoke --function-name feldenkrais-scraper-prod \
  --payload '{"url":"https://example.com/page"}' \
  --cli-binary-format raw-in-base64-out /tmp/result.json
```

### Manually trigger transcription

```bash
aws lambda invoke --function-name feldenkrais-transcription-prod \
  --payload '{"action":"transcribe_pending"}' \
  --cli-binary-format raw-in-base64-out /tmp/result.json
```

### Check logs

```bash
aws logs tail /aws/lambda/feldenkrais-transcription-prod --since 10m
```

## Deployment

### Prerequisites

- AWS CLI configured
- Terraform >= 1.0
- Python 3.12
- ffmpeg Lambda layer deployed

### Deploy

```bash
cd terraform

# Build Python dependencies layer
./build_deps_layer.sh

# Deploy (first time - creates ffmpeg layer via serverless repo)
terraform apply \
  -var="environment=prod" \
  -var="groq_api_key=YOUR_KEY" \
  -var="ffmpeg_layer_arn=arn:aws:lambda:us-east-1:ACCOUNT:layer:ffmpeg:1"
```

## Database Schema

The SQLite database (`knowledge.db`) contains:

- **document**: Media files with metadata (title, source_url, s3_key)
- **analysis**: Transcriptions linked to documents
- **transcription_job**: Tracks async transcription jobs

## Audio Processing

Large audio files (>20MB) are automatically:
1. Downloaded from S3
2. Split into 20-minute chunks using ffmpeg
3. Transcribed chunk by chunk via Groq Whisper API
4. Reassembled with adjusted timestamps
