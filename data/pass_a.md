# Pass A: Structural Decomposition & Routing

## Overview
Analyze the Feldenkrais lesson transcript to:
1. Classify which VL Framework(s) apply
2. Decompose the lesson into SPIFFER-Q tagged steps

## Input Format
The lesson text is pre-segmented with markers like `[1]`, `[2]`, etc. Each marker indicates a sentence or instruction unit. When you identify steps, reference the segment IDs that correspond to that step.

---

## Part 1: VL Framework Classification

Classify the lesson across the five VL Frameworks with a score from 0.0 to 1.0:

| Framework | Focus | Key Indicators |
|-----------|-------|----------------|
| **VL-1** Vertical Self | Gravity, skeletal columns, longitudinal force | Standing, weight transfer, spine as column, lengthening |
| **VL-2** Homunculus | Micro-differentiation, joint resolution | Small precise movements, sensing specific joints, body mapping |
| **VL-3** Steering & Tone | Eyes, jaw, neck, autonomic regulation | Eye movements, jaw relaxation, neck turning, breathing |
| **VL-4** Proximal-Distal | Limb-to-core power, reach | Arm/leg reaching, connection to trunk, crawling patterns |
| **VL-5** Folding-Unfolding | Flexion/extension, protective reflexes | Curling/uncurling, abdominal engagement, fetal references |

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
- Segment IDs from the source text (e.g., `[45, 46, 47]` if the step covers segments 45-47)
- Brief note on the pedagogical purpose (optional)

---

## Output Format

Provide your analysis in the structured format with:
1. `classifications` - scores for each VL framework
2. `primary_framework` - the dominant VL
3. `secondary_frameworks` - VLs scoring >= 0.3
4. `rationale` - brief explanation of classification
5. `steps` - the SPIFFER-Q decomposed lesson, each with `segments` array referencing source text
