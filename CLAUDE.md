# Claude Code Context

## Project Overview

Feldenkrais lesson analysis system with:
- Knowledge base pipeline (transcription, analysis, storage)
- LLM abstraction layer supporting Anthropic, OpenAI, and Gemini
- Web interface for structured lesson analysis
- Book pipeline (per-lesson analysis, booklets, cross-query synthesis)

## Public/Private Repo Split

Most code is in the **public** repo. The **private** submodule at `private/` contains only paywalled content scrapers, the Chrome capture extension, lesson text files, and framework prompt data.

- **Public repo** (`feldenkrais`): All infrastructure, web app, book pipeline, analysis engine, tests, terraform, discovery tools
- **Private submodule** (`private/`): Scrapers for paywalled sources (FM, FP, Kinesophics, etc.), Chrome extension for FF capture, lesson text files, framework prompts (VL v1)

### What's in the private submodule
- Scrapers: `src/feldenkrais_manhattan_scraper.py`, `src/feldenkrais_project_scraper.py`, `src/kinesophics_scraper.py`, etc.
- Chrome extension: `tools/extension/` (captures from Feldenkrais First)
- Capture server: `tools/server.py`
- Lesson texts: `data/lessons/*.txt`
- Framework prompts: `data/frameworks/v1/vl1.md` through `vl5.md`

### Working with the submodule
```bash
# Clone with private content:
git clone --recurse-submodules git@github.com:badjacent/feldenkrais.git

# Or add submodule after cloning:
git submodule update --init

# After pulling, update submodule:
git submodule update --remote
```

### How the split works
- `web.py` conditionally imports `web_book.py` (try/except ImportError) for book features
- `analysis.py` checks `private/data/` first via `_resolve_data_path()`, falls back to public `data/`
- Without the submodule: all features work except VL framework prompts need to be in public `data/`
- Framework prompt files (`vl1.md`-`vl5.md`) live in `private/data/frameworks/v1/`

## Key Documentation

### Protocol & Schema Management
See `data/PROTOCOL_SCHEMA_GUIDE.md` for:
- How to manage analysis protocols and their JSON schemas
- File pairing convention (`.md` + `.schema.json`)
- How to archive, update, and export schemas

### Operations
See `terraform/OPERATIONS.md` for:
- Lambda trigger management
- FM loader usage

## Code Structure

### Core packages (src/kb/)
- `web.py` — FastAPI app, middleware, shared state, HTML builders
- `routes/` — Route modules (auth, ingestion, analysis, pass0b, search, finder, admin, pages)
- `db/` — Database mixin package (17 domain modules: documents, cache, auth, embeddings, pipeline_state, etc.)
- `analysis/` — Analysis package (12 modules: core, pass0, pass_a, pass0b variants, framework builders)
- `llm.py` — LLM abstraction (Anthropic, OpenAI, Gemini)
- `agent.py` — Agent with LLM-powered transform layer
- `auth.py` — OAuth + cookie sessions

### Book pipeline (src/kb/)
- `web_book.py` — Book/booklet/analyzer/explore routes
- `pipeline_graph.py` — Per-lesson pipeline DAG
- `cross_query.py` — Cross-lesson queries with cache
- `critics.py` — Multi-critic review architecture
- `review_agent.py` — Automated book improvement agent
- `query_agent.py` — LLM-powered lesson exploration

### Workflow packages (src/kb/ subdirectories)
- `pipeline/` — DAG engine (`engine.py`) + batch CLI (`batch.py`)
- `ingest/` — Lambda handlers: loader, transcribe, youtube, scraper + load_fp_free CLI
- `search/` — Embeddings, search eval, judge, search doc iteration
- `ops/` — Monitoring scripts

### Tests
- `tests/unit/` — 184 unit tests covering database, analysis, web, LLM, pipeline engine
- `tests/integration/` — Integration tests for loader, scraper, transcription

## VL Framework Data (Single Source of Truth)

**All VL framework metadata lives in `private/data/frameworks/{version}/frameworks.json`.**

This includes:
- Framework names, subtitles, focus areas
- Person Inside names and descriptions (The Riser, The Cartographer, etc.)
- Shadow names and descriptions
- Lens descriptions for booklets

**DO NOT** create or use separate .md files for VL prose descriptions. The following files in `private/data/archive/reference_docs/` are historical reference only and are NOT used by the code:
- `new_vl.md`, `new_vl2.md` - Old framework notes
- `council_description.md` - Council of Five prose (now in frameworks.json)
- `core_protocol.md`, `router.md`, `arch.md` - Old architecture docs

The `vl1.md` through `vl5.md` files in `private/data/frameworks/{version}/` ARE used - these are LLM prompts for analysis, separate from the UI prose in frameworks.json.

## Running the Web Interface

```bash
cd src/kb
uvicorn web:app --reload
```

Requires `.env` with `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY` depending on provider.

## Database

Uses Neon PostgreSQL. Connection string in `.env` as `DATABASE_URL`.

Database class is a mixin assembly in `src/kb/db/` — 17 domain modules (documents, cache, auth, embeddings, pipeline_state, lesson_groups, dashboard, etc.). `src/kb/database.py` is a thin re-export for backward compatibility.

## Deploying

```bash
cd terraform
./gen_tfvars.sh   # Generate terraform.tfvars from .env
terraform apply   # Auto-loads terraform.tfvars
```

Or first time: `./build_web_layer.sh` to build the deps layer.

## Practical Notes

### Package Management
- Uses `uv` (not pip directly): `uv pip install <package>`
- Virtual environment at `.venv/`

### Model Names (easy to get wrong)
- Anthropic: `claude-3-5-haiku-20241022`, `claude-sonnet-4-20250514` (NOT `claude-haiku-3-5-...`)
- Gemini: `gemini-3-flash-preview`, `gemini-2.0-flash` (NOT `gemini-3.0-flash`)
- OpenAI: `gpt-4o`

### Structured Output Considerations
- Models may truncate long structured output, especially tables
- Add explicit prompting: "include all movements through the end, do not truncate"
- Increase `default_max_tokens` (currently 65536 for Gemini)
- The web app currently uses `gemini-3-flash-preview` for speed during prototyping

### Lesson Files
- Stored in `private/data/lessons/*.txt`
- Filename becomes display name (underscores/hyphens → spaces, title-cased)
