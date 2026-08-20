# Similarity system — accepted amendments (2026-08-19)

Reading order for the implementing agent:
`SIMILARITY_SYSTEM.md` (the system spec) → this file (owner-accepted deltas)
→ `SIMILARITY_MEASURES.md` (the concrete measure implementation, which already
incorporates these). Where documents disagree, this file wins over the system
spec; the measures doc wins on formula details.

1. **Build §1 as a projection over pass0b, not fresh extraction.** Most of the
   structured profile already exists in the analysis database: body-region /
   movement-pattern / theme / position tags (~1,200 lessons), step-level
   position data (compute position weights from step durations), per-step
   somatic fields (`transmission_route` → prox/dist; `differentiation`,
   `inhibition` → coupling pairs; `ground_push` → support), language-layer
   sensory-channel annotations, condensed summaries (≈ the canonical movement
   summary), and embeddings already in pgvector. Genuinely new extraction is
   limited to: **trademark mechanisms** (1.1) and **lesson architecture** (1.9),
   plus ordinal weighting passes over existing tags.
2. **Renditions are a first-class relation, gated before scoring.** The corpus
   contains duplicates and same-lesson-different-teacher recordings (Circular
   Breathing ×2, Global Breathing ×2, AY lessons in multiple renditions).
   Detect rendition groups (`lesson_key`) via transcript shingling / raw-cosine
   / shared AY-or-book identity; exclude from similar/different/diverse
   results; expose as a separate "renditions" listing.
3. **Trademark mechanisms carry a taught-vs-referenced mode.** Lexical matching
   fails in both directions (lessons that teach the bell hand without naming
   it; lessons that only reminisce about it). Extraction must be semantic, the
   vocabulary normalized with synonyms (bell_hand = jellyfish_hand), and
   `mode ∈ {taught, referenced}` gates the similarity lift.
4. **Three axes added to the schema** (dropped from the owner's own
   practitioner taxonomy): `sensory_emphasis` (eyes / breath / floor-contact /
   felt-length), `movement_scale` + `vigor` (micro↔large, quieting↔vigorous),
   and `imagination_ratio` (imagined vs executed movement). Architecture also
   gains flags: `side_compare` (one-side-then-compare structure) and
   `act_vs_relationship`.
5. **Ordinal extraction, computed weights.** The LLM emits levels
   (primary/secondary/present), never free-form 0–1 strengths; anything
   derivable from step data is computed, not asked.
6. **Type gate**: ATM lessons only (the corpus includes FI demos, lectures,
   discussions); multi-lesson recordings must be split before profiling.
7. **Corpus size**: ~2,050 transcribed today (not 1,000), growing toward
   5–10k via the free-lesson census. Size artifacts for 10k; define
   incremental matrix updates; full reprocess remains normal.
8. **Embedding calibration is mandatory** (quantile-normalize cosines against
   random-pair ECDF) — see measures doc §2.5.
9. **Evaluation fixtures are pre-registered** from known ground truth:
   bell-hand family precision, the Circular-vs-Global "near but
   distinguishable" margin test, rendition exclusion, taught-only mechanism
   lift, and the teacher-leakage AUC alarm — see measures doc §6.
10. **Unify with the Atlas chooser**: one representation, two consumers
    (this internal tool; the public chooser's quantized, paywall-filtered
    projection per the Atlas repo's `docs/CHOOSER_DESIGN.md`). Human triplets
    from the chooser later replace LLM-judged triplets for weight tuning.
    Do not build a second, divergent similarity engine.
