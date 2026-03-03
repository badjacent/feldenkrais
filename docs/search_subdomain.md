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

Single Lambda, single database, multiple embedding indexes. No separate deployment.

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

Two embedding indexes coexist in the `lesson_embedding` table, distinguished by a `method` column:

**Default index** (`method='default'`) — all lessons:
1. Pass0b condensed analysis flattened to plain text: title + settings + group labels/actions/body descriptions
2. Text → Gemini `text-embedding-004` (1536-dim, task_type=`RETRIEVAL_DOCUMENT`)

```bash
.venv/bin/python src/kb/build_embeddings.py --apply
```

**Concepts index** (`method='concepts'`) — lessons with concept extraction:
1. Concept extraction pass (`pass_0b_concepts`) produces a 200-400 word distinctive report: position inventory, movement sequences, key body connections, what makes the lesson unique
2. Also extracts structured concept tags (body regions, movement patterns, positions, teaching strategies) and search terms
3. Distinctive report → Gemini embedding
4. Concept tags stored in `lesson_concept` table for direct text matching

```bash
.venv/bin/python src/kb/search/run_concept_extraction.py --apply   # LLM extraction
.venv/bin/python src/kb/search/build_concept_index.py --apply       # embed + populate tags
```

Both indexes use SHA-256 hashes for incremental rebuilds.

### Query (real-time) — Cascade Search

The default search method is **Combined**, a cascade that uses multiple signals:

1. User query → Gemini embedding (task_type=`RETRIEVAL_QUERY`)
2. Run both default and concepts embedding searches (top 20 each)
3. Compute confidence gate: similarity gap (rank 1 - rank 5) from concepts results
4. **If gap ≥ 0.07** ("confident"): the concepts index has a clear best match — use its results directly
5. **If gap < 0.07** ("blended"): results are ambiguous — merge default + concepts via Reciprocal Rank Fusion (RRF) with 3x concepts weight
6. Specificity band calculated from concepts similarity gap:
   - **low** (gap < 0.0167): many equally-good matches
   - **mid** (0.0167–0.0215): several good matches
   - **mid-high** (0.0215–0.0297): top result stands out
   - **high** (≥ 0.0297): clear best answer
7. Query logged to `search_query_log`

The cascade parameters (gate threshold, RRF weights) were optimized via grid search over 129 eval queries. The optimizer (`optimize_combined.py`) is re-run when the lesson set or eval questions change.

### Search Methods Available

Users can select from four methods via a dropdown:

| Method | How it works |
|--------|-------------|
| **Combined** (default) | Cascade: confident → concepts alone, ambiguous → RRF blend |
| **Standard** | Cosine search against default embeddings (all lessons) |
| **Distinctive** | Cosine search against concept embeddings (extracted lessons) |
| **Tag Match** | Direct text matching on concept tags, ranked by match count |

### Evaluation

Search quality is measured with `run_search_eval.py` against curated question sets. Current results on the 43-lesson FP Free reference set (129 queries):

| Method | P@1 | P@5 | MRR | Found |
|--------|-----|-----|-----|-------|
| Standard | 34.1% | 58.9% | 0.451 | 72.9% |
| Distinctive | 41.9% | 78.3% | 0.565 | 86.8% |
| **Combined (cascade)** | **44.2%** | **81.4%** | **0.601** | **90.7%** |

```bash
.venv/bin/python src/kb/search/run_search_eval.py --questions data/search_eval_fp_free.json --method combined
.venv/bin/python src/kb/search/optimize_combined.py --output data/combined_optimization.json
```

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
-- Embedding storage (pgvector) — multi-method via composite PK
CREATE TABLE lesson_embedding (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'default',
    embedding vector(1536),
    summary_text TEXT,
    summary_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (document_id, method)
);
CREATE INDEX idx_lesson_embedding_hnsw ON lesson_embedding
    USING hnsw (embedding vector_cosine_ops);

-- Concept tags for tag-based search
CREATE TABLE lesson_concept (
    document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (document_id, category, value)
);
CREATE INDEX idx_lesson_concept_value ON lesson_concept (value);
CREATE INDEX idx_lesson_concept_category ON lesson_concept (category, value);

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
| `src/kb/web.py` | FastAPI app, all routes, cascade search, page builders |
| `src/kb/database.py` | DB layer, `search_lessons()`, `search_by_concepts()`, `public_only` filtering |
| `src/kb/analysis.py` | Analysis engine including `run_pass_0b_concepts()` |
| `src/kb/build_embeddings.py` | Default embedding builder CLI |
| `src/kb/search/run_concept_extraction.py` | Batch concept extraction (LLM pass) |
| `src/kb/search/build_concept_index.py` | Concept embedding + tag table builder |
| `src/kb/search/run_search_eval.py` | Search quality evaluation harness |
| `src/kb/search/generate_search_eval.py` | LLM-generated eval questions from condensed cards |
| `src/kb/search/optimize_combined.py` | Grid search optimizer for cascade parameters |
| `src/kb/llm.py` | `get_embeddings()` — Gemini embedding API |
| `terraform/main.tf` | Infrastructure (Route 53, ACM, API Gateway) |
