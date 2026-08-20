# Feldenkrais Lesson Similarity System

## Goal

Build a system for a corpus of roughly 1,000 Feldenkrais lesson recordings that can, given one lesson:

* find lessons that are similar
* find lessons that are meaningfully different
* explain why lessons are similar or different
* eventually discover natural families or clusters of lessons

Each lesson has:

* an audio recording
* a transcript
* ordinary metadata such as duration
* potentially teacher, source, date, lesson title, etc.

The important problem is not computational scale. With only ~1,000 lessons, brute-force comparison is inexpensive. The main design problem is defining a useful representation of **what a Feldenkrais lesson is doing**.

---

## Core Approach

Represent each lesson in two complementary ways:

1. **An interpretable structured Feldenkrais profile**
2. **A semantic embedding based on a canonical movement-oriented summary**

Metadata such as lesson duration is a third, lower-weight source of information.

Conceptually:

```text
Lesson
  |
  +-- Structured Feldenkrais profile
  |
  +-- Canonical movement summary -> embedding
  |
  +-- Metadata
```

Similarity should combine these rather than rely on a single raw transcript embedding.

---

# 1. Structured Feldenkrais Profile

Use an LLM to analyze each transcript and emit a constrained structured representation.

The initial schema should focus on features that appear useful for recognizing lesson families.

## 1.1 Trademark movement or mechanism

Some lessons have a highly recognizable movement or device that functions almost as a signature.

Examples:

* pelvic clock
* seesaw breathing
* bell hand
* rolling the head with the eyes
* heel pressing into the floor
* walking the pelvis
* sliding a shoulder blade
* coordinated opening and closing of the knees
* rolling from back toward side through an arm or leg

Some lessons have no obvious trademark movement.

Suggested representation:

```json
{
  "trademark_movements": [
    {
      "name": "bell hand",
      "strength": 0.9
    }
  ]
}
```

Allow:

```json
{
  "trademark_movements": []
}
```

Trademark mechanisms should eventually use normalized names rather than arbitrary strings.

This feature may deserve special treatment in similarity scoring because a strong trademark match can be more informative than several weaker anatomical similarities.

---

## 1.2 Primary body regions

Represent the obvious intended bodily emphasis of the lesson, not every body part mentioned.

Possible initial vocabulary:

* head_neck
* eyes
* jaw_mouth
* shoulders_scapulae
* arms_hands
* ribs_chest
* spine
* pelvis
* hips
* legs
* feet
* whole_body

Use continuous weights rather than binary tags.

Example:

```json
{
  "body_regions": {
    "shoulders_scapulae": 0.8,
    "ribs_chest": 0.9,
    "arms_hands": 0.7,
    "pelvis": 0.2
  }
}
```

---

## 1.3 Position

Capture the dominant physical configuration.

Possible values:

* supine
* prone
* side_lying
* sitting
* kneeling
* standing
* mixed
* transitional

Prefer proportional representation when a lesson uses multiple positions.

```json
{
  "positions": {
    "supine": 0.8,
    "side_lying": 0.2
  }
}
```

Starting position and dominant position can later be separated if useful, but do not introduce that complexity initially unless the corpus makes it necessary.

---

## 1.4 Major mechanical or spatial themes

Combine several potentially redundant dimensions under one category.

Initial vocabulary:

* flexion
* extension
* rotation
* side_bending
* rolling
* translation_sliding
* reaching_lengthening
* folding
* weight_shift
* support_transfer
* arching_rounding

Again use strengths:

```json
{
  "mechanical_themes": {
    "rotation": 0.8,
    "rolling": 0.5,
    "flexion": 0.2
  }
}
```

A lesson can strongly contain more than one theme.

---

## 1.5 Proximal/distal organization

Capture where movement appears to originate and how it propagates.

Possible conceptual categories:

* primarily proximal
* proximal_to_distal
* distal_to_proximal
* reciprocal
* whole_chain

A continuous representation may ultimately be preferable.

For example:

```json
{
  "proximal_distal": {
    "distal_to_proximal": 0.8,
    "reciprocal": 0.2
  }
}
```

Examples:

* move the hand and discover effects in ribs/pelvis -> distal to proximal
* move the pelvis and allow the arm to follow -> proximal to distal

---

## 1.6 Coupling and differentiation

Capture which parts of the person are being differentiated, coupled, opposed, or recombined.

Examples:

* eyes vs head
* head vs sternum
* shoulders vs ribs
* ribs vs pelvis
* pelvis vs legs
* arm vs shoulder
* leg vs pelvis

Possible structured form:

```json
{
  "differentiations": [
    {
      "parts": ["shoulder", "ribs"],
      "strength": 0.9
    },
    {
      "parts": ["arm", "shoulder"],
      "strength": 0.6
    }
  ]
}
```

