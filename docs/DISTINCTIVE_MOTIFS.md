# Distinctive motifs — analysis-tree handoff

Status: owner-approved design direction, 2026-08-20. This document records the
conversation and decisions for the agent working on the main Feldenkrais
analysis system. It specifies the **measure and extraction contract**, not a
similarity metric.

## Purpose

Some Awareness Through Movement lessons have a recognizable, unusually
Feldenkrais-specific organizing idea that may be the notable thing about the
lesson. Examples include bell hands and vibrations propagating through the
body. Many lessons have no such motif, and an empty result is correct.

Capture these ideas as **distinctive motifs** so they can later support:

- a chooser UI that lists established motifs;
- browsing and search;
- interpretable descriptions of what distinguishes a lesson; and
- future similarity work, whose scoring is deliberately not specified here.

This is not the same as the existing generic `movement_themes` axis. Rolling,
rotation, flexion, reaching, and weight transfer are ordinary mechanical
themes. A distinctive motif is a recognizable Feldenkrais-specific idea or
device that organizes or meaningfully distinguishes a lesson.

## Existing system and intended home

The current similarity profile contains a `marks` field with values shaped as
`{mechanism_id, strength, mode}`. The similarity code can consume marks, but the
current projection supplies none; dedicated extraction was intentionally left
unimplemented.

Evolve this concept into `distinctive_motifs`. Preserve migration compatibility
for existing `marks` payloads if necessary, but use the clearer terminology in
new analysis contracts and UI-facing artifacts.

Distinctive-motif extraction belongs in the **main Feldenkrais analysis tree**.
Atlas should receive only a versioned, public-safe projection of approved motif
IDs and labels for confirmed public lessons. Atlas should not perform this
analysis independently or develop a divergent motif vocabulary.

## Controlled motif registry

Motifs form a finite controlled vocabulary. It may become fairly large, but an
LLM must never create a public vocabulary entry merely by emitting a new tag.

An established registry entry should contain at least:

```yaml
- id: bell_hands
  label: Bell hands
  definition: >-
    A soft, responsive organization of the hand in which changes through the
    fingers and palm participate in movement elsewhere in the body.
  aliases:
    - bell hand
    - jellyfish hand
  include_when:
    - The configuration is explicitly taught or repeatedly explored.
  exclude_when:
    - The teacher merely asks the student to soften a hand.
  positive_examples: []
  counterexamples: []
  status: established
  chooser_visible: true
```

Registry states:

- `candidate`: retained observation, unavailable for public lesson assignment;
- `established`: approved for extraction and public projection;
- `deprecated`: no longer assigned, retained for migration/audit; and
- `merged`: redirected to another canonical motif ID.

Stable IDs survive label and definition changes. Aliases normalize wording but
must not silently merge substantively different ideas.

### Cost of vocabulary growth

Adding a motif should require a deliberate review package rather than a numeric
penalty. A candidate needs:

1. a concise definition;
2. positive evidence from more than one lesson or source when available;
3. at least one counterexample or exclusion rule;
4. comparison with its nearest established motifs;
5. evidence that an existing motif cannot adequately represent it; and
6. an explicit add, merge, broaden, defer, or reject decision.

Candidate observations may accumulate without changing the registry. This
preserves novelty while preventing one bespoke motif per lesson.

## Per-lesson measure

For each analyzed lesson, measure which established motifs are meaningfully
taught or referenced and how they function in the lesson.

Recommended categorical assignment:

```json
{
  "motif_id": "bell_hands",
  "mode": "taught",
  "role": "central",
  "certainty": "clear",
  "evidence": [
    {
      "step_ids": [4, 5, 6],
      "reason": "The hand organization is introduced and systematically varied."
    }
  ]
}
```

Allowed values:

- `mode`: `taught` or `referenced`;
- `role`: `central`, `supporting`, or `incidental`; and
- `certainty`: `clear`, `probable`, or `uncertain`.

Definitions should be fixed in the extraction prompt:

- `taught`: the lesson develops, varies, or asks the student to experience the
  motif—not merely names it;
- `referenced`: the motif is mentioned or used as prior knowledge without being
  developed;
