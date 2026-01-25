# Private Knowledge Base - Requirements

## Overview

A private knowledge base system that stores documents and their index on S3, with text-to-speech capabilities via Groq. Designed as a Python API for future Lambda integration.

## Core Components

### 1. S3 Storage

```
┌─────────────────────────────────────────────────────────┐
│                      S3 Bucket                          │
│                                                         │
│  db/                                                    │
│  └── knowledge.db ◄─────────────┐  (Standard storage)  │
│                                 │                       │
│  staging/                       │  (Standard - temp)    │
│  └── (upload files here)        │                       │
│                                 │                       │
│  errors/                        │  (Standard - inspect) │
│  └── (failed loads)             │                       │
│                                 │                       │
│  archive/                       │  (Glacier after 30d) │
│  ├── 1.pdf                      │                       │
│  ├── 2.html                     │                       │
│  ├── 3.txt                      │                       │
│  └── ...                        │                       │
└─────────────────────────────────│───────────────────────┘
                                  │
        ┌─────────────────────────┘
        │ s3_key references
        ▼
┌───────────────────────────────────────┐
│           SQLite (knowledge.db)       │
│                                       │
│  document_source ◄──┐                 │
│                     │                 │
│  document_type ◄────┼── document ─────┼──► analysis
│                     │    - id         │      - document_id
│                     │    - title      │      - type_id ◄── analysis_type
│                     │    - source_url │      - data (JSON)
│                     └─── - s3_key ────────► archive/{id}.{ext}
│                          - tags       │
└───────────────────────────────────────┘
```

- **Document Store**: S3 bucket/prefix for storing document copies
- **Database Store**: SQLite database file stored on S3
- Configurable bucket name and prefixes
- Support for AWS credentials via standard methods (env vars, IAM role, config file)

### 2. Database (SQLite)

Relational database (small scale, no contention concerns). Stored on S3, downloaded locally for operations.

#### Enums

**DocumentSource** - Author or origin of the document:
- TBD (e.g., `FELDENKRAIS`, `OTHER`, etc.)

**DocumentType** - Determines which processor handles the document:
- `TEXT` - Articles, transcripts, plain text
- `AUDIO` - Podcasts, audio recordings
- `VIDEO` - YouTube videos, video recordings

**AnalysisType** - Type of analysis performed on a document:
- `TRANSCRIPTION` - Text transcription of audio/video
- `ANNOTATION` - Manual or automated annotations
- TBD (other analysis types as needed)

#### SQL Schema

```sql
CREATE TABLE document_source (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE document_type (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE document (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_url TEXT,
    s3_key TEXT,
    source_id INTEGER NOT NULL REFERENCES document_source(id),
    type_id INTEGER NOT NULL REFERENCES document_type(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tags TEXT  -- JSON array or comma-separated
);

CREATE TABLE analysis_type (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES analysis_type(id),
    data JSON NOT NULL,  -- Analysis content (transcription text, annotations, etc.)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_document_source ON document(source_id);
CREATE INDEX idx_document_type ON document(type_id);
CREATE INDEX idx_analysis_document ON analysis(document_id);
CREATE INDEX idx_analysis_type ON analysis(type_id);
```

### 3. Document Storage (S3 Archive)

- Store copies of source documents in S3
- Most documents sourced from internet
- Preserve original format
- Archive-only: rarely accessed, kept for potential reprocessing when models improve

**Structure**: Flat by document ID
```
s3://bucket/
  ├── db/knowledge.db              (SQLite, Standard storage)
  ├── staging/                     (Standard - temporary uploads)
  ├── errors/                      (Standard - failed loads)
  └── archive/{document_id}.{ext}  (Glacier Flexible after 30d)
```

**Storage Class & Lifecycle**:
- Archive files start in S3 Standard
- Lifecycle rule transitions to Glacier Flexible after 30 days
- Retrieval options for older docs:
  - Standard (3-5 hours) for batch reprocessing
  - Bulk (5-12 hours) for large batches at lower cost

### 4. Python API (Knowledge Base)

- Designed for Lambda integration (stateless operations)
- Sync database to/from S3 as needed
- Core operations:
  - Add document (from URL or content)
  - List documents
  - Get document metadata

---

## Ingestion

Two-phase system: **Ingest** (get files into staging) → **Load** (staging to archive + DB).

