"""Model eval slice — rank LLMs on Feldenkrais lesson-analysis quality.

The task (analyzing an ATM lesson in dialogue) has no ground-truth answer key,
so this is a rubric + preference eval, not an accuracy benchmark. It reuses the
manual gemini-3.5-vs-preview comparison logic as an automated harness:

  1. Load a fixed set of seed questions from a real Q&A dialogue in the DB
     (the questions the user actually asked while reading the lesson), plus the
     lesson's cached analysis context (transcript + Pass A decomposition).
  2. Run every CANDIDATE model over every question with that context.
  3. Score each answer with a PANEL of judges from different families, on a
     rubric that explicitly penalizes the observed failure mode (confabulated,
     unverifiable mechanism dressed up as fact) rather than rewarding length.
  4. Also run pairwise-vs-reference: each candidate answer against the answer
     the user actually liked (the original dialogue response), so we measure
     "better or worse than what I already had," blind and order-randomized.
  5. Emit a ranking + per-model failure notes + a per-judge bias check.

The rubric is the whole point: naive "which answer is better" judging has a
verbosity bias, and the degenerate run in this corpus failed by getting LONGER
and more elaborate. The rubric inverts that — groundedness and restraint over
elaboration.

Usage:
    cd /Users/michaelhollander/github/feldenkrais
    set -a; source .env; set +a
    # smoke test: 2 questions, 2 candidates, 1 judge
    PYTHONPATH=src/kb .venv/bin/python -m ops.model_eval --smoke
    # full run
    PYTHONPATH=src/kb .venv/bin/python -m ops.model_eval \
        --doc 54f069a9-c520-4db4-a0c7-c284c3b84eaa --questions 16

Notes:
  - Constructs LLM instances directly (bypasses the settings:llm_enabled kill
    switch) since an eval is an explicit, deliberate batch of paid calls.
  - Sequential calls. For a large sweep, the Batches API would halve cost, but
    for ~16 questions this is simpler and fast enough.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(REPO / ".env")

from database import Database
from llm import LLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("model_eval")

OUT_DIR = REPO / "data" / "model_eval"
DEFAULT_DOC = "54f069a9-c520-4db4-a0c7-c284c3b84eaa"  # ATM Book #5 flexors/extensors (preview run)


# ── Model roster ────────────────────────────────────────────────────────────
# Current model IDs (2026-07). Providers: anthropic | openai | gemini | zai.

@dataclass
class Model:
    label: str
    provider: str
    model: str
    family: str            # for self-preference bias detection
    max_tokens: int = 2048
    thinking_level: str | None = None


CANDIDATES = [
    Model("gemini-3-flash", "gemini", "gemini-3-flash-preview", "gemini", 4096, "MEDIUM"),
    Model("gemini-3.5-flash", "gemini", "gemini-3.5-flash", "gemini", 4096),
    Model("claude-fable-5", "anthropic", "claude-fable-5", "anthropic", 4096),
    Model("claude-opus-4-8", "anthropic", "claude-opus-4-8", "anthropic", 4096),
    Model("claude-sonnet-4-6", "anthropic", "claude-sonnet-4-6", "anthropic", 4096),
    Model("gpt-5", "openai", "gpt-5", "openai", 4096),
    Model("gpt-5-pro", "openai", "gpt-5-pro", "openai", 4096),
    Model("glm-4.6", "zai", "glm-4.6", "zai", 4096),
]

# Substring hints for --list-models (chat/reasoning models worth eval'ing).
_LIST_HINTS = {
    "openai": ("gpt-5", "gpt-6", "o1", "o3", "o4", "gpt-4.1"),
    "anthropic": ("claude-",),
    "gemini": ("gemini-",),
    "zai": ("glm-",),
}


def list_provider_models(provider: str) -> list[str]:
    """Query a provider's API for the model IDs the current key can serve."""
    m = make_llm(Model("_probe", provider, "_", provider))
    if provider in ("openai", "zai"):
        ids = [x.id for x in m.client.models.list()]
    elif provider == "anthropic":
        ids = [x.id for x in m.client.models.list()]
    elif provider == "gemini":
        ids = [x.name.split("/")[-1] for x in m.client.models.list()]
    else:
        return []
    hints = _LIST_HINTS.get(provider)
    if hints:
        ids = [i for i in ids if any(h in i for h in hints)]
    return sorted(ids)


