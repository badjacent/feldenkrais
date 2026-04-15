# Pass 0b Spatial v3: Step-Level Position Sequence

You are extracting a **position sequence** from a Feldenkrais lesson — a plain-language description of every body configuration the student passes through, step by step.

The goal is to make this lesson searchable by someone who remembers a position: "lying on back with one knee crossed over the other, arms reaching to the ceiling."

## Rules

1. **Describe the FULL body configuration at each step.** Not just "supine" — include which limbs are bent, which are long, where the hands are, what touches what.
2. **Use plain language a student would use.** Say "lying on back, right knee bent, foot flat on floor" not "supine with right foot standing" (unless you also say what "standing" means).
3. **Be specific about sides.** "Right knee crossed over left" not just "knees crossed."
4. **Note transitions.** When the position changes between steps, the new step should describe the new position fully.
5. **Rest steps get positions too.** "Lying on back, legs long, arms at sides" — not just "rest."
6. **Separate position from action.** The `position` field describes the static configuration. The `action` field describes what moves.

## Feldenkrais vocabulary

These terms appear in lessons and should be translated to plain language:

| Lesson term | Means |
|---|---|
| "stand the feet" / "feet standing" | Feet flat on floor, knees bent (while lying down) |
| "knees standing" | Same — knees bent, feet flat on floor |
| "long legs" | Legs straight, extended on the floor |
| "home position" | The base configuration the student returns to between explorations |
| "scan" | Lie still and notice sensations — not a position change |

## Input Format

You receive the Pass A decomposition: groups with labels and descriptions, and steps with instructions and segment text. Use both the instruction summary and the original segment text to determine the exact position.

## Output

For each step, produce:
- `step_number`: matching the input
- `group_number`: which group this step belongs to
- `position`: the full body configuration in plain language
- `action`: what the student does in this position (the movement or attention task)
- `is_transition`: true if this step is primarily about changing position (e.g., "roll to your side", "come to sitting")
- `is_rest`: true if this step is primarily rest/scanning

Also produce a `position_summary`: a deduplicated list of the distinct body configurations used in this lesson, in the order they first appear. For each entry, include alternate phrasings — different ways a student might describe or remember the same position. People remember positions visually and kinesthetically, not by the teacher's exact words. Include:
- What the position **looks like** from the outside ("arms making a tent shape above the chest")
- What it **feels like** from the inside ("hands pressing together overhead")
- **Common names** if the shape has one ("figure four," "bridge," "fetal position")
- **Simpler descriptions** that a beginner might use ("lying on my side with knees pulled up")

The `position` field on each step should be the primary plain-language description. The `position_summary` entries should include the alternates.
