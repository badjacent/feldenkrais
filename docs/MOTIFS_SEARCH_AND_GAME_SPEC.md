# Spec: Motif/Axes Search Page + Chooser Game (Web Integration v1)

Status: ready for implementation
Audience: implementation agent working in this repo (`src/kb` web app)
Owner: Michael Hollander
Depends on data: distinctive-motifs pass (registry **motifs-v5**, prompt
**motifs-p1**) and movement-axes pass (prompt **axes-p1**), both already
implemented in `src/kb/analysis/motifs.py` / `analysis/axes.py` and run via
`src/kb/run_motifs.py`.

## 1. Purpose and scope

Expose the new motif and movement-axes annotations on the website as:

1. A **faceted search page** — filter lessons by motif and by axis values.
2. A **chooser game** — iterated triple-choice: show three lessons, the user
   picks the most appealing, the next triple is informed by the pick,
   converging on a lesson recommendation.

**Partial dataset**: only lessons that have been through the motif/axes
passes are eligible. At the time of writing that is the ~78 free Feldenkrais
Project lessons; further cohorts (e.g. Kinesophics, ~122 docs) will be added
by the owner over time. Because coverage is partial:

- Both pages get their **own URLs**, separate from the existing `/` and
  `/search` experience. Do not modify the existing search page.