def parse_models_arg(spec: str) -> list[Model]:
    """Parse --models 'label:provider:model[:thinking],...' into Model objects."""
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        label, provider, model = parts[0], parts[1], parts[2]
        thinking = parts[3] if len(parts) > 3 else None
        out.append(Model(label, provider, model, provider, 4096, thinking))
    return out

# Panel of judges from DIFFERENT families. Each answer is scored by every judge;
# scores are averaged, but per-judge scores are retained so same-family
# favoritism is VISIBLE in the report rather than hidden.
JUDGES = [
    Model("judge:claude-opus", "anthropic", "claude-opus-4-8", "anthropic", 2048),
    Model("judge:gemini-3", "gemini", "gemini-3-flash-preview", "gemini", 2048, "HIGH"),
]


def make_llm(m: Model) -> LLM:
    env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "zai": "ZAI_API_KEY",
    }[m.provider]
    return LLM(
        provider=m.provider,
        model=m.model,
        api_key=os.environ.get(env),
        default_max_tokens=m.max_tokens,
        thinking_level=m.thinking_level,
    )


# ── Lesson context (transcript + Pass A) ────────────────────────────────────

def _cache_key(text: str, suffix: str) -> str:
    return hashlib.sha256((text.strip() + suffix).encode()).hexdigest()[:16]


def build_lesson_context(db: Database, doc_id: str) -> str:
    """Assemble the analysis context bundle a candidate answers against:
    the transcript plus the cached Pass A group/step decomposition."""
    transcript = db.get_document_transcription(doc_id)
    if not transcript:
        raise SystemExit(f"No transcript for {doc_id}")
    t = transcript.strip()

    parts = ["## Lesson transcript\n\n" + t]

    pass_a = db.get_cache(_cache_key(t, ":decompose_v3"))
    if pass_a and pass_a.get("groups"):
        step_by_num = {
            s.get("step_number"): s
            for s in pass_a.get("steps", [])
            if isinstance(s, dict)
        }
        lines = ["\n\n## Lesson structure (Pass A decomposition)"]
        for g in pass_a["groups"]:
            lines.append(f"\n### Group {g.get('group_number', '?')}: {g.get('label', '')}")
            if g.get("description"):
                lines.append(g["description"])
            for ref in g.get("steps", []):
                sn = ref if isinstance(ref, int) else (ref.get("step_number") if isinstance(ref, dict) else "?")
                instr = step_by_num.get(sn, {}).get("instruction", "")
                lines.append(f"- Step {sn}: {instr}")
        parts.append("\n".join(lines))
    else:
        logger.warning("No Pass A cache for %s — context is transcript only", doc_id)

    return "\n".join(parts)


CANDIDATE_SYSTEM = (
    "You are a knowledgeable Feldenkrais practitioner having a conversation about a "
    "specific Awareness Through Movement lesson. Answer the question using the analysis "
    "context provided. Be specific and grounded in what this particular lesson actually "
    "does. Where you name a mechanism, it should be traceable to the lesson's structure "
    "or to well-established anatomy — do not invent impressive-sounding physiology. If "
    "something is your interpretation rather than established fact, say so. Keep responses "
    "to 2-4 short paragraphs.\n\n{context}"
)


def load_seed(db: Database, doc_id: str, limit: int) -> list[dict]:
    """First `limit` dialogue turns for the doc, chronologically. These are the
    substantive lesson-walkthrough questions; the degenerate 'keep going'
    repeats sort last and are excluded by the limit. Each turn's original
    `response` is kept as the pairwise reference (the answer the user liked)."""
    with db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT query, response FROM lesson_chat_log WHERE document_id = %s "
            "ORDER BY created_at LIMIT %s",
            (doc_id, limit),
        )
        return [{"question": r["query"], "reference": r["response"]} for r in cur.fetchall()]


