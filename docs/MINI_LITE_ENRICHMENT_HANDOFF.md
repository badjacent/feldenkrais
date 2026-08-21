# Handoff: Mini Lite-Enrichment Worker (axes, motifs, display card)

Status: ready for implementation
Audience: implementation agent (builds the worker; operates on the Mac mini)
Owner: Michael Hollander
Date: 2026-08-21

## Purpose

A **self-contained, per-lesson enrichment process** that runs on the Mac mini
and produces, from a raw transcript alone, with **one cheap Gemini call per
artifact**:

1. **Movement axes** (flexion_extension, rotation, side_bending,
   cross_lateral, vigor) — the pass already exists (`run_axes_lite`).
2. **Motif assignments** (closed-set, from the registry) — lite variant to
   build.
3. **A display card** (short description, positions, themes) — to build.
   This is what makes crawled lessons *presentable*: most free lessons on
   the web have no usable description.

Downstream (owner side, not this handoff): the Atlas projection builder
coalesces **full-analysis values when they exist, lite values otherwise**,
with provenance fields, so lite enrichment makes a lesson visible in
search/game/display immediately and full analysis upgrades it later without
any coordination.

## Hard boundaries

- **NOT part of the analysis DAG.** Do not register `analysis_step` rows, do
  not write `document_analysis_state`, do not touch group gates, do not
  acquire the `feldenkrais:analysis-worker` lease. The DAG is for full
  analysis only. This worker is a separate loop, like the transcription
  worker.
- **Gemini only** (`gemini` provider key, `gemini-3-flash-preview`). This is
  benchmarked, not a preference: on a 61-lesson sample, transcript-direct
  Gemini agreed with full-analysis axes 70–84% exactly and 95–100% within
  one ordinal step; `gpt-5.6-luna` was worse on every axis (57–70% exact),
  systematically compressed the flexion/extension tails toward
  `mixed_or_balanced`, under-detected cross-lateral work, and ran ~2× slower.
- **Public sources only**: scope documents to
  `atlas_projection.PUBLIC_SOURCES` (import it; do not copy the list).
- **Never overwrite full-analysis caches.** Lite passes write only their own
  cache suffixes (below). Precedence is resolved at projection-build time,
  not at write time.
- Results land in the shared `cache` table (keyed by transcript hash), so
  they are visible to the workstation and the projection builder with no
  file transfer.

## What already exists (pull latest master)

- `analysis/axes.py::run_axes_lite(text, provider_key=None)` — cache suffix
  `:movement_axes_lite:axes-lite-p1`, prompt/schema
  `data/axes_lite.md|.schema.json`, quote-based evidence. Benchmarked; use
  as-is.
- `run_motifs.py` — CLI patterns to follow (dry-by-default, `--docs-file`,
  `--apply`).
- Cache/LLM bootstrap pattern: see the top of `run_motifs.py`
  (`cache_helpers.init`, `llm_config.init`, `analysis.init`).
- `private/ops/mini_analysis/SETUP.md` steps 1–4 cover the mini
  prerequisites (repo + private submodule, venv, `GOOGLE_API_KEY`). The
  private submodule is REQUIRED here — the motif registry lives at
  `private/data/motifs_registry.yaml`. No AWS credentials are needed for
  this worker.
- Note: as of this writing the mini has NO working per-lesson LLM path (its
  earlier analysis attempts failed on a missing `GOOGLE_API_KEY`). This
  worker is the first; step 4 of the setup doc is a prerequisite.

## To build

### 1. `run_motifs_lite` (in `analysis/motifs.py`)

Closed-set motif assignment from the raw transcript:

- Prompt `data/motifs_lite.md`: reuse the rules of `data/motifs.md`
  verbatim (closed set, mode/role/certainty, exclusions, empty-is-normal)
  with two changes: input is the transcript, and evidence is 1–2 short
  quotes per assignment instead of step ids. Include the same
  `{registry_version}`/`{registry_block}` placeholders; reuse
  `_registry_block()` and the enum-injection of established ids into the
  schema (`data/motifs_lite.schema.json`, modeled on `motifs.schema.json`
  with a quotes-based evidence object).
- Cache suffix: `:distinctive_motifs_lite:{registry_version}:motifs-lite-p1`
  (registry version IN the key, same as the full pass).
- No candidate lane in lite (registry growth stays a full-analysis
  concern): schema has `assignments` only.
- Post-validate like `run_motifs` (drop malformed rows).

### 2. `run_lesson_card` (new module `analysis/lesson_card.py`)

One call producing public-safe display content. Prompt
`data/lesson_card.md`, schema `data/lesson_card.schema.json`, cache suffix
`:lesson_card:card-p1`. Fields:

```json
{
  "summary": "2–3 plain sentences: what the lesson does and how it works.",
  "suggested_title": null,                         // optional display-only title
  "lesson_type": "atm",                           // atm|talk|interview|demonstration|mixed
  "starting_position": "lying",                   // normalized primary starting position
  "positions": ["lying on back", "side lying", ...],
  "themes": ["breath", "rolling", "eyes", ...],   // 2–5 plain words each
  "support_context": ["floor"],                    // floor|chair|standing|wall
  "equipment": ["chair"],                          // usually []
  "primary_language": "en",                        // normalized language code
  "notable_feature": null,                          // optional one-sentence distinctive feature
  "audience_notes": "optional single sentence (e.g. 'mostly in standing')"
}
```

Rules to encode in the prompt: plain language (no clinical or Feldenkrais
jargon — write for someone who has never taken a lesson); NO transcript
quotes; no teacher biography claims; describe the lesson, not its benefits
(no therapeutic claims); `summary` must not exceed ~60 words.

Additional field rules:

- `lesson_type` is a closed enum: `atm`, `talk`, `interview`,
  `demonstration`, or `mixed`. It describes the recording's actual content,
  not its title or source category.
- `starting_position` is the position in which the guided movement lesson
  substantially begins. Use the same normalized position vocabulary as
  `positions`; do not infer it from a passing introductory instruction.
- `positions` includes substantially used practice positions, not incidental
  mentions. The projection may normalize surface labels further for Atlas.
- `support_context` is a closed list drawn from `floor`, `chair`, `standing`,
  and `wall`. Include only supports or practice contexts actually used.
- `primary_language` is a normalized language code such as `en`, `de`, or
  `es`. It is transcript language, not source-country inference.
- `notable_feature` is optional. When present it is one short, factual,
  plain-language sentence describing what is especially characteristic of
  this lesson. Leave it null when the transcript does not support a genuinely
  distinctive observation. If the controlled motif pass already captures the
  same fact, this field may phrase it for display but does not create a new
  motif or taxonomy entry.
- `suggested_title` is optional and display-only. Supply it only when the
  source title is missing, generic, numbered, or otherwise unhelpful. It must
  describe the lesson conservatively, never replace the stored source title,
  and never be used as an identity or deduplication key.

The existing `audience_notes` field should not contain inferred suitability
claims. It may describe a factual practice constraint, but must not claim that
a lesson is appropriate for beginners, older people, a diagnosis, pain, or a
therapeutic outcome.

### 3. Worker CLI (`src/kb/ingest/lite_enrich.py` or similar)

Follow the `run_motifs.py` bootstrap. Subcommands:

- `list` — transcribed docs in `PUBLIC_SOURCES`, showing per-doc status:
  full analysis present / lite present / nothing (compute cache keys for
  both tiers; "full present" means the full motif+axes caches at current
  versions exist).
- `run [--limit N] [--source S] [--apply]` — for each doc missing BOTH
  full and lite artifacts: run `run_axes_lite`, `run_motifs_lite`,
  `run_lesson_card`. Dry by default. Skip docs that already have full
  analysis (they need nothing from this worker). Docs with partial lite
  coverage fill in the missing artifact only (cache checks make this
  automatic).
- Bounded and resumable: process in submission order, print one line per
  doc, tolerate transient DB/LLM failures by skipping (the next run
  retries — caches make re-runs free).
- Unattended loop on the mini: same tmux pattern as
  `private/ops/mini_analysis/SETUP.md` step 6, pointed at this CLI.

### 4. Registry-version behavior

When the owner bumps the motif registry, lite motif caches invalidate
naturally via the version-in-key. The worker's `run` then re-extracts lite
motifs for affected docs on its next pass. No other coordination.

## Owner-side follow-ups (not yours)

- Projection builder gains full→lite coalescing plus provenance fields
  (`axes_source`/`motifs_source`: `"full" | "lite"`) and optional `display`
  block from the lesson card — additive contract change for the Atlas
  reader.
- IAM / S3 publish bridge (owner will set up; this worker never needs it).

## Acceptance checks

1. `list` on the current corpus shows all ~224 public-source docs as "full
   present" and runs nothing for them (`run --apply` is a no-op today).
2. On a doc with lite-only enrichment: axes values are all valid enums;
   motif assignments only use established registry ids; lesson card summary
   is ≤60 words, jargon-free, and quote-free; lesson type, starting position,
   support context, and primary language use their bounded vocabularies;
   optional suggested title and notable feature satisfy their display-only
   rules.
3. Nothing in `analysis_step` / `document_analysis_state` /
   `group_analysis_gate` changes after a worker run.
4. Re-running `run --apply` immediately is all cache hits (fast, no LLM
   calls).
5. Worker runs with only `DATABASE_URL` + `GOOGLE_API_KEY` set (no AWS).