```
┌─────────────────────────────────────────────────────────┐
│                       INGEST                            │
│                                                         │
│  URL Fetch:    Lambda fetches ──► staging/              │
│  Direct:       Lambda stores ──► staging/               │
│  Manual:       You upload ──► staging/                  │
│                                                         │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                       LOAD                              │
│                                                         │
│  staging/ ──► Loader Lambda ──► archive/ + DB record    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Staging Structure

Files in staging include a **sidecar JSON** for metadata:

```
staging/
  ├── my-video.mp4
  ├── my-video.mp4.json      ← metadata sidecar
  ├── article.pdf
  └── article.pdf.json
```

**Sidecar JSON format:**
```json
{
  "title": "My Video",
  "source_url": "https://youtube.com/...",
  "source_id": 1,
  "type_id": 3,
  "tags": ["feldenkrais", "lesson"]
}
```

### Phase 1: Ingest (to staging)

**1. URL Fetch**
```
Client ──► Lambda ──► Fetch URL ──► staging/{filename}
                              └──► staging/{filename}.json
```
- Lambda fetches content from URL
- Writes file + sidecar JSON to staging
- Supports: web articles, PDFs, YouTube URLs

**2. Direct Upload (< 6MB)**
```
Client ──► Lambda ──► staging/{filename}
                 └──► staging/{filename}.json
```
- Content + metadata sent in request payload
- Lambda writes both to staging
- Supports: plain text, small documents

**3. Manual Upload (any size)**
```
You ──► S3 staging/ (console, CLI, aws s3 cp)
    ──► Create .json sidecar manually or with script
```
- Upload file via any S3 method
- Create matching `.json` sidecar with metadata
- Supports: large videos, audio files, any size

### Phase 2: Load (staging to archive)

```
Loader Lambda:
  GRACE_PERIOD = 5 minutes

  # Build list of main files (from both .json and non-.json triggers)
  main_files = set()
  for each file in staging/:
      if file ends with .json:
          main_files.add(file without .json suffix)
      else:
          main_files.add(file)

  for each file in main_files:
      log("processing {file}")

      if file missing:
          log("skipping - main file not found (sidecar arrived first)")
          continue

      if sidecar missing:
          if file.age < GRACE_PERIOD:
              log("skipping - no sidecar, within grace period")
              continue
          else:
              log("error - no sidecar after grace period")
              move file to errors/
              continue

      try:
          metadata = parse {file}.json
          document_id = uuid()
          copy file to archive/{document_id}.{ext}
          insert DB record
          delete file + sidecar from staging/
          log("loaded → {document_id}")
      except:
          if file.age < GRACE_PERIOD:
              log("failed - within grace period, will retry")
          else:
              log("error - failed after grace period")
              move file + sidecar to errors/
```

- Triggered by S3 events (any file in staging/) or manually
- **Concurrency = 1**: prevents race conditions when both file + sidecar arrive simultaneously
- Grace period (5 min) allows for slow uploads / dirty reads
- Files only move to `errors/` after grace period expires
- All actions logged

### Lambda Functions

**Ingest:**
1. **ingest-url** - Fetch from URL → staging + sidecar
2. **ingest-direct** - Accept payload → staging + sidecar

**Load:**
3. **load** - Process staging → archive + DB

### Request/Response (draft)

**Ingest URL:**
```json
// Request
{
  "url": "https://example.com/article.pdf",
  "title": "Article Title",
  "source_id": 1,
  "type_id": 1,
  "tags": ["tag1"]
}

// Response
{
  "staging_key": "staging/article.pdf",
  "status": "staged"
}
```

**Ingest Direct:**
```json
// Request
{
  "filename": "notes.txt",
  "content": "base64-encoded-content...",
  "title": "My Notes",
  "source_id": 1,
  "type_id": 1
}

// Response
{
  "staging_key": "staging/notes.txt",
  "status": "staged"
}
```

**Load:**
```json
// Request (process all staged files)
{}

// Response
{
  "loaded": [
    {"staging_key": "staging/article.pdf", "document_id": 123},
    {"staging_key": "staging/notes.txt", "document_id": 124}
  ],
  "errors": []
}
```

---

## Logging

A separate logging system for recording actions and attempted actions across all components.

### Schema

```sql
CREATE TABLE log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    entry JSON NOT NULL  -- Unstructured log data
);

