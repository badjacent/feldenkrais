# Architecture Brief: Somatic Analysis Pipeline (v2.0)

## Workflow Objective
To transform raw Feldenkrais transcripts into a structured "Negotiation Analysis." The pipeline identifies the pedagogical strategy, identifies the neurological "Questions" being asked, and tracks the transition from cortical effort to subcortical support.

---

## Stage 1: Pass A — Structural Deconstruction & Routing
**Type:** Classification & Extraction
**Objective:** Create a granular "Somatic Map" of the lesson.

### 1.1 The Somatic Router
Perform a weighted classification (0.0 - 1.0) across the five VL Frameworks:
* **VL-1 (Vertical Self):** Focus on gravity, skeletal columns, and longitudinal force.
* **VL-2 (Homunculus Mapping):** Focus on micro-differentiation and joint resolution.
* **VL-3 (Steering & Tone):** Focus on eyes, jaw, and autonomic inhibition.
* **VL-4 (Proximal-Distal):** Focus on limb-to-core power and reach.
* **VL-5 (Folding/Unfolding):** Focus on abdominal release and the protective reflex.

### 1.2 The Decomposer & Tagger
Segment the transcript into discrete movements and apply **SPIFFER-Q** tags:
* **S (Sensation):** Mapping pressures/contours.
* **P (Pauses):** Integration periods.
* **I (Imagery):** Metaphors/Mental geometry.
* **F (Function):** Global goals (walking, reaching).
* **F (Focus):** Internal spotlight direction.
* **E (Effort):** Commands for reduction of tone.
* **R (Relationships):** Connection between body parts.
* **Q (The Question):** The specific neurological riddle or inquiry.

### 1.3 The Inhibitor
Identify the **Constraints**: Explicit instructions on what the student must *not* do (e.g., "Don't use the elbow as a lever").

---

## Stage 2: Pass B — The VL Synthesis (Negotiation Engine)
**Type:** Analytical Synthesis
**Objective:** Interpret the data from Pass A through the lens of the "Vertical Self" framework.

### 2.1 The Negotiation Framework
Analyze the interaction between the two intelligence systems for every step:
1.  **The Striver (Postural Effort):** Cortical, voluntary, "noisy" bracing.
2.  **The Foundation (Postural Support):** Subcortical, reflexive, "quiet" equilibrium.

### 2.2 Negotiation States
Classify the "Washing Gradient" for each segment:
* **Domination:** Striver-led (High effort, habitual bracing).
* **Negotiation:** The "Washing" phase (Exploration, confusion, erosion of habit).
* **Integration:** Foundation-led (Skeletal clarity, involuntary ease).

---

## Stage 3: Unified Output Schema
The final output must be rendered as a single, scannable table.

| Seq | SPIFFER-Q | The Question | Negotiation State | Functional Outcome |
|:---|:---|:---|:---|:---|
| ID | Tags | The Riddle | Domination/Negotiation/Integration | Skeletal/Structural Result |

---

## Implementation Requirements for Agent
* **Multi-Active VLs:** If the Router flags multiple frameworks (e.g., VL-1 and VL-2), the "Functional Outcome" must address both skeletal support and joint resolution.
* **Context Preservation:** Use the output of Pass A as the primary context for Pass B to ensure technical accuracy and avoid "hallucinating" movements not present in the transcript.
* **Somatic Narrative:** The "Negotiation State" must show a logical progression (the gradual washing of the self-image) rather than random fluctuations.