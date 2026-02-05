# Methodology Proposals: Synthetic Evaluation Panel

These are concrete next steps for building out the multi-critic evaluation system. Each proposal is independent — they can be implemented in any order.

---

## 1. Typed Critic Outputs (Replace Free-Text Reviews)

**Current state:** The review node returns unstructured text. The review_summary node parses it into a ranked issue list. This loses signal and makes it hard to track whether issues recur across iterations.

**Proposal:** Each critic returns a structured JSON object with typed fields. Examples:

- **Student Critic** returns per-section scores (recognition, insight, invitation, accessibility, engagement) on a 1–5 scale, plus specific callout strings.
- **Specificity Critic** returns per-section counts of concrete movement references and a list of flagged generic sentences (with line numbers or section IDs).
- **Coherence Critic** returns a cross-reference matrix: for each pair of sections, a boolean "consistent" flag and a brief explanation when false.

**Why it matters:** Structured output makes the improvement loop deterministic. Instead of "improve the reflection section," the agent gets "reflection section scored 2/5 on student engagement; flagged sentences: [list]." This also enables tracking scores across iterations to detect regressions.

**Implementation:** Add a JSON schema per critic type. Use structured output mode (already supported in the LLM layer). The review_summary node becomes an aggregator over typed inputs rather than a text parser.

---

## 2. Critic Calibration via Human Annotation

**Problem:** LLM critics drift. A specificity critic that flags 40% of sentences as "generic" is useless. One that flags 5% might be too lenient.

**Proposal:** For 3–5 pamphlets, have a human annotator independently score the same dimensions the critics score. Compare human scores to critic scores. Use the gap to:
- Adjust prompt wording (if the critic systematically misses a category)
- Set thresholds (e.g., "a specificity score below 3.0 triggers revision")
- Establish inter-rater reliability as a baseline metric

**Scale:** This is a one-time investment of ~2 hours per critic. The calibration data becomes a test set for prompt regression testing — if you change a critic prompt, re-run it against the calibrated set and check that scores still correlate.

---

## 3. Issue Fingerprinting and Tracking

**Current state:** Each iteration produces a fresh review. There's no way to know if an issue from iteration 2 was fixed in iteration 3 or just wasn't mentioned.

**Proposal:** Assign each issue a fingerprint based on: critic type + section ID + issue category. Track fingerprints across iterations. This enables:
- **Fix verification:** Did the issue disappear after the edit?
- **Regression detection:** Did a fix introduce a new issue in a different section?
- **Convergence metric:** Number of open issues should decrease monotonically. If it plateaus or increases, stop iterating.

**Implementation:** Store issue fingerprints in the pipeline cache alongside iteration metadata. The review_summary aggregator matches new issues against prior fingerprints before ranking.

---

## 4. Critic Debate / Arbitration

**Inspired by:** CollabEval and MAJ-EVAL papers.

**Problem:** Critics sometimes disagree. The specificity critic flags a passage as "too abstract," but the student critic scores it highly on engagement. Which wins?

**Proposal:** After individual critics run, add a lightweight arbitration step:
1. Collect all issues that touch the same section.
2. If critics conflict (one flags, another praises), present both perspectives to an arbitrator LLM with the instruction: "Given these two evaluations of the same section, which issue should be prioritized and why?"
3. The arbitrator doesn't override — it adds a priority weight and a rationale.

**When to add this:** Only after you have 2+ critics running and observe conflicting signals. Don't build it speculatively.

---

## 5. Stratified Improvement Targeting

**Current state:** The improvement agent picks ~3 issues per iteration from a flat ranked list.

**Proposal:** Require that each iteration addresses issues from at least 2 different critics. This prevents the agent from spending 5 iterations on specificity while ignoring coherence.

**Implementation:** Simple constraint in the issue selection step: partition the ranked list by critic type, pick the top issue from the top 2–3 partitions, then fill remaining slots from the overall ranking. This is a 10-line change in the improvement loop.

---

## 6. A/B Scoring for Edit Validation

**Problem:** The improvement agent makes an edit, but you don't know if it actually helped until the full review re-runs.

**Proposal:** Before committing an edit, run the relevant critic on just the affected section — once with the old text, once with the new text. If the new text scores worse on any dimension, flag it for human review before committing.

**Cost:** One extra LLM call per edit per relevant critic. Cheap if critics return structured scores (no need to re-run the full review pipeline).

**When to add this:** After typed critic outputs are implemented (Proposal 1). Without structured scores, there's nothing to compare.

---

## 7. Convergence Detection

**Current state:** The improvement loop runs for a fixed number of iterations.

**Proposal:** Define convergence as: all critic scores above threshold AND no new high-priority issues for 2 consecutive iterations. Stop early if converged; flag for human review if not converged after N iterations.

**Metrics to track per iteration:**
- Total issue count (should decrease)
- Worst section score per critic (should increase)
- Number of new issues introduced (should be zero or near-zero in later iterations)
- Edit size (should decrease — large edits late in the process suggest instability)

---

## 8. Cross-Document Learning

**Longer-term proposal.** After generating books for 10+ lessons:

- Aggregate critic scores across all books. Which sections consistently score lowest? This reveals systematic prompt problems, not per-lesson issues.
- Build a "common fixes" library: if the specificity critic repeatedly flags the same kind of generic sentence, add that pattern to the generation prompt as a negative example.
- Track which improvement actions actually work. If "add a concrete movement reference" consistently raises specificity scores, prioritize that action type.

This turns the evaluation panel from a per-book tool into a system-wide quality signal.

---

## Recommended Implementation Order

1. **Typed critic outputs** (Proposal 1) — foundation for everything else
2. **Student persona critic** — highest-value new critic, currently missing
3. **Specificity critic** — catches the most common LLM failure mode
4. **Issue fingerprinting** (Proposal 3) — enables convergence detection
5. **Convergence detection** (Proposal 7) — makes the loop self-terminating
6. **Calibration** (Proposal 2) — once you have real scores to calibrate against
7. **Everything else** — as needed based on observed failure modes
