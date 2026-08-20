# Lesson similarity — measure specification (v1)

Implements the measures for `docs/SIMILARITY_SYSTEM.md`, as amended by
`docs/SIMILARITY_AMENDMENTS.md`, mapped onto the existing pass0b analysis. Every measure returns a value in [0,1]; every
component is inspectable so explanations fall out of the arithmetic rather
than being generated after the fact.

## 0. Preconditions (gates, applied before any scoring)

- **Type gate**: only `lesson_type = ATM` documents participate (no FI demos,
  lectures, discussions). Multi-lesson recordings must be split first.
- **Rendition gate**: compute rendition groups once, offline:
  `rendition(A,B)` if raw-transcript MinHash/shingle overlap > 0.65 **or**
  raw-embedding cosine > 0.985, **or** shared canonical-lesson identity
  (AY number / ATM-book lesson parsed from titles+metadata). Same group ⇒
  `lesson_key` shared. Renditions are *excluded* from similar/different/
  diverse results and surfaced only via an explicit "renditions" relation.

## 1. Per-lesson feature vector (sources in parentheses)

| Field | Form | Source |
|---|---|---|
| `regions` | weights over 12-region vocab | region tags + step mentions; ordinal → {1.0 primary, 0.6 secondary, 0.3 present} |
| `positions` | distribution over 8 positions | **computed from step durations** (position-v3 where present; tags fallback) |
| `themes` | weights over ~11 mechanical themes | movement-pattern tags, ordinal-mapped |
| `sensory` | weights over {eyes, breath, contact/floor, felt-length, vestibular} | language-layer channel annotations |
| `prox_dist` | distribution over 5 categories | aggregated per-step `transmission_route` |
| `diffs` | weighted set of part-pairs (canonically ordered, region-vocab parts) | per-step `differentiation` fields |
| `scalars` | rolling, reversibility, transition_emphasis, support_level, movement_scale, vigor, imagination_ratio ∈ [0,1] | ordinal LLM extraction (levels → numbers), steps where derivable |
| `arch` | soft one-hot over 7 architecture types + flags {side_compare, act_vs_relationship} | new extraction, cross-checked vs condensed group structure |
| `marks` | set of {mechanism_id, strength ∈ [0,1], mode ∈ taught/referenced} | new extraction, normalized vocabulary + synonyms (bell_hand=jellyfish_hand, …) |
| `E` | embedding of canonical movement summary | condensed-summary embedding (pgvector) |
| `meta` | duration and normalized content language | metadata |

**Ordinal, not free continuous**: the LLM never emits "0.8"; it emits levels
that map to fixed numbers. Weights that can be computed from step data are
computed, not asked.

## 2. Per-field similarity functions

### 2.1 Weighted-set fields (`regions, positions, themes, sensory, prox_dist`)
Ruzicka (generalized Jaccard):

    sim_F(A,B) = Σ_i min(a_i, b_i) / Σ_i max(a_i, b_i)      (0 if both empty → field missing)

Chosen over cosine because it is bounded, sparse-friendly, and **the min terms
are the explanation** ("both emphasize ribs: 0.8 vs 0.9").

### 2.2 Differentiation pairs (`diffs`)
Ruzicka over pair-space with a pair kernel: exact pair = 1; pairs sharing one
part = 0.5 (v1 may ship exact-only; the kernel is a flagged v1.1).

### 2.3 Scalars
Per scalar: `1 − |a − b|`. Field score = mean over scalars present in both.

### 2.4 Architecture (`arch`)
Affinity matrix over types (1.0 diagonal; hand-set neighbor credit, e.g.
`single_motif ↔ variations_around_motif = 0.6`; unrelated = 0). Plus flag
agreement: `0.7·type_affinity + 0.15·[side_compare equal] + 0.15·[act_vs_rel equal]`.

### 2.5 Embedding (`E`) — with mandatory calibration
Raw cosine over canonical-summary embeddings, then **quantile-normalized**
against the empirical distribution of 10k random ATM pairs:

    sim_E(A,B) = ECDF_corpus( cos(E_A, E_B) )

Without this, same-domain cosines (all ≈0.6–0.95) flatten or dominate the
blend. The ECDF is a stored, versioned artifact.

