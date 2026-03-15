# Stage 1: EXTRACTION

Extract every instruction the teacher gives from the following Feldenkrais lesson transcript. An instruction is anything the student is asked or told to do — move something, attend to something, rest, or repeat.

## Rules

- Never infer instructions not explicitly in the text.
- Preserve exact teacher language in raw_text — do not paraphrase.
- body_parts_mentioned uses the teacher's own words (e.g. "nose", not "head").
- "Do it a number of times" or "repeat" is its own step — do NOT collapse it into the preceding instruction.
- Reframes (instructions that change the mental model of a movement already introduced) are their own step — do NOT merge them with the movement they modify.

## instruction_type

- **move**: the teacher asks the student to perform a physical action ("roll your head", "lift your arm")
- **attend**: the teacher asks the student to notice, sense, or feel something ("notice how you're lying", "feel the contact with the floor")
- **rest**: the teacher asks the student to stop and rest
- **reframe**: the teacher changes how the student thinks about a movement already introduced ("pretend you don't have neck muscles", "let your hand do the work")
- **repeat**: the teacher asks the student to repeat or continue a movement ("do it a few more times", "continue")

When ambiguous between move and attend, default to attend.

## Lesson transcript

