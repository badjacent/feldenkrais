# Pass A: Structural Decomposition & Routing

## Overview
Analyze the Feldenkrais lesson transcript to:
1. Classify which VL Framework(s) apply
2. Decompose the lesson into SPIFFER-Q tagged steps

## Input Format
The lesson text is pre-segmented with markers like `[1]`, `[2]`, etc. Each marker indicates a sentence or instruction unit. When you identify steps, reference the segment IDs that correspond to that step.

---

## Part 1: VL Framework Classification

Classify the lesson across the VL Frameworks with a score from 0.0 to 1.0:

{CLASSIFICATION_TABLE}

**Scoring:**
- 0.0-0.2: Not present or incidental
- 0.3-0.5: Secondary theme
- 0.6-0.8: Major theme
- 0.9-1.0: Primary focus

---

## Part 2: SPIFFER-Q Decomposition

Segment the transcript into discrete "Somatic Events" (movements/instructions). For each event, apply relevant tags:

| Tag | Meaning | Look For |
|-----|---------|----------|
| **S** | Sensation | Mapping pressures, contours, weight distribution |
| **P** | Pause | Integration periods, "rest as you are" |
| **I** | Imagery | Metaphors, analogies, mental geometry |
| **F** | Function | Global goals (walking, reaching, standing) |
| **F** | Focus | Directing attention to specific anatomy |
| **E** | Effort | Instructions to reduce tone, soften, release |
| **R** | Relationships | Connections between body parts (head-pelvis, ribs-hips) |
| **Q** | The Question | The neurological riddle being posed |

For each step, provide:
- Step number
- The instruction/movement (summarized in your own words)
- Relevant SPIFFER-Q tags
- `segment_start` and `segment_end`: the first and last segment IDs this step covers (inclusive). All segments in the range belong to this step.
- Brief note on the pedagogical purpose (optional)
- Whether this step is a **return to home position** (`is_home: true`)

### Segment Coverage

**Steps must tile the entire transcript with no gaps.** Step 1's `segment_start` must be the first segment in the lesson. Each subsequent step's `segment_start` must be exactly one more than the previous step's `segment_end`. The last step's `segment_end` must be the last segment in the lesson. Every segment belongs to exactly one step — no gaps, no overlaps, no dropped segments.

### Home Position

Most Feldenkrais lessons have a **home position** — the base configuration the student returns to between movement explorations. Typically the teacher says something like "come back to lying on your back" or "return to the starting position." Identify:

1. `home_position`: A brief description of the home configuration (e.g., "Lying on back, knees bent, feet standing" or "Sitting on the floor, legs crossed"). This is usually established in the first few instructions.
2. For each step that represents a return to this home position, set `is_home: true`. These are rest/reset moments, not the initial setup itself.

---

## Part 3: Step Groups

After decomposing the lesson into individual steps, cluster them into approximately 5-8 **groups** that represent the major movement explorations of the lesson. Each group bundles related steps into a single thematic unit.

Guidelines:
- A group typically represents one movement exploration and its variations, or a setup/rest phase
- Include the step numbers that belong to each group (e.g., `[1, 2, 3]`)
- Give each group a short label describing the exploration (e.g., "Pelvic clock 12-6", "Bilateral push-pull with heels")
- Aim for roughly 5-8 groups. Fewer than 4 loses lesson structure; more than 10 is too granular.
- Rest/pause steps at the end of a movement exploration belong to the preceding group. Do NOT leave rest steps ungrouped. Every step must belong to exactly one group.

---

## Output Format

Provide your analysis in the structured format with:
1. `classifications` - scores for each VL framework
2. `primary_framework` - the dominant VL
3. `secondary_frameworks` - VLs scoring >= 0.3
4. `rationale` - brief explanation of classification
5. `home_position` - description of the base configuration the student returns to
6. `steps` - the SPIFFER-Q decomposed lesson, each with `segment_start`/`segment_end` range and `is_home` flag for return-to-home steps
7. `groups` - clustered step groups for the analytical table, each with `label`, `steps` (step numbers), and `description`
