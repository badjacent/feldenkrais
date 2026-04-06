# QuietBones Content Pipeline

Technical notes on how explorations are generated and refined.

## Tools

- **`generate.py`** — Markdown → audio. Parses speech/silence segments, calls ElevenLabs TTS, stitches MP3.
- **`regen.py`** — Regenerate a section of an exploration via LLM (Gemini). CLI + JSON mode for editor integration.
- **`.cursorrules`** — Cursor inline editing with QuietBones voice context.

## Planned: Generate-Critique Loop

Automate the iterative refinement that produced 001_behind_the_bone.

### Architecture

1. **Generator** — Takes source lesson analysis + creative direction, produces a draft exploration.
2. **Critic** — Evaluates the draft against the exemplar and design principles. Produces specific, actionable feedback.
3. **Loop** — Feeds critic output back to the generator for revision. Runs N rounds or until the critic passes.

### Critic calibration

The critic needs to catch the same things human feedback caught in the original session:

- **Clinical/biomechanical language** — "differentiation of clavicle from scapula" should never appear. The analysis vocabulary must be fully translated.
- **Instructive vs. inviting tone** — "Notice the breath motion" is too directive. "Let the space breathe" invites.
- **Direct choices** — "Choose a side" should become "Think of a ___. Interesting to note which side your attention went to."
- **Comparison timing** — Compare sides *before* moving to the second side, not after.
- **Pacing** — Sentences too long or too dense. Needs breathing room.
- **Rest/release language** — Never says "relax." Attention placement leads to release; the listener discovers it.

### Open questions

- How many rounds of critique are enough? Diminishing returns after 2-3?
- Should the critic have access to the source lesson analysis, or only the exploration + design principles?
- Can the same model generate and critique, or does cross-model critique work better?
- How to handle the "voice" problem — the exemplar's voice was shaped by human feedback. Can a critic learn it from one example?

### Next step

Run the loop manually in a Claude Code conversation first:
1. Pick a source lesson (already analyzed in the pipeline)
2. Provide creative direction (body region, theme, learning goal)
3. Claude drafts → self-critiques → revises
4. Human reviews the output and the critique quality
5. Use findings to calibrate the automated critic

This produces exploration 002 and validates the loop design simultaneously.

## Prerequisites for generation

- Source lesson transcribed and analyzed (decompose, somatic, language, spatial, condensed)
- Human provides: body region, theme/concept, learning goal
- 001_behind_the_bone.md available as exemplar for voice/tone
