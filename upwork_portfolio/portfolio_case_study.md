# Case Study: AI-Powered Content Generation Pipeline with Synthetic Evaluation

## Overview

Built an end-to-end system that ingests source material (lesson transcripts), runs multi-pass LLM analysis, generates a structured multi-chapter book, and iteratively improves it using a panel of synthetic evaluators — all orchestrated through a DAG-based pipeline with parallel execution, caching, and automatic rollback.

## The Problem

The Feldenkrais Method is a somatic education practice built around guided movement lessons. A practitioner verbally leads students through sequences of slow, exploratory movements — rolling, turning, reaching — designed to reorganize how the nervous system coordinates action. The lessons are subtle: the same instruction can serve different purposes depending on context, and the pedagogical structure is often invisible to the student during the experience.

A subject-matter expert had a library of ~200 lesson transcripts. The goal: turn each transcript into a reader-facing booklet that analyzes the lesson from five distinct interpretive perspectives, making the hidden structure visible. Each perspective generates a pamphlet — narrative arc, movement-by-movement analysis, internal experience descriptions — and the five pamphlets are assembled into a volume. The content needed to be specific to each lesson (not generic), internally consistent across sections, and accessible to a non-expert reader.

Target: fully automated, with human review only at the quality gate.

## System Architecture

```diagram
End-to-end pipeline flow, left to right.

On the far left, three source icons (web scraper, file upload, API) converge with arrows into
a single box labeled "Lesson Transcript."

An arrow flows right into a box labeled "Pass A: Segmentation," then another arrow into a box
labeled "Pass B: Synthesis (×5 lenses)."

An arrow flows right into a box labeled "Book Assembly," then right into a rounded box labeled
"Synthetic Evaluation Panel" containing four small critic icons in a row (student, specificity,
coherence, cross-chapter). An arrow from the panel flows down into a box labeled "Ranked Issue List."

From the issue list, an arrow curves back left into a box labeled "Improvement Agent (Claude Code)"
with a human figure icon next to it labeled "Human Supervisor." An arrow from the improvement
agent curves back up to the Pass B / Book Assembly stage, forming a visible loop. A small lock
icon sits on this return arrow, labeled "Pipeline Lock."

The overall shape is a left-to-right flow with a feedback loop curving back from the right side.
```

### 1. Ingestion & Storage
- Lesson transcripts ingested from multiple sources (web scraping, file upload, API)
- PostgreSQL database (Neon) for structured storage, caching, and pipeline state
- Document metadata, source tracking, and status management

### 2. Multi-Pass LLM Analysis
- **Pass A (Segmentation):** Breaks the lesson into functional segments, classifies movement types, identifies pedagogical patterns. Structured JSON output validated against a schema.
- **Pass B (Synthesis):** For each of five analytical lenses, generates a deep analysis: narrative arc, movement-by-movement table, internal voice descriptions. Each lens asks a fundamentally different question about the same source material.
- Provider-agnostic LLM abstraction layer supporting Gemini, OpenAI, and Anthropic with automatic retry, structured output, and per-model configuration (thinking levels, temperature, token limits).

### 3. DAG Pipeline Engine
- ~40-node directed acyclic graph per book with parallel execution, content-addressed caching (nodes only recompute when upstream content actually changes), and automatic staleness propagation when prompt files are edited.
- Reliability layer: per-node timeouts with exponential backoff, zombie detection, DB-based global lock to prevent partial-change processing.

### 4. Book Generation
- Template engine assembles analysis outputs into HTML pamphlets
- **Query blocks:** template sections marked for LLM fill — a general-purpose agent extracts them, generates content, runs a self-review pass, and revises flagged sections
- Five pamphlets assembled into a book with volume introduction, analytical framework overview, and per-chapter content
- Full book rendered as web page with interactive elements, exportable to PDF/EPUB

### 5. Synthetic Evaluation Panel
- Multi-critic architecture (student persona, specificity, coherence, cross-chapter) — each returns structured, typed output.
- Outputs aggregated into a ranked issue list with component attribution and fix sketches.

### 6. Supervised Improvement Loop

```diagram
A circular flow showing one improvement iteration, with a timeline running left to right
across the top labeled "Iteration 4 — 12 min."

Step 1 (left): A box labeled "Review Summary" with three highlighted issues inside, each with
a severity badge (high / medium / low) and a critic attribution tag (e.g. "specificity,"
"student persona"). The agent selects the top 3. Unselected issues are grayed out.

Step 2: An arrow to a box labeled "Agent Plans Edits." Inside, a short list: the file to edit,
the section, and a one-line description of the change. A human figure watches from beside
the box, with a speech bubble: "approve / steer / halt."

Step 3: An arrow to a box labeled "Agent Implements." Inside, a mini diff view showing red
(removed) and green (added) lines. A lock icon above the box is labeled "Pipeline locked —
background runner paused."

Step 4: An arrow to a box labeled "Pipeline Rebuilds." Only the stale subgraph runs (3 of 40
nodes highlighted, the rest grayed out). The lock icon is now open.

Step 5: An arrow to a box labeled "Re-evaluate." Two of the three original issues are now
crossed out (resolved). One new issue has appeared, flagged in yellow. A small chart below
shows issue count over iterations: 11 → 8 → 5 → 4, with the current iteration circled.

An arrow from Step 5 curves back to Step 1 for the next iteration.
```

- A Claude Code agent reads the aggregated review, selects ~3 high-impact actionable issues, plans edits, implements them, commits, and triggers a pipeline rebuild.
- **Pipeline lock** prevents the background runner from processing partial changes mid-edit.
- **Rollback protocol:** checkpoint before changes, monitor pipeline for errors or performance regressions, automatic revert if quality degrades.
- Can run in unattended loop mode, but currently operates under human supervision — the subject-matter expert reviews each iteration's output and can steer, override, or halt the process. The automation handles the mechanical work; the human holds the editorial judgment.
- Each iteration logged with: issues addressed, files changed, generation times, review impact (did the issue disappear? did new issues appear?).

## Technical Highlights

| Aspect | Detail |
|--------|--------|
| **LLM Providers** | Gemini 3 Flash, GPT-5, Claude Sonnet 4 — hot-swappable per node |
| **Pipeline** | 40+ node DAG, parallel execution, content-addressed cache |
| **Thinking Control** | Per-node Gemini thinking level (MINIMAL for generation, MEDIUM for review) |
| **Database** | Neon PostgreSQL — pipeline state, cache, lock, execution metadata |
| **Deployment** | Local dev server with hot reload; AWS Lambda for production |
| **Reliability** | Exponential backoff, per-node timeouts, zombie detection, DB-based global lock |
| **Cost Control** | Dry-run estimation, selective invalidation, VL-scoped edits to minimize recomputation |

## Outcome

- Full book generation from transcript: ~15 minutes
- Automated improvement iterations: ~10 minutes each
- The synthetic evaluation panel catches issues a single reviewer misses: generic filler, internal contradictions, reader disengagement points
- System handles Gemini API instability gracefully — timeouts, retries, model fallback per node
