# Pass 0b Condensed: Lesson Cards

You are given a complete analysis of a Feldenkrais lesson — the structural decomposition (Pass A), step-level somatic enrichment (Pass 0b), and step-level language analysis (Pass 0b-lang). Your task is to condense this rich analysis into a **setting card** for the lesson as a whole, plus **one card per movement exploration group**.

## Purpose

These cards are for a reader who wants to enter the space of the lesson — to understand what happens, what it feels like, and why. The cards should preserve the essential character of different experiential registers rather than flattening everything into one voice.

## Five Points of View

The pass 0b analysis captures different "listeners" in the person. Each has its own experiential character. Your cards must preserve all five:

| Point of View | Character | Source Fields |
|---|---|---|
| **The Action** | Concrete, sequential, immediate — what you do | `mechanical_directive`, `clarified` |
| **The Body** | Biomechanical, spatial, structural — what is happening physically | `activation`, `inhibition`, `transmission_route`, `ground_push`, `differentiation` |
| **The Attention** | Sensory, perceptual, present-tense — what to notice | `attentional_cue`, `sensory_inquiry`, `attention_cues` |
| **The Intention** | Metacognitive, strategic, narrative — why this matters | `intent`, `explanatory_language` |
| **The Voice** | Linguistic, tonal, aesthetic — how the teacher speaks | `effort_language`, `image_language`, `distinctive_phrasing` |

## Group Cards (generate FIRST)

Produce one card per group, in group order. For each group, synthesize all the steps in that group across both analyses into a single coherent card.

### Fields per group card:

- **group_number**: The group number from Pass A.
- **group_label**: The group label from Pass A.
- **title**: A short evocative title for this card — capture the essential quality of what happens in this group. Not the same as group_label; aim for something a reader would find inviting.
- **the_action**: What you do — the essential physical sequence condensed into 2-4 sentences. Written as direct instruction ("You lift... you turn..."), integrating the clarified movements from all steps in this group into a flowing description. Not a list.
- **the_body**: What is happening physically — the biomechanical story of this group. Which structures activate, which must release, where force travels, what is being differentiated from what. Written as a short paragraph (3-5 sentences) that a practitioner could read and understand the movement logic.
- **the_attention**: What to notice — the attentional and sensory landscape of this group. What the teacher directs you to feel, sense, listen for. Preserve the teacher's literal language where it is striking. Written as 2-4 sentences.
- **the_intention**: Why this matters — the pedagogical purpose and the teacher's explanations. What is the learning strategy? What cause-and-effect reasoning does the teacher offer? Written as 1-3 sentences.
- **the_voice**: How the teacher speaks here — the linguistic character of this group: effort language, imagery, metaphors, distinctive phrasing. Preserve literal quotes. If no distinctive language in this group, write "None". Written as 1-3 sentences.

## Setting Card (generate AFTER group cards)

The setting card creates the felt space of the lesson for the reader. Its purpose is to help the reader enter the lesson — not to describe it from outside. Generate this AFTER the group cards so that if output is truncated by token limits, the group cards (which are more critical) are preserved.

### Fields:

- **title**: A short evocative title for the lesson as a whole.
- **somatic_intent**: What the lesson is physically about — the muscular trajectory and somatic architecture. What will the body learn? What is the central differentiation or transmission being developed? 2-4 sentences. Be specific to this lesson — avoid Feldenkrais generalities.
- **space**: The felt space of the lesson: starting position, quality of the environment the teacher creates, the overall attentional tone. This should help the reader *enter* the lesson — not describe it from outside. 2-3 sentences.
- **teacher_presence**: The teacher's tone and additional pedagogical intent. How does this teacher approach this material? What is their characteristic way of guiding? Any particular emphasis or teaching strategy visible in their language? 2-3 sentences.
- **arc**: The lesson's movement arc — how the groups build on each other. What is the learning trajectory from beginning to end? 2-3 sentences.

## Important

- Generate group cards FIRST, then the setting card. This protects group cards from truncation.
- Include all groups — do not skip any.
- Each PoV section should feel like it's written by a different "voice" — the action voice is direct and physical, the body voice is analytical and spatial, the attention voice is sensory and present-tense, the intention voice is reflective and strategic, the voice section preserves the teacher's actual language.
- Preserve striking literal language from the teacher — especially in the_attention and the_voice sections.
- The setting card should synthesize across all groups, not just describe the first one.
- Be specific to this lesson. Avoid generic Feldenkrais platitudes ("awareness through movement," "reducing effort," "listening to the body") unless they are specifically warranted by this lesson's content.

## Lesson Data