# ── Rubric scoring ──────────────────────────────────────────────────────────

RUBRIC_SCHEMA = {
    "type": "object",
    "description": "Rubric scores for one Feldenkrais lesson-analysis answer.",
    "properties": {
        "groundedness": {"type": "integer", "minimum": 0, "maximum": 3,
                         "description": "Claims traceable to the lesson structure or established anatomy. 0=multiple confabulated/false mechanisms; 3=every claim checks out."},
        "faithfulness": {"type": "integer", "minimum": 0, "maximum": 3,
                         "description": "Stays anchored to what THIS lesson actually does, vs generic Feldenkrais boilerplate. 0=could be about any lesson; 3=precisely this lesson."},
        "insight": {"type": "integer", "minimum": 0, "maximum": 3,
                    "description": "Surfaces a real, non-obvious somatic idea. 0=restates the question; 3=genuinely illuminating."},
        "restraint": {"type": "integer", "minimum": 0, "maximum": 3,
                      "description": "Flags uncertainty vs asserting confident mechanism; does NOT pad with elaboration. 0=confident confabulation / dressed-up filler; 3=says what it knows and marks what it's inferring."},
        "confabulation_flag": {"type": "boolean",
                               "description": "True if the answer states any physiologically dubious or unverifiable mechanism as established fact (e.g. fascial 'rehydration', meningeal tension, cranial-sacral fluid claims)."},
        "note": {"type": "string", "description": "One sentence: the single biggest strength or the single worst problem."},
    },
    "required": ["groundedness", "faithfulness", "insight", "restraint", "confabulation_flag", "note"],
    "additionalProperties": False,
}

RUBRIC_SYSTEM = (
    "You are a rigorous evaluator of Feldenkrais lesson analysis. You reward answers that "
    "are grounded, specific, and honest about the limits of what can be known, and you "
    "PENALIZE answers that manufacture confident, impressive-sounding physiological "
    "mechanism that cannot be verified. Length is not quality: a longer answer that adds "
    "unverifiable elaboration scores LOWER on restraint, not higher. Score strictly."
)


def score_rubric(judge: LLM, context: str, question: str, answer: str) -> dict:
    prompt = (
        f"{context}\n\n---\n\nQUESTION:\n{question}\n\n---\n\nANSWER TO SCORE:\n{answer}\n\n"
        "---\n\nScore this answer against the rubric."
    )
    return judge.complete_structured_json(prompt, RUBRIC_SCHEMA, system=RUBRIC_SYSTEM, max_tokens=1024)


PAIRWISE_SCHEMA = {
    "type": "object",
    "description": "Blind pairwise judgment between two answers.",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"],
                   "description": "Which answer is better on groundedness + specificity + honesty. 'tie' only if genuinely indistinguishable."},
        "reason": {"type": "string", "description": "One sentence."},
    },
    "required": ["winner", "reason"],
    "additionalProperties": False,
}


def score_pairwise(judge: LLM, context: str, question: str, a: str, b: str) -> dict:
    prompt = (
        f"{context}\n\n---\n\nQUESTION:\n{question}\n\n---\n\nANSWER A:\n{a}\n\n"
        f"---\n\nANSWER B:\n{b}\n\n---\n\n"
        "Which answer is better? Judge on groundedness, specificity to THIS lesson, and "
        "honesty about uncertainty. A longer answer that adds unverifiable mechanism is "
        "WORSE, not better. Answer A, B, or tie."
    )
    return judge.complete_structured_json(prompt, PAIRWISE_SCHEMA, system=RUBRIC_SYSTEM, max_tokens=512)


# ── Runner ──────────────────────────────────────────────────────────────────

def safe_call(fn, *a, **k):
    for attempt in range(3):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            logger.warning("  call failed (attempt %d): %s", attempt + 1, str(e)[:200])
            time.sleep(2 * (attempt + 1))
    return None


