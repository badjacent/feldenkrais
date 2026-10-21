# Analysis profiles: one analysis per document per model

Status: **planned, not started.** Written 2026-09-22. Implementation starts only after
the first Astra run is complete (see "Is the Astra run finished?" below). Nothing in
this plan was applied when it was written; every finding was checked read-only against
the live database and the Mac mini.

## Goal

Evaluate several models (today `gemini-3-flash` and `astra-low`; next `gemini-3.1`,
`astra-medium`, `opus-5.5`) on the same lessons, going forward. Analysis should run on,
and be permitted per, **source × model**, so one document can carry one analysis per
model.

## Owner decisions

| # | Question | Decision |
|---|---|---|
| 1 | Name for the historical Gemini results | `gemini-3-flash`. Not deprecated. |
| 2 | Which profile production reads, including search | Canonical profile. Search is not a real production feature yet, so keep it simple. |
| 3 | Schema strictness for comparisons | Option C: every field required, optional fields explicitly nullable, objects closed. |
| 4 | Who consumes the queue | A dedicated worker. The web app stops consuming. |
| 5 | Today's Astra run | Stays as `astra-low`. `astra-medium` is a separate, new profile. |
| 6 | Cost control | One simple global on/off switch per model. No time windows, no budgets, no cost ledger. |

## What the 2026-09-22 Astra change did (review)

Commits `0ac251a`, `65bbee6`, `54e1249`.

- Added a `codex` provider (`llm_config` key `codex-astra`, model `gpt-6-astra`,
  `reasoning_effort: low`). It shells out to the Codex CLI on the signed-in
  subscription and records no token usage.
- Chooses the model per step from the document's **source name**: a name ending
  `-Astra` means Astra, anything else Gemini (`get_analysis_provider` in
  `pipeline/analysis.py`, `document_analysis_provider` in `ingest/analysis_sources.py`).
- Astra results are cached under `:codex-astra`-suffixed keys. The analysis writers
  already had this convention (`provider_key != DEFAULT_LLM` appends the key), and the
  new read side matches it, so Gemini and Astra results cannot collide.
- Made the candidate web app an always-on queue consumer (8 concurrent, 900 s step
  timeout), plus database-reconnect fixes in `db/core.py` and `web.py`. The stop button
  now only ensures the queue is running.
- New auto-groups inherit their gates from `FELDENKRAIS_FIRST`.
- `0ac251a` also swept in the three-tier deployment work (promote script, candidate
  runners, plists) under an Astra commit message. History only; nothing to fix.

State of the data:

- 931 documents were imported on 2026-09-22 directly into 76 new `-Astra` sources.
- One existing document, "FI Demo: Arching Through Rounding" (`501c9674`), was moved
  out of `FELDENKRAIS_ACCESS_YOUTUBE`. Its new source kept `public = true`.
- 48 further `-Astra` sources were created with no documents.
- The results really are Astra's: all completed decompositions in the Astra sources are
  backed by `:codex-astra` cache entries.

Problems the design must remove:

1. Progress is keyed (document, step) and gates (group, step), so a document can hold
   only one model's analysis. The model can only live in the source name, and models
   cannot be compared on the same lesson.
2. `load_document_cached_analysis` silently falls back to the Gemini result when the
   Astra one is missing. FI Demo shows Astra decompose and language beside Gemini
   somatic, condense and concepts. Its progress rows date from the 2026-09-05 Gemini
   run, so the pipeline treats those steps as done and will never produce Astra versions.
3. Astra output sits in the shared derived tables (124 `lesson_embedding` rows, 9,690
   `lesson_concept` rows) with nothing marking which model produced it.
4. The Codex path rewrites every schema so all fields are required and objects closed,
   for Astra only. The system prompt is pasted in front of the user prompt, a "return
   only JSON" line is added, temperature and token limits are ignored.
5. The candidate web app executes analysis with the **worker** checkout's interpreter
   and code (`ANALYSIS_WORKER_PYTHON`, `ANALYSIS_WORKER_CWD` in
   `ops/mini/run-candidate-analyzer.sh`), so the promote step does not govern analysis
   code. The worker checkout `~/src/feldenkrais` on the Mini sits at `3f4f0ff` with
   today's analysis files as uncommitted edits. Those match the commits, but `web.py`,
   `db/core.py` and `ops/mini/run-analysis-worker.sh` there carry different local edits.

## Design

### Profiles

New table `analysis_profile`:

| Column | Meaning |
|---|---|
| `key` (PK) | `gemini-3-flash`, `astra-low`, ... |
| `provider`, `model` | e.g. `codex`, `gpt-6-astra` |
| `params` (jsonb) | reasoning effort, thinking level |
| `cache_tag` | appended to cache keys; `NULL` means the historical bare keys |
| `contract_version` | schema contract the profile runs under (2 or 3) |
| `enabled` | the global on/off switch; new profiles start `false` |
| `canonical` | exactly one; what lesson pages and search read by default |
| `max_concurrency` | fixed operational limit, not a cost control |
| `created_at`, `notes` | |

