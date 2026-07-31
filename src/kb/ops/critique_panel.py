"""Critique panel — get structured feedback on a manuscript from several models.

Sends a markdown document to a panel of models (Gemini + GLM via API, GPT via
the Codex CLI on the user's subscription) and collects structured critique:
organization, weakest sections, anything that reads as AI-generated, factual
overreach, and prioritized fixes. Writes a merged markdown report.

Usage:
    cd /Users/michaelhollander/github/feldenkrais
    set -a; source .env; set +a
    PYTHONPATH=src/kb .venv/bin/python -m ops.critique_panel \
        private/products/book/demystifying/flexors_extensors/manuscript.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(REPO / ".env")

from database import Database  # noqa: F401  (unused, kept for env parity)
from llm import LLM

CODEX_BIN = os.environ.get("CODEX_BIN", "/opt/homebrew/bin/codex")

CRITIQUE_SCHEMA = {
    "type": "object",
    "description": "Structured critique of a booklet manuscript for paid publication.",
    "properties": {
        "overall": {"type": "string", "description": "1-2 sentences: is this publishable, and the single biggest lever."},
        "organization": {"type": "string", "description": "Is the structure clear? Concrete issues or endorsements."},
        "weakest_sections": {"type": "array", "items": {"type": "string"},
                             "description": "Named chapters/parts that are weakest, each with why."},
        "ai_tells": {"type": "array", "items": {"type": "string"},
                     "description": "Passages that read as generic/AI-generated/padded — quote a few words each."},
        "factual_concerns": {"type": "array", "items": {"type": "string"},
                             "description": "Anatomy/mechanism claims that seem wrong, overstated, or unverifiable."},
        "top_fixes": {"type": "array", "items": {"type": "string"},
                      "description": "3-5 concrete, prioritized edits, most important first."},
    },
    "required": ["overall", "organization", "weakest_sections", "ai_tells", "factual_concerns", "top_fixes"],
    "additionalProperties": False,
}

CRITIQUE_SYSTEM = (
    "You are a demanding editor reviewing a booklet that will be sold to the public — "
    "a companion/close-reading of a Feldenkrais 'Awareness Through Movement' lesson, part of "
    "a series called 'Demystifying the Feldenkrais Method.' The reader is assumed to own the "
    "book and to have done the lesson. Judge it as a paid product: is the organization clear, "
    "which parts are weakest, what reads as generic AI filler, and are any anatomical or "
    "mechanistic claims wrong or overstated? Be specific and unsparing; quote the text when you "
    "flag something. Reward grounded, honest writing; penalize confident but unverifiable "
    "physiology. Do not pad your critique."
)


def critique_api(label: str, provider: str, model: str, doc: str, thinking=None) -> dict | None:
    env = {"gemini": "GOOGLE_API_KEY", "zai": "ZAI_API_KEY",
           "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider]
    llm = LLM(provider=provider, model=model, api_key=os.environ.get(env),
              default_max_tokens=4096, thinking_level=thinking)
    prompt = f"Here is the full manuscript to critique:\n\n{doc}\n\n---\n\nReturn your structured critique."
    for attempt in range(3):
        try:
            return llm.complete_structured_json(prompt, CRITIQUE_SCHEMA, system=CRITIQUE_SYSTEM, max_tokens=4096)
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] attempt {attempt+1} failed: {str(e)[:150]}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None


def critique_codex(label: str, model: str, doc: str) -> dict | None:
    """Run the critique through Codex (GPT on the user's subscription), forcing
    JSON with --output-schema."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as sf:
        json.dump(CRITIQUE_SCHEMA, sf)
        schema_path = sf.name
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as of:
        out_path = of.name
    prompt = (CRITIQUE_SYSTEM + "\n\n---\n\nManuscript:\n\n" + doc +
              "\n\n---\n\nReturn ONLY the structured critique as JSON.")
    cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
           "--output-schema", schema_path, "-o", out_path, "-m", model, prompt]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd="/tmp")
        raw = Path(out_path).read_text().strip()
        # -o writes the final message; with --output-schema it should be the JSON object.
        start = raw.find("{")
        return json.loads(raw[start:]) if start >= 0 else None
    except Exception as e:  # noqa: BLE001
        print(f"  [{label}] codex failed: {str(e)[:150]}", file=sys.stderr)
        return None
    finally:
        Path(schema_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


def section(title: str, items) -> list[str]:
    out = [f"**{title}:**"]
    if isinstance(items, str):
        out.append(items)
    elif items:
        out += [f"- {x}" for x in items]
    else:
        out.append("- (none)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manuscript")
    ap.add_argument("--no-codex", action="store_true", help="Skip the Codex/GPT critic")
    args = ap.parse_args()

    doc = Path(args.manuscript).read_text()
    print(f"Manuscript: {len(doc)} chars", file=sys.stderr)

    results = {}
    api_critics = [
        ("gemini-3-flash", "gemini", "gemini-3-flash-preview", "HIGH"),
        ("glm-4.6", "zai", "glm-4.6", None),
    ]
    for label, prov, model, think in api_critics:
        print(f"Running {label}...", file=sys.stderr)
        results[label] = critique_api(label, prov, model, doc, think)

    if not args.no_codex:
        print("Running codex/gpt-5.6-sol...", file=sys.stderr)
        results["gpt-5.6-sol"] = critique_codex("gpt-5.6-sol", "gpt-5.6-sol", doc)

    out_dir = REPO / "private" / "products" / "book" / "demystifying" / "flexors_extensors"
    stamp = str(int(time.time()))
    raw = out_dir / f"critique_{stamp}.json"
    raw.write_text(json.dumps(results, indent=2))

    L = [f"# Critique panel — {Path(args.manuscript).name}", ""]
    for label, r in results.items():
        L.append(f"## {label}")
        if not r:
            L.append("_(no response)_\n"); continue
        L += section("Overall", r.get("overall", ""))
        L += section("Organization", r.get("organization", ""))
        L += section("Weakest sections", r.get("weakest_sections", []))
        L += section("AI tells", r.get("ai_tells", []))
        L += section("Factual concerns", r.get("factual_concerns", []))
        L += section("Top fixes", r.get("top_fixes", []))
        L.append("")
    report = out_dir / f"critique_{stamp}.md"
    report.write_text("\n".join(L))
    print(f"Wrote {report}", file=sys.stderr)
    print("\n".join(L))


if __name__ == "__main__":
    main()