def run(doc_id: str, candidates: list[Model], judges: list[Model], n_questions: int):
    db = Database(os.environ["DATABASE_URL"])
    context = build_lesson_context(db, doc_id)
    seed = load_seed(db, doc_id, n_questions)
    logger.info("Loaded %d questions; context = %d chars; %d candidates, %d judges",
                len(seed), len(context), len(candidates), len(judges))

    cand_llms = {m.label: make_llm(m) for m in candidates}
    judge_llms = {j.label: make_llm(j) for j in judges}
    cand_family = {m.label: m.family for m in candidates}
    judge_family = {j.label: j.family for j in judges}

    rows = []  # per (question, candidate)
    for qi, item in enumerate(seed):
        q, ref = item["question"], item["reference"]
        logger.info("Q%d/%d: %s", qi + 1, len(seed), q[:70])
        sysprompt = CANDIDATE_SYSTEM.format(context=context)

        answers = {}
        for m in candidates:
            ans = safe_call(cand_llms[m.label].complete, q, system=sysprompt, max_tokens=m.max_tokens)
            answers[m.label] = ans
            logger.info("  %-18s -> %s", m.label, f"{len(ans)} chars" if ans else "FAILED")

        for m in candidates:
            ans = answers[m.label]
            if not ans:
                rows.append({"q": qi, "question": q, "model": m.label, "family": m.family,
                             "answer": None, "chars": 0, "rubric": {}, "pairwise": {}})
                continue

            # Rubric: every judge scores, averaged; per-judge kept for bias check.
            per_judge = {}
            for j in judges:
                sc = safe_call(score_rubric, judge_llms[j.label], context, q, ans)
                if sc:
                    per_judge[j.label] = sc

            # Pairwise vs the reference answer (the one the user liked), blind + order-randomized.
            per_judge_pw = {}
            flip = (qi + hash(m.label)) % 2 == 0  # deterministic per-cell order swap
            A, B = (ans, ref) if not flip else (ref, ans)
            for j in judges:
                pw = safe_call(score_pairwise, judge_llms[j.label], context, q, A, B)
                if pw:
                    # normalize: did the CANDIDATE win?
                    w = pw["winner"]
                    cand_is = ("A" if not flip else "B")
                    outcome = "win" if w == cand_is else ("tie" if w == "tie" else "loss")
                    per_judge_pw[j.label] = {"outcome": outcome, "reason": pw.get("reason", "")}

            rows.append({
                "q": qi, "question": q, "model": m.label, "family": m.family,
                "answer": ans, "chars": len(ans),
                "rubric": per_judge, "pairwise": per_judge_pw,
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time()))
    raw_path = OUT_DIR / f"eval_{stamp}.json"
    raw_path.write_text(json.dumps({"doc": doc_id, "rows": rows}, indent=2))
    logger.info("Wrote raw results -> %s", raw_path)

    report = build_report(rows, candidates, judges, cand_family, judge_family, doc_id)
    rep_path = OUT_DIR / f"eval_{stamp}.md"
    rep_path.write_text(report)
    logger.info("Wrote report -> %s", rep_path)
    print("\n" + report)


CRITERIA = ["groundedness", "faithfulness", "insight", "restraint"]