A profile never changes once it has produced results; a different effort level or model
is a new profile.

| Profile | cache_tag | contract | enabled | canonical |
|---|---|---|---|---|
| `gemini-3-flash` | `NULL` | 2 | yes | yes |
| `astra-low` | `codex-astra` | 2 (strict rewrite) | yes | no |
| `gemini-3.1` | `gemini-3.1` | 3 | no | no |
| `astra-medium` | `astra-medium` | 3 | no | no |
| `opus-5.5` | `opus-5.5` | 3 | no | no |

`llm_config.get_llm` builds its client from the profile row instead of a hardcoded key.

### Keys

| Table | Today | Planned |
|---|---|---|
| `document_analysis_state` | (document_id, step_name) | (document_id, profile_key, step_name), plus `contract_version` |
| `group_analysis_gate` | (group_id, step_name) | (group_id, profile_key, step_name) |
| `lesson_embedding` | (document_id, method) | (document_id, profile_key, method) |
| `lesson_concept` | (document_id, category, value) | (document_id, profile_key, category, value) |

Transcription (`analysis`, `type_id = 1`) stays per document.

### Cache keys

Unchanged for existing results, so nothing re-runs:

```
key = sha256(transcript.strip() + step_suffix + contract_tag + profile_tag)[:16]
  step_suffix  today's per-step suffix, e.g. ":somatic_v2", ":decompose_v3"
  contract_tag ""     for contract 2
               ":c3"  for contract 3
  profile_tag  ""     when cache_tag is NULL
               ":" + cache_tag otherwise
```

Two documents with identical transcripts share results, as they do today.

### When a step is runnable

For a (document, profile, step):

- a gate permits (group containing the document, profile, step), and
- the profile is `enabled`, and
- there is no progress row, or `completed_version < analysis_step.current_version`, or
  `contract_version != profile.contract_version`, or the row is stale, and
- its dependencies are complete **for the same profile**.

The worker checks `enabled` before every claim. Turning a model off takes effect within
one step; a step already in flight finishes.

**Trap.** `analysis_step.current_version` stays global. Bumping it marks the step stale
for every profile and every document, which queues a full re-run for every model. That
remains a deliberate act. The schema contract is pinned per profile precisely so that
moving new models to v3 does not re-run `gemini-3-flash`.

### Schema contract v3 (option C)

Every field required, objects closed, fields that can legitimately not apply explicitly
nullable. The prompt says to use `null` when a field does not apply. Written once in
standard JSON Schema; each provider adapter in `llm.py` translates nullability into what
its API accepts. `_codex_output_schema` becomes a check that the schema is already strict
rather than a rewrite.

Audit of the twelve pipeline schemas (fields optional today that the Codex rewrite forces):

| Schema | Fields |
|---|---|
| `pass_0b` (somatic) | differentiation, activation, inhibition, transmission_route, ground_push |
| `pass_a` (decompose) | lesson_title, secondary_frameworks, steps.note, steps.is_home, groups.description |
| `pass_0b_spatial` | contacts.qualifier |
| `motifs` | evidence.reason |

The other eight (`pass_0b_lang`, `pass_0b_word_lang`, `pass_0b_condensed`,
`pass_0b_concepts`, `pass_0b_spatial_v3`, `axes`, `motifs_raw`, `lesson_card`) only need
their objects closed.

Observed on 160 lessons per model (different lesson sets, so no difference here can be
attributed to the model): Gemini omits these fields only 0.2% of the time but fills them
with "none"/empty 26–36% of the time; Astra fills them with placeholders 11–42% of the
time and writes about twice as much. Explicit nulls make "does not apply" countable.

Every result payload records profile, model, effort, contract version, elapsed time,
and token usage where the provider reports it.

A like-for-like comparison is between profiles on the same contract (for example
`gemini-3.1` against `astra-medium` on v3), on the same documents.

### Worker

- A dedicated launchd job in its own checkout on the Mini (e.g. `~/src/feldenkrais-analysis`),
  deployed from pushed commits through a promote step like the candidate web app. No
  uncommitted edits on the Mini.
- Claims (document, profile, step) together; per-profile concurrency from `max_concurrency`.
- `run-one` takes an explicit `--profile`; step executors receive the profile; remove
  `get_analysis_provider`, `_provider_cache_suffix` and `ingest/analysis_sources.py`.
- Codex needs `codex login` as the worker's macOS user.

### Web app

- Stops consuming the queue: drop `ANALYSIS_QUEUE_ENABLED` and the `ANALYSIS_WORKER_*`
  exports from `ops/mini/run-candidate-analyzer.sh` and the consumer thread in `web.py`.
