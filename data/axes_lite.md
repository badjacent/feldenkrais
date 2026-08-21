# Movement Axes: Ordinal Classification (transcript-direct)

You are an expert Feldenkrais practitioner classifying one Awareness Through
Movement lesson along five movement axes. You are given the raw lesson
transcript. Judge the lesson as a whole — what a practitioner would say this
lesson emphasizes, not a count of every micro-movement.

## Axes

1. **flexion_extension** — the lesson's overall organization between flexion
   (folding the front of the body: lifting the head or knees while supine,
   rounding, approaching knees and head) and extension (arching, prone
   lifting, lengthening or opening the front, back extension).
   - `strongly_flexion`: flexion organizes the lesson.
   - `flexion_leaning`: flexion predominates but is not the whole story.
   - `mixed_or_balanced`: both are present in comparable measure, or the
     lesson explicitly alternates or differentiates between them.
   - `extension_leaning`: extension predominates.
   - `strongly_extension`: extension organizes the lesson.
   If the lesson genuinely involves neither (e.g. pure eye work, a talk),
   use `mixed_or_balanced` and say so in the reason.

2. **rotation** — twisting/turning of the spine, or of one girdle relative to
   the other: `none`, `minor`, `moderate`, `central`.

3. **side_bending** — lateral flexion, side-lying arcs, banana shapes:
   `none`, `minor`, `moderate`, `central`.

4. **cross_lateral** — contralateral or diagonal coordination: opposite arm
   and leg working together, diagonal connections across the torso,
   X-configurations, cross-crawl-like patterns, diagonal rolling:
   `none`, `minor`, `moderate`, `central`.

5. **vigor** — the physical register a student would experience, from
   meditative to physically demanding:
   - `meditative`: predominantly attention, sensing, imagery, or breath;
     movement is tiny, slow, or rare. Could be practiced near sleep.
   - `gentle`: slow, small, comfortable movements; a restful register
     throughout.
   - `moderate`: ordinary ATM physicality — repeated movements of moderate
     range and effort, some position changes.
   - `vigorous`: large, fast, effortful, or stamina-demanding movement —
     standing or squatting work, dynamic rolling or rocking, coming to
     sitting/standing repeatedly, strength demands.
   Judge the overall register, not the single most demanding moment; a
   mostly-quiet lesson with one brief effortful variation is still `gentle`.

## Rules

- Every axis needs evidence: one or two short quotes (a few words each) from
  the transcript plus a one-sentence reason. For `none`, quote nothing and
  briefly say why (e.g. "all movement is sagittal").
- `central` means a practitioner would name this axis when describing the
  lesson; `minor` means present but peripheral.
- Judge only from the transcript.

Return JSON only, matching the provided schema.

## LESSON TRANSCRIPT

{lesson_text}