There may also be a coarse global measure such as:

```json
{
  "differentiation_level": 0.8
}
```

This category may absorb much of what might otherwise be called "constraint structure."

---

## 1.7 Rolling and reversibility

These are related enough to initially treat together while retaining separate scalar values.

Possible attributes:

```json
{
  "rolling": 0.6,
  "reversibility": 0.9,
  "transition_emphasis": 0.4
}
```

Interpretation:

* `rolling`: importance of rocking or rolling as a movement pattern
* `reversibility`: degree to which easy return and continuous reversibility are central
* `transition_emphasis`: degree to which the lesson is about moving between configurations rather than remaining within one

---

## 1.8 Support / gravity demand

Represent how much the lesson depends on support and weight-bearing.

Possible continuum:

```text
highly supported
partially supported
active weight shifting
weight bearing
upright / gravity demanding
```

A scalar may be sufficient:

```json
{
  "support_level": 0.9
}
```

Where a higher value means more external support / less active weight-bearing.

---

## 1.9 Lesson architecture

Capture the broad organizational structure of the lesson.

Initial categories:

* single_motif_progressively_elaborated
* variations_around_motif
* parts_then_integration
* side_a_side_b_comparison
* multiple_explorations_converging
* functional_progression
* diffuse_exploration

Example:

```json
{
  "lesson_architecture": {
    "type": "single_motif_progressively_elaborated",
    "confidence": 0.8
  }
}
```

This may be one of the more subjective fields and should be evaluated carefully during validation.

---

## 1.10 Functional theme

Optional.

Use only when there is a conspicuous functional organization, such as:

* reaching
* rolling over
* sitting up
* standing
* walking
* stepping
* looking/orienting
* breathing

Do not force every lesson into a functional category.

---

# 2. Metadata

Include ordinary objective metadata separately from the Feldenkrais analysis.

For example:

```json
{
  "duration_minutes": 47,
  "teacher": "...",
  "source": "...",
  "date": "...",
  "title": "...",
  "language": "en"
}
```

Duration should probably have relatively little weight in similarity.

Normalized content language should also have a small weight. Same-language
lessons are slightly more useful alternatives for a chooser; this must remain a
minor objective metadata term rather than a proxy for movement similarity.

Teacher should generally **not** directly affect movement similarity unless explicitly requested.

Otherwise the system risks grouping lessons by teaching style rather than movement content.

---

# 3. Canonical Movement Summary

In addition to the structured profile, generate a short standardized natural-language description of the lesson.

Target roughly 150–300 words.

The summary should describe:

* dominant position
* central movements
* body regions
* how movement propagates
* major differentiations
* rolling or weight transfer
* progression of the lesson
* what appears to be its main organizing mechanism

It should **not** simply summarize the transcript.

Example:

> Supine lesson organized around movements of the right hand and arm. Small rotation of the hand progressively involves the shoulder, ribs, and sternum. The lesson explores differentiation of arm movement from movement of the chest while repeatedly allowing the movement to propagate proximally. There is mild rolling toward the side but no full transition. Movements remain small and readily reversible. A single distal movement is progressively elaborated into a larger whole-body relationship.

The purpose of this canonical summary is to strip away irrelevant transcript variation such as:

* teacher verbal style
* repetitions
* jokes or digressions
* generic Feldenkrais instructions
* introductory remarks
* excessive anatomical wording
* pauses and transcription artifacts

Generate an embedding from this canonical summary.

Do **not** rely exclusively on an embedding of the raw transcript.

---

# 4. Lesson Representation

Conceptually represent each lesson as:

[
L_i = (S_i, E_i, M_i)
]

where:

* (S_i) = structured Feldenkrais features
* (E_i) = embedding of canonical movement summary
* (M_i) = metadata

The structured feature vector should remain human-readable.

The semantic embedding can be high-dimensional and opaque.

---

# 5. Similarity

Similarity between two lessons should consist of multiple component similarities.

Conceptually:

[
Sim(A,B)
========

w_s Sim_S(A,B)
+
w_e Sim_E(A,B)
+
w_m Sim_M(A,B)
]

A reasonable starting point might be:

```text
Structured Feldenkrais features: 50%
Semantic embedding:             40%
Metadata:                       10%
```

These are placeholders and should be tuned empirically.

---

# 6. Structured Similarity

Within the structured features, start with approximate weights such as:

```text
trademark movement       20%
body regions             15%
position                 10%
mechanical themes        15%
proximal/distal           8%
differentiation          10%
rolling/reversibility     8%
support/gravity           4%
lesson architecture      10%
```

These numbers should **not** be considered authoritative.

