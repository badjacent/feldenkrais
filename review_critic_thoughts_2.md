# Review Critic Taxonomy

## The Problem with a Single Unstructured Critic

A general-purpose critic tends to gravitate toward surface-level observations: repetition, vocabulary overlap, tone inconsistency. It lacks a *reading stance* — it doesn't know what it's optimizing for, so it optimizes for everything and nothing. The result is a flat ranked list where "some paragraphs sound similar" competes with "the reflection section doesn't help the reader re-enter the lesson."

## Types of Critics

### 1. Structural Critic
**Reads for:** Architecture, flow, pacing, information hierarchy.
- Does the pamphlet have a clear arc (setup → development → integration → reflection)?
- Is there redundancy across sections, or does each section do distinct work?
- Does the ordering match how a reader would naturally move through the material?
- Are transitions between sections smooth or abrupt?

**Output:** Structural map with annotations on what each section accomplishes, gaps, and redundancies.

### 2. Persona Critic (The Student)
**Reads as:** Someone who just finished the ATM lesson and is holding the pamphlet.
- Do I recognize my experience in this text? Does it describe what I actually felt?
- Does it deepen my understanding of what just happened, or does it lecture me?
- Is there anything I can take into my next practice session?
- Where do I lose interest or feel talked-at?
- Does the "Next Time" section give me something concrete and inviting?

**This is probably your highest-value critic.** The current review has no user proxy. A student critic would catch problems like: "this analytical table means nothing to me" or "the shadow voice description is fascinating but the intro paragraph is generic."

### 3. Persona Critic (The Teacher)
**Reads as:** A Feldenkrais practitioner who might hand this to a student.
- Would I be comfortable giving this to someone after class?
- Does it accurately represent the lesson's pedagogy without oversimplifying?
- Does it reveal something about the lesson I might use in my teaching?
- Is the VL framework language accessible or does it sound like jargon?

### 4. Coherence Critic
**Reads for:** Internal consistency across sections within one pamphlet.
- Do the journey narrative, arc table, pedagogical glosses, and reflection tell a consistent story?
- If the arc table says "the pelvis discovers its relationship to the head," does the journey section actually describe that discovery?
- Do the Person Inside voice and Shadow voice align with the analytical content?

### 5. Specificity Critic
**Reads for:** Grounding in the actual lesson content vs. generic filler.
- Could this paragraph appear in *any* Feldenkrais pamphlet, or is it specific to this lesson?
- Are movement descriptions concrete (names body parts, describes actions) or abstract ("the body integrates")?
- Does the text reference specific steps/moments from the lesson?

**This critic catches the #1 failure mode of LLM-generated content:** plausible but generic text that could apply to anything.

### 6. Voice/Tone Critic
**Reads for:** Consistency and appropriateness of register.
- Is the tone inviting or clinical?
- Does it shift register unexpectedly (casual → academic → poetic)?
- Is the Person Inside voice genuinely experiential or is it performing introspection?
- Does prescriptive language creep in ("you should feel...")?

### 7. Cross-Pamphlet Critic (already partially covered by current review)
**Reads across all 5 pamphlets for:** Differentiation between lenses.
- Does each lens ask a genuinely different question about the lesson?
- Are the journeys different experiences or the same narrative with different vocabulary?
- This is where "lens bleed" lives, but it needs to distinguish *vocabulary overlap* (fine) from *interpretive overlap* (problem).

## Recommended Priority

1. **Persona: Student** — highest value, currently missing entirely. Catches "who is this for?" problems.
2. **Specificity** — catches the most common LLM failure mode. Could be a simple scoring rubric: count concrete movement references per section.
3. **Coherence** — catches internal contradictions between sections that a general critic misses because it reads linearly.
4. **Structural** — useful but lower priority; the structure is template-driven so there's less variance.

## Implementation Thought

Each critic could return a focused, typed output rather than a free-text review:
- Student critic: per-section "would I keep reading?" score + specific callouts
- Specificity critic: per-section count of concrete references + flagged generic sentences
- Coherence critic: cross-reference matrix (does section X align with section Y?)

