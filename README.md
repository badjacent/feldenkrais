# Feldenkrais Knowledge Base

A system for scraping, transcribing, analyzing, and publishing Feldenkrais lesson content as structured booklets.

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

## The Book

A Feldenkrais lesson is typically an hour of spoken movement instructions. The goal of this system is to turn each lesson into a rich, multi-perspective *experience* — not just a transcript, but a guide that helps the reader understand what the lesson is doing to them and why.

Each lesson is analyzed through five "Vital Lenses" (VLs), each representing a different point of view on the same movement experience:

| Lens | Name | Sees |
|------|------|------|
| VL-1 | The Riser | Weight, gravity, and the ground's upward push through the skeleton |
| VL-2 | The Cartographer | Body mapping — what regions are detailed vs. blurred |
| VL-3 | The Tuner | Signal quality between intention and action |
| VL-4 | The Engine | Power flow from center to periphery through the kinetic chain |
| VL-5 | The Soft Front | The state of the front body — open or defended, armored or soft |

The five analyses are synthesized and compiled into a **booklet** for each lesson. Booklets are then assembled into the **book**.

## Publishing Pipeline

The publishing pipeline analyzes a transcribed lesson through all five VL frameworks and renders a booklet.

```
lesson transcription
        |
        v
   Analysis A
   (segmentation, classification, SPIFFER-Q decomposition)
        |
        v
   Analysis B  ×5 in parallel
   (one per VL framework: somatic narrative + analytical table)
        |
        v
   Cross-Query Enrichment  ×5 in parallel
   (each VL reviews peer analyses, asks focused follow-ups)
        |
        v
   VL Synthesis
   (merges all 5 enriched analyses)
        |
        v
   Booklet Template
   (renders HTML with unfilled query blocks using primary VL)
        |
        v
   Booklet
   (LLM fills query blocks via QueryAgent)
        |
        v
   Book Assembly
```

### Key files

| File | Role |
|------|------|
| `src/kb/pipeline_graph.py` | Pipeline DAG definition (source nodes, transforms, dependencies) |
| `src/kb/pipeline.py` | Pipeline runner (hash-based change detection, scheduling) |
| `src/kb/web.py` | Analysis passes, booklet rendering, web UI |
| `src/kb/query_agent.py` | QueryAgent that fills LLM query blocks in booklet HTML |
| `src/kb/cross_query.py` | Cross-query enrichment pass |
| `data/frameworks/v1/` | VL framework prompts (`vl1.md`–`vl5.md`) and metadata (`frameworks.json`) |
| `data/pass_a.schema.json` | JSON schema for Analysis A output |
| `data/pass_b.schema.json` | JSON schema for Analysis B output |

### Running the pipeline

```bash
.venv/bin/python src/kb/pipeline.py
```

The pipeline uses hash-based change detection — nodes only re-run when their inputs change.

## Database

Neon PostgreSQL (connection string in `.env` as `DATABASE_URL`). Key tables:

- **document**: Media files with metadata (title, source_url, s3_key)
- **analysis**: Transcriptions linked to documents
- **transcription_job**: Tracks async transcription jobs
- **cache**: Pipeline results, booklet HTML, and pipeline coordination state

## Audio Processing

Large audio files (>20MB) are automatically:
1. Downloaded from S3
2. Split into 20-minute chunks using ffmpeg
3. Transcribed chunk by chunk via Groq Whisper API
4. Reassembled with adjusted timestamps