CREATE INDEX idx_log_job_id ON log(job_id);
CREATE INDEX idx_log_timestamp ON log(timestamp);
```

### Storage

TBD - Options:
- Separate SQLite database on S3
- CloudWatch Logs
- Other logging service

---

## Groq Transcription (Audio-to-Text)

Transcribes audio documents using Groq's Whisper API. Uses the **Batch API** for cost savings (50% discount).

### Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      TRANSCRIPTION FLOW                                  │
│                                                                          │
│  1. SELECT untranscribed audio documents from DB                         │
│                           │                                              │
│                           ▼                                              │
│  2. GENERATE pre-signed S3 URLs for each audio file                      │
│                           │                                              │
│                           ▼                                              │
│  3. CREATE batch JSONL file with transcription requests                  │
│                           │                                              │
│                           ▼                                              │
│  4. UPLOAD batch file to Groq                                            │
│                           │                                              │
│                           ▼                                              │
│  5. CREATE batch job (async, 24hr completion window)                     │
│                           │                                              │
│                           ▼                                              │
│  6. POLL for completion (or webhook callback)                            │
│                           │                                              │
│                           ▼                                              │
│  7. DOWNLOAD results, store as analysis records in DB                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Groq API Details

**Endpoint**: `https://api.groq.com/openai/v1/audio/transcriptions`

**Models**:
| Model | Cost | Speed | Accuracy |
|-------|------|-------|----------|
| `whisper-large-v3-turbo` | $0.04/hr | 216x realtime | 12% WER |
| `whisper-large-v3` | $0.111/hr | 189x realtime | 10.3% WER |

**File Limits**:
- Free tier: 25 MB
- Dev tier: 100 MB
- Supported formats: mp3, wav, flac, m4a, ogg, mp4, mpeg, mpga, webm

**Batch API** (50% discount):
- Must use `url` parameter (not file upload)
- JSONL format, max 50,000 requests per batch
- Completion window: 24 hours to 7 days

### Batch Request Format

```jsonl
{"custom_id": "doc-uuid-1", "method": "POST", "url": "/v1/audio/transcriptions", "body": {"model": "whisper-large-v3-turbo", "url": "https://s3.amazonaws.com/bucket/archive/uuid1.mp3?signed...", "response_format": "verbose_json", "language": "en"}}
{"custom_id": "doc-uuid-2", "method": "POST", "url": "/v1/audio/transcriptions", "body": {"model": "whisper-large-v3-turbo", "url": "https://s3.amazonaws.com/bucket/archive/uuid2.mp3?signed...", "response_format": "verbose_json", "language": "en"}}
```

### Batch Response Format

```jsonl
{"id": "...", "custom_id": "doc-uuid-1", "response": {"status_code": 200, "body": {"text": "transcribed text...", "segments": [...], "language": "en"}}}
```

### Job Tracking

Track transcription jobs to handle async workflow and retries:

```sql
CREATE TABLE transcription_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES document(id),
    batch_id TEXT,              -- Groq batch ID
    status TEXT NOT NULL,       -- pending, completed, failed
    attempts INTEGER DEFAULT 1,
    error TEXT,                 -- Error message if failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_transcription_job_document ON transcription_job(document_id);
CREATE INDEX idx_transcription_job_status ON transcription_job(status);
CREATE INDEX idx_transcription_job_batch ON transcription_job(batch_id);
```

**Status values**:
- `pending`: Submitted to Groq, awaiting result
- `completed`: Successfully transcribed, analysis record created
- `failed`: Groq returned error (may retry if under max attempts)

**Query for documents needing transcription**:
```sql
SELECT d.* FROM document d
WHERE d.type_id IN (2, 3)  -- AUDIO, VIDEO
-- No successful transcription yet
AND NOT EXISTS (
    SELECT 1 FROM analysis a
    WHERE a.document_id = d.id AND a.type_id = 1
)
-- Not currently pending
AND NOT EXISTS (
    SELECT 1 FROM transcription_job j
    WHERE j.document_id = d.id AND j.status = 'pending'
)
-- Not permanently failed (under max retries)
AND NOT EXISTS (
    SELECT 1 FROM transcription_job j
    WHERE j.document_id = d.id
    AND j.status = 'failed'
    AND j.attempts >= 3
)
```

### Storage

Transcriptions stored as `analysis` records:
- `document_id`: Reference to source audio document
- `type_id`: TRANSCRIPTION (1)
- `data`: JSON with full transcription result

