"""Test LLM spatial reasoning against position equivalence dataset.

Usage:
    .venv/bin/python scripts/test_position_equivalences.py [--model gemini-2.0-flash]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from google import genai


def load_pairs():
    """Parse the equivalence dataset from markdown table."""
    path = Path(__file__).parent.parent / "data" / "position_equivalences.md"
    pairs = []
    in_table = False
    for line in path.read_text().splitlines():
        if line.startswith("| id"):
            in_table = True
            continue
        if line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                pairs.append({
                    "id": int(cols[0]),
                    "a": cols[1],
                    "b": cols[2],
                    "expected": int(cols[3]),
                    "category": cols[4],
                })
    return pairs


def test_batch(pairs, model="gemini-2.0-flash", batch_size=10):
    """Send pairs to the model in batches and collect scores."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    results = []

    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]

        pair_text = "\n".join(
            f'{p["id"]}) "{p["a"]}" vs "{p["b"]}"'
            for p in batch
        )

        prompt = f"""Rate the similarity of these body position descriptions on a scale of 0-10.
10 = exactly the same physical configuration. 0 = completely different.

Think about what the body actually looks like in each case — not the words used.
If two descriptions would result in the same physical arrangement of the body, score high.
If the words sound similar but the body is in a different position, score low.

Pairs:
{pair_text}

Return JSON array: [{{"id": N, "score": N, "reason": "..."}}]"""

        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        batch_results = json.loads(resp.text)

        # Index by id
        score_map = {r["id"]: r for r in batch_results}
        for p in batch:
            r = score_map.get(p["id"], {})
            results.append({
                **p,
                "actual": r.get("score", "?"),
                "reason": r.get("reason", ""),
            })

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-2.0-flash")
    args = parser.parse_args()

    pairs = load_pairs()
    print(f"Testing {len(pairs)} pairs against {args.model}\n")

    results = test_batch(pairs, args.model)

    # Print results
    total_error = 0
    bad = []
    print(f"{'ID':>3}  {'Exp':>3}  {'Act':>3}  {'Err':>4}  {'Category':<30}  Description")
    print(f"{'—'*3}  {'—'*3}  {'—'*3}  {'—'*4}  {'—'*30}  {'—'*40}")

    for r in results:
        exp = r["expected"]
        act = r["actual"]
        if act == "?":
            err_str = "  ? "
            err = 0
        else:
            err = abs(exp - act)
            total_error += err
            err_str = f"{err:>4}"

        flag = " !!!" if err >= 4 else ""
        print(f"{r['id']:>3}  {exp:>3}  {act:>3}  {err_str}  {r['category']:<30}  \"{r['a'][:30]}\" vs \"{r['b'][:30]}\"{flag}")

    avg_err = total_error / len(results)
    bad_count = sum(1 for r in results if isinstance(r["actual"], int) and abs(r["expected"] - r["actual"]) >= 4)

    print(f"\nAvg error: {avg_err:.2f}")
    print(f"Bad (error >= 4): {bad_count}/{len(results)}")

    # Print the worst misses
    if bad_count:
        print(f"\nWorst misses:")
        for r in sorted(results, key=lambda r: abs(r["expected"] - r["actual"]) if isinstance(r["actual"], int) else 0, reverse=True):
            if isinstance(r["actual"], int) and abs(r["expected"] - r["actual"]) >= 4:
                print(f"  #{r['id']}: expected {r['expected']}, got {r['actual']} — {r['reason']}")


if __name__ == "__main__":
    main()
