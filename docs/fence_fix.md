# Vocabulary Fence Fix: From Partitions to Viewpoints

## The Problem

The five VL lenses (Riser, Cartographer, Tuner, Engine, Soft Front) were treated as **vocabulary partitions** — each lens had a blacklist of words that "belonged" to other lenses. This created three compounding problems:

1. **Conceptually wrong.** The lenses are *viewpoints*, not *mechanisms*. A rolling movement involves vertical force (VL-1), joint differentiation (VL-2), intent clarity (VL-3), power transmission (VL-4), and front-body availability (VL-5). The word "release" or "soften" is relevant to any lens — what differs is the *interpretive question*, not the vocabulary.

2. **Unenforceable by LLMs.** Negative constraints ("do NOT use these 30 words") are imperfectly followed by language models. The more blacklisted words we added, the more the LLM either ignored them or produced awkward, stilted prose to avoid them.

3. **Self-reinforcing review loop.** The review agent flagged vocabulary overlap as a high-severity defect. The improve-book loop responded by adding more blacklists. The LLM couldn't follow them. The reviewer flagged again. Iterations 10-30 of improve-book were largely spent in this cycle.

## The Fix

### Philosophy Change

**Before:** "Each lens OWNS certain words. Never use another lens's words."

**After:** "Each lens OWNS a question. Interpret every movement through that question. Common movement words are shared."

### What's Genuinely Lens-Specific (Core Concepts)

| Lens | Core Concepts (the interpretive frame) |
|---|---|
| VL-1 Riser | buoyancy, ground reaction force, skeletal stacking, weight-dropping, vertical column, pressure wave |
| VL-2 Cartographer | differentiation, mapping, resolution, cortical self-image, body map, territory, contour, degrees of freedom |
| VL-3 Tuner | intent, orientation, actuation, motor plan, signal clarity, congruency, eyes leading |
| VL-4 Engine | torque, kinetic chain, transmission, coupling, proximal-distal organization, load transfer, lever arm |
| VL-5 Soft Front | yielding, permission, armor, flexion/extension balance, front-body availability, Red Light reflex |

### What's Shared (Should NOT Be Fenced)

- **Sensory descriptors:** release, soften, ease, weight, pressure, contact, length, effort
- **Anatomical terms:** spine, pelvis, hip, shoulder, vertebra, scapula
- **Movement verbs:** rotate, tilt, shift, lift, drop, lengthen, shorten
- **State words:** support, clarity, connection, freedom

### The Test for Problematic Bleed

> Could you swap the lens label and the chapter would still make sense?

If yes: the interpretive frame has drifted — that's real bleed.
If no: the chapters share some words but ask genuinely different questions — that's healthy overlap.

## Files Changed

### VL Framework Prompts (data/frameworks/v1/)

**vl1.md (The Riser)**
- Removed: `**Blacklisted words for VL-1:** Do NOT use: propulsion, propel, thrust, driving, drive, torque, engine, drivetrain, kinetic chain, levers, coupling, power flow, transmission (as power delivery), differentiation, mapping, resolution, territory, pixels, actuation, signal, noise, orientation (as intent), yielding, permission, armor, flexion/extension balance.`
- Removed: `Do NOT use VL-4 language (propulsion, thrust, driving, torque, kinetic chain, levers, transmission, engine, drivetrain) when writing through The Riser's lens.`
- Removed: `Do NOT use VL-5 language (armor, protection, permission, belly softening) when writing through The Riser's lens.`
- Added: `**Core concepts**` section listing the Riser's native ideas
- Added: `**Interpretive frame rule**` — "Shared words are fine; what matters is the interpretation stays in The Riser's lane."
- Kept: Anti-contradiction ("dense," "heavy," "massive") and jargon ban (these are about voice quality, not vocabulary partitioning)

**vl2.md (The Cartographer)**
- Removed: `**Blacklisted words for VL-2:** Do NOT use: stacking, column, buoyancy, ground reaction, torque, coupling, kinetic chain, transmission, actuation, signal, orientation, yielding, permission, armor, liquid, water, illuminate, illumination.`
- Added: `**Core concepts**` and `**Interpretive frame rule**`

**vl3.md (The Tuner)**
- Removed: `**Blacklisted words for VL-3:** Do NOT use: stacking, column, buoyancy, ground reaction, height, verticality, vertical, pressure wave, load, weight-bearing, skeletal support, self-supporting, density, differentiation, mapping, resolution, territory, pixels, torque, coupling, kinetic chain, transmission (as power delivery), yielding, permission, armor, flexion/extension balance.`
- Removed: `**Distinction from VL-1**` strict vocabulary ban
- Added: `**Core concepts**` and `**Interpretive frame rule**`
- Kept: Grounding rule (no radio/electronics metaphors — this is about voice quality)