```json
{
  "text": "Full transcription text...",
  "segments": [
    {"start": 0.0, "end": 5.2, "text": "First segment..."},
    {"start": 5.2, "end": 10.1, "text": "Second segment..."}
  ],
  "model": "whisper-large-v3-turbo",
  "language": "en",
  "batch_id": "batch_abc123",
  "transcribed_at": "2024-01-15T10:30:00Z"
}
```

### Process Components

**1. Batch Creator** (runs on demand or scheduled):
- Query DB for audio documents without transcription analysis
- Generate pre-signed S3 URLs (valid for batch completion window)
- Create JSONL batch file
- Upload to Groq Files API
- Create batch job
- Store batch job ID for tracking

**2. Batch Poller** (scheduled or event-driven):
- Check status of pending batches
- On completion: download results, parse, store analysis records
- Handle failures: retry or mark as failed

### CLI Usage

```bash
# Create a transcription batch for all untranscribed audio
python -m kb.transcribe create-batch

# Check batch status
python -m kb.transcribe status <batch_id>

# Process completed batch results
python -m kb.transcribe process-results <batch_id>

# Transcribe a single file (sync, for testing)
python -m kb.transcribe single <document_id>
```

### Configuration

```
GROQ_API_KEY=...
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
GROQ_BATCH_COMPLETION_WINDOW=24h
GROQ_MAX_RETRIES=3
S3_PRESIGNED_URL_EXPIRY=86400  # 24 hours, must be >= completion window
```

### Lambda Integration (future)

**Option A**: Scheduled Lambda
- Runs daily/hourly
- Creates batches, polls for results

**Option B**: Step Functions
- State machine for batch lifecycle
- Wait states for polling

### Chunking (Large Files)

For files exceeding 100MB (dev tier limit), split into chunks before submission.

**Strategy**: Split by size with small overlap to avoid cutting words.

```
┌─────────────────────────────────────────────────────────────┐
│                    CHUNKING FLOW                             │
│                                                              │
│  Original: 150MB audio file                                  │
│                     │                                        │
│                     ▼                                        │
│  1. CHECK size > 100MB? Yes                                  │
│                     │                                        │
│                     ▼                                        │
│  2. SPLIT using ffmpeg into ~80MB chunks with 5s overlap     │
│     ├── chunk_0.mp3 (0:00 - 1:00:05)                        │
│     ├── chunk_1.mp3 (1:00:00 - 2:00:05)                     │
│     └── chunk_2.mp3 (2:00:00 - end)                         │
│                     │                                        │
│                     ▼                                        │
│  3. SUBMIT each chunk as separate transcription job          │
│                     │                                        │
│                     ▼                                        │
│  4. REASSEMBLE transcriptions:                               │
│     - Adjust timestamps by chunk offset                      │
│     - Deduplicate overlap regions                            │
│     - Concatenate text                                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Chunk tracking** - extend `transcription_job` table:
```sql
ALTER TABLE transcription_job ADD COLUMN chunk_index INTEGER;  -- NULL if not chunked
ALTER TABLE transcription_job ADD COLUMN chunk_offset_seconds REAL;  -- Start time of chunk
ALTER TABLE transcription_job ADD COLUMN total_chunks INTEGER;  -- NULL if not chunked
```

**Reassembly query**:
```sql
-- Get all completed chunks for a document, ordered
SELECT * FROM transcription_job
WHERE document_id = ? AND status = 'completed'
ORDER BY chunk_index;
```

**Reassembly logic**:
```python
def reassemble_chunks(chunks: list[TranscriptionJob]) -> dict:
    full_text = []
    full_segments = []

    for chunk in chunks:
        result = json.loads(chunk.result_data)
        offset = chunk.chunk_offset_seconds

        # Adjust segment timestamps
        for segment in result["segments"]:
            segment["start"] += offset
            segment["end"] += offset

            # Skip segments in overlap region (already covered by previous chunk)
            if chunk.chunk_index > 0 and segment["start"] < offset + OVERLAP_SECONDS:
                continue

            full_segments.append(segment)

        full_text.append(result["text"])

    return {
        "text": " ".join(full_text),
        "segments": full_segments
    }
```

**Configuration**:
```
GROQ_MAX_FILE_SIZE_MB=100    # Dev tier limit
GROQ_CHUNK_SIZE_MB=80        # Target chunk size (with headroom)
GROQ_CHUNK_OVERLAP_SECONDS=5 # Overlap to avoid word cutoffs
```

**ffmpeg command**:
```bash
# Split into 80MB chunks with 5s overlap
ffmpeg -i input.mp3 -f segment -segment_time 3600 -segment_start_number 0 \
  -c copy chunk_%d.mp3
