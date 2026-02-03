# /improve-book Changelog

## Iteration 1 — 2026-02-03

### Issues addressed
- #1 (high): Verbatim content duplication across The Question, The Arc, and The Lens sections
- #2 (high): Inconsistent terminology and hallucinations (Council vs Foundation vs Riser)
- #9 (medium): Volume introduction fails to orient reader to five lenses

### Changes made
- `data/frameworks/v2/pass_b.md`: Added Column Differentiation Rules (Question=inquiry, Negotiation State=process, Functional Outcome=result). Added Terminology Guardrails (Person Inside not Council). Fixed "Council has taken over" → "Person Inside has taken over".
- `src/kb/web.py`: Updated volume-intro prompt to include brief one-sentence lens previews.

### Commit
`3742ca8`

### Performance
Baseline (pre-changes): booklet:VL-1 = 139.7s

### Review impact
Not yet measured (pipeline hadn't regenerated).

---

## Iteration 2 — 2026-02-03

### Issues addressed
- #2 (high): Shadow Voice mischaracterization (describing success instead of habit)
- #3 (high): Formulaic 'Mad Libs' style chapter introductions
- #4 (high): Lens Bleed — over-reliance on 'cortical noise' as universal explanation

### Changes made
- `data/frameworks/v2/pass_b.md`: Added strict Shadow Voice Rules — Shadow must always describe habit/effort, never clarity or success. Added arc progression guidance.
- `src/kb/web.py`: Added forbidden phrases list to booklet-intro prompt (6 items).
- `data/frameworks/v2/pass_b.md`: Added lens-specific alternatives to "cortical noise" (limit once per booklet).

### Commit
`83b4258`

### Performance
Not yet measured.

### Review impact
Next review still showed "cortical noise" as #1 issue. Shadow voice (#8) and naming (#2) issues persisted but shifted rank.

---

## Iteration 3 — 2026-02-03

### Issues addressed
- #5 (medium): Naming discrepancy (Cartographer vs Body Mapping)
- #6 (medium): Generic Lens section repeats definitions instead of analyzing lesson
- #7 (medium): Formulaic and clinical pedagogical glosses

### Changes made
- `data/frameworks/v2/frameworks.json`: Renamed VL-2 from "Body Mapping" to "The Cartographer" for consistency with other lenses.
- `src/kb/web.py`: Added `lens-synthesis` query type — new LLM call per booklet to synthesize lens with specific lesson mechanics. Updated `render_booklet_lens` to include "In This Lesson" section.
- `src/kb/web.py`: Rewrote pedagogical-gloss prompt with verbose forbidden patterns and examples.

### Commit
`106574a`, `c8b8e14` (KeyError fix for shadow_description in prompt_vars)

### Performance
**Severe regression.** Booklet generation times:
- VL-1: 139.7s (pre-changes, baseline)
- VL-2: 394.1s (2.8x slower)
- VL-3: 459.5s (3.3x slower)
- VL-4: 745.6s (5.3x slower)
- VL-5: 466.3s (3.3x slower)

Cause: Verbose pedagogical-gloss prompt (runs 7-8x per booklet) + extra lens-synthesis LLM call.

### Review impact
Review showed naming issue (#2) persisted (likely stale cache). "Council" hallucination (#3) still present.

### Reverted/simplified
See Iteration 4.

---

## Iteration 4 — 2026-02-03

### Issues addressed
- #1 (high): Verbatim duplication of 'cortical noise' text across sections
- #6 (medium): Next Time section relies on wordy platitudes

### Changes made (+ simplifications from Iteration 3)
**Simplifications to fix performance regression:**
- `src/kb/web.py`: Pedagogical-gloss prompt: 921 chars → ~280 chars. Removed verbose forbidden patterns and examples, kept one-line instruction.
- `src/kb/web.py`: Booklet-intro prompt: 1211 chars → ~850 chars. Trimmed 6-item forbidden phrases to brief note.
- `src/kb/web.py`: Removed `lens-synthesis` LLM query block entirely (saves 1 call per booklet). Documented in `data/archive/lens_synthesis_feature.md`.

**New fixes:**
- `data/frameworks/v2/pass_b.md`: Required Primary Inquiry and Washing Gradient to be distinct paragraphs. Replaced "cortical noise" in Washing Gradient description.
- `src/kb/web.py`: Booklet-reflection: added "no platitudes, give actionable movement cues."

### Commit
`6eee26f`

### Performance
Awaiting pipeline run with simplified prompts. Expected to return closer to ~140s baseline.

### Review impact
Awaiting next review.

---

## Infrastructure fixes (same session)

### Pipeline resilience
- `e3c4e65`: Clear output_hash on ERROR so downstream nodes become stale
- `a7fdc4f`: Cascade staleness when upstream is STALE/ERROR/PENDING (not just missing output)
- `c7aa767`: Add retry logic (3 attempts, exponential backoff) to QueryAgent._generate

### Skill updates
- `fde44dd`: Added `loop N`, `auto`, `wait` arguments to /improve-book skill
- `ea3b393`: Added changelog tracking to /improve-book workflow

---

## Iteration 5 — 2026-02-03

### Issues addressed
- #2 (high): Verbatim text repetition — "cortical noise" paragraphs duplicated between sections
- #3 (high): Lens bleed VL-1 vs VL-4
- #4 (high): Prescriptive sensory voice ("You felt," "You likely noticed")

### Changes made
- `data/frameworks/v2/vl4.md`: Added "Distinction from VL-1" section — VL-1=vertical force, VL-4=horizontal power. Forbids VL-1 language (gravity, ground reaction, vertical column) in Engine analysis.
- `data/frameworks/v2/pass_b.md`: Added Voice constraints — no prescriptive "You felt" language, no reusing paragraphs across sections.

### Commit
(pending)

### Performance
Previous iteration with simplified prompts:
- VL-1: 72.9s, VL-3: 63.6s, VL-4: 61.7s, VL-5: 68.2s
(VL-2: 391.4s was built from old verbose templates)

### Review impact
Awaiting next review.
