# search.aionyourside.net — Technical Summary

## Overview

Public semantic search interface for Feldenkrais lessons. Same Lambda as the main site (`aionyourside.net`), differentiated by Host header middleware. Only lessons from sources marked `public=TRUE` are visible.

Currently: **67 lessons** from FELDENKRAIS_PROJECT_FREE (source ID 12).

## Architecture

```
User → Route 53 (search.aionyourside.net)
     → API Gateway custom domain
     → Same Lambda as main site
     → Middleware detects Host header, sets public_only=True
     → Two pages: search home (/) and lesson detail (/lesson/{id})
```

Single Lambda, single database, single embedding index. No separate deployment.

## Pages

### Home (`/`)
- Semantic search box → `POST /api/search`
- Three featured lesson cards (hardcoded document IDs)
- Browse section: all public lessons listed by source, linked to `/lesson/{id}`
- Example queries as clickable links

### Lesson Detail (`/lesson/{document_id}`)
- Server-rendered HTML shell, client JS loads data from APIs
- Three tabs: **Outline** (condensed cards), **Cards** (step-by-step navigator), **Detailed** (full grouped view)
- Audio player with word-level transcript highlighting (Whisper alignment)
- Collapsible chat panel with context checkboxes
- Auto-loads all analysis on page open (no Run button)

## Search Pipeline

### Indexing (offline)
1. Pass0b analysis produces structured step-level biomechanical data (somatic, language, condensed)
2. `build_summary_from_condensed()` flattens condensed cards into plain text: title + settings + group labels/actions/body descriptions
3. Text → Gemini `gemini-embedding-001` (1536-dim, task_type=`RETRIEVAL_DOCUMENT`)
4. Stored in `lesson_embedding` table (pgvector, HNSW index with cosine similarity)
5. SHA-256 hash of summary text enables incremental rebuilds (skip unchanged)

```bash
.venv/bin/python src/kb/build_embeddings.py --apply        # incremental
.venv/bin/python src/kb/build_embeddings.py --apply --force # full rebuild
```

### Query (real-time)
1. User query → Gemini embedding (task_type=`RETRIEVAL_QUERY`)
2. pgvector `<=>` cosine distance search, top 10 results
3. Specificity band calculated from gap between rank-1 and rank-5 similarity:
   - **low** (gap < 0.0167): many equally-good matches
   - **mid** (0.0167–0.0215): several good matches
   - **mid-high** (0.0215–0.0297): top result stands out
   - **high** (≥ 0.0297): clear best answer
4. Query logged to `search_query_log` (query, result count, band, top-5 results)

Thresholds calibrated on 1788 eval queries across 600 lessons.

## Routing & Middleware

**Detection** (`web.py`):
```python
SEARCH_DOMAIN = os.environ.get("SEARCH_DOMAIN", "")  # "search.aionyourside.net"

def _is_search_site(request):
    return host == SEARCH_DOMAIN or host.startswith(SEARCH_DOMAIN + ":")
```

**Guard middleware** — on search subdomain, only allows:
- `/` (home)
- `/lesson/*` (detail pages)
- `/api/*` (all API endpoints)

Everything else redirects to `/`. Main site routes (pass0b, book, analyzer, explore) are inaccessible.

**Data filtering** — `Database.public_only` flag gates all queries:
- `search_lessons()`: joins `document_source`, filters `WHERE ds.public = TRUE`
- `get_embedding_hashes()`: same filter
- `get_docs_with_condensed()`: same filter
- `get_available_lessons()`: same filter

## Database Schema

```sql
-- Embedding storage (pgvector)
CREATE TABLE lesson_embedding (
    document_id UUID PRIMARY KEY REFERENCES document(id) ON DELETE CASCADE,
    embedding vector(1536),
    summary_text TEXT,
    summary_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_lesson_embedding_hnsw ON lesson_embedding
    USING hnsw (embedding vector_cosine_ops);

-- Query analytics
CREATE TABLE search_query_log (
    id SERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    result_count INTEGER,
    specificity_band TEXT,
    specificity_gap REAL,
    top_results JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Source visibility
ALTER TABLE document_source ADD COLUMN public BOOLEAN NOT NULL DEFAULT TRUE;
```

## Infrastructure (Terraform)

All conditional on `var.search_domain_name != ""`:

| Resource | Purpose |
|----------|---------|
| `aws_acm_certificate.search` | TLS cert for search subdomain |
| `aws_route53_record.search_cert_validation` | DNS validation |
| `aws_apigatewayv2_domain_name.search` | Custom domain → API Gateway |
| `aws_apigatewayv2_api_mapping.search` | Maps domain to existing web API |
| `aws_route53_record.search` | A record alias to API Gateway |

Lambda env var: `SEARCH_DOMAIN=search.aionyourside.net`

## Key API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search` | POST | Semantic search (form field: `query`) |
| `/api/lesson-info/{id}` | GET | Title, source, has_audio, transcript |
| `/api/audio-url/{id}` | GET | Presigned S3 URL (2hr TTL) |
| `/api/whisper-segments/{id}` | GET | Transcript segments with timestamps |
| `/api/whisper-words/{id}` | GET | Word-level timestamps for highlighting |
| `/api/pass0b-combined-json` | POST | Step-level biomechanical analysis |
| `/api/pass0b-condensed` | POST | Condensed card view |
| `/api/pass0b-word-lang-json` | POST | Word-level language annotations |

## Key Files

| File | Role |
|------|------|
| `src/kb/web.py` | FastAPI app, all routes, page builders, middleware |
| `src/kb/database.py` | DB layer, `search_lessons()`, `public_only` filtering |
| `src/kb/build_embeddings.py` | Offline embedding builder CLI |
| `src/kb/cache_helpers.py` | `build_summary_from_condensed()` |
| `src/kb/llm.py` | `get_embeddings()` — Gemini embedding API |
| `src/kb/run_search_eval.py` | Search quality evaluation harness |
| `terraform/main.tf` | Infrastructure (Route 53, ACM, API Gateway) |
