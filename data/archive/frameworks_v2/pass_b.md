# Pass B: VL Framework Synthesis

## Overview
You are provided with:
1. **The original lesson text**
2. **Pass A analysis** containing:
   - VL Framework classification (scores and rationale)
   - SPIFFER-Q decomposed steps

Your task is to apply the specified VL Framework lens to interpret the lesson through the Negotiation Framework.

---

## The Negotiation Framework

For each step from Pass A, evaluate the **Negotiation State**:

### Bracing
The Shadow is in control; high noise, tension, and parasitic effort.
- Localized effort
- Speed, urgency
- Gripping, holding breath
- Linear "trying"

### Softening
The Washing is in progress; confusion, exploration, and the melting of habit.
- Oscillation, rhythm ("Washing" markers)
- Micro-movements
- Healthy confusion
- Trial and error

### Transmission
The Person Inside has taken over; skeletal clarity and "involuntary" ease.
- "Ease," "lightness"
- Global involvement
- Skeletal transmission of force
- "Involuntary" quality

---

## Framework-Specific Lens

Apply the VL Framework logic provided below. For each step, evaluate:
- Evidence of **Shadow Mode** vs **Foundation Mode**
- Progress toward the **Functional Goal**
- The specific **Question** being asked through this framework's lens

## The Person Inside

Each VL framework defines an inner experiencer — a "person inside the person" who receives the lesson from a specific point of view. The VL file describes this inner being and their shadow (the Shadow in that VL's terms).

For each row of the analytical table, write:
- **`vl_voice`**: A 2-3 sentence first-person account from the Person Inside. What do they perceive, feel, or notice at this point in the lesson? Write as if this inner being is narrating their experience. Use present tense. Be specific and sensory — describe what is felt in the body, not just abstract concepts.
- **`shadow_voice`**: A 1-2 sentence account of what the Shadow is doing at this point.

### Shadow Voice Rules (CRITICAL)

The Shadow is the HABIT PATTERN — the parasitic effort, the unnecessary tension. The Shadow voice must ALWAYS describe what the habit is doing, NOT what is being achieved.

**The Shadow NEVER:**
- Expresses clarity, quietude, or successful integration
- Describes "standing down completely" or "finding ease"
- Uses language of achievement or resolution
- Sounds peaceful or content

**The Shadow ALWAYS:**
- Describes gripping, bracing, holding, clenching, or pulling
- Uses language of effort, resistance, or protection
- Speaks from the perspective of the habit that is being challenged

**Arc progression:** Early in the lesson, the Shadow is in full control ("I am clenching the jaw, pulling the shoulders up"). Mid-lesson, the Shadow is being challenged ("I try to grip but the oscillation keeps loosening my hold"). Late in the lesson, the Shadow may be quieter but still present ("I still want to grab, but there is less to hold onto").

The Shadow NEVER "succeeds" — success belongs to the Person Inside/Foundation voice.

---

## Column Differentiation Rules (CRITICAL)

Each column in the analytical table serves a DISTINCT purpose. Do NOT repeat the same content across columns.

### The Question (Column 3)
**Purpose:** Frame the riddle being posed to the nervous system at this point in the lesson.
- Write as an inquiry, not a statement
- Focus on WHAT is being asked, not what happens
- Example: "Can the hip joint initiate the roll without the shoulder joining in?"

### Negotiation State (Column 4)
**Purpose:** Describe the PROCESS — what the Shadow and Person Inside are doing right now.
- Focus on the current state of negotiation between effort and ease
- Describe specific physical manifestations (gripping, releasing, exploring)
- Example: "Shadow gripping the lower back; beginning to soften as oscillation continues"

### Functional Outcome (Column 5)
**Purpose:** State the RESULT — the skeletal/structural change that emerges.
- Focus on what CHANGED, not what was asked or explored
- Use concrete anatomical language
- Example: "Hip joint differentiates from lumbar spine; pelvis rolls independently"

**Anti-Repetition Rule:** If you find yourself writing similar content in two columns, you are doing it wrong. The Question frames the inquiry, the Negotiation State describes the process, and the Functional Outcome states the result. These are three different things.

---

## Terminology Guardrails

**Required terms:**
- "Person Inside" (not "Council member")
- "Shadow" for the habit pattern
- "Foundation" for the emergent ease

**Avoid these terms:**
- "Council" as a noun (use "Person Inside" instead)
- Generic state labels like "Softening" for every row — use lens-specific vocabulary

**Limit overused explanations:**
- "Cortical noise" — use at most ONCE per booklet. Find lens-specific alternatives:
  - VL-1 (Riser): "collapse," "buckling," "compression"
  - VL-2 (Cartographer): "blur," "clumping," "undifferentiated"
  - VL-3 (Tuner): "static," "fixed," "obscured intent"
  - VL-4 (Engine): "leaking," "disconnected," "orphaned"
  - VL-5 (Soft Front): "armor," "bracing," "clenching"

**Voice:**
- Do NOT prescribe the reader's experience ("You felt," "You noticed," "You likely experienced"). Use invitational language ("The lesson invites," "One might discover," "This is where...").
- Do NOT reuse the same paragraph in multiple sections. Each section (Question, Arc, Lens) serves a distinct purpose.

---

## Output Format

### 1. Negotiation Framework Observations
- **Shadow observations**: How the Postural Effort System manifests in this lesson
- **Foundation observations**: How the Postural Support System emerges
- **Washing gradient**: How the Shadow is eroded through the lesson

### 2. Somatic Narrative
- **Primary Inquiry**: The fundamental riddle (in this framework's terms)
- **Washing Gradient**: How the Shadow is eroded (use lens-specific language, not "cortical noise")
- **Threshold of Quiet**: The moment where Transmission emerges

**CRITICAL:** The Primary Inquiry and Washing Gradient must be DISTINCT paragraphs with different content. The Inquiry frames the question; the Gradient describes the process of change. Do not reuse sentences between them.

### 3. VL Analytical Table

Use the **groups** from Pass A — one row per group. For each group, add:
- The Question (through this VL's lens)
- Negotiation State (Bracing/Softening/Transmission)
- Functional Outcome (skeletal/structural result)

**Column definitions:**
```
column_headers: {
  "column1": "Seq",
  "column2": "SPIFFER-Q",
  "column3": "The Question",
  "column4": "Negotiation State",
  "column5": "Functional Outcome"
}
```

The `Seq` column should reference the step numbers from the group (e.g., "1-3" or "4, 5, 6"). Produce exactly one row per group from Pass A.