The system should be designed so weights can easily be changed.

---

# 7. Trademark Mechanisms Need Special Handling

A trademark movement may be stronger evidence of lesson family than an ordinary feature.

For example, two pelvic-clock lessons may be closely related even when:

* one lasts 40 minutes and the other 70
* one remains supine and another progresses to standing
* one emphasizes breathing and another emphasizes hip movement

Possible approach:

[
Similarity
==========

BaseSimilarity
+
\lambda \cdot TrademarkMatch
]

Eventually create a small ontology for trademark mechanisms.

Example:

```text
pelvic_clock
  closely_related:
    - pelvic_rolling
    - pelvic_differentiation

shoulder_clock
  partially_related:
    - scapular_sliding
```

Do not build a large ontology before observing the extracted corpus.

---

# 8. Similarity Matrix

At this corpus size, computing every pairwise similarity is trivial.

For 1,000 lessons:

[
1000^2 = 1,000,000
]

comparisons.

A matrix can therefore be computed and cached:

```text
             lesson1 lesson2 lesson3 ...
lesson1        1.00    0.74    0.21
lesson2        0.74    1.00    0.32
lesson3        0.21    0.32    1.00
...
```

This can support immediate nearest-neighbor retrieval.

However, treat the similarity matrix as a derived artifact.

The canonical data should remain the lesson profiles and embeddings.

There is no need for a vector database in the first version.

SQLite, Postgres, Parquet, NumPy, or similar storage is sufficient.

---

# 9. Different Kinds of "Different"

Do not assume that "different" always means the lesson with minimum similarity.

Support at least three concepts.

## 9.1 Maximally Different

Find lessons with lowest overall similarity.

Useful for:

> Show me something completely unlike this lesson.

---

## 9.2 Contrastive

Find lessons that share one important dimension while differing strongly on another.

Examples:

* same body region, different movement organization
* same position, different mechanical theme
* same trademark mechanism, different progression
* both shoulder/rib lessons, but one distal-to-proximal and one proximal-to-distal
* same pelvis emphasis, but one supine and one upright/weight-bearing

This is likely to be more interesting pedagogically than pure minimum similarity.

---

## 9.3 Diverse Alternatives

Given a lesson, return a set of related lessons that do not all resemble one another.

For example, instead of returning five nearly identical pelvic-clock lessons:

1. close pelvic-clock lesson
2. related pelvis/rib differentiation lesson
3. rolling lesson
4. upright weight-transfer lesson
5. distal-to-proximal lesson with analogous organization

A simple Maximal Marginal Relevance-style selection could work:

* reward similarity to the query
* penalize similarity to already-selected results

---

# 10. Explainability

Every retrieval result should eventually be explainable.

Example:

> **Lesson 281 — similarity 0.82**
>
> Similarities:
>
> * both are predominantly supine
> * both emphasize ribs and shoulders
> * both work distal-to-proximal
> * both differentiate shoulder movement from rib movement
>
> Differences:
>
> * Lesson 281 uses substantially more rolling
> * it involves the pelvis earlier
> * its structure moves from several variations toward a whole-body roll

This is important both for users and for debugging the similarity model.

Avoid presenting only:

```text
cosine similarity = 0.82
```

---

# 11. LLM Extraction Pipeline

Initial processing pipeline:

```text
Audio
  |
Transcript
  |
  v
LLM Lesson Analyzer
  |
  +----------------------------+
  |                            |
  v                            v
Structured Profile      Canonical Movement Summary
  |                            |
  v                            v
Feature Vector              Embedding
  |                            |
  +-------------+--------------+
                |
                v
         Similarity Engine
                |
       +--------+--------+
       |                 |
       v                 v
Nearest Neighbors   Contrast/Diversity
       |
       v
Similarity Explanation
```

The LLM should receive explicit extraction instructions and a constrained schema.

Prefer JSON-schema or typed structured output rather than free-form parsing.

---

# 12. Validation Strategy

Do not manually classify all 1,000 lessons before building the system.

Instead:

1. Define an initial schema.
2. Run extraction across the corpus.
3. Manually inspect roughly 50–100 representative lessons.
4. Inspect nearest-neighbor results.
5. Identify fields that are:

   * informative
   * redundant
   * vague
   * inconsistently extracted
   * missing
6. Revise the schema.
7. Re-run extraction across the entire corpus.

At this scale, reprocessing the whole corpus should be considered normal.

---

# 13. Expect the Corpus to Suggest New Features

Do not assume the current taxonomy is complete.

Repeated patterns may emerge during inspection, such as:

