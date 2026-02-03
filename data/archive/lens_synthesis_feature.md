# Lens Synthesis Feature (Removed)

Removed in commit `6eee26f` (2026-02-03). Originally added in `106574a`.

## What it was

An LLM query block added to the "The Lens" section of each booklet, after the static Person Inside and Shadow descriptions. It generated a 2-3 sentence paragraph synthesizing the lens framework with the specific lesson's mechanics.

## Why it was added

Review issue #6 flagged that "The Lens section repeats definitions instead of analyzing the lesson." The static text (from frameworks.json) is generic — it describes The Riser, The Cartographer, etc. in the abstract but doesn't connect to the lesson the reader just did.

## What it looked like

A new `<h3>In This Lesson</h3>` subsection appeared at the bottom of The Lens section, containing LLM-generated text like:

> In this lesson, The Riser would have noticed the pelvis beginning to transmit force upward as the oscillations softened the lower back's grip...

## Implementation

### Query type (in BOOKLET_QUERY_TYPES)

```python
"lens-synthesis": {
    "name": "Lens Synthesis",
    "location": "The Lens section, after static framework description",
    "purpose": "Synthesize the lens's philosophy with THIS specific lesson's mechanics.",
    "voice": "Reflective, specific.",
    "length": "2-3 sentences",
    "prompt": (
        "You just read about {framework_name} — the {person_inside}. The lens description says: "
        "\"{lens_description}\"\n\n"
        "The Shadow for this lens is: \"{shadow_description}\"\n\n"
        "This particular lesson's central question was: \"{primary_inquiry}\"\n\n"
        "Write 2-3 sentences that synthesize the lens with THIS lesson. How does {framework_name}'s "
        "perspective specifically illuminate what happened in this lesson? What would the "
        "{person_inside} have noticed? How did the Shadow manifest in the movements you just did?\n\n"
        "Be SPECIFIC to this lesson — do not repeat the generic framework description. "
        "Show the reader how the lens applies HERE."
    ),
    "context_keys": [
        "framework_name",
        "person_inside",
        "lens_description",
        "shadow_description",
        "primary_inquiry",
    ],
}
```

### render_booklet_lens changes

`render_booklet_lens` gained a `somatic_narrative` parameter. After the static Shadow description, it added:

```python
primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
if primary_inquiry:
    context = {
        "framework_name": intro.get("name", ""),
        "person_inside": intro.get("person_inside", ""),
        "lens_description": intro.get("lens", ""),
        "shadow_description": intro.get("shadow_description", ""),
        "primary_inquiry": primary_inquiry,
    }
    html.append('<h3>In This Lesson</h3>')
    html.append(booklet_query_block("lens-synthesis", context))
```

The call site also changed to pass somatic_narrative:
```python
sections.append(render_booklet_lens(framework_id, primary_vl, somatic_narrative))
```

### prompt_vars addition

`shadow_description` was added to the `prompt_vars` dict in `booklet_query_block` (commit `c8b8e14`):
```python
"shadow_description": context.get("shadow_description", ""),
```
This key is still present and harmless.

## Why it was removed

1. Added 1 LLM call per booklet (5 calls total per pipeline run)
2. Contributed to 3-4x slowdown in booklet generation (394-745s vs 140s)
3. Review summary never flagged the Lens section as improved

## Lighter alternative (no LLM call)

Instead of an LLM query block, pull the `primary_inquiry` from the somatic narrative and render it as static HTML:

```python
primary_inquiry = (somatic_narrative or {}).get("primary_inquiry", "")
if primary_inquiry:
    html.append(f'<h3>In This Lesson</h3>')
    html.append(f'<p>The question this lesson posed to {intro["person_inside"]}: '
                f'<em>{primary_inquiry}</em></p>')
```

This gives lesson-specific content in The Lens section with zero LLM cost.
