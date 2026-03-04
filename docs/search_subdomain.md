# How Search Ranking Works

## The Problem

Feldenkrais lessons are hard to search. They all use the same vocabulary — "lie on your back," "notice the weight," "roll to one side" — and a naive text search returns everything with roughly equal confidence. A lesson about pelvic clocks and a lesson about shoulder differentiation can look nearly identical to a keyword matcher, or even to a basic embedding search, because the somatic language overlaps so heavily.

The core challenge: how do you rank lessons when the vocabulary is shared but the *content* is distinct?

## What Each Lesson Knows

Before search happens, each lesson goes through an analysis pipeline that produces structured data at multiple levels:

**Structural analysis** breaks the transcript into movement steps grouped into phases — what position the student is in, what they're asked to do, how the lesson progresses.

**Somatic analysis** tags each step with body mechanics: muscle activation, differentiation, force transmission, attention direction.

**Language analysis** captures the teaching voice: imagery, attention cues, inquiry patterns.

**Concept extraction** reads the full analysis and produces two things:
- A **distinctive report** (200-400 words): position inventory, movement sequences, key body connections, and what makes this lesson different from others. This is the lesson's primary search document.
- **Concept tags**: structured labels for body regions, movement patterns, positions, and teaching strategies.

**Spatial configuration extraction** identifies the physical setup of each movement group: body position, contact points, holds (which body part holds which), and movement constraints. These spatial configurations are appended to the distinctive report, so the embedding captures concrete physical arrangements — "interlaced fingers behind head," "hand under buttock," "knees stacked" — alongside the conceptual description.

The concept extraction step is the key to search quality. It takes the shared somatic vocabulary and distills it into what's *specific* to each lesson. The spatial layer adds the physical setup vocabulary that students naturally search with.

## Four Ranking Strategies

We built and evaluated four approaches:

### Standard embedding search

Embed a summary of each lesson's structural analysis (titles, settings, group labels, action descriptions). Embed the user's query. Rank by cosine similarity.

This is the simplest approach and covers the full corpus. It works when the query closely matches the structural language of the lesson, but struggles with lessons that are structurally similar — the top results often look interchangeable.

### Distinctive embedding search

Embed the distinctive report from concept extraction instead. This document is specifically written to differentiate lessons, so the embeddings capture what's unique rather than what's generic.

Much better precision than standard, but only available for lessons that have gone through concept extraction.

### Tag matching

Match query words directly against concept tag values in the database. Rank by number of matching tags. No embeddings involved — pure text overlap.

Works for very specific queries ("pelvic clock") but fails for natural language ("lesson where you feel your spine lengthen").

### Combined (cascade)

The approach we settled on, which outperforms all individual methods.

## The Cascade Approach

The insight: when the distinctive search is confident in its top result, it's almost always right. When it's not confident, blending in the standard search helps. The question is how to know when it's confident.

We measure confidence using the **similarity gap** — the difference in cosine similarity between the #1 and #5 results from the distinctive search. A large gap means one lesson stands out clearly. A small gap means several lessons matched about equally well.

The cascade works in two modes:

**Confident mode** — When the similarity gap exceeds a threshold (0.07), the distinctive search found a clear winner. Use its results directly.

**Blended mode** — When the gap is below threshold, results are ambiguous. Merge the standard and distinctive results using Reciprocal Rank Fusion (RRF), weighting the distinctive results 3x higher than standard.

### Why not always blend?

We tried pure linear fusion first (RRF across all methods with various weights). It performed *worse* than distinctive alone. The problem: the standard index contains hundreds of lessons that aren't in the distinctive index, and these act as distractors. When the distinctive search has a strong signal, blending dilutes it with noise from the larger corpus.

The cascade avoids this by only blending when it needs to — when the distinctive search itself indicates uncertainty.

## Tuning the Parameters

The cascade has three parameters: the confidence gate threshold, the RRF constant, and the concepts weight in blended mode. Rather than hand-tuning these, we built a grid search optimizer.

### Evaluation set

An LLM generates search queries from lesson analysis cards — three per lesson covering different query types (action, body region, sensation), plus one spatial configuration query per lesson describing a specific body arrangement. Each query has a known correct answer. The current set is 172 queries across 43 lessons.

### Grid search

The optimizer (`optimize_combined.py`) pre-computes all search results once, then simulates hundreds of parameter combinations at near-zero cost. For each combination, it scores precision (is the right lesson ranked #1?), recall (does it appear in the top 10?), and mean reciprocal rank.

This is reusable — when the lesson library or evaluation set changes, re-running the optimizer takes about a minute and produces updated parameters.

### Current results

| Method | P@1 | P@5 | MRR | Found in top 10 |
|--------|-----|-----|-----|-----------------|
| Standard | 29.1% | 51.7% | 0.392 | 65.7% |
| Distinctive | 41.9% | 78.3% | 0.565 | 86.8% |
| **Combined (cascade)** | **43.6%** | **77.9%** | **0.581** | **86.6%** |

These numbers reflect the expanded 172-question eval set that includes spatial configuration queries — a harder benchmark than the original 129-question set. The cascade finds the right lesson in the top 5 nearly 80% of the time, and somewhere in the top 10 over 86% of the time.

### Specificity bands

The similarity gap also serves as a user-facing signal. We calibrated four bands from the gap distribution:

| Band | Gap range | What it means |
|------|-----------|---------------|
| Low | < 0.017 | Many lessons match equally — results are all relevant |
| Mid | 0.017–0.022 | Several good matches, somewhat differentiated |
| Mid-High | 0.022–0.030 | Top results stand out, query is fairly specific |
| High | ≥ 0.030 | Clear best answer |

Search results display the band so users can calibrate their expectations.

## What Improves It

The system is designed to get better with more data:

- **More lessons with concept extraction** — currently 43 of 700+ lessons have distinctive reports. Expanding coverage means the confident mode fires more often and the blended mode has a richer concepts index to draw from.
- **Spatial enrichment** — adding spatial configuration data (positions, holds, constraints) to the distinctive embedding improved spatial query P@1 from 11.6% to 41.9%, bringing it in line with other query types. Lessons that have both concept and spatial extraction get the strongest search performance.
- **More evaluation queries** — the grid search is only as good as its test set. Real user queries, once collected, replace synthetic ones and better reflect actual search behavior.
- **Prompt iteration** — the distinctive report prompt can be refined. Longer, more physically specific reports produced measurably better embeddings (we saw this when expanding from 2-3 sentences to 200-400 words).