**vl4.md (The Engine)**
- Removed: `Avoid language that belongs to other VLs:` section with specific examples
- Removed: `Do NOT use VL-1 language (gravity, ground reaction, vertical column, buoyancy) when writing through The Engine's lens.`
- Added: `**Core concepts**` and `**Interpretive frame rule**`
- Kept: Voice/tone constraints and style density examples (these are about the Engine's distinct voice, not vocabulary partitioning)

**vl5.md (The Soft Front)**
- Removed: `**Blacklisted words for VL-5:** Do NOT use: stacking, column, buoyancy, ground reaction, differentiation, mapping, resolution, territory, torque, coupling, kinetic chain, transmission, actuation, signal, noise, static, orientation.`
- Removed: `Do NOT use VL-1 language (stacking, ground reaction, column, buoyancy) when writing through The Soft Front's lens.`
- Removed: `Do NOT borrow "floor rising" or "pressure wave" imagery from VL-1`
- Added: `**Core concepts**` and `**Interpretive frame rule**`
- Reframed: VL-3 distinction now says "VL-3 *interprets* the jaw as orientation" rather than "VL-3 *owns* the jaw"
- Kept: Permission-language constraint, negative tone constraint, terminology note (these are about VL-5's specific voice and philosophy)

### Pass B Prompt (data/frameworks/v1/pass_b.md)

- Replaced: `**Arc Vocabulary Fence:** ... Do NOT borrow terms from other frameworks — "transmission" belongs only to VL-4...`
- With: `**Arc Interpretive Frame:** ... The lens is a VIEWPOINT, not a vocabulary partition — common movement words are shared and fine. What must stay distinct is the INTERPRETIVE QUESTION...`
- Also removed: `"allows"` from banned words list (it's a common verb, not a lens-specific term)

### Web.py Query Type Prompts (src/kb/web.py)

**lesson-concepts prompt**
- Replaced: `VOCABULARY FENCE: Only use the vocabulary listed above for {framework_name}. Do NOT borrow terms from other lenses.`
- With: `INTERPRETIVE FRAME: The lenses are viewpoints, not vocabulary partitions. Common movement words are shared and fine. What must stay distinct is the QUESTION each lens asks...`

**pedagogical-gloss prompt**
- Replaced: `LENS VOCABULARY: ... Do not use vocabulary from other lenses.`
- With: `LENS FRAME: ... Common movement terms are fine across all lenses — what matters is that the INTERPRETATION matches the lens.`

### Review Agent (src/kb/review_agent.py)

**Gross review prompt (lens_bleed category)**
- Before: "Only flag lens bleed when two chapters feel INTERCHANGEABLE"
- After: Expanded guidance explaining viewpoints-not-partitions philosophy. "Do NOT flag shared words as bleed." The test is whether two chapters ask the SAME INTERPRETIVE QUESTION.

**Fine review prompt (overlap guidance)**
- Before: "Do NOT flag overlap as a problem unless it is pure redundancy"
- After: "The lenses are VIEWPOINTS, not vocabulary partitions. Shared descriptive words appearing across sections or chapters is NORMAL — do NOT flag shared vocabulary as a defect."

### Pipeline Summary (src/kb/pipeline_graph.py)

**Review summary prompt**
- Added: `IMPORTANT CONTEXT on lens vocabulary` block instructing the summarizer to downgrade vocabulary-overlap issues unless chapters are genuinely asking the same interpretive question.

## What Was NOT Changed

- **TONAL PERSONA rules** in lens-synthesis — these describe voice *quality* (the Riser sounds architectural, the Engine sounds mechanical), which is about character, not vocabulary partitioning.
- **Overused imagery limits** (floating head, dissolve, etc.) — these prevent cliche, not vocabulary bleed.
- **Shadow voice rules** — lens-specific shadow descriptions are about *what the habit does* through each lens's eyes, which is interpretive framing.
- **Anti-contradiction words** (e.g., Riser's column shouldn't be "dense") — these prevent self-contradicting concepts within a lens.
- **Banned generic words** (liquid, illuminate, superlatives) — these are quality controls, not lens partitions.

## Expected Effects

1. **Less stilted prose.** The LLM can use natural language without contorting to avoid blacklisted words.
2. **Broken review loop.** The reviewer will stop flagging vocabulary overlap as a top-3 issue, breaking the cycle that dominated iterations 10-30 of improve-book.
3. **Better differentiation.** Paradoxically, by freeing the vocabulary, each lens should sound MORE distinct — because the LLM can focus on the interpretive frame rather than spending effort on word avoidance.
4. **Fewer false positives.** The review agent will focus on genuine quality issues (voice, arc, specificity) instead of vocabulary policing.
