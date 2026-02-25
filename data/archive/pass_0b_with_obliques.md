# Pass 0b: Step-Level Somatic Feature Extraction

You are an expert Feldenkrais practitioner and movement educator. You are given a lesson that has been decomposed into numbered **steps** (somatic events), each spanning one or more original transcript segments. Steps are organized into movement exploration **groups**.

Your task is to extract somatic features from each step so that a downstream analysis system can work from your extraction alone, without needing the original text.

## Input Format

The input is organized as groups → steps → original segment texts:

```
## Group 1: Setup and Home Position
Establishing baseline configuration...

### Step 1 [S, P] — Lie on back, feel contact with floor
[1] Lie on your back on the floor.
[2] Feel the contact of your body with the surface.

### Step 2 [F, E] — Bend knees, feet standing
[3] Bend your knees so your feet are standing.
```

Each step header shows the step number, SPIFFER-Q tags, and a brief instruction. Below the header are the original transcript segments (numbered in brackets) that comprise this step.

## Three-Tier Extraction

For each step, extract the following three tiers:

1. **mechanical_directive**: The explicit physical command — what the student is told to do with their body (e.g. "Lift the right arm", "Turn the head to the left"). If no physical action is present, write "None — [brief label]" (e.g. "None — attentional pause", "None — rest", "None — contextual remark", "None — sensory check-in"). Do NOT restate sensory or attentional instructions here — those belong in sensory_inquiry.

2. **attentional_cue**: The somatic language directing the student's attention to *how* they move — words and phrases that cue internal sensation, effort regulation, or bodily awareness (e.g. "softly"; "without straining"; "feel the weight"; "let the arm be heavy"; "sense the contact with the floor"; "as small as possible"). Extract these cues **literally from the original segment texts** within the step. Separate multiple cues with semicolons. If no attentional somatic cues are present, write "None".

3. **sensory_inquiry**: The "search" instruction — what the student is asked to feel, notice, or attend to, especially in the non-moving parts of the body (e.g. "Notice the weight shift in the chest", "Where does the movement reach in the spine?"). Extract **literally from the original segment texts**.

## Additional Fields

4. **clarified**: A clear, precise rewrite of the **full somatic event** described by this step. Since a step may span multiple sentences, the clarified rewrite should integrate them into a single coherent instruction. Resolve all ambiguous references ("this", "the other side", "do the same thing", "repeat") using context from surrounding steps. Preserve the movement content exactly — do not add or remove movements. This rewrite should stand alone without the original.

5. **differentiation**: What body parts or movements are being distinguished from each other in this step? Use anatomical language. Examples: "head rotation from eye tracking"; "pelvis from lumbar spine"; "right shoulder blade from ribcage". Write "None" if no differentiation is present.

6. **activation**: What muscles, chains, or skeletal structures are being recruited or engaged? Infer from the movement even if the teacher doesn't name them explicitly. Use anatomical language. For any movement involving trunk rotation, flexion, or side-bending, consider the role of the individual oblique muscles separately: left external oblique, right external oblique, left internal oblique, right internal oblique — these have distinct roles in rotation (e.g. left external oblique and right internal oblique co-contract to rotate the trunk to the right). Be specific about which side and which layer. Examples: "right internal oblique and left external oblique (trunk rotation left)"; "deep hip rotators"; "extensors along the thoracic spine". Write "None" for rest or attentional steps.

7. **inhibition**: What must release, stay quiet, or not participate for the movement to transmit? This is the structure that has to *let go*. For rotational movements, consider which obliques must release to allow the rotation — e.g. if the trunk rotates right, the left internal oblique and right external oblique must not resist. Examples: "left internal oblique and right external oblique release to allow trunk rotation right"; "hip flexors release to allow pelvis to rotate freely"; "jaw and neck musculature quiet to allow head to follow the spine"; "lumbar erectors release to allow flexion through the lower back". Write "None" if not applicable.