```

### Audio Preprocessing (Optional)

Before transcription, optionally compress to reduce size and avoid chunking:

```bash
# Convert to 64kbps mono MP3 (good for voice)
ffmpeg -i input.mp3 -ac 1 -ab 64k -ar 16000 output.mp3
```

| Original | After compression |
|----------|-------------------|
| 2hr stereo 128kbps (115MB) | 2hr mono 64kbps (58MB) |

This is optional - Groq downsamples to 16kHz mono anyway. But preprocessing locally:
- Reduces upload time to Groq
- Avoids chunking in most cases
- No quality loss for transcription (Whisper doesn't need high bitrate)

**When to preprocess**:
- File > 80MB and not already low bitrate
- High bandwidth cost is a concern

### Error Handling

- **File too large**: Skip, log warning, mark as failed with "file_too_large" error
- **Unsupported format**: Skip, mark as failed with "unsupported_format" error
- **Groq transient failure**: Increment attempts, set status=failed, retry in next batch if under max
- **Groq permanent failure**: Mark as failed, won't retry (e.g., invalid audio)
- **API rate limits**: Back off, retry batch submission

**Retry logic**:
```
MAX_RETRIES = 3

On batch result:
  for each item:
    if success:
      create analysis record
      update job: status=completed
    if failure:
      update job: status=failed, attempts++, error=message
      if attempts < MAX_RETRIES:
        (will be picked up by next batch run)
      else:
        (permanently failed, requires manual intervention)
```

### Testing

**Unit tests** (mocked Groq API):
- Query logic for untranscribed documents
- Job state transitions (pending → completed/failed)
- Retry logic (attempts < max)
- Chunk reassembly with timestamp adjustment

**Integration test** (real Groq, minimal cost):
```python
def test_transcription_batch_of_one():
    # 1. Upload short test audio to S3 (5-10 seconds)
    # 2. Create document record
    # 3. Run batch creator (creates batch of 1)
    # 4. Poll until complete (or timeout)
    # 5. Verify:
    #    - transcription_job.status == 'completed'
    #    - analysis record exists with transcription text
```

**Test audio file**: Include a short spoken audio file in `tests/fixtures/test_audio.mp3` (~10 seconds, ~50KB).

**Cost**: ~$0.001 per test run (10 seconds at $0.02/hr after batch discount).

### Cost Estimation

At $0.04/hr (turbo) with 50% batch discount = $0.02/hr

| Audio Volume | Estimated Cost |
|--------------|----------------|
| 10 hours | $0.20 |
| 100 hours | $2.00 |
| 1000 hours | $20.00 |

---

## Groq TTS Processor

A separate process/module that converts document text to speech. Operates independently from the core knowledge base.

### Process Flow

1. **Input**: Receive document text (from knowledge base or direct input)
2. **Chunk**: Split text into segments within Groq API limits
3. **Process**: Send each chunk to Groq TTS API
4. **Join**: Concatenate audio chunks into final output
5. **Output**: Return or store the complete audio file

### Storage

- Intermediate chunks: Temporary (in-memory or temp files during processing)
- Final audio output: TBD (S3, return to caller, or store reference in knowledge base)

### Schema (if persistent storage needed)

```sql
CREATE TABLE audio_chunk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    audio_data BLOB NOT NULL,
    duration_seconds REAL,
    voice TEXT,
    model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, chunk_index)
);