- `central`: it plausibly identifies “the notable thing about this lesson”;
- `supporting`: it materially supports the lesson without organizing it; and
- `incidental`: it is genuinely present but brief and non-organizing.

Do not ask the model for pseudo-precise free continuous strength values. A later
consumer can map these auditable categories to numbers if a metric eventually
requires that.

## Extraction design

Run one versioned, cached LLM extraction per eligible analyzed lesson. Do not
make pairwise LLM calls between lessons.

The input should be limited to existing analysis artifacts:

- the controlled motif registry with definitions and counterexamples;
- canonical or condensed lesson summary;
- structured lesson steps;
- somatic and movement analysis;
- relevant language and attention analysis; and
- short evidence already present in those artifacts when needed.

The current analyzed cohort is English, so the first registry and prompt can be
English-only. Preserve ordinary content-language metadata for future cohorts.

The model must use only supplied material: no web search, outside Feldenkrais
knowledge, or ungrounded reconstruction of the transcript.

### Two output lanes

#### 1. Closed-set assignments

The model may assign only established registry IDs. Every assignment requires
evidence pointers into supplied analysis. Empty assignments are valid and
expected.

```json
{
  "registry_version": "motifs-v1",
  "assignments": [],
  "candidate_motifs": []
}
```

#### 2. Candidate observations

The model may separately describe a possible unregistered motif. This creates a
review proposal only; it never changes the registry or public assignments.

```json
{
  "candidate_label": "Vibrations spreading through the skeleton",
  "description": "Rhythmic movement initiated locally and experienced as propagation through multiple body regions.",
  "evidence": [{"step_ids": [8, 9, 10]}],
  "nearest_existing_motifs": [
    "oscillation",
    "proximal_distal_transmission"
  ],
  "why_distinct": "The organizing idea is propagation of vibration rather than voluntary transmission of movement."
}
```

Candidate generation should be conservative. The extraction contract should
permit `candidate_motifs: []` and explicitly discourage novelty for its own sake.

## Versioning and persistence

Cache and persist results using at least:

- lesson/document identity;
- hash of the analysis inputs;
- motif-registry version;
- extraction prompt/schema version; and
- model/provider identifier.

A registry change must produce a controlled re-extraction plan. Do not mutate
old results in place or make them appear to have been generated under the new
registry.

Persist assignment evidence and candidate proposals privately. A future public
projection should contain only safe fields such as:

```json
{
  "distinctive_motifs": [
    {
      "motif_id": "bell_hands",
      "label": "Bell hands",
      "mode": "taught",
      "role": "central"
    }
  ],
  "motif_registry_version": "motifs-v1"
}
```

Do not publish prompts, raw model output, analysis text, evidence excerpts, or
private document identifiers.

## Chooser-facing requirements

The chooser may eventually list established motifs as human-readable choices.
The registry—not per-lesson model wording—owns the public label and definition.

`chooser_visible` should require enough clear/probable assignments to avoid a
dead-end option. Rare established motifs can remain searchable without appearing
in the primary chooser. The exact threshold and any ranking/scoring are outside
this document.

Begin with a flat vocabulary. Add parent/child or related-motif structure only
when corpus evidence demonstrates a concrete navigation or maintenance need.

## Validation expectations

Before broad extraction:

1. Hand-author a small registry containing unambiguous initial motifs.
2. Create positive, negative, taught-versus-referenced, and central-versus-
   incidental fixtures.
3. Run a small reviewed cohort of already-analyzed English lessons.
4. Inspect every assignment and candidate proposal.
5. Measure inter-run stability when the same inputs and versions are reused.
6. Confirm reruns hit the cache and create no analysis-queue work.
7. Confirm public projection contains only approved IDs and safe categorical
   fields.

## Explicit non-goals

- Do not design or tune a motif similarity metric yet.
- Do not define similarity between different motifs yet.
- Do not automatically establish motifs from model output.
- Do not require every lesson to have a motif.
- Do not run transcription or re-run unrelated analysis stages.
- Do not build a second motif extractor in Atlas.

Execution of this new extraction pass remains subject to the owner’s existing
approval boundary for lesson analysis. This document authorizes design and
implementation handoff; it does not by itself authorize a production cohort or
unattended analysis worker.
