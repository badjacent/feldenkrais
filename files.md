# Markdown & Data Files — Usage Reference

## Actively Used by Code

### LLM Prompt Files (loaded at runtime)

| File | Used by | Purpose |
|------|---------|---------|
| `data/frameworks/v1/pass_a.md` | `web.py:load_pass_a()`, `pipeline_graph.py` | Pass A analysis prompt — classifies lesson into VL frameworks |
| `data/frameworks/v1/pass_b.md` | `web.py:load_pass_b()`, `pipeline_graph.py` | Pass B analysis prompt — deep VL-specific analysis base template |
| `data/pass_0b.md` | `web.py:load_pass_0b()` | Pass 0B prompt — preliminary analysis step |
| `data/explore_init.md` | `web.py` (line ~3802) | Initial exploration round prompt |
| `data/explore_round.md` | `web.py` (line ~3802) | Follow-up exploration round prompt |

### JSON Schema Files (loaded at runtime)

| File | Used by | Purpose |
|------|---------|---------|
| `data/pass_a.schema.json` | `web.py:build_pass_a_schema()`, `pipeline_graph.py` | Structured output schema for Pass A |
| `data/pass_b.schema.json` | `web.py:load_pass_b()`, `pipeline_graph.py` | Structured output schema for Pass B |
| `data/pass_0b.schema.json` | `web.py:load_pass_0b()` | Structured output schema for Pass 0B |
| `data/pass_b_narrative.schema.json` | `web.py:run_pass_b_split()` | Schema for narrative portion of split Pass B |
| `data/pass_b_table.schema.json` | `web.py:run_pass_b_split()` | Schema for table portion of split Pass B |
| `data/explore.schema.json` | `web.py` (line ~3809) | Schema for explore mode responses |
| `data/frameworks/v1/frameworks.json` | `web.py`, `pipeline_graph.py` | Single source of truth for VL character definitions (essence, voice, metadata) |

### Claude Code Skills (invoked via `/command`)

| File | Purpose |
|------|---------|
| `.claude/commands/improve-book.md` | `/improve-book` — analyzes review summary and plans book improvements |
| `.claude/commands/load-fp.md` | `/load-fp` — scrapes audio lessons from feldenkraisproject.com |
| `.claude/commands/load-fm.md` | `/load-fm` — scrapes videos from Feldenkrais Manhattan |
| `.claude/commands/restart-server.md` | `/restart-server` — restarts the dev server |
| `.claude/commands/enable-llm.md` | `/enable-llm` — enables LLM calls |
| `.claude/commands/disable-llm.md` | `/disable-llm` — disables LLM calls |

### Project Configuration

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Claude Code project instructions — loaded into every session |

## Used by Terraform (Lambda Deployment)

These files are packaged into the Lambda zip by `terraform/main.tf`:

| Source File | Packaged As | Notes |
|-------------|-------------|-------|
| `data/frameworks/v1/pass_a.md` | same path | |
| `data/frameworks/v1/pass_b.md` | same path | |
| `data/archive/prompt_files_from_v1/vl1.md` | `data/frameworks/v1/vl1.md` | Legacy VL prompts for Lambda only |
| `data/archive/prompt_files_from_v1/vl2.md` | `data/frameworks/v1/vl2.md` | Legacy VL prompts for Lambda only |
| `data/archive/prompt_files_from_v1/vl3.md` | `data/frameworks/v1/vl3.md` | Legacy VL prompts for Lambda only |
| `data/archive/prompt_files_from_v1/vl4.md` | `data/frameworks/v1/vl4.md` | Legacy VL prompts for Lambda only |
| `data/archive/prompt_files_from_v1/vl5.md` | `data/frameworks/v1/vl5.md` | Legacy VL prompts for Lambda only |
| `data/explore_init.md` | same path | |
| `data/explore_round.md` | same path | |
| `data/pass_a.schema.json` | same path | |
| `data/pass_b.schema.json` | same path | |
| `data/explore.schema.json` | same path | |

Also copied to `terraform/data/` for local Terraform reference:
- `terraform/data/frameworks/v1/pass_a.md`, `pass_b.md`, `vl1-5.md`
- `terraform/data/explore_init.md`, `explore_round.md`
- `terraform/data/frameworks/v1/frameworks.json`

## Documentation Only (not loaded by code)

| File | Purpose |
|------|---------|
| `README.md` | Project readme |
| `data/PROTOCOL_SCHEMA_GUIDE.md` | How to manage analysis protocols and schemas |
| `terraform/OPERATIONS.md` | Lambda trigger management and FM loader usage |
| `docs/pipeline.md` | Pipeline architecture documentation |
| `docs/fence_fix.md` | Documentation of a fence-fix issue |
| `CD_SETUP_TODO.md` | CD setup checklist |
| `data/archive/improve_book_changelog.md` | Changelog for `/improve-book` iterations |

## Archive / Not Used by Code

### `data/archive/reference_docs/` — Historical notes, not loaded

| File | Was |
|------|-----|
| `new_vl.md`, `new_vl2.md` | Old framework development notes |
| `core_protocol.md` | Old core protocol |
| `core_protocol_terraform.md` | Terraform version of old protocol |
| `router.md` | Old router prompt |
| `router_terraform.md` | Terraform version of old router |
| `council_description.md` | Council of Five prose (now in frameworks.json) |
| `arch.md` | Old architecture notes |

### `data/archive/frameworks_v2/` — Abandoned v2 framework drafts

| File | Notes |
|------|-------|
| `pass_a.md`, `pass_b.md` | v2 analysis prompts (unused) |
| `vl1.md` - `vl5.md` | v2 VL prompts (unused) |

### `data/archive/prompt_files_from_v1/` — Legacy VL prompts

| File | Notes |
|------|-------|
| `vl1.md` - `vl5.md` | Original VL prompt files; still used by Terraform Lambda packaging (see above) but NOT by local code — local code now generates VL prompts from `frameworks.json` character definitions |

### `data/washing-archive/` — Old protocol versions

| File | Notes |
|------|-------|
| `washing_original.md` | Original washing protocol |

### Other archived/unused

| File | Notes |
|------|-------|
| `data/archive/washing.md` | Old washing protocol |
| `data/archive/lens_synthesis_feature.md` | Feature spec for lens synthesis |
| `data/spiffer_summary.md` | SPIFFER-Q reference summary — not loaded by code |
| `data/discovery_notes.md` | Discovery notes — not loaded by code |
| `.claude/plans/query-agent-db-cache.md` | Completed plan file |

### Unused schema files

| File | Notes |
|------|-------|
| `data/washing.schema.json` | Old washing schema — referenced only in a comment in `web.py:1897` |
| `data/router.schema.json` | Old router schema — not loaded by code |
| `data/pass_0.schema.json` | Old pass_0 schema — not loaded by code |

## Non-project files

| File | Notes |
|------|-------|
| `review_critic_thoughts.md`, `review_critic_thoughts_2.md` | Working notes (root dir) |
| `upwork_portfolio/methodology_proposals.md` | Portfolio content |
| `upwork_portfolio/portfolio_case_study.md` | Portfolio content |
| `upwork_portfolio/portfolio_diagrams.md` | Portfolio content |
| `data/practitioners/instructions.md` | Practitioner directory instructions |
| `data/practitioners/directory.md` | Practitioner directory data |
| `data/frameworks/v1/frameworks2.json` | Alternate/draft frameworks JSON (not loaded) |