- Both pages must show a coverage notice ("Covering N lessons from <sources>;
  more coming"), with N computed from the index, never hard-coded.

Non-goals for v1: no changes to existing search/lesson routes beyond adding
links; no exposure of the raw-motif lane or candidate motifs; no extraction
triggered from the web path (read-only consumers of cached analysis); no
per-user persistence for the game.

## 2. Data model (what exists today)

All artifacts live in the `cache` table (`key` text PK, `data` jsonb),
addressed by content-hash keys. Key derivation (see `cache_helpers.py`):

```
key = sha256(transcript.strip() + SUFFIX)[:16]
```

where `transcript` is `Database.get_document_transcription(document_id)` and:

| Artifact | SUFFIX | Notes |
|---|---|---|
| Motif assignments | `:distinctive_motifs:{registry_version}:motifs-p1` | registry_version currently `motifs-v5` |
| Movement axes | `:movement_axes:axes-p1` | registry-independent |

Load the registry with `analysis.load_motif_registry()` (reads
`private/data/motifs_registry.yaml`, private-first resolution). Registry
fields you need: `version`, and per motif `id`, `label`, `definition`,
`status` (only `established` are assignable; `merged` stubs like
`pelvic_clock`/`heel_clock` are never assignable and must be ignored), and
**`chooser_visible`** (bool) — motifs with `chooser_visible: false` must NOT
be shown as filters or game features, even if assigned.

Motif payload shape (per lesson):

```json
{"assignments": [{"motif_id": "bell_hands", "mode": "taught|referenced",
                  "role": "central|supporting|incidental",
                  "certainty": "clear|probable|uncertain",
                  "evidence": [{"step_ids": [..], "reason": "..."}]}],
 "candidate_motifs": [...],            // DO NOT expose on the site
 "registry_version": "motifs-v5", "prompt_version": "motifs-p1", ...}
```

Axes payload shape (per lesson):

```json
{"axes": {
   "flexion_extension": {"value": "strongly_flexion|flexion_leaning|mixed_or_balanced|extension_leaning|strongly_extension",
                          "evidence": [{"step_ids": [..], "reason": "..."}]},
   "rotation":      {"value": "none|minor|moderate|central", "evidence": [...]},
   "side_bending":  {"value": "none|minor|moderate|central", "evidence": [...]},
   "cross_lateral": {"value": "none|minor|moderate|central", "evidence": [...]}
 }, "prompt_version": "axes-p1", ...}
```

Current registry (motifs-v5), 8 established motifs, all chooser-visible:
`bell_hands`, `clock`, `seesaw_breathing`, `circular_breath_orbit`,
`pressure_waves`, `eyes_jaw`, `hand_holding_foot`, `arms_legs_x`
(plus merged stubs `pelvic_clock`, `heel_clock` — ignore). Do not hard-code
this list — always read the registry at index-build time.

## 3. Required first step: materialized index

Do not hash transcripts per request. Build a small materialized index and
have both pages read only the index.

**Builder**: add an `index` subcommand to `src/kb/run_motifs.py` (follow the
existing subcommand pattern and the `--docs-file` convention). It:

1. Enumerates candidate docs (all transcribed docs, or a `--docs-file` list).
2. For each doc, computes both cache keys; docs with **both** motif (at the
   current registry version) and axes payloads present are eligible.
3. Classifies `is_atm`: false if title matches `Talk:|Discussion|Interview`
   (case-insensitive) or doc metadata `lesson_type` says non-ATM; plus an
   owner-editable exclusion list inside the builder file.
4. Writes ONE cache row, key `web:motif_axes_index:v1`, payload:

```json
{"built_at": "...", "registry_version": "motifs-v5",
 "motif_prompt_version": "motifs-p1", "axes_prompt_version": "axes-p1",
 "motifs": [{"id": "...", "label": "...", "definition": "..."}],   // chooser_visible only
 "docs": [{"document_id": "...", "title": "...", "source_id": 4,
           "source_name": "FELDENKRAIS_PROJECT", "is_atm": true,
           "duration_minutes": null,
           "motifs": [{"id": "...", "mode": "...", "role": "...", "certainty": "..."}],
           "axes": {"flexion_extension": "...", "rotation": "...",
                    "side_bending": "...", "cross_lateral": "..."}}]}
```

   - `docs[].motifs` includes only chooser-visible motif ids.
   - Evidence reasons are NOT in the index (keep it small); the lesson page
     already exists for depth. If you want per-result "why" text, add the
     first evidence `reason` string per motif — acceptable, still small.
5. Prints a summary (docs eligible, per-motif counts) and refuses to write an
   empty index.

Rebuilding is owner-triggered (CLI) — no auto-rebuild, no cron. When the
registry version bumps, the old index keeps serving until the owner rebuilds
(mention the staleness in the summary output if versions mismatch).

## 4. Search page

**URL**: propose `/browse` (verify it's unclaimed; `/explore` and `/finder`
are taken by existing modules). Add a matching JSON endpoint
`/api/browse` if the page needs one; server-rendered HTML in the style of the
existing pages (see `web.py` HTML builders) is equally fine and preferred if
simpler.

**Access**: the underlying lessons are public (FP free library) but MUST
still be filtered through the existing access layer: call
`_set_source_access(request)` like other routes and drop docs whose source
isn't accessible / `is_document_accessible` fails. The page itself is
anonymous-accessible.

**Filters** (all optional, combined with AND):

- **Motif chips**: one chip per chooser-visible motif (label from registry;
  definition as tooltip/hover). Selecting multiple motifs = lesson must have
  ALL selected (AND). Per-selection sub-controls, with defaults:
  - mode: `taught` only (default) / include `referenced`
  - minimum role: `supporting` (default; i.e. central+supporting) /
    `central` only / include `incidental`
- **Flexion–extension**: multi-select over the 5 ordinal values (e.g. a user
  picks `strongly_flexion` + `flexion_leaning` to mean "flexion lessons").
- **Rotation / Side-bending / Cross-lateral**: minimum-level select —
  "any" (default), "at least moderate", "central". ("A lot of cross-lateral
  movement" = at least moderate.)
- **ATM only** toggle, default ON (talks/discussions hidden by default).

**Results**: title (linking to the existing `/lesson/{document_id}` page),
source name, motif badges (label + role), and the four axis values rendered
in plain words (e.g. "flexion-leaning · rotation: central · cross-lateral:
moderate"). Result count shown. Order: alphabetical by title is fine for v1
(no relevance scoring — filters do the work).

Empty-state: show the coverage notice prominently and suggest removing
filters.

## 5. Chooser game

**URL**: propose `/game` (verify unclaimed).

**Mechanics** (v1, deliberately simple):

1. Pool = index docs with `is_atm: true` (after the same access filtering).
2. Round 1: pick 3 lessons spread far apart (see distance note below).
3. Show 3 cards: title, axis values in plain words, motif labels (no ids,
   no jargon), duration if present. Buttons: pick one, or "none of these"
   (reshuffle round without recording a pick).
4. On pick: next round = the picked lesson stays conceptually as the
   "anchor" (do not re-show it); show 3 new lessons chosen to be *near* the
   anchor but *spread among themselves*.
5. After 3 picks (or when the candidate neighborhood is exhausted), show the
   final recommendation: the lesson nearest the last anchor, with its motif/
   axis summary and a link to `/lesson/{id}` — plus a "runners-up" list of
   the next 2.
6. A "start over" control resets state.

**Distance measure — implementer's choice.** The feature *metric* (which
dimensions matter) is fixed: the four ordinal axes plus the chooser-visible
motif set (mode `taught` only, weighted by role). The distance *measure* over
those features is NOT specified and is yours to choose — anything reasonable,
e.g. map ordinal axis values to integer ranks and take L1, plus a
motif-overlap term (role-weighted Jaccard). Requirements on whatever you
choose:

- deterministic;
- documented in a comment block at the definition site;
- isolated behind a single function (`distance(a, b) -> float`) so it can be
  swapped later without touching the game loop;
- axes and motifs both contribute (neither term degenerate).

**State**: keep it client-side. The dataset is small (≤ a few hundred docs);
serve the eligible pool as JSON (either embedded in the page or via
`/api/game/dataset`) and run rounds entirely in the browser. No server-side
sessions, no persistence, no logging requirement for v1 (a fire-and-forget
pick log is optional; if added, follow the pattern of `log_search_query`).

## 6. Versioning and refresh behavior

- Pages read the index only; they display `registry_version` + `built_at`
  discreetly in the footer.
- New cohorts appear by: owner runs extraction (existing CLI) → owner runs
  `index` → pages update. No web-side awareness needed.
- Registry bumps (motifs-v5, …) invalidate motif cache keys; the index
  builder targets whatever version the registry file declares. Until the
  owner re-extracts + rebuilds, the site serves the old index — this is
  acceptable and by design.

## 7. Acceptance checks

Data-level (run after building the index from the FP docs list; the owner's
`/tmp/fp_free_docs.txt` enumerates the cohort, or use `--source` filters):

1. Index has ≥ 60 docs, all with both motif and axes blocks; talks/
   discussions flagged `is_atm: false`.
2. Merged stub ids (`pelvic_clock`, `heel_clock`) never appear in the index;
   clock lessons carry `clock`.
3. Search checks (confirmed against the motifs-v5 FP extraction,
   2026-08-20). Pin tests to role=central assignments only — central is
   stable across re-extractions; supporting/incidental assignments flip
   between runs and must not be load-bearing in tests:
   - motif `eyes_jaw` at role=central → returns the eye/jaw lessons
     ("10. The Movement of the Eyes …", "Easing the Jaw, Neck, and
     Shoulders", "Relaxing Your Neck and Jaw", "Easier Sitting Workshop
     Lesson 2", "Sitting and Turning with Length"), and NOT ~40 lessons
     where it is merely supporting/incidental.
   - motif `pressure_waves` at role=central → "The Anti-Gravity Lesson".
   - motif `bell_hands` → "Calming Your Nervous System, Integrating Hands
     and Eyes".
   - motif `hand_holding_foot` at role=central → "08. Perfecting the
     Self-Image".
   - motif `arms_legs_x` at role=central → "03. Some Fundamental Properties
     of Movement" and "11. Generalize Your Skills".
4. Cross-lateral "at least moderate" returns a nonempty, plausible subset.
5. Game never shows a talk/discussion; never shows the same lesson twice in
   one session; "none of these" reshuffles without ending the game.
6. Existing `/search`, `/lesson/{id}` behavior unchanged.

## 8. Pointers into the codebase

- Route modules: `src/kb/routes/` (register like the others in `web.py`).
- HTML builder conventions: `web.py` (`build_*_html` functions).
- Access control: `_set_source_access`, `is_document_accessible`
  (see `routes/search.py` for usage).
- Cache access: `Database.get_cache(key)`; key derivation
  `cache_helpers.get_cache_key`.
- Registry loader: `analysis.load_motif_registry()` (call once at index
  build; the web path should read motif labels from the index, not the
  registry, so the site works without the private submodule).
- CLI to mimic for the index subcommand: `src/kb/run_motifs.py`.
- Known operational wart: `Database.initialize()` can hit a transient
  deadlock on `youtube_queue` when the crawler is active — CLI tools should
  simply be re-run; do not add retry complexity to the web path (it doesn't
  call `initialize()` per request).
