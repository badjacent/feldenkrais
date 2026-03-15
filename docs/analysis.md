# Analysis Pipeline

The analysis pipeline processes transcribed lesson documents through a series of LLM-powered analysis steps. It sits between ingestion (which ends at `transcribed`) and the book pipeline (which is on-demand and dormant).

## Architecture

Every analysis step is gated per-document. Nothing runs unless explicitly opted in.

### Two Tables

**`analysis_step`** — global registry of steps and their current versions:

| name | current_version | cache_suffix | depends_on |
|------|----------------|--------------|------------|
| decompose | 3 | :decompose_v3 | [] |
| somatic | 2 | :somatic_v2 | [decompose] |
| language | 1 | :language_v1 | [decompose] |
| language_patterns | 3 | :language_patterns_v3 | [] |
| concept_tags | 2 | :concept_tags_v2 | [decompose, somatic] |
| spatial | 2 | :spatial_v2 | [decompose, somatic] |
| condense | 4 | :condense_v4 | [somatic, language] |
| embed | 1 | — | [condense] |
| index_concepts | 1 | — | [concept_tags] |
| index_spatial | 1 | — | [spatial] |
| extract_steps | 2 | :extract_steps_v2 | [] |
| map_body_parts | 3 | :map_body_parts_v3 | [extract_steps] |

**`document_analysis_gate`** — per-document, per-step ledger:

| Column | Type | Purpose |
|--------|------|---------|
| document_id | UUID | FK to document |
| step_name | TEXT | FK to analysis_step |
| permitted | BOOLEAN | Gate: is this step allowed to run? |
| completed_version | INTEGER | Version last completed (null = never) |
| completed_at | TIMESTAMPTZ | When last completed |
| upstream_hash | TEXT | Hash of upstream versions at completion time |
| stale | BOOLEAN | Whether upstream has changed since last run |
| error | TEXT | Error message if last run failed |

## State Machine

A step for a given document is in one of five states:

```
                    set permitted=true
    inactive ─────────────────────────────► gated
    (no gate row)                           (permitted=false, stale)
                                                │
                                    set permitted=true
                                                │
                                                ▼
                        ┌──────────────────► stale ◄──────────────┐
                        │                   (permitted=true,      │
                        │                    needs work)          │
                        │                       │                 │
                        │                   run pipeline          │
                        │                       │                 │
                        │              ┌────────┴────────┐        │
                        │              ▼                 ▼        │
                        │          current            error       │
                        │     (completed_version   (error set)    │
                        │      = current_version,     │           │
                        │      stale=false)      clear error      │
                        │              │              │           │
                        │     upstream completes      └───────────┘
                        │     OR version bumped
                        └──────────────┘
```

### State Definitions

- **inactive**: No gate row exists. The step has never been considered for this document.
- **gated**: Gate row exists with `permitted=false`. The step would need work but isn't allowed to run. Shows as locked in the UI.
- **stale**: `permitted=true` AND needs work. "Needs work" means any of:
  - `completed_version IS NULL` (never run)
  - `completed_version < current_version` (version behind)
  - `stale = TRUE` (upstream completed a new version)
- **current**: `permitted=true`, `completed_version = current_version`, `stale=false`, no error. Up to date.
- **error**: An error occurred on the last run. Must be cleared before the step can retry.

### Runnable Condition

A step is **runnable** when ALL of these are true:

1. `permitted = TRUE`
2. Needs work (stale, version behind, or never run)
3. `error IS NULL`
4. All `depends_on` steps have `completed_version IS NOT NULL` for this document

### Staleness Cascade

When a step completes at a new version, all downstream steps (those with it in their `depends_on`) are marked `stale = TRUE` for that document. This is automatic — but downstream steps still won't run unless they are also `permitted`.

## Dependency Graph

```
                          ┌──────────┐
                          │decompose │
                          └────┬─────┘
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
                 ▼             ▼             ▼
           ┌──────────┐ ┌──────────┐  ┌──────────┐
           │ somatic  │ │ language │  │          │
           └────┬─────┘ └────┬─────┘  │          │
                │             │        │          │
       ┌────────┼─────────────┘        │          │
       │        │                      │          │
       │   ┌────┴─────────┐           │          │
       │   │   condense   │           │          │
       │   └──────┬───────┘           │          │
       │          │                    │          │
       │          ▼                    │          │
       │   ┌──────────┐               │          │
       │   │   embed   │              │          │
       │   └──────────┘               │          │
       │                               │          │
       ▼                               ▼          ▼
 ┌─────────────┐               ┌──────────────────┐
 │ concept_tags│               │     spatial       │
 └──────┬──────┘               └────────┬─────────┘
        │                               │
        ▼                               ▼
 ┌──────────────┐              ┌───────────────┐
 │index_concepts│              │ index_spatial  │
 └──────────────┘              └───────────────┘


 Also depends on decompose + language:

 ┌───────────────────┐
 │ language_patterns │  (depends on decompose + language)
 └───────────────────┘

 Independent branch:

 ┌──────────────┐
 │ extract_steps │
 └──────┬───────┘
        │
        ▼
 ┌──────────────┐
 │map_body_parts│
 └──────────────┘
```

### Dependencies at a glance

| Step | Depends on |
|------|-----------|
| decompose | — |
| somatic | decompose |
| language | decompose |
| concept_tags | decompose, somatic |
| spatial | decompose, somatic |
| condense | somatic, language |
| embed | condense |
| index_concepts | concept_tags |
| index_spatial | spatial |
| language_patterns | decompose, language |
| extract_steps | — |
| map_body_parts | extract_steps |

## Version Upgrade Workflow

When you improve an analysis prompt (e.g., somatic v2 → v3):

1. Update the prompt in `analysis.py`
2. Update cache suffix (`:somatic_v2` → `:somatic_v3`)
3. Bump `current_version` in the `analysis_step` table
4. All documents with `completed_version = 2` for `somatic` are now behind — but nothing runs
5. Opt in test documents via the UI (click cells to toggle permission)
6. Run pipeline — only permitted + stale steps execute
7. Review results, then bulk opt-in remaining documents

This gives you incremental rollout: try the new version on a few documents, validate quality, then enable the rest.

## UI

The analysis dashboard at `/analysis` shows a matrix of documents x steps:

- Click a cell to toggle `permitted`
- Colors indicate state: green (current), yellow (stale/will run), red (error), gray (gated), light gray (inactive)
- "Run Pipeline" button executes all runnable steps in dependency order

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /analysis | Dashboard page |
| GET | /api/analysis/dashboard | Dashboard HTML (AJAX refresh) |
| POST | /api/analysis/gate | Toggle single gate `{document_id, step_name, permitted}` |
| POST | /api/analysis/gate/bulk | Bulk toggle `{document_ids, step_name, permitted}` |
| POST | /api/analysis/run | Run pipeline `{document_id?}` |
| POST | /api/analysis/clear-error | Clear error for retry `{document_id, step_name}` |

## CLI

```bash
# Show status
.venv/bin/python -m kb.pipeline.analysis status

# Dry run
.venv/bin/python -m kb.pipeline.analysis run --dry-run

# Run all runnable steps
.venv/bin/python -m kb.pipeline.analysis run

# Run for one document
.venv/bin/python -m kb.pipeline.analysis run --document <doc_id>
```

## Key Files

- `src/kb/database.py` — Schema, gate methods, dashboard query
- `src/kb/pipeline/analysis.py` — Pipeline runner, step executors, CLI
- `src/kb/web.py` — Routes, HTML builders for `/analysis` dashboard
- `src/kb/analysis.py` — The actual analysis functions (unchanged)