def build_report(rows, candidates, judges, cand_family, judge_family, doc_id) -> str:
    labels = [m.label for m in candidates]
    jlabels = [j.label for j in judges]

    # Aggregate rubric (mean across judges + questions), pairwise win rate, confab rate, length.
    agg = {}
    for lab in labels:
        cells = [r for r in rows if r["model"] == lab and r["answer"] is not None]
        crit_means = {}
        for c in CRITERIA:
            vals = [sc[c] for r in cells for sc in r["rubric"].values() if c in sc]
            crit_means[c] = sum(vals) / len(vals) if vals else float("nan")
        confab = [sc["confabulation_flag"] for r in cells for sc in r["rubric"].values() if "confabulation_flag" in sc]
        confab_rate = (sum(confab) / len(confab)) if confab else float("nan")
        pw = [p["outcome"] for r in cells for p in r["pairwise"].values()]
        wins = pw.count("win"); losses = pw.count("loss"); ties = pw.count("tie")
        net = (wins - losses) / len(pw) if pw else float("nan")
        chars = [r["chars"] for r in cells]
        composite = sum(v for v in crit_means.values() if v == v) / len(CRITERIA)
        agg[lab] = {
            "n": len(cells), "crit": crit_means, "composite": composite,
            "confab_rate": confab_rate, "pw_net": net,
            "pw": f"{wins}W/{losses}L/{ties}T",
            "avg_chars": sum(chars) / len(chars) if chars else 0,
        }

    ranked = sorted(labels, key=lambda l: agg[l]["composite"], reverse=True)

    L = [f"# Model eval — {doc_id}",
         f"\nCandidates: {len(candidates)} · Judges: {', '.join(jlabels)} · "
         f"Questions: {max((r['q'] for r in rows), default=-1) + 1}",
         "\n## Ranking (composite rubric, 0-3; pairwise vs the reference answer the user kept)\n",
         "| # | model | composite | ground | faith | insight | restraint | confab | pairwise net | avg chars |",
         "|---|-------|-----------|--------|-------|---------|-----------|--------|--------------|-----------|"]
    for i, lab in enumerate(ranked, 1):
        a = agg[lab]
        c = a["crit"]
        L.append(
            f"| {i} | {lab} | {a['composite']:.2f} | {c['groundedness']:.2f} | "
            f"{c['faithfulness']:.2f} | {c['insight']:.2f} | {c['restraint']:.2f} | "
            f"{a['confab_rate']:.0%} | {a['pw_net']:+.2f} ({a['pw']}) | {a['avg_chars']:.0f} |"
        )

    # Per-judge bias check: mean composite each judge gives to each family.
    L.append("\n## Per-judge bias check (mean composite each judge awards, by candidate family)\n")
    fams = sorted(set(cand_family.values()))
    L.append("| judge (family) | " + " | ".join(fams) + " |")
    L.append("|" + "---|" * (len(fams) + 1))
    for j in judges:
        cells = []
        for fam in fams:
            vals = []
            for r in rows:
                if r["answer"] is None or cand_family[r["model"]] != fam:
                    continue
                sc = r["rubric"].get(j.label)
                if sc:
                    vals.append(sum(sc[c] for c in CRITERIA) / len(CRITERIA))
            cells.append(f"{sum(vals)/len(vals):.2f}" if vals else "—")
        star = " ⚠same-fam" if judge_family[j.label] in fams else ""
        L.append(f"| {j.label} ({judge_family[j.label]}){star} | " + " | ".join(cells) + " |")
    L.append("\n*A judge scoring its own family notably higher than the other judge does is "
             "self-preference bias — discount accordingly.*")

    # Failure notes: confabulation examples.
    L.append("\n## Confabulation flags (answers a judge marked as stating dubious mechanism as fact)\n")
    any_flag = False
    for r in rows:
        if r["answer"] is None:
            continue
        flagged = [j for j, sc in r["rubric"].items() if sc.get("confabulation_flag")]
        if flagged:
            any_flag = True
            note = next((sc["note"] for sc in r["rubric"].values() if sc.get("confabulation_flag")), "")
            L.append(f"- **{r['model']}** on Q{r['q']+1}: {note} _(flagged by {', '.join(flagged)})_")
    if not any_flag:
        L.append("- None flagged.")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=DEFAULT_DOC)
    ap.add_argument("--questions", type=int, default=16)
    ap.add_argument("--smoke", action="store_true", help="2 questions, 2 candidates, 1 judge")
    ap.add_argument("--list-models", metavar="PROVIDER",
                    help="List model IDs the key can serve (openai|anthropic|gemini|zai), then exit")
    ap.add_argument("--models", metavar="SPEC",
                    help="Override candidates: 'label:provider:model[:thinking],...'")
    args = ap.parse_args()

    if args.list_models:
        for i in list_provider_models(args.list_models):
            print(i)
        return

    if args.smoke:
        cands = [CANDIDATES[0], CANDIDATES[2]]  # gemini-3-flash, claude-fable-5
        judges = [JUDGES[0]]
        run(args.doc, cands, judges, 2)
    else:
        cands = parse_models_arg(args.models) if args.models else CANDIDATES
        run(args.doc, cands, JUDGES, args.questions)


if __name__ == "__main__":
    main()
