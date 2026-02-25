# Pass 0b-lang: Step-Level Language Analysis

You are an expert in Feldenkrais pedagogy and language analysis. You are given a lesson that has been decomposed into numbered **steps** (somatic events), each spanning one or more original transcript segments. Steps are organized into movement exploration **groups**.

Your task is to extract the teacher's **actual language** at each step — the specific words and phrases they use to direct attention, frame effort, create images, and explain causation. This analysis is for teacher training: a trainee can study how experienced teachers use language by seeing the literal words they choose and what those choices accomplish.

**Extract literally from the original segment texts.** Do not paraphrase, summarize, or interpret. If the teacher says "listen for the work in the fibers," extract that phrase — do not write "directs attention to muscle sensation."

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

## Step-Level Extraction

For each step, extract the following five categories of language. Every field should contain **literal quotes** from the original segment texts within the step. Semicolons separate multiple extractions. Write "None" if no language of that type is present.

Many steps are plain movement commands with no interesting language — write "None" for all fields in those steps. Do not force extractions where the language is generic.

1. **attention_cues**: How the teacher directs the student's attention and perception — what to notice, feel, sense, listen for, imagine, or attend to. The verb matters: "listen for" frames attention differently than "feel" or "notice." Extract the full phrase including the attentional verb. Examples: "listen for the work in the fibers"; "feel the contact of your lower back with the floor"; "notice the relationship between your pelvis and your ribs"; "imagine the arm floating."

2. **effort_language**: How the teacher frames effort, ease, permission, and constraint. Includes reduction language, permission language, negation/prohibition, and minimization. Examples: "almost insufficient effort"; "you don't need to insist"; "do not contract your abdomen"; "softly"; "the most quiet quality"; "as small as possible."

3. **image_language**: Metaphors, analogies, physical imagery, and vivid descriptive language that makes the abstract concrete. Examples: "think of your pelvis like a bowl of soup"; "this big sheath of muscle that wraps around"; "a kind of cinching of the waist"; "pushing the accelerator and the brake at the same time."

4. **explanatory_language**: Cause-and-effect explanations, biomechanical reasoning, and pedagogical transparency (explaining why the lesson is designed this way). Examples: "the pressure will just emerge out of the specificity"; "these are synergists — muscles that work together"; "Feldenkrais with his engineering hat on, where now he's created a situation where..."

5. **distinctive_phrasing**: Any word choices that are notably unusual, specific to this teacher's voice, or otherwise remarkable — language that you wouldn't hear from a generic instructor. This catches signature vocabulary and striking turns of phrase that don't fit the above categories. Examples: "source the muscular effort" (using "source" as a verb); "taste what the walking feels like"; "lengthen the pubic bone down away from the head."

## Lesson-Level Summary

After analyzing all steps, produce a summary of the teacher's overall language patterns:

1. **speaker_principles**: 3–7 principles of how this teacher uses language to connect to the student. Each principle should:
   - Name the pattern (e.g. "Uses negation to redirect effort")
   - Give a specific example from the lesson
   - Explain what it accomplishes pedagogically — what does it help the student do, feel, or understand?
   Frame these so a trainee teacher can learn from them.

2. **signature_vocabulary**: Words or phrases that appear to be distinctive to this teacher — not generic Feldenkrais language that any teacher would use, but choices that reveal this particular teacher's voice. List each with brief context for how it's used.

3. **dominant_attention_mode**: The primary way this teacher directs attention across the whole lesson (e.g. "proprioceptive listening," "kinesthetic feeling," "visual imagery"). One phrase.

4. **effort_philosophy**: A 1–2 sentence summary of this teacher's relationship to effort, as expressed through their language across the lesson.

5. **teaching_voice_summary**: A short paragraph (3–5 sentences) characterizing how this teacher uses language as a pedagogical tool. Written for a trainee teacher — what can they learn from this teacher's approach to language? Be specific and practical, not abstract.

## Important

- Process steps in order — each step's context depends on what came before.
- Do NOT skip steps. Every step must appear in your output.
- Do NOT merge or split steps. The step numbers must match 1:1 with the input.
- Include the `group_number` from the group each step belongs to.
- Extract language **literally from the original segment texts**. Do not paraphrase.
- Not every step will have all fields populated. Many steps are simple commands with no distinctive language — write "None" for those fields.
- The summary should synthesize patterns across the whole lesson, not just list what individual steps contained.
- Discover patterns fresh from the text. Do not assume what patterns you will find.

## Lesson Steps

