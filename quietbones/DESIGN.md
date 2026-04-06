# QuietBones

A relaxation/meditation app built on Feldenkrais principles. Focused on micromovements and pre-movement organization. Positioned as a meditation app, not a Feldenkrais app.

The link to Feldenkrais is soft — existing lesson analysis feeds the content generation, but users never see the analysis layer.

## Experiment thesis

Build the entire product agentic — content design, app building, ad targeting. Push AI further than conventional wisdom says it should go, and see where it actually breaks.

## Terminology

- **Exploration** — a single guided session. Not "meditation," not "lesson."

## Architecture: Primitives and Concerns

Content is composed from **primitives** (what happens) shaped by **concerns** (cross-cutting qualities).

### Primitives

The building blocks of a meditation session. All are lying-down, low-effort, skeleton-oriented.

1. **Micromovements** — tiny, slow, exploratory physical movements. The only primitive involving actual movement. "Let your right knee drift a millimeter toward the ceiling."

2. **Attentional cues** — pure awareness placement, no movement. An "inner eye" that moves within the body. "Notice the weight of your left shoulder blade."

3. **Eye movements** — eyes closed, looking toward a body part or direction. Between physical and attentional. Has real neurological effects on tonus.

4. **Imagined lines** — creating spatial relationships with the mind as a constraint engine. "Imagine a line between your left ankle and your right ankle." Gives the nervous system geometry to organize around.

### Concerns

Cross-cutting qualities that shape how every primitive is delivered. A session must be coherent across all concerns.

- **Pacing** — rest, pause duration, density of instructions, progression speed. Includes rest/pause as structural elements (not just breaks).
- **Tone** — the feeling quality of the voice/language.
- **Language** — vocabulary constraints, word palette, sentence structure, reading level. Bones, weight, floor, space, direction, line — not chakras, energy, or "relax."

### Core learning goal

Users learn **local inhibition** — the skill of relaxing one specific place without full-body collapse. Not "relax everything" but "how did I just let go of *that*?"

The explorations never say "relax." They place attention with increasing specificity, and the listener discovers that something let go. Over many sessions, they build intuition for how to locally release — which is the real product.

This reframes how the primitives work:
- **Attentional cues** — invitations for local inhibition, not just sensing
- **Micromovements** — reveal where you're holding (if you can't make a tiny movement, something is bracing)
- **Imagined lines** — spatial context that lets the nervous system release unnecessary bracing
- **Eye movements** — releasing eye tension often unlocks neck/shoulders without touching them

### Open questions

- **Progression** — how a session builds or evolves. Part of pacing, or its own concern?
- **Body region** — whether a session stays local or travels. Concern or primitive parameter?
- **Difficulty/depth** — how subtle or demanding the instructions are.
- **Breathing** — deliberately absent from primitives. Keeping it out would be distinctive.

## Agent pipeline (planned)

1. **Content agent** — takes Feldenkrais lesson analysis, outputs meditation scripts using primitives shaped by concerns
2. **Voice agent** — TTS pipeline turns scripts into audio
3. **App agent** — builds web app with session player
4. **Growth agent** — ad copy, targeting, optimization

Content agent draws from existing biomechanical analysis (pass0b: differentiation, activation, inhibition, transmission routes) to determine which body parts to connect and in what sequence.

## Exploration design: a case study

The first exploration, "Behind the Bone," was derived from Jeff Haller's "The Proximal Foundation of Candelabra Arms." This is one way an exploration can come into being — not the only way, but a documented one.

### Prerequisites

- The source lesson has been transcribed and run through the analysis pipeline (decompose, somatic, language, spatial, condensed)
- A human has identified a creative direction: a theme, a body region, and a learning goal

### Process

1. **Select a region of the source lesson.** Not the whole lesson — a portion with a clear somatic idea. For "Behind the Bone," it was the first quarter of Candelabra Arms, where Haller explores the clavicle from interior and exterior perspectives.

2. **Extract the somatic idea, not the movements.** The source lesson involves rolling clavicles, feeling the front and back surfaces. The exploration doesn't reproduce these movements. It takes the *underlying principle* — that a bone has an inside and an outside, and you can place attention on either — and re-expresses it using the primitives vocabulary (attentional cues, imagined lines, micromovements).

3. **Apply a learning goal.** For "Behind the Bone," the goal was local inhibition: the listener discovers they can release tension in a specific place by attending to it with increasing specificity. The exploration never says "relax." It places attention, and the listener finds that something let go.

4. **Draft iteratively with feedback.** The first draft is a starting point. Key refinements in this case:
   - Added a sequence where the listener places an imagined hand on the outside of a rib while attention is on the inside, draws a line between them, then gently grasps and moves — bridging interior and exterior
   - Replaced "notice the breath motion" with "let the space breathe" — the exploration invites, it doesn't instruct observation
   - Rather than asking the listener to choose a side, the exploration says "think of a \_\_\_\_, interesting to note which side it's on" — the choice reveals something about the listener's organization
   - Comparison (sensing the difference between sides) happens before moving to the second side, not after

5. **Convert to audio.** The markdown file uses `...` markers for silence. `generate.py` handles TTS and silence stitching. Pacing is slow — the voice should feel unhurried, not sedated.

### What the analysis pipeline provides

The decompose/somatic/spatial analyses give the structural skeleton: which body parts are involved, what differentiations are explored, what transmission routes are activated. This is the raw material, but it arrives in clinical/biomechanical language that never appears in the exploration.

The translation from analysis language to exploration language is the core creative act. "Differentiation of clavicle from scapula" becomes "notice the space between the front of this bone and the back of it." The analysis tells you *what* to explore; the concerns (pacing, tone, language) shape *how*.

### What AI can contribute at each step

- **Step 1 (region selection):** AI can suggest promising regions based on density of attentional cues and variety of body parts in the analysis
- **Step 2 (somatic idea extraction):** AI can identify the underlying principles from the analysis, though a human decides which ones are interesting
- **Step 3 (learning goal):** Primarily human — this is the editorial voice of the product
- **Step 4 (drafting):** AI drafts, human refines — the refinements are where the product's character emerges
- **Step 5 (audio):** Fully automated once the markdown is final

## Platform

Web first (skip App Store gate), iOS later.