CREATE INDEX idx_audio_chunk_job ON audio_chunk(job_id);
```

### Configuration

- Groq API credentials
- Voice/model selection
- Chunking strategy (character count, sentences, paragraphs)
- Output format (MP3, WAV, or Groq default)

---

## Testing & Environments

### Design Principles

1. **Infrastructure-as-code** - Use Terraform, CDK, or SAM to define all infrastructure. Enables spinning up/tearing down complete test environments.

2. **Separate business logic from Lambda handlers** - Core logic in pure Python modules, thin Lambda wrappers. Allows unit testing without Lambda runtime.

3. **Configurable bucket/prefix names** - Environment-specific configuration for S3 bucket and prefixes. Test environments use separate buckets or prefixes.

4. **Abstract Groq API** - Interface wrapper around Groq API allows mocking in tests without hitting real API.

### Environments

| Environment | S3 Bucket | Groq API | Purpose |
|-------------|-----------|----------|---------|
| test | `{bucket}-test` | Mocked | Automated tests, CI |
| dev | `{bucket}-dev` | Real | Manual development/debugging |
| prod | `{bucket}` | Real | Production |

### Test Strategy

- **Unit tests**: Pure Python logic, mocked Groq
- **Integration tests**: Real AWS (test bucket), mocked Groq
- **End-to-end tests**: Real AWS (dev bucket), real Groq (limited)

### Local Development

Code should run locally without Lambda, for faster development iteration.

**Config loader:**
- Reads from Lambda environment variables when in Lambda
- Reads from `.env` file or config file when local
- Same config keys either way

**Database modes:**
- **S3 mode** (Lambda/production): Download from S3 → work on /tmp → upload back
- **Local mode** (development): Work directly on local SQLite file, no S3 sync

**Temp directory:**
- Configurable via `TEMP_DIR` (Lambda: `/tmp`, local: `./tmp` or system temp)

**CLI entry points:**
```bash
# Ingest from URL
python -m kb.ingest url "https://example.com/article.pdf" --title "Article"

# Ingest from local file
python -m kb.ingest file ./myfile.pdf --title "My File"

# Run loader
python -m kb.load

