# Pass 0b Segmentation: Identify Pedagogical Arcs

You are given the group structure and summaries of a Feldenkrais lesson. Your task is to identify whether the lesson contains distinct pedagogical arcs (segments) that would benefit from separate synthesis.

## When to Segment

Only segment when:

- The teacher shifts to a genuinely different organizing principle (not just a new variation or position)
- The later groups introduce a new perceptual or biomechanical experiment unrelated to the earlier work
- A reader would be confused if the lesson's "about" statement tried to cover both arcs

## When NOT to Segment

Do NOT segment for:
- Position changes (lying to sitting, side to back) — these are variations, not new arcs
- Rest periods or scanning — these are pauses within an arc
- Increasing complexity — building from simple to complex is a single arc
- Bilateral work — doing the same exploration on the other side is the same arc
- Integration movements — returning to a simpler version to feel the learning

## Constraints

- Minimum 2 groups per segment
- Maximum 3 segments per lesson (if you think there are more, you're over-segmenting)
- Segments must be contiguous — no skipping groups
- All groups must be assigned to exactly one segment
- Single-segment lessons (the majority) produce one segment containing all groups

## Output

For each segment, provide:
- **segment_number**: Sequential, starting at 1
- **groups**: Array of group numbers in this segment
- **theme**: A brief phrase capturing the organizing principle of this segment
- **transition_note**: For all segments except the last, describe what shifts at the boundary. Null for the last segment.

## Lesson Data

