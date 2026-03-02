# Claude Code Context

## Stale Plan Files
The plan file at `~/.claude/plans/immutable-percolating-goblet.md` (SQLite to Neon migration) is **completed** - ignore it.

## Project Overview

Feldenkrais lesson analysis system with:
- Knowledge base pipeline (transcription, analysis, storage)
- LLM abstraction layer supporting Anthropic, OpenAI, and Gemini
- Web interface for structured lesson analysis

## Public/Private Repo Split

This repo is the **public** part. Private/proprietary content lives in a separate repo
(`feldenkrais-private`) mounted as a git submodule at `private/`.

- **Public repo** (`feldenkrais`): Core infrastructure (web.py, analysis.py, llm.py, pipeline.py, database.py, etc.), pass0b analysis, terraform, scrapers
- **Private submodule** (`private/`): Book features (web_book.py, pipeline_graph.py, cross_query.py, critics.py, review_agent.py, query_agent.py), frameworks data, lesson files, schemas, scripts

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
- `web.py` adds `private/src/` to sys.path and conditionally imports `web_book.py` (try/except ImportError)
- `analysis.py` checks `private/data/` first via `_resolve_data_path()`, falls back to public `data/`
- Without the submodule: landing page, pipeline batch (pass0b), search work; book/analyzer/explore features are hidden
- With the submodule: full /book, /booklet, /analyzer, /explore features appear

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

## Key Files

- `src/kb/llm.py` - LLM abstraction (Anthropic, OpenAI, Gemini)
- `src/kb/schemas.py` - Pydantic models for structured analysis output
- `src/kb/web.py` - FastAPI web interface for lesson analysis
- `src/kb/analysis.py` - Analysis engine (pass0b, pass_a, frameworks)
- `src/kb/agent.py` - Agent with LLM-powered transform layer
- `private/src/web_book.py` - Book/analyzer routes (private)
- `private/src/pipeline_graph.py` - Book pipeline DAG (private)

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

### Web App
- Requires `python-dotenv` to load `.env`
- Run with `--reload` for auto-reload on file changes
- GET `/analyze` redirects to home (handles browser refresh after POST)
- Lambda deployment via Function URL (see `terraform/OPERATIONS.md`)

### Deploying Web Lambda
```bash
cd terraform
./build_web_layer.sh  # First time or when dependencies change
terraform apply -var="google_api_key=$GOOGLE_API_KEY" ...
terraform output web_function_url  # Get public URL
```