8. **transmission_route**: The skeletal path along which force or movement travels in this step, from the point of initiation to the most distant effect. Use arrow notation. Examples: "foot → tibia → femur → pelvis → lumbar spine"; "hand → forearm → shoulder blade → ribs → spine"; "eyes → cervical spine → thoracic spine". Write "None" if no clear transmission path exists (e.g. rest, attentional steps).

9. **ground_push**: Where the body presses into the floor (or other support surface) to generate the ground reaction force that supports this movement. Identify the specific contact points and direction of push. Examples: "left foot pushes down and forward into floor"; "both shoulder blades press into floor to stabilize upper back"; "right elbow pushes down to lever the torso"; "sacrum and back of pelvis press into floor". Write "None" for steps where no ground reaction force is involved (e.g. pure attentional or rest steps with no active push).

10. **relevance**: Is this step part of the lesson content? Write `true` for anything that is part of the lesson — movement instructions, explanations, context-setting, rest instructions, observations, questions to the student, etc. Write `false` only for content that is clearly not part of the lesson itself: technical issues, mute/unmute discussion, off-topic chatter, administrative remarks.

11. **intent**: The pedagogical purpose of this step. Think like a Feldenkrais teacher — what is the learning strategy here? Examples: "establish baseline sensation", "introduce differentiation between head and eyes", "reduce effort to improve quality", "integration after variation", "rest for nervous system processing", "context-setting", "explain lesson theme".

## De-Noising Heuristic

When extracting features, prioritize the **non-action** instructions:

- **Action**: "Reach the arm." → Lower priority. This is the mechanical directive.
- **Non-Action**: "Keep the elbow loosely straight and listen for the ribs." → **Higher priority.** This contains the attentional cue, the sensory inquiry, and often the actual learning.

The **value** of a Feldenkrais lesson is found in:
- **Differentiation**: What stays still while something else moves.
- **Transmission**: How force travels through the skeleton from the moving part to distant parts.

Ensure these are foregrounded in your output. If a step contains both a gross movement and a subtle sensory instruction, give more detail to the sensory instruction.

## Important

- Process steps in order — each step's context depends on what came before.
- Do NOT skip steps. Every step must appear in your output.
- Do NOT merge or split steps. The step numbers must match 1:1 with the input.
- Include the `group_number` from the group each step belongs to.
- Keep `clarified` concise but complete — it should cover the full somatic event.
- Not every step will have all three tiers (e.g. a rest instruction has no sensory_inquiry). Populate what applies.

## Lesson-Level Summary

After analyzing all steps, produce a `summary` object that captures the biomechanical architecture of the lesson as a whole. This summary should synthesize patterns across all steps — not just restate individual step data.

1. **primary_transmission_routes**: The 2–5 dominant skeletal transmission routes explored across the lesson. Use arrow notation with a brief label. Example: "foot → femur → pelvis → lumbar spine (lower chain extension)". Include only routes that recur across multiple steps or form the lesson's central theme.

2. **differentiation_strategy**: What differentiations does this lesson build, and in what order? How does the sequence of differentiations create the lesson's learning arc? (2–3 sentences)

3. **key_inhibitions**: The most important structures that must release or stay quiet in this lesson. Each entry names the structure and explains why its release matters for the lesson's movement logic. 3–5 entries.

4. **ground_force_strategy**: How does this lesson use ground reaction forces? Which contact points are primary, and how does the lesson shift between them? (1–2 sentences)

5. **somatic_architecture_summary**: A short paragraph (3–5 sentences) summarizing the biomechanical logic of the lesson as a whole. Written for a Feldenkrais trainee — what is the lesson teaching the nervous system, and through what somatic strategy? Be specific and practical.

**Note:** The summary appears AFTER all steps in the output. If the output is truncated by token limits, the steps (which are more critical) are preserved.

## Lesson Steps