This would make the review_summary step much more actionable — instead of "improve the reflection section," it becomes "the reflection section scored 2/5 on student engagement because it doesn't reference any specific movement from the lesson."

## The Student Critic in Detail

Since you asked specifically: yes, a student critic would be extremely valuable. The prompt would set up the persona:

> You just completed a 45-minute Feldenkrais ATM lesson. You're slightly drowsy, pleasantly surprised by how different your body feels. Your teacher hands you a short pamphlet about the lesson you just did. Read it as that person.

The critic would evaluate:
- **Recognition** — "Do I see my experience reflected here?"
- **Insight** — "Did I learn something I didn't notice during the lesson?"
- **Invitation** — "Do I want to do this lesson again?"
- **Accessibility** — "Is there anything I don't understand?"
- **Engagement** — "Where did I zone out?"

Each of these could be scored per-section, giving you a heat map of where the pamphlet loses the reader.

## How Much of This is Established vs. Improvised?

**Honest answer:** The taxonomy above is a synthesis. The individual pieces draw from real traditions, but I assembled them for this use case. Here's what's established and where to learn more:

### What's well-established

**Multi-agent LLM evaluation (LLM-as-Judge)** is a rapidly growing research area. The core finding: multiple specialized LLM critics with distinct roles consistently outperform a single general-purpose critic. Key papers:

- **[MAJ-EVAL (Multi-Agent-as-Judge)](https://arxiv.org/abs/2507.21028)** — Automatically constructs multiple evaluator personas with distinct dimensions, then has them debate. Tested in education and medical domains. Key insight: "no single human annotator can represent the relevant multi-stakeholder perspectives."
- **[CollabEval](https://www.amazon.science/publications/enhancing-llm-as-a-judge-via-multi-agent-collaboration)** — Three-phase collaborative evaluation (initial evaluation, multi-round discussion, final judgment). Outperforms single-LLM across all dimensions.
- **[Agent-as-a-Judge](https://arxiv.org/html/2508.02994v1)** — Uses an agent to evaluate another agent, examining the entire chain of actions, not just the final output.
- **[Awesome-LLM-as-a-Judge](https://github.com/llm-as-a-judge/Awesome-LLM-as-a-judge)** — Curated list of papers on the topic.

**Persona-based evaluation** is standard in UX/content strategy. Having a "user persona" read content and evaluate it from their perspective is textbook content design practice.

**Rubric-based text evaluation** is established in education (writing assessment rubrics) and NLP evaluation (human evaluation protocols for generated text).

**Role decomposition** is an emerging pattern. For example, the RADAR framework assigns explicit roles: Security Criterion Auditor, Vulnerability Detector, Counterargument Critic, and Holistic Arbiter — each contributing a distinct evaluative dimension.

### What I synthesized

The specific seven-critic taxonomy (structural, student persona, teacher persona, coherence, specificity, voice, cross-pamphlet) — I assembled this for the Feldenkrais pamphlet use case. The *categories* are grounded in real evaluation dimensions from the literature, but the specific application to your pamphlets is novel.

The "specificity critic" concept maps to what the NLG evaluation literature calls **faithfulness** or **groundedness** — whether generated text is grounded in source material rather than hallucinated/generic.

The "student persona critic" maps directly to the **persona-based multi-perspective panels** pattern identified in the multi-agent evaluation literature, where distinct stakeholders (teacher vs. parent vs. child) each evaluate along different dimensions.

### Where to learn more

- The [Awesome-LLM-as-a-Judge](https://github.com/llm-as-a-judge/Awesome-LLM-as-a-judge) repo is the best single starting point
- [Arize's LLM-as-a-Judge primer](https://arize.com/llm-as-a-judge/) for practical implementation patterns
- The MAJ-EVAL paper for the "automatically construct persona-based evaluators" approach — directly relevant to what you'd build
- [CritiqueLLM](https://ar5iv.labs.arxiv.org/html/2311.18702) for scaling LLM-as-critic with structured evaluation dimensions
