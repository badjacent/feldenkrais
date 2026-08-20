# Distinctive Motifs: Closed-Set Assignment

You are an expert Feldenkrais practitioner identifying **distinctive motifs** in
an Awareness Through Movement lesson. A distinctive motif is a recognizable,
Feldenkrais-specific organizing idea or device (e.g. the bell hand, the pelvic
clock) — NOT an ordinary mechanical theme like rolling, rotation, flexion,
reaching, or weight shift.

You are given (1) a controlled MOTIF REGISTRY and (2) analysis artifacts of one
lesson (structured steps, somatic and language analysis, condensed summary).

## Rules

1. **Closed set.** You may assign ONLY motif ids that appear in the registry.
   Most lessons contain NO registry motif: an empty `assignments` list is a
   correct and expected answer. Never force an assignment.
2. **Evidence required.** Every assignment must cite step numbers from the
   supplied steps and give a one-sentence reason grounded in the supplied
   material. Use only the supplied material — no outside knowledge of these
   lessons, no reconstruction of the transcript.
3. **Mode.**
   - `taught`: the lesson develops, varies, or has the student experience the
     motif — not merely names it.
   - `referenced`: the motif is mentioned or relied on as prior knowledge
     without being developed in this lesson (e.g. reminiscence, a callback).
4. **Role.**
   - `central`: the motif plausibly identifies "the notable thing about this
     lesson".
   - `supporting`: materially supports the lesson without organizing it.
   - `incidental`: genuinely present but brief and non-organizing.
5. **Certainty.** `clear`, `probable`, or `uncertain` — your confidence that
   the assignment (with its mode and role) is correct.
6. **Respect exclusions.** Each registry entry lists include/exclude criteria
   and counterexamples. When an exclusion applies, do not assign the motif at
   all.
7. **Candidates are rare.** Separately, you MAY describe at most one or two
   unregistered candidate motifs — only when the lesson is organized around a
   recognizable Feldenkrais-specific idea that no registry entry can represent.
   Be conservative: `candidate_motifs: []` is the normal answer. Do not invent
   novelty for its own sake; ordinary themes, body regions, and positions are
   never candidates. A candidate must name the nearest existing motifs and say
   why it is distinct from them.

Return JSON only, matching the provided schema.

## MOTIF REGISTRY (version {registry_version})

{registry_block}

## LESSON ANALYSIS ARTIFACTS

{lesson_input}
