# Carriage of the Head — Claude Conversation

Date: 2025-02-25

## Lessons Involved

- **Carriage of the Head, Part 1** — Andrew Gibbons (document ID: `2bf94f97-cf71-49df-951e-2ee8c0665e51`): The actual lesson under discussion
- **Carriage of the Head** — Andrew Gibbons, Cleaning Up the Classics Lesson 6 (document ID: `f07fde95-c9fc-40d7-a944-dc4ad4c5ab93`): Initially analyzed by mistake (different lesson — a prone-to-sitting spiral lesson)
- **Carriage of the Head, Andrew Gibbons Version (Truncated)** (document ID: `ad1f9d94-baba-4736-b8dd-4b5200a83100`): Created during this conversation — first 6 groups of Part 1 plus a generic wind-down, omitting the focal length experiment (groups 7-8)

## Code Changes Made (before the analysis discussion)

### 1. Fixed Pass 0b Debug View
- Rewrote `render_debug_log()` to be self-contained: unique IDs (random prefix), inline JS, flat tab bar, no nested collapsibles
- Merged somatic + lang debug logs before rendering in `api_pass0b_combined()` (both success and error paths)
- Previously broken: duplicate HTML IDs, missing `showDebugTab()` JS on pass0b page

### 2. Condensed Card Display Reformatting (Player Page)
- **Setting card**: somatic_intent → italic text (no bullet/label); space, teacher_presence, arc → bullet points
- **Group cards**: the_action → plain text (no bullet/label); body, attention, intention, voice → bullet points
- Replaced the colored `pov-section` divs and `setting-field` divs with simpler layout

### 3. Removed Oblique References from pass_0b.md
- Archived original to `data/archive/pass_0b_with_obliques.md`
- Removed detailed individual oblique muscle guidance from `activation` and `inhibition` field descriptions
- Kept simple "obliques (trunk rotation left)" example in activation

## Investigation: Missing Eyes/Teleceptors in Setting Card

### The Observation
The condensed "setting" card for Carriage of the Head Part 1 was missing the central role of the eyes — specifically, the eye opposition mechanism (eyes as counterweight to body rotation) that runs through the first 6 groups and is arguably the lesson's core teaching.

### Initial Confusion
We first pulled analyses for the wrong lesson (`f07fde95` — Cleaning Up the Classics Lesson 6, a prone-to-sitting spiral lesson). In that lesson, eyes/teleceptors genuinely appear only at the very end (steps 30-31 of 31), so the initial theories focused on signal loss from low word frequency. Once we switched to the correct lesson (Part 1, `2bf94f97`), the picture changed dramatically.

### What the Correct Lesson Shows
The condensed setting card for Part 1 titled the lesson "Focal Length and the Spinal Cascade" and centered `somatic_intent` on "visual focal length and the muscular tone of the deep suboccipital region." The group cards *did* capture the eye opposition well (groups 2-5 all mention eyes as counterweight). But the setting card elevated the focal length experiment (groups 7-8) to the primary description, rather than the eye-opposition mechanism (groups 2-6) which is the structural through-line.

### Key Insight: Two Layers in the Lesson
1. **Primary mechanism (groups 2-6)**: Eyes oppose the body's rotation, completing the movement chain. The head is pinned, the body spirals, the eyes go opposite as a counterweight. This is the core teaching.
2. **Secondary observation (groups 7-8)**: Focal length modulates suboccipital tone. Long gaze frees the atlas/axis; short gaze locks them. This *explains why* the long-distance gaze helps the counterweight work, but it's a modifier, not the main mechanism.

The setting card got the hierarchy backwards — likely because the focal length content is more novel/distinctive to the LLM, while the eye-opposition is subtler and distributed across many groups.

### Broader Principle: Implicit Biomechanics
This led to a discussion about how the eyes are part of the rotational story *whether or not the teacher mentions them*. The VOR, the suboccipital coupling, the teleceptor orientation — these are always operative in rotation. A "signal lesson" like this one makes the implicit explicit.

This is a fundamental limitation of text-based extraction: `pass_0b.md` extracts what's said, but the biomechanical reality includes mechanisms that aren't named. The `feldenkrais_principles.md` file can document these, but there's currently no pipeline stage that applies them.

### Future Direction: Intent System
The idea is to develop a system where:
1. Principles are compiled in `feldenkrais_principles.md` (done — principle #8 added)
2. A future "intent" pass identifies which principles are at play in a given lesson
3. A secondary analysis integrates that knowledge into the narrative output (e.g., the setting card)

This remains architectural — no code changes made for this yet.

### Actions Taken
- Added **Principle #8: Teleceptors and the completion of rotational chains** to `data/feldenkrais_principles.md` — covers VOR, suboccipital muscles, eye-spine coupling, focal length effects on tone
- Added Carriage of the Head Part 1 to the source lessons list in the principles file
- Created truncated lesson (`ad1f9d94`) with groups 1-6 plus wind-down, to enable testing the eye-opposition content without the focal length add-on
