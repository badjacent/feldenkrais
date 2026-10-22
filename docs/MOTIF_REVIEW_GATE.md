# Atlas motif vocabulary gate

Status: owner-corrected decision, 2026-08-23

## Decision

Human review governs the **set of motifs**, not each lesson assignment.

A motif may appear in the public Atlas chooser only when its controlled-registry
entry has both:

```yaml
status: established
chooser_visible: true
```

Changing either field is an explicit owner/editor action in the versioned motif
registry. Gemini cannot create, establish, rename, merge, deprecate, or expose a
motif by emitting an assignment.

Once a motif is established and chooser-visible, current Gemini lesson
assignments to it flow automatically into the public projection. There is no
per-lesson human approval queue.

## Two separate lanes

### Controlled vocabulary

The registry owns stable IDs, labels, definitions, include/exclude guidance,
status, and chooser visibility. New candidate observations remain private until
a human deliberately adds or promotes a registry entry. Registry changes are
reviewable Git changes and advance the registry version.

### Lesson assignments

Gemini assigns only IDs supplied from the established registry. Assignments are
cached and versioned by transcript, prompt, model/provider, and registry
version. For a chooser-visible motif, current assignments are exported
automatically as `public_motif_ids`.

The public projection also retains safe categorical assignment data for audit
and future similarity work. It never publishes prompts, raw responses,
transcripts, evidence excerpts, private document IDs, or candidate motifs.

## Projection contract

For each lesson:

- `motifs` contains current assignments limited to established,
  chooser-visible registry IDs;
- `public_motif_ids` contains the unique IDs from those assignments;
- an absent or malformed public list fails closed in the Atlas importer;
- a motif removed from chooser visibility disappears on the next projection;
- a changed assignment appears or disappears on the next projection without a
  human per-lesson decision.

The projection-level motif registry contains only established,
chooser-visible entries. Atlas derives labels from that registry rather than
model wording.

## Human review workflow

When adding or changing the vocabulary, review the proposed registry diff for:

1. a stable ID and concise public label;
2. a definition that distinguishes the motif from ordinary movement themes;
3. positive examples from more than one lesson or source when available;
4. at least one exclusion or counterexample;
5. comparison with the nearest established motifs;
6. an explicit add, merge, broaden, deprecate, hide, or reject decision.

This gate controls taxonomy growth without turning catalog maintenance into
hundreds of individual editorial decisions.

## Validation

Tests must prove that:

- candidate, deprecated, merged, or chooser-hidden motifs cannot enter the
  public registry or `public_motif_ids`;
- Gemini cannot invent IDs outside the supplied registry;
- visible established assignments are projected automatically;
- changing visibility removes assignments from the next projection;
- the Atlas consumes `public_motif_ids`, never private/raw motif observations.
