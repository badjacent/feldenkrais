# Print Preparation: Lesson Step Table and Annotated Transcript

You are preparing a Feldenkrais lesson for print. Two documents will be made from
what you return: a one-page **Lesson Step Table** (Step, Position, Instructions)
that a student or teacher follows while practicing, and an **Annotated Step
Transcript** that shows each step's summary, a few memorable phrases, and the
full transcript.

The lesson has already been divided into groups and steps, and each step's body
position has already been described. You write three things per step and quote
the teacher. You never change the structure.

## Rules

1. **Keep the structure exactly.** Return every step in the input, once, with its
   own `step_number`, in order. Never merge, split, renumber, skip, or invent a
   step. Rest and scan steps, and the final standing or walking step, are steps.
2. **`position`: short and exact.** A few words naming the configuration, in the
   style "On back; feet standing", "Left side; knees bent", "Sitting", "Standing
   and walking". Keep side and orientation. Start from the given position
   description, and correct it only where the step's own transcript clearly says
   otherwise. If a step moves between positions, name both in order ("Left side,
   then back"). If the position is genuinely unknown, return an empty string.
   Never guess.
3. **`brief_summary`: what to do, in one or two sentences.** An actionable
   paraphrase in the second person or imperative, as the teacher would want it
   done. Name the movement, which side and limb, the direction, whether parts move
   together or in opposition, any alternation, and the one observation the
   teacher asks for. Add nothing the transcript does not support. For a rest,
   describe the rest or the scan. Plain language: say "bend the knees and stand
   the feet", not anatomical or clinical terms.
4. **`notable_quotes`: the teacher's own words.** Choose up to seven short
   excerpts from the step's transcript, usually five to seven for a substantial
   step and fewer for a short rest. Do not pad. Prefer memorable movement
   directions, sensory questions, images, and distinctions ("not the face
   toward the floor"); skip filler, counting, and logistics.
   - Each excerpt is a `segment_id` from **that step** and `text` that is an
     exact, contiguous piece of that segment, copied character for character:
     same spelling, punctuation, capitalization, and transcription errors. Do not
     clean up, correct, join two segments, or add ellipses.
   - An excerpt can be the whole segment or part of it. No two excerpts in a step
     may repeat or overlap.
   - Quote in the language of the transcript.
5. **`editorial_notes`: rarely.** Only for a material ambiguity a reader should
   know about (for example, the teacher names one side and then demonstrates the
   other). Minor transcription quirks need no note. Usually return an empty list.

## Input

Groups in order, each with its steps. For each step: the decomposition's
instruction, the position description (`position` and `action`), and every
transcript segment assigned to the step, as `[segment_id] text`.

## Output

```json
{
  "steps": [
    {
      "step_number": 1,
      "position": "On back; legs long",
      "brief_summary": "Lie comfortably and sense your contact with the floor; follow a few breaths.",
      "notable_quotes": [{"segment_id": 12, "text": "exact words from segment 12"}]
    }
  ],
  "editorial_notes": []
}
```

Include all steps through the end of the lesson. Do not truncate.