* movement initiated by pressure into the floor
* changing contact with support
* contralateral vs ipsilateral organization
* crossing the midline
* orientation of eyes/head relative to movement
* limb used as a lever
* limb moved independently
* use of one side to organize the other
* spiraling organization
* explicit inhibition of a habitual movement
* oscillatory vs directional movement
* movement generated through skeletal transmission rather than muscular effort

If a pattern repeatedly explains why humans regard lessons as similar, promote it into the structured schema.

---

# 14. Clustering and Discovery

Once useful representations exist, use them not only for retrieval but for exploratory analysis.

Possible analyses:

* hierarchical clustering
* k-means or related clustering
* UMAP or other low-dimensional projection
* nearest-neighbor graphs
* community detection

The goal is not necessarily to impose known Feldenkrais categories.

A particularly interesting question is:

> What natural families of lessons emerge from the corpus?

For example, a Bell Hands lesson may turn out to cluster less strongly with other "hand lessons" than with a larger family characterized by:

```text
small distal movement
        ↓
proximal propagation
        ↓
rib / shoulder differentiation
        ↓
larger whole-body organization
```

Such emergent families may be more meaningful than classifications based purely on anatomy.

---

# 15. First-Version Technical Scope

Keep V1 deliberately simple.

## Required

* ingest transcript + metadata
* LLM structured extraction
* LLM canonical summary
* semantic embedding generation
* persistent storage
* configurable similarity calculation
* all-pairs similarity matrix
* nearest-neighbor lookup
* similarity explanation
* basic inspection/debug UI or CLI

## Not required initially

* vector database
* elaborate ontology
* training a custom embedding model
* supervised machine learning
* complex clustering UI
* manual tagging of entire corpus

---

# 16. Suggested Initial Data Model

A lesson record could resemble:

```json
{
  "lesson_id": "lesson_0347",

  "metadata": {
    "title": "Bell Hands",
    "duration_minutes": 48,
    "teacher": "...",
    "source": "..."
  },

  "analysis": {
    "trademark_movements": [
      {
        "name": "bell_hand",
        "strength": 0.95
      }
    ],

    "body_regions": {
      "arms_hands": 0.9,
      "shoulders_scapulae": 0.8,
      "ribs_chest": 0.8,
      "pelvis": 0.2
    },

    "positions": {
      "supine": 0.9,
      "side_lying": 0.1
    },

    "mechanical_themes": {
      "rotation": 0.7,
      "rolling": 0.4,
      "reaching_lengthening": 0.5
    },

    "proximal_distal": {
      "distal_to_proximal": 0.85,
      "reciprocal": 0.15
    },

    "differentiations": [
      {
        "parts": ["arm", "ribs"],
        "strength": 0.8
      },
      {
        "parts": ["shoulder", "ribs"],
        "strength": 0.9
      }
    ],

    "rolling": 0.4,
    "reversibility": 0.9,
    "transition_emphasis": 0.2,
    "support_level": 0.95,

    "lesson_architecture": {
      "type": "single_motif_progressively_elaborated",
      "confidence": 0.85
    },

    "functional_themes": []
  },

  "canonical_summary": "...",

  "embedding": []
}
```

---

# 17. Important Design Principle

The system should maintain **two simultaneous notions of representation**:

### Explicit representation

Human-readable Feldenkrais features.

Advantages:

* interpretable
* adjustable
* explainable
* supports targeted queries such as "same ribs emphasis but more upright"
* exposes classification mistakes

### Implicit representation

Semantic embedding of the canonical lesson description.

Advantages:

* captures relationships not anticipated by the schema
* provides graceful similarity between concepts
* prevents the hand-designed taxonomy from becoming the entire model

Neither should replace the other.

The useful system is the combination.

---

# 18. Longer-Term Possibility: Learn Similarity From Human Choices

Design V1 so that later it would be easy to record judgments such as:

```text
For lesson A:

B is more similar than C.
```

or:

```text
A and B:
very similar / somewhat similar / unrelated
```

Once enough judgments exist, the system could learn:

* optimal feature weights
* which structured attributes actually matter
* transformations of the semantic embedding
* a custom similarity metric

Do not require this for V1, but avoid architectural decisions that make it difficult later.

---

# Desired Outcome

Given one lesson, the application should eventually be able to answer queries such as:

> Show me the five lessons most similar to this one.

> Find another lesson that uses the same mechanism but in a different position.

> Find a ribs/shoulders lesson that is organized in the opposite proximal/distal direction.

> Show me three related lessons that are substantially different from one another.

> Find something maximally different from this lesson.

> Why did the system consider these two lessons similar?

> What larger family does this lesson appear to belong to?

The important design objective is therefore not merely **vector search**.

It is to construct an interpretable and evolvable representation of Feldenkrais lesson organization that supports both computational similarity and meaningful human explanation.
