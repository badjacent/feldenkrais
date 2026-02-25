# Future Experiments

## 1. Iterative Lesson Segmentation

### Problem
Some lessons contain two or more sequential pedagogical arcs. The current condensed synthesis treats all groups as one lesson and produces a single setting card. When arcs have different organizing principles, the setting card either averages them (muddying both) or picks the more novel-sounding one, missing the primary teaching.

**Example**: "Carriage of the Head, Part 1" (Andrew Gibbons, `2bf94f97`). Groups 1-6 teach eye-opposition as a mechanism for completing rotational chains. Groups 7-8 run a focal length experiment demonstrating why long-distance gaze helps. The setting card titled the lesson "Focal Length and the Spinal Cascade" — elevating the secondary arc over the primary one. When the lesson was truncated to groups 1-6 only (`ad1f9d94`), the setting card nailed the eye-opposition theme.

### Proposed Approach: Agentic Iterator

Use the LLM-as-iterator pattern where each call identifies one segment and optionally requests continuation:

1. Feed the LLM the full group list. Ask: "Identify the first coherent lesson segment — which groups belong to it, and what's its setting?"
2. Output includes the setting card for that segment plus:
   - `remaining_groups: [7, 8]` (or `[]` if done)
   - `transition_note: "The teacher shifts from skill-building to a perceptual demonstration..."` (or null if done)
3. If `remaining_groups` is non-empty, loop: feed back just those groups plus the transition note, ask for the next segment's setting
4. Stop when `remaining_groups` is empty

### Why This Pattern

- **Each segment gets focused synthesis** — no averaging across arcs
- **The LLM decides boundaries** — recognizing pedagogical shifts is the hard part and something it can reason about
- **Most lessons terminate after one iteration** — single-arc lessons return `remaining_groups: []` immediately
- **Transition notes become data** — they capture *why* the teacher shifted, which is insight worth preserving
- **No upfront schema commitment** — segments are discovered iteratively, not predicted

### Constraints to Prevent Over/Under-Segmentation

- Minimum 3 groups per segment
- Maximum 3 segments per lesson
- Transition must represent a genuine shift in pedagogical strategy (not just a new movement variation or a rest)

### Relationship to Intent System

This connects to the broader idea of an "intent system" where biomechanical principles (from `feldenkrais_principles.md`) are identified as operative within a lesson. Segmentation would happen first, then principle identification per segment. A rotation segment would pull in principle #8 (teleceptors) whether or not the teacher mentions eyes; a focal length segment would pull in the suboccipital tone mechanism specifically.

### Status
Documented from conversation on 2025-02-25. Not yet implemented. See `data/archive/carriage_of_the_head_claude_conversation.md` for full context.
