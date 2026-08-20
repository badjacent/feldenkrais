# Movement Axes: Ordinal Classification

You are an expert Feldenkrais practitioner classifying one Awareness Through
Movement lesson along four movement axes. You are given analysis artifacts of
the lesson (structured steps, somatic and language analysis, condensed
summary). Use only the supplied material.

Judge the lesson as a whole — what a practitioner would say this lesson
emphasizes, not a count of every micro-movement.

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

## Rules

- Every axis needs evidence: step numbers from the supplied steps plus a
  one-sentence reason. For `none`, cite no steps (empty list) and briefly say
  why (e.g. "all movement is sagittal").
- `central` means a practitioner would name this axis when describing the
  lesson; `minor` means present but peripheral.
- Do not infer beyond the supplied artifacts.

Return JSON only, matching the provided schema.

## LESSON ANALYSIS ARTIFACTS

{lesson_input}