- Analysis page: one on/off switch per profile; the gates grid gains a profile selector.
- Lesson pages name the profile they show, have a profile switcher and a side-by-side
  compare view, and never fall back silently to another profile.
- Search reads the canonical profile only.

### Sources return to meaning provenance

- Strip `-Astra` from the 76 sources and rename their auto-groups to match.
- Move FI Demo back to `FELDENKRAIS_ACCESS_YOUTUBE`; restore nothing else about it. Its
  Gemini results are `gemini-3-flash`; its two stray Astra results (decompose, language)
  become cache hits when `astra-low` runs its remaining steps.
- Delete the 48 empty `-Astra` sources and their groups.
- Their gates become (group, `astra-low`, step).
- The custom group "NewMateria Astra Pilot" stays as a custom group.

## Order of work

0. **Wait** until the Astra run is finished (query below). Decide what to do about the
   three somatic errors.
1. Recommended first: fix the ~22 pre-existing unit-test failures (FastAPI 0.141.1 defers
   router inclusion, so tests asserting on `app.routes` see only the docs routes; three
   transcribe tests fail on a temp-file path). This work touches routes heavily and a red
   suite hides regressions; `ops/promote-candidate.sh` currently needs
   `--allow-test-failures`.
2. Stop the web app's queue consumer.
3. Take a Neon branch as the rollback point.
4. Migrate additively: create `analysis_profile`; add `profile_key` with a default and
   `contract_version` to the four tables; backfill (`astra-low` for documents in `-Astra`
   sources, `gemini-3-flash` for everything else); then swap the primary keys.
5. Verify before going further:
   - row counts per table unchanged;
   - Gemini results still load through the bare keys and Astra through `:codex-astra`;
   - **the number of runnable `gemini-3-flash` steps equals the pre-migration runnable
     count.** This catches an accidental full re-run.
6. Deploy the profile-aware worker and web app; then clean up the sources.
7. Land the v3 schemas and adapters; pin the new profiles to contract 3.
8. Bring each new model up on a pilot group: add its gates, flip its switch, review
   outputs, then widen.

## File map

| Area | Files |
|---|---|
| Profiles, clients | `src/kb/llm_config.py`, `src/kb/llm.py` (`_codex_output_schema`, nullability per provider) |
| Queue, progress, gates | `src/kb/db/pipeline_state.py`, `src/kb/db/lesson_groups.py`, `src/kb/db/dashboard.py` |
| Worker | `src/kb/pipeline/analysis.py`, `ops/mini/run-analysis-worker.sh`, `ops/mini/launchd/com.feldenkrais.analysis.plist`, `ops/promote-candidate.sh` |
| Remove | `src/kb/ingest/analysis_sources.py`, `tests/unit/test_analysis_sources.py` |
| Readers | `src/kb/cache_helpers.py`, `src/kb/routes/lesson_data.py`, `src/kb/routes/search.py` |
| Analysis page | `src/kb/html_builders.py`, `src/kb/routes/analysis_routes.py` |
| Web consumer | `src/kb/web.py`, `ops/mini/run-candidate-analyzer.sh` |
| Derived tables | `src/kb/search/build_concept_index.py`, `src/kb/search/build_spatial_index.py`, embedding writers in `pipeline/analysis.py` |
| Schemas | `data/pass_a`, `pass_0b`, `pass_0b_spatial`, `motifs` `.schema.json` and their prompts (`data/pass_0b.md`, `data/motifs.md`; the decompose prompt is versioned at `data/frameworks/v1/pass_a.md`); close objects in the other eight |

## Is the Astra run finished?

Read-only. Finished when every row reads done = permitted (less accepted errors).

```sql
with astra_docs as (
  select distinct m.document_id, m.group_id
  from lesson_group_member m
  join lesson_group lg on lg.id = m.group_id
  join document_source ds on ds.id = lg.auto_source_id
  where ds.name like '%-Astra'),
work as (
  select a.document_id, g.step_name
  from astra_docs a
  join group_analysis_gate g on g.group_id = a.group_id and g.permitted)
select w.step_name,
       count(distinct w.document_id) as permitted,
       count(distinct w.document_id) filter (
         where s.completed_version >= st.current_version
           and not s.stale and s.error is null) as done,
       count(distinct w.document_id) filter (where s.error is not null) as errors
from work w
join analysis_step st on st.name = w.step_name
left join document_analysis_state s
  on s.document_id = w.document_id and s.step_name = w.step_name
group by 1 order by 1;
```

At the time of writing (2026-09-22 evening): decompose 618/618; condense 268, language
275, somatic 270 of 618; 3 somatic errors; 58% of step runs overall.

## Explicitly out of scope

Time windows, budgets, a per-step cost ledger, and cost estimates. Results still record
usage, so accounting can be added later without a migration.
