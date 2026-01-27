# Claude Code Context

## Stale Plan Files
The plan file at `~/.claude/plans/immutable-percolating-goblet.md` (SQLite to Neon migration) is **completed** - ignore it.

## Project Overview

Feldenkrais lesson analysis system with:
- Knowledge base pipeline (transcription, analysis, storage)
- LLM abstraction layer supporting Anthropic, OpenAI, and Gemini
- Web interface for structured lesson analysis

## Key Documentation

### Protocol & Schema Management
See `data/PROTOCOL_SCHEMA_GUIDE.md` for:
- How to manage analysis protocols and their JSON schemas
- File pairing convention (`.md` + `.schema.json`)
- How to archive, update, and export schemas
- Current protocol: `data/washing.md` + `data/washing.schema.json`
- Archived protocols: `data/washing-archive/`

### Operations
See `terraform/OPERATIONS.md` for:
- Lambda trigger management
- FM loader usage

## Key Files

- `src/kb/llm.py` - LLM abstraction (Anthropic, OpenAI, Gemini)
- `src/kb/schemas.py` - Pydantic models for structured analysis output
- `src/kb/web.py` - FastAPI web interface for lesson analysis
- `src/kb/agent.py` - Agent with LLM-powered transform layer

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
- Stored in `data/lessons/*.txt`
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