### 2.6 Metadata
`sim_dur = exp(−|Δduration| / 30 min)`. For known normalized content-language
codes, `sim_lang = 1` when the primary language codes match and `0` otherwise;
missing/`unknown` language is dropped and the blend is renormalized. Language is
a small usability term, not evidence of movement similarity. Teacher/source
**never** enter similarity (available only as explicit filters).

## 3. Aggregation

### 3.1 Structured score
    S(A,B) = Σ_F w_F · sim_F(A,B)  /  Σ_{F present in both} w_F

Missing/empty fields are dropped and weights renormalized (older sparse
analyses aren't punished); participating fields are recorded for the
explanation. Default w_F: regions .18, themes .16, positions .12,
diffs .14, prox_dist .10, sensory .08, scalars .10, arch .12.

### 3.2 Base blend
    Base(A,B) = 0.52·S + 0.38·sim_E + 0.05·sim_dur + 0.05·sim_lang

Present terms are renormalized when structured data, embedding, duration, or
language is unavailable.

### 3.3 Trademark lift (not a weighted field)
    t(A,B) = max over shared mechanisms of  m(mode_A, mode_B) · min(strength_A, strength_B)
      m(taught,taught)=1.0, m(taught,referenced)=0.3, m(referenced,referenced)=0
      (later: ontology relatedness multiplier, e.g. closely_related=0.6)

    Sim(A,B) = Base + λ · t · (1 − Base),   λ = 0.5

Interpolation-toward-1 is bounded, monotone, and directly explainable
("shared taught mechanism *bell_hand* lifted 0.61 → 0.81").

## 4. Retrieval operators

- **Similar**: top-k by `Sim`, renditions excluded.
- **Rendition list**: the `lesson_key` group, separate endpoint.
- **Maximally different**: bottom-k by `Sim` among profile-complete ATMs.
- **Contrastive(A; hold H, vary V)** (H, V = field groups):
      score = sim_H(A,B) − sim_V(A,B) + 0.05·Base   → top-k
  Generic over any field pair; covers all the spec's examples.
- **Diverse alternatives** (MMR): greedily pick
      argmax_B [ 0.7·Sim(A,B) − 0.3·max_{C∈picked} Sim(B,C) ],  k=5.

## 5. Explanation (deterministic, no LLM required)

Each result carries: per-field `(sim_F, w_F, contribution)`; the top-3 shared
elements (largest min-terms inside Ruzicka fields); the top-3 divergences
(largest |a_i−b_i|); trademark lift if applied; fields dropped as missing.
Template-render to prose; optional LLM polish later. The number is never
shown without its decomposition.

## 6. Calibration, tuning, and evaluation

- **Weight tuning (v1.5)**: LLM-judged triplets ("does B or C more resemble
  A?"), ~500, actively sampled where field rankings disagree; fit weights by
  Bradley–Terry / logistic regression on triplet constraints. Human triplets
  from the Atlas chooser can replace/augment later (same data shape).
- **Fixture tests (session-derived ground truth, run on every reprocess)**:
  1. Bell-hand family (Haller cluster + DZB Segment 7 + Soothe & Calm +
     FP/OpenATM): precision@10 from any member.
  2. **Circular vs. Global Breathing: near but distinguishable** — margin
     test: renditions of each cluster together; the two clusters must not merge
     (Sim within-cluster − Sim across-cluster > 0.1).
  3. Rendition gate: the known duplicate pairs never appear in similar-results.
  4. Reciprocal/seesaw-breathing set: mechanism lift fires on taught, not on
     the referenced-only lessons (Dragging Arms case).
  5. **Teacher-leakage**: teacher predictability (AUC) from canonical-summary
     embeddings must drop materially vs. raw-transcript embeddings; alarm if
     same-teacher pairs' mean Sim exceeds cross-teacher mean by > 0.05 after
     controlling for mechanism.
- **Versioning**: every artifact (profiles, ECDF, matrix, weights) keyed by
  `(schema_version, extraction model, weight set)`; content-hash caching per
  house pattern; full reprocess is normal.

## 7. Storage / compute

Postgres: `lesson_profile` (jsonb + scalars + lesson_key + versions);
embeddings stay in pgvector. The all-pairs matrix (float16, ~2k lessons now,
sized for 10k) is a derived artifact (NumPy/Parquet), rebuilt on version bump,
incrementally extended on new lessons (one row/column each). No vector DB.
