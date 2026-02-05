# Review Critic Taxonomy: Thinking Through Multi-Critic Review

## What we have now

The current `review_agent.py` runs a single omnibus critic that checks for coherence, voice, repetition, lens bleed, and weakest chapter -- all in one pass. It works, but it's asking one LLM call to hold too many evaluation frames simultaneously. The result is that some dimensions get shallow treatment. The fine review is better scoped (per-section), but it's still one voice evaluating everything at once.

The question: what kinds of specialized critics exist, and which ones would actually help here?

## A taxonomy of critic types

Roughly, text review critics cluster into these families:

**Structural critics** look at how the text is organized. Does the arc make sense? Are sections the right length relative to each other? Does the chapter order build logically? For the booklets, this means: does the Journey's three-part structure actually track a Bracing-to-Transmission arc, or does it plateau?

**Voice/tone critics** listen for consistency. Is the Person Inside voice distinct from the pedagogical gloss? Does the Shadow sound like a habit or like a villain? Do the five lenses each sound like themselves across chapters? The current agent catches some of this under "voice inconsistencies," but a dedicated voice critic could hold a style guide in its prompt and compare against it systematically.

**Accuracy/fidelity critics** check whether the text matches a source. In our case: does the booklet actually describe the movements in the lesson transcript? If the lesson says "slide the foot along the floor" and the booklet says "lift the foot," that's a fidelity error. This is one the current reviewer cannot do well, because it doesn't have the lesson transcript -- it only sees the rendered booklet.

**Audience/persona critics** read the text as a specific person would. This is the big one we're missing (more below).

**Redundancy/freshness critics** look for repeated phrases, cliches, and template-sounding language across the volume. The current agent does this at the gross level ("repetition"), but a dedicated critic could track exact n-grams across all chapters and flag where the same sentence structure appears with different nouns swapped in.

**Domain-coherence critics** check whether the Feldenkrais concepts are used correctly. Does "washing" mean what we defined it to mean? Is "transmission" being used as a negotiation state or just as a generic synonym for "sending"? This is almost like a terminology linter.

**Emotional/tonal arc critics** look at whether the text builds feeling appropriately. Does the booklet start reflective and end with a sense of discovery? Or does it peak too early and then just repeat? This is distinct from structure (which is about organization) -- it's about whether the emotional trajectory serves the reader.

## Which ones matter most for the booklets

Top tier (would catch things we're currently missing):
- **Student persona critic** -- see below
- **Fidelity critic** -- needs access to the lesson transcript, but would catch the most embarrassing errors (describing movements that didn't happen)
- **Voice consistency critic** -- with the style guide baked in, could enforce the specific rules about Shadow voice, Person Inside voice, etc. that are already in pass_b.md

Worth having:
- **Redundancy critic** -- a dedicated pass that literally counts phrase reuse across chapters
- **Structural arc critic** -- specifically checking the Bracing -> Softening -> Transmission progression

Lower priority (current system handles OK):
- Lens bleed detection (already has good instructions for this)
- General coherence (the gross review covers this)

## The student persona critic

This is the most interesting one. The current critic reads like an editor. But the actual audience is someone who just finished lying on the floor for 45 minutes, doing something strange and beautiful, and now they open a little booklet to help them understand what happened.

What the student persona would check that the current critic does not:

**"I don't know what you're talking about"** -- Jargon that slipped through. "Negotiation state," "cortical noise," "SPIFFER-Q" -- none of these should appear. But also subtler things: does the booklet assume familiarity with Feldenkrais concepts that a first-timer wouldn't have? The student persona catches the curse of knowledge.

**"This doesn't match what I felt"** -- The booklet describes a sensation ("your lower back settles into the floor") but the student's actual experience might be confusion, discomfort, or nothing at all. A student persona critic would flag places where the booklet asserts experience rather than inviting it. This connects to the existing ban on "You felt" language, but goes deeper -- it's about whether the text creates space for the student's actual experience or overwrites it.

**"Why should I care about this section?"** -- The structural critic checks if sections are ordered well. The student persona asks whether each section earns its place emotionally. If the student just had a powerful experience on the floor, does the "How Feldenkrais Lessons Work" section feel like a gift or a lecture?

**"I'm lost"** -- Not structural confusion but experiential confusion. The student persona notices where the text assumes you've been following a thread that was never made explicit. Transitions that work editorially but don't work experientially.

**"This feels generic"** -- The student just did THIS specific lesson. If the booklet could apply to any lesson, the student persona would catch it. "This could be about any lesson" is a damning critique that the current editor-voice might not make.

## How multiple critics work together

A few patterns:

**Sequential pipeline**: Run the fidelity critic first (catch factual errors), then voice (catch style problems), then student persona last (catch experiential gaps). Each downstream critic gets the previous critic's flags so it doesn't repeat them. Pro: each critic can be focused. Con: slow and expensive.

**Parallel ensemble**: Run all critics simultaneously on the same text, then merge their reports. Pro: fast. Con: you get duplicate flags, and you need a merge step.

**Tiered gating**: Run a cheap "triage" critic first that classifies chapters as "fine / needs-work / broken." Only send "needs-work" and "broken" chapters to the full critic panel. Pro: saves cost on chapters that are already good. Con: the triage critic might miss things.

For our case, I'd lean toward **parallel ensemble with a lightweight merge**. The booklet volume is small enough (5 chapters) that the cost of running 3-4 critics per chapter is manageable. The merge step could itself be an LLM call: "Here are four critic reports on the same chapter. Deduplicate, prioritize, and produce a single action list."

## Practical considerations

**Cost**: Each critic is one LLM call per chapter. With 5 chapters and 4 critics, that's 20 calls. At Gemini Flash pricing, this is negligible. At Claude Sonnet pricing, it's a few dollars per full review. Probably fine for a review that runs occasionally, not on every render.

**Prompt design**: Each critic needs a tight persona and a constrained output format. The student persona critic should NOT also be checking for voice consistency -- that's the voice critic's job. The more focused the prompt, the better each critic performs. Resist the urge to add "also check for..." to every critic.

**Aggregation**: The merge step matters. Raw output from 4 critics will have overlap. Options: (a) LLM-powered merge that deduplicates and prioritizes, (b) structured JSON output from each critic that can be mechanically merged by field, (c) just concatenate the reports and let the human reader filter. For now, (c) is probably fine -- this is a tool for the author, not an automated pipeline.

**Fix targeting**: The current system's `fix_target` field (pointing to specific prompts or renderers) is great and should carry through to all critics. The student persona critic might generate fix targets like `prompt:pedagogical-gloss` ("this gloss is too technical") or `data:somatic_narrative` ("the primary inquiry doesn't match the student's likely experience").

## Next steps to consider

1. Extract the student persona critic as the first new critic -- it fills the biggest gap
2. Give it the lesson transcript (not just the rendered booklet) so it can check fidelity too
3. Keep the current review_agent as the "editor critic" but narrow its scope
4. Add a simple merge step that concatenates reports with section headers
5. Run both critics in parallel and compare their outputs on a few chapters to see where they diverge
