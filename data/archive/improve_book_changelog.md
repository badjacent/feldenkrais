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

---

## Iteration 6 — 2026-02-03

*Note: Prior session also committed b99bb1f (pass_b anti-repetition rules, VL-4 mechanical metaphors, shadow voice structure rules) which is reflected in the baseline review.*

### Issues addressed
- #1 (high): Verbatim repetition between 'The Question' and 'How the Lesson Washed Away Effort'
- #3 (high): VL-4 voice inconsistency — shifting from analytical to 'influencer' language
- #4 (medium): Pedagogical glosses using academic teacher-speak

### Changes made
- `src/kb/web.py`: Removed `washing_gradient` from `render_booklet_inquiry()` — it was displayed in both Question and Arc sections, causing verbatim duplication. Now only appears in Arc.
- `src/kb/web.py`: Rewrote pedagogical-gloss prompt to require mechanical logic with example ("Supporting the knee removes leg weight..."), blacklisted teacher-speak phrases.
- `data/frameworks/v1/vl4.md`: Added blacklist of informal adjectives (juicy, delicious, magical, etc.) to enforce technical diagnostic voice.

### Commit
`c7acbbc`

### Performance
- booklet:VL-1: 16.8s
- booklet:VL-2: 17.7s
- booklet:VL-3: 15.6s
- booklet:VL-4: 10.8s
- booklet:VL-5: 15.8s

### Review impact
- Old #1 (Question/Arc verbatim repetition) **resolved** — no longer appears in review
- VL-4 voice still flagged (#3) but improved — now about "lacks technical rigor" not "influencer fluff"
- Pedagogical glosses still flagged (#4) but improved — now about "abstract jargon" not "teacher-speak"
- New #1: Static 'How Lessons Work' boilerplate (was #10 last time, now top priority)
- New #2: Arc timeline redundant with Journey badges (persistent issue)

---

## Iterations 11-20 — 2026-02-05

*Automated batch of 10 iterations targeting accessibility, jargon elimination, and boilerplate removal.*

### Iteration 11
- **Issues:** #1 jargon in lesson-concepts, #2 boilerplate booklet-intro, #3 truncation
- **Changes:** Added jargon ban to lesson-concepts (contralateral, proximal, etc.), anti-boilerplate rule to booklet-intro, completeness guards to booklet-reflection and lens-synthesis
- **Commit:** `80d0f09`
- **Impact:** Truncation (#3) fully resolved. Jargon (#1) dropped to #4.

### Iteration 12
- **Issues:** #1 lens-synthesis boilerplate, #2 abstract jargon, #3 scientific tone in reflection
- **Changes:** Added anti-boilerplate and jargon ban to lens-synthesis prompt, plain language ban to booklet-reflection
- **Commit:** `20eabb0`
- **Impact:** Boilerplate shifted — now about hardcoded HTML, not LLM output.

### Iteration 13
- **Issues:** #1 hardcoded "Every Feldenkrais lesson..." boilerplate, #2 undefined Washing/Foundation, #3 abstract metaphors
- **Changes:** Removed hardcoded Question preamble from HTML template, added inline-definition requirement for Washing/Foundation in lesson-concepts, banned abstract metaphors in booklet-reflection
- **Commit:** `2ac7a4b`
- **Impact:** Hardcoded boilerplate fully eliminated. Jargon issues now focus on VL-specific terms.

### Iteration 14
- **Issues:** #1 dry Journey glosses, #2 clinical jargon in gloss, #5 VL-5 assessment language
- **Changes:** Reframed pedagogical-gloss to focus on "felt consequence", expanded gloss banned nouns (motor plan, etc.), added clinical assessment ban to vl5.md
- **Files:** `src/kb/web.py`, `data/frameworks/v1/vl5.md`
- **Commit:** `b64840f`

### Iteration 15
- **Issues:** #1 instruction-echoing in Journey, #2 jargon leaking through lens-synthesis, #5 generic rest
- **Changes:** Reframed gloss as "invisible lesson", strengthened jargon ban with compound-term prohibition (eye-differentiation challenges, midline habit), added rest-section specificity rule
- **Commit:** `8fc8277`

### Iteration 16
- **Issues:** #1 clinical jargon in booklet-reflection, #4 abstract vocab in lesson-concepts, #8 inconsistent accessibility
- **Changes:** Expanded reflection jargon ban (skeletal resonance, heavy propping, etc.), added everyday-physics requirement to lesson-concepts, added global 8th-grade reading level constraint to all booklet queries
- **Commit:** `14aabc7`

### Iteration 17
- **Issues:** #1 abrupt endings in Journey, #2 jargon still in lens-synthesis, #5 generic rest instructions
- **Changes:** Added completeness guard to pedagogical-gloss, moved jargon ban to TOP of lens-synthesis prompt (primacy effect), added lens-specific rest handling rule
- **Commit:** `725094b`

### Iteration 18
- **Issues:** #1 VL-5 Red Light reflex jargon, #2 VL-1 "buoyant column", #4 textbook tone
- **Changes:** Added plain-language translation table to vl1.md (buoyancy → "feeling of being held up", etc.), expanded lesson-concepts jargon ban (Red Light reflex, buoyant column, ground reaction force), banned reorganize/integrate verbs
- **Files:** `data/frameworks/v1/vl1.md`, `src/kb/web.py`
- **Commit:** `fe1d1c0`

### Iteration 19
- **Issues:** #1 anatomical terms (cervical, supine), #4 VL-5 Red Light reflex, #5 voice inconsistency
- **Changes:** Banned cervical/lateral/supine/prone in lesson-concepts with translations, added plain-language translation table to vl5.md, enforced second-person voice in pedagogical-gloss
- **Files:** `data/frameworks/v1/vl5.md`, `src/kb/web.py`
- **Commit:** `fc7d94c`

### Iteration 20
- **Issues:** #1 clinical movement terms in Journey, #4 VL-3 signal jargon, #7 anatomy in booklet-intro
- **Changes:** Added plain-language translation table to vl3.md (motor plan → "what you intended to do", etc.), expanded gloss banned terms (vertical column, signal static, visual field, sacrum), added plain anatomy rule to booklet-intro
- **Files:** `data/frameworks/v1/vl3.md`, `src/kb/web.py`
- **Commit:** `86bf15b`

### Performance (all iterations)
Booklet generation times remained stable throughout:
- booklet:VL-1: 16.5s
- booklet:VL-2: 15.0s
- booklet:VL-3: 14.4s
- booklet:VL-4: 13.7s
- booklet:VL-5: 15.1s

No regressions detected. No rollbacks needed.

### Summary of progress
**Resolved issues:**
- Hardcoded "Every Feldenkrais lesson..." boilerplate (eliminated in iter 13)
- Truncated text in booklet-reflection and lens-synthesis (eliminated in iter 11)
- Undefined Washing/Foundation jargon (inline definitions added in iter 13)

**Significantly improved:**
- Clinical/anatomical jargon (extensive ban lists across all components, plain-language translations for VL-1, VL-3, VL-5)
- Generic boilerplate in booklet-intro (anti-boilerplate, unique hooks required)
- Journey section tone (shifted from instruction-echoing to "invisible lesson" framing)
- Reading level (global 8th-grade constraint added)

**Persistent issues (addressable in future iterations):**
- Textbook tone still creeps into "What Changed" sections via lens-synthesis
- VL-3 (Tuner) specificity remains challenging — signal metaphors need more grounding
- Generic rest/scanning instructions still appear in Journey despite rules
