# Building the Attribute Vocabulary

Part of the representative-subset project: reduce ~2,000 ATMs to a 50–100 lesson
basis set for teaching materials. This document covers step one — deriving a
fixed, controlled list of lesson attributes so that every lesson can be scored
into an interpretable vector.

## Why a controlled vocabulary

The `lesson_concept` table already tags lessons with the right *kinds* of
things, but with free vocabulary. As of July 2026:

| category          | rows  | distinct values | problem |
|-------------------|-------|-----------------|---------|
| movement_pattern  | 4,665 | 1,143           | "flexion", "spinal flexion", "folding" are separate values |
| theme             | 3,200 | 2,133           | nearly every value unique — unusable for comparison |
| constraint        | 2,433 | 2,156           | same |
| body_region       | 6,122 | 307             | cleanest, but synonyms ("ribs"/"ribcage") |

Two lessons about the same thing get different strings, so nothing can be
compared, stratified, or counted across the corpus. A frozen vocabulary of
~25–40 attributes fixes this: every lesson gets a value for every attribute,
producing a dense, human-readable vector per lesson.

The attribute vector **complements** the text embedding, not replaces it:

- **Embedding** — similarity math, near-duplicate detection, diversity sampling
- **Attribute vector** — stratification, coverage auditing ("do I have any
  extension-dominant sitting lessons?"), and human-readable justification for
  why a lesson made the subset

## Derivation method: half bottom-up, half top-down

### 1. Bottom-up: mine the existing tags

The head of the existing tag distribution is exactly the vocabulary we want —
it just needs deduplication and a frequency cutoff:

1. Take `lesson_concept` values for `movement_pattern`, `body_region`,
   `working_position`, `theme`, `teaching_strategy` with frequency ≥ N
   (start with N=10; the long tail below that mostly consists of paraphrases
   of the head).
2. Embed the values, cluster (agglomerative over cosine distance works at this
   scale), and have an LLM propose a canonical name plus synonym map per
   cluster — e.g. "spinal extension", "back extension", "arching" → `extension`.
3. Emit a draft: canonical attribute, definition, member tags with counts.

This grounds the vocabulary in what is actually in the corpus rather than what
sounds plausible.

Reference — current top movement_pattern tags: rolling (348), head rotation
(207), reaching (116), pelvic clock (95), pelvic tilt (79), flexion (74),
walking (72), spiral (71), pelvic lift (70), lateral flexion (65), weight
shifting (61), spinal extension (54).

### 2. Top-down: canonical Feldenkrais axes

Reconcile the mined list against the known descriptive axes of the method, so
nothing important is missing just because the tagging pass under-produced it:

- Flexion / extension / rotation / side-bending
- Rolling, reaching, pushing/ground force, weight shifting
- Differentiation (eyes–head, head–shoulders, pelvis–ribs, ...)
- Breathing
- Transitions (lying ↔ sitting ↔ standing)
- Eye/oculomotor work, jaw/mouth work
- Developmental themes (crawling, creeping, rolling patterns)

### 3. Human review (the only step that needs judgment)

The merged draft goes to a single review pass. Decisions to make per entry:
keep / rename / merge / split / drop. Target size ~25–40 attributes. This is a
small one-time artifact where domain judgment matters more than any algorithm.

## Structure: facets, not clusters

Attributes are **independent axes** — each lesson gets a value on every one.
They are not mutually exclusive buckets. Proposed facet groups:

### Facet A: movement actions
`flexion`, `extension`, `rotation`, `side-bending`, `rolling`, `reaching`,
`pushing-ground-force`, `weight-shifting`, `pelvic-clock`, `bridging/pelvic-lift`,
`transitions-floor-to-sitting`, `walking/standing-integration`, `spiraling`

### Facet B: body-region emphasis
`pelvis`, `lumbar-spine`, `thoracic-spine/ribs`, `cervical-spine/neck`,
`shoulder-girdle/scapula`, `hips`, `feet/ankles`, `hands/arms`, `eyes`,
`jaw/mouth/face`, `breathing-apparatus`

### Facet C: working positions
`supine`, `prone`, `side-lying`, `sitting`, `kneeling/all-fours`, `standing`

### Facet D: pedagogical features
`uses-imagined-movement`, `left-right-comparison`, `unusual-constraint`,
`differentiation-focus`, `effort-reduction-focus`, `scan-heavy`,
`developmental-sequence`

(All candidates — the review pass decides the final list.)

## Output format

A single versioned file, e.g. `data/representative_subset/attribute_rubric.v1.yaml`:

```yaml
version: 1
attributes:
  - id: extension
    facet: movement_action
    name: Extension
    definition: >
      Lengthening/arching of the spine or hip extension as a worked movement
      theme — not incidental extension during transitions.
    synonyms: [spinal extension, back extension, arching, backbend]
    scoring_anchors:
      0: absent or purely incidental
      1: appears in one variation or briefly
      2: a significant component of the lesson
      3: the central theme of the lesson
```

Two rules once frozen:

1. **Every score is relative to a rubric version.** Changing a definition means
   bumping the version and rescoring that attribute.
2. **Ordinal scores (0–3) with anchors, not floats.** LLMs are consistent at
   "absent / incidental / significant / central"; they are not consistent at
   0.0–1.0.

## Validation before full rollout

Before scoring all ~1,500 covered lessons:

1. Score a pilot of ~50 lessons twice (or with two different models).
2. Measure per-attribute agreement (exact match and off-by-one rate).
3. Attributes that don't reproduce get redefined, merged, or dropped —
   an attribute the model can't score consistently isn't a real attribute.
4. Spot-check a handful against lessons you know well.

Scoring input should be the cached Pass A decomposition (groups/steps), not the
raw transcript — cheaper, already structured, and requiring an evidence quote
for any score ≥ 2 keeps the model honest.

## Storage

```sql
CREATE TABLE lesson_attribute (
    document_id uuid REFERENCES document(id),
    rubric_version int NOT NULL,
    attribute text NOT NULL,
    score smallint NOT NULL CHECK (score BETWEEN 0 AND 3),
    evidence text,
    created_at timestamptz DEFAULT now(),
    PRIMARY KEY (document_id, rubric_version, attribute)
);
```

Keyed by `document_id`, **not** by text hash. The analysis cache's
text-hash keying leaves orphaned entries whenever a transcript is re-run or
normalized (observed: stale Pass A entries in the cache that no current
document maps to). Attribute scores should survive transcript touch-ups;
if a transcript changes materially, rescore explicitly.

## Known prerequisite: redo compacted overviews

The condensed/Overview artifact (`:condense_v4`) is bimodal: 599 of 1,592
condensed lessons have fewer than 5 group cards (cluster at 3–4), from a period
when the condense query produced compacted summaries. If condensed output feeds
scoring or teaching materials, those 599 need a rerun first. Pass A
(`:decompose_v3`) is healthy — only ~16 current docs have <5 groups and nearly
all are talks/discussions where that is correct.

## Pipeline summary

1. Mine + cluster existing tags → draft vocabulary (script)
2. Merge with top-down canonical axes (script + LLM)
3. Human review → freeze `attribute_rubric.v1.yaml` (you)
4. Pilot: 50 lessons × 2 runs → agreement report (script)
5. Revise rubric if needed → full scoring pass over covered corpus (script)
6. Scores land in `lesson_attribute`, ready for stratified subset selection
