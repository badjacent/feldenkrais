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
