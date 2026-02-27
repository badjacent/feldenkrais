# Pass 0b Word-Level Language Annotation

You are an expert in Feldenkrais pedagogy and language analysis. You are given a lesson decomposed into numbered **steps**, each with original transcript text, and a prior language analysis that identified key phrases in five categories.

Your task: for each step, locate the **exact verbatim phrases** in the transcript text that belong to each language category, and provide a short pedagogical note for each.

## Input Format

Each step shows:
1. The step header with step number, tags, and instruction
2. The original transcript segments (numbered in brackets)
3. The prior language analysis for this step (what was already identified)

Your job is to **anchor** those findings precisely to the transcript text and add micro-commentary explaining the pedagogical function.

## Output Format

Produce a **flat array** of phrase annotations across all steps. Each annotation includes the `step_number` it belongs to. Steps with no interesting language simply have no entries in the array.

Each annotation has:

- **step_number**: Which step this phrase comes from.
- **text**: A verbatim substring from that step's transcript segments. Must match exactly — same words in the same order as they appear in the transcript. Include enough words to capture the complete linguistic unit.
- **category**: One of: `attention`, `effort`, `image`, `explanatory`, `distinctive`
- **note**: 5-15 words explaining the pedagogical function of this phrase. What is the teacher doing with this language? Be specific and practical.

### Categories

- `attention` — How the teacher directs the student's perception: what to feel, notice, sense, listen for. The attentional verb matters.
- `effort` — How the teacher frames effort, ease, permission, and constraint. Reduction, minimization, prohibition, permission.
- `image` — Metaphors, analogies, physical imagery that makes the abstract concrete.
- `explanatory` — Cause-and-effect reasoning, biomechanical explanation, pedagogical transparency.
- `distinctive` — Unusual word choices specific to this teacher's voice. Signature vocabulary.

## Constraints

- **Verbatim only**: `text` must be a contiguous substring of the step's transcript. Do not paraphrase or rearrange words.
- **Many steps will have no annotations**: Simple movement commands ("bend your knees") have no interesting language. Simply omit them from the output.
- **Do not force annotations**: Only annotate phrases that genuinely serve a pedagogical language function. Quality over quantity.
- **Use the prior analysis as a guide**: The language analysis has already identified interesting phrases. Your job is to locate them precisely in the text and explain their function. You may also find additional phrases the prior analysis missed.
- **One phrase, one category**: If a phrase serves multiple functions, choose the primary one.
- **Keep notes practical**: Write for a trainee teacher who wants to learn this technique. "proprioceptive direction — 'feel' as attentional verb" is better than "directs attention."
- **Cover all steps**: Scan every step for interesting language, but only include annotations for steps that have it.

## Lesson Steps