# List documents
python -m kb.query list
```

**Required config:**
```
# .env (local) or Lambda env vars
KB_BUCKET=my-bucket
KB_ENV=dev                    # dev | test | prod
KB_DB_MODE=local              # local | s3
KB_DB_PATH=./knowledge.db     # for local mode
GROQ_API_KEY=...
```

---

## AWS Infrastructure

### Decisions

| Setting | Value |
|---------|-------|
| Region | us-east-1 |
| IaC tool | Terraform |
| Python runtime | 3.12 |
| Lambda memory | 512MB |
| Lambda timeout | 2 min |
| Lambda concurrency | Unrestricted (see note) |
| Bucket naming | `feldenkrais-{env}` (e.g., `feldenkrais-dev`, `feldenkrais-prod`) |

### S3 Event Trigger

- Trigger on: `s3:ObjectCreated:*`
- Prefix filter: `staging/`
- Triggers loader Lambda for any file (including `.json` sidecars)

### IAM (Lambda Execution Role)

```
- s3:GetObject      (staging/*, archive/*, db/*)
- s3:PutObject      (archive/*, errors/*, db/*)
- s3:DeleteObject   (staging/*)
- s3:ListBucket     (staging/*)
- logs:CreateLogGroup
- logs:CreateLogStream
- logs:PutLogEvents
```

### Race Condition Handling (TODO)

Lambda concurrency=1 would prevent race conditions when multiple S3 events trigger simultaneously, but requires sufficient account concurrency quota (min 10 unreserved).

**Alternative approaches to implement:**
- DynamoDB-based lock/semaphore
- SQS FIFO queue with deduplication
- Global semaphore pattern in Lambda

For now, race conditions are possible if file + sidecar arrive near-simultaneously. Low risk for single-user system with manual uploads.

---

## Scraper

A utility that takes a URL, discovers media files on the page, and stages them for ingestion.

### Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        SCRAPER                               │
│                                                              │
│  URL ──► Fetch Page ──► Find Media ──► Download ──► staging/ │
│                              │                         │     │
│                              │                         └──► .json sidecar
│                              ▼                               │
│                    ┌─────────────────┐                       │
│                    │ Media Discovery │                       │
│                    ├─────────────────┤                       │
│                    │ • Direct links  │                       │
│                    │ • <audio> tags  │                       │
│                    │ • <video> tags  │                       │
│                    │ • Embedded players                      │
│                    │ • iframe embeds │                       │
│                    └─────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

### Input

```json
{
  "url": "https://example.com/recordings/session-1",
  "source_id": 1,
  "type_id": 2,
  "tags": ["workshop", "2024"],
  "title_override": null
}
```

- **url** (required): Page to scrape for media
- **source_id** (required): Document source enum ID
- **type_id** (required): Document type enum ID (AUDIO=2, VIDEO=3)
- **tags** (optional): Tags to apply to all discovered media
- **title_override** (optional): If set, use this title; otherwise extract from page

### Output

For each media file discovered:
1. Download media file to `staging/{filename}`
2. Create sidecar `staging/{filename}.json` with metadata

The loader Lambda then processes these normally.

### Media Discovery

**Direct Links** - Scan page for links to media files:
- Audio: `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.aac`
- Video: `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`

**HTML5 Media Tags**:
```html
<audio src="...">
<video src="...">
<source src="...">
```

**Squarespace Audio Blocks**:
```html
<div class="sqs-audio-embed" data-url="https://static1.squarespace.com/static/...mp3/original/file.mp3">
```
- Look for elements with class `sqs-audio-embed`
- Extract audio URL from `data-url` attribute (or similar)
- URL pattern: `https://static1.squarespace.com/static/{site-id}/t/{content-id}/{timestamp}/{filename}.mp3/original/{filename}.mp3`

**Other Embedded Players** (future):
- YouTube (`youtube.com`, `youtu.be`) - requires yt-dlp
- Vimeo (`vimeo.com`)
- SoundCloud (`soundcloud.com`)

### Metadata Extraction

For each media file, extract:
- **title**: From page `<title>`, `<h1>`, or filename
- **source_url**: Original page URL (not direct media URL)
- **filename**: Sanitized from URL or page title

### Sidecar Generation

```json
{
  "title": "Session 1 - Workshop Recording",
  "source_url": "https://example.com/recordings/session-1",
  "source_id": 1,
  "type_id": 2,
  "tags": ["workshop", "2024"],
  "scraper_metadata": {
    "media_url": "https://example.com/media/session1.mp3",
    "discovered_at": "2024-01-15T10:30:00Z",
    "page_title": "Session 1 - Workshop Recording"
  }
}
```

### Configuration

```
SCRAPER_USER_AGENT=Mozilla/5.0 ...
SCRAPER_TIMEOUT=30
SCRAPER_MAX_FILE_SIZE=500MB
```

### Authentication (future)

Some sites require login to access media. Options:
- Cookie-based auth (provide cookies via config)
- Session auth (provide credentials, scraper logs in)
- Header auth (provide auth headers)

Not implemented initially - start with public URLs only.

### CLI Usage

```bash
# Scrape a single URL
python -m kb.scrape "https://example.com/recordings/page" \
  --source-id 1 \
  --type-id 2 \
  --tags workshop,2024

# Dry run (list media without downloading)
python -m kb.scrape "https://example.com/..." --dry-run

# Scrape multiple URLs from file
python -m kb.scrape --urls-file urls.txt --source-id 1 --type-id 2
```

### Lambda Integration (future)

Could be exposed as Lambda for API access:
```json
// Request
{
  "url": "https://example.com/recordings/page",
  "source_id": 1,
  "type_id": 2
}

// Response
{
  "staged": [
    {"filename": "session1.mp3", "staging_key": "staging/session1.mp3"},
    {"filename": "session2.mp3", "staging_key": "staging/session2.mp3"}
  ],
  "errors": []
}
```

### Limitations

- No JavaScript rendering (static HTML only)
- No authentication initially
- Single page at a time (no crawling/following links)
- Media must be directly downloadable (no streaming-only content)

### Example Site Analysis: AHSE Convention Recordings

Investigated `associationforhannasomaticeducation.com`:
- Site uses Squarespace with audio embedded via `sqs-audio-embed` blocks
- Audio URLs hosted on `static1.squarespace.com/static/...`
- Convention recordings have clear URL patterns: `/convention-recordings/{presenter}-{date}`
- Multiple audio tracks per page possible

**Scraping approach:**
1. Fetch page HTML
2. Parse for `sqs-audio-embed` elements
3. Extract `data-url` or similar attribute containing MP3 URL
4. Download from Squarespace static CDN

Note: Standard HTML parsing tools may miss these if they convert to markdown first. Need raw HTML parsing (BeautifulSoup, lxml).

---

## Open Questions

1. **Chunking strategy**: By character count, sentences, paragraphs?
2. **Audio format**: MP3, WAV, or Groq default?
3. **Database sync**: Download full DB per request, or keep local cache?
4. **Video transcripts**: Extract text ourselves or expect pre-transcribed input?
5. **DocumentSource values**: What sources/authors to include?
6. **TTS output storage**: Where does final audio go? (S3, ephemeral, linked back to document?)

## Out of Scope (for now)

- Web UI
- Multi-user access control
- Full-text search/RAG
- CLI interface
- Real-time sync
