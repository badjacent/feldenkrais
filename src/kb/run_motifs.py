#!/usr/bin/env python3
"""Distinctive-motifs extraction runner (docs/DISTINCTIVE_MOTIFS.md).

Owner-gated: no scheduling, no unattended cohorts. Default is a dry listing;
--apply runs extraction (cached, so reruns are free).

Usage (from src/kb, .env loaded):
    .venv/bin/python run_motifs.py list                       # eligible docs by source
    .venv/bin/python run_motifs.py run --doc <id> [--apply]
    .venv/bin/python run_motifs.py run --limit 10 --source FF_JEFF_HALLER --apply
    .venv/bin/python run_motifs.py show --doc <id>            # cached result
    .venv/bin/python run_motifs.py fixtures                   # check ground-truth expectations
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from database import Database
import cache_helpers
import llm_config
import analysis
import yaml

DATABASE_URL = os.environ.get("DATABASE_URL")
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_db = Database(DATABASE_URL) if DATABASE_URL else None
if _db:
    _db.initialize()
cache_helpers.init(_db)
llm_config.init(_db)
analysis.init(_db, DATA_DIR, llm_config.get_llm)

from analysis import run_motifs, run_raw_motifs, run_axes, load_motif_registry  # noqa: E402
from cache_helpers import get_cache_key, load_cached_analysis  # noqa: E402
from analysis.motifs import PROMPT_VERSION, RAW_PROMPT_VERSION  # noqa: E402
from analysis.axes import AXES_PROMPT_VERSION, AXIS_VALUES  # noqa: E402


def _cache_key_for(text: str, registry_version: str) -> str:
    return get_cache_key(text.strip() + f":distinctive_motifs:{registry_version}:{PROMPT_VERSION}")


def _raw_cache_key_for(text: str) -> str:
    return get_cache_key(text.strip() + f":raw_motifs:{RAW_PROMPT_VERSION}")


def _eligible(limit: int, source: str | None) -> list[dict]:
    """Transcribed audio/video docs; ATM-only filtering is advisory via metadata."""
    sql = """
        SELECT d.id, d.title, ds.name AS source_name,
               COALESCE(d.metadata->>'lesson_type',
                        d.metadata->'lesson_metadata'->>'lesson_type') AS lesson_type
        FROM document d
        JOIN document_source ds ON ds.id = d.source_id
        JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
        WHERE d.type_id IN (2, 3)
    """
    params: list = []
    if source:
        sql += " AND ds.name = %s"
        params.append(source)
    sql += " ORDER BY d.created_at DESC LIMIT %s"
    params.append(limit)
    with _db.get_cursor(commit=False) as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    # Advisory ATM gate: skip docs explicitly typed as non-ATM.
    return [r for r in rows
            if (r.get("lesson_type") or "ATM").lower() in ("atm", "unknown", "")]


def cmd_list(args):
    with _db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT ds.name, count(*) FROM document d
            JOIN document_source ds ON ds.id = d.source_id
            JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
            WHERE d.type_id IN (2,3)
            GROUP BY ds.name ORDER BY 2 DESC
        """)
        for name, n in cur.fetchall():
            print(f"{n:6d}  {name}")


def _run_one(doc_id: str, apply: bool, provider: str | None) -> dict:
    text = _db.get_document_transcription(doc_id)
    if not text or not text.strip():
        return {"document_id": doc_id, "skipped": "no_transcription"}
    registry = load_motif_registry()
    key = _cache_key_for(text, registry.get("version", "motifs-v0"))
    cached = load_cached_analysis(key)
    if cached:
        return {"document_id": doc_id, "cached": True,
                "assignments": cached.get("assignments", []),
                "candidates": len(cached.get("candidate_motifs", []))}
    if not apply:
        return {"document_id": doc_id, "would_extract": True}
    result = run_motifs(text, provider_key=provider)
    return {"document_id": doc_id, "cached": False,
            "assignments": result.get("assignments", []),
            "candidates": len(result.get("candidate_motifs", []))}


def cmd_run(args):
    docs = _doc_list(args)
    print(f"{len(docs)} document(s)")
    for d in docs:
        out = _run_one(str(d["id"]), args.apply, args.provider)
        title = (d.get("title") or "")[:55]
        marks = ", ".join(f"{a['motif_id']}/{a['mode']}/{a['role']}"
                          for a in out.get("assignments", [])) or "-"
        flag = ("cached" if out.get("cached")
                else "dry" if out.get("would_extract")
                else out.get("skipped", "extracted"))
        print(f"  [{flag:9s}] {marks:60s} {title}")


def _doc_list(args) -> list[dict]:
    if getattr(args, "doc", None):
        return [{"id": args.doc, "title": ""}]
    if getattr(args, "docs_file", None):
        out = []
        for line in Path(args.docs_file).read_text().splitlines():
            if line.strip():
                did, _, title = line.partition("\t")
                out.append({"id": did.strip(), "title": title.strip()})
        return out
    return _eligible(args.limit, args.source)


def cmd_raw(args):
    docs = _doc_list(args)
    print(f"{len(docs)} document(s)")
    for d in docs:
        text = _db.get_document_transcription(str(d["id"]))
        title = (d.get("title") or "")[:55]
        if not text or not text.strip():
            print(f"  [no_transcription] {title}")
            continue
        cached = load_cached_analysis(_raw_cache_key_for(text))
        if cached:
            out, flag = cached, "cached"
        elif not args.apply:
            print(f"  [dry      ] {title}")
            continue
        else:
            out, flag = run_raw_motifs(text, provider_key=args.provider), "extracted"
        marks = ", ".join(f"{m['label']}/{m['mode']}/{m['role']}"
                          for m in out.get("raw_motifs", [])) or "-"
        print(f"  [{flag:9s}] {marks:70s} {title}")


def _stem(tok: str) -> str:
    if len(tok) > 5 and tok.endswith("ing"):
        tok = tok[:-3]
    elif len(tok) > 3 and tok.endswith("s"):
        tok = tok[:-1]
    return tok


def _axes_cache_key_for(text: str) -> str:
    return get_cache_key(text.strip() + f":movement_axes:{AXES_PROMPT_VERSION}")


def cmd_axes(args):
    docs = _doc_list(args)
    print(f"{len(docs)} document(s)")
    for d in docs:
        text = _db.get_document_transcription(str(d["id"]))
        title = (d.get("title") or "")[:50]
        if not text or not text.strip():
            print(f"  [no_transcription] {title}")
            continue
        cached = load_cached_analysis(_axes_cache_key_for(text))
        if cached:
            out, flag = cached, "cached"
        elif not args.apply:
            print(f"  [dry      ] {title}")
            continue
        else:
            out, flag = run_axes(text, provider_key=args.provider), "extracted"
        ax = out.get("axes", {})
        vals = " ".join(f"{a[:4]}={ax.get(a, {}).get('value', '?')}" for a in AXIS_VALUES)
        print(f"  [{flag:9s}] {vals}  {title}")


def cmd_axes_report(args):
    """List lessons grouped by their value on one axis."""
    if args.axis not in AXIS_VALUES:
        sys.exit(f"unknown axis {args.axis!r}; one of {list(AXIS_VALUES)}")
    docs = _doc_list(args)
    from collections import defaultdict
    by_val = defaultdict(list)
    missing = 0
    for d in docs:
        text = _db.get_document_transcription(str(d["id"]))
        cached = load_cached_analysis(_axes_cache_key_for(text)) if text else None
        if not cached:
            missing += 1
            continue
        a = cached.get("axes", {}).get(args.axis)
        title = (d.get("title") or str(d["id"]))[:65]
        if a:
            by_val[a["value"]].append((title, a.get("evidence", [])))
    for val in reversed(AXIS_VALUES[args.axis]):
        rows = by_val.get(val, [])
        print(f"\n== {args.axis} = {val} ({len(rows)}) ==")
        for title, ev in sorted(rows):
            print(f"  {title}")
            if args.evidence and ev:
                print(f"      {ev[0].get('reason','')[:110]}")
    if missing:
        print(f"\n({missing} doc(s) not yet extracted)")


def _norm_label(label: str) -> str:
    toks = "".join(c if c.isalnum() else " " for c in label.lower()).split()
    return " ".join(_stem(t) for t in toks)


def _registry_lexicon(registry: dict) -> dict:
    """Map normalized label/alias/id → motif id, for raw-label matching."""
    lex = {}
    for m in registry["motifs"]:
        target = m.get("merged_into") or m["id"]
        for name in [m["id"], m.get("label", "")] + list(m.get("aliases", [])):
            if name:
                lex[_norm_label(name)] = target
    return lex


def _match_raw_label(label: str, lex: dict) -> str | None:
    """Exact normalized match, else 2+-token subset match against the lexicon."""
    norm = _norm_label(label)
    if norm in lex:
        return lex[norm]
    toks = set(norm.split())
    for key, mid in lex.items():
        ktoks = set(key.split())
        small, big = (toks, ktoks) if len(toks) <= len(ktoks) else (ktoks, toks)
        if len(small) >= 2 and small <= big:
            return mid
    return None


def cmd_diff(args):
    """Per-lesson raw-vs-matched diff + cohort frequency table of misses."""
    docs = _doc_list(args)
    registry = load_motif_registry()
    ver = registry.get("version")
    lex = _registry_lexicon(registry)
    from collections import Counter, defaultdict
    miss_freq = Counter()
    miss_docs = defaultdict(list)
    recall_gaps = []
    n_raw = n_matched_docs = 0
    for d in docs:
        text = _db.get_document_transcription(str(d["id"]))
        if not text:
            continue
        raw = load_cached_analysis(_raw_cache_key_for(text))
        matched = load_cached_analysis(_cache_key_for(text, ver))
        if not raw or not matched:
            print(f"  [missing {'raw' if not raw else 'matched'}] {d.get('title','')[:60]}")
            continue
        n_raw += 1
        assigned_ids = {a["motif_id"] for a in matched.get("assignments", [])}
        if assigned_ids:
            n_matched_docs += 1
        title = (d.get("title") or str(d["id"]))[:60]
        for m in raw.get("raw_motifs", []):
            mid = _match_raw_label(m["label"], lex)
            if mid is None:
                miss_freq[_norm_label(m["label"])] += 1
                miss_docs[_norm_label(m["label"])].append(
                    (title, m["mode"], m["role"]))
            elif mid not in assigned_ids:
                recall_gaps.append((title, m["label"], mid, m["mode"], m["role"]))
    print(f"\n{n_raw} docs with both lanes; {n_matched_docs} had matched assignments")
    if recall_gaps:
        print("\n== RAW found a registry motif the matched pass did NOT assign ==")
        for title, label, mid, mode, role in recall_gaps:
            print(f"  {mid:22s} (raw: {label}, {mode}/{role})  {title}")
    print(f"\n== UNMATCHED RAW MOTIFS (registry gaps), by frequency ==")
    for label, n in miss_freq.most_common():
        print(f"  {n:3d}  {label}")
        for title, mode, role in miss_docs[label][: args.examples]:
            print(f"         {mode}/{role}  {title}")


def cmd_sync_step(args):
    """Register/refresh the motifs, raw_motifs, and axes steps in the
    analysis-pipeline DAG (analysis_step table).

    New steps start with NO group gates — nothing runs until the owner
    permits them per group on /analysis. When the motif registry version
    changes, re-running this bumps the step version so completed docs become
    re-runnable (still gate-controlled).
    """
    registry = load_motif_registry()
    ver = registry.get("version")
    deps = ["somatic", "language", "condense"]
    rows = [
        ("motifs", f":distinctive_motifs:{ver}:{PROMPT_VERSION}",
         f"Distinctive motif assignment (registry {ver})"),
        ("raw_motifs", f":raw_motifs:{RAW_PROMPT_VERSION}",
         "Open-vocabulary motif observation"),
        ("axes", f":movement_axes:{AXES_PROMPT_VERSION}",
         "Ordinal movement axes"),
    ]
    with _db.get_cursor() as cur:
        for name, suffix, desc in rows:
            cur.execute("SELECT cache_suffix, current_version FROM analysis_step WHERE name = %s", (name,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """INSERT INTO analysis_step (name, current_version, cache_suffix, depends_on, description)
                       VALUES (%s, 1, %s, %s, %s)""",
                    (name, suffix, deps, desc))
                print(f"created  {name} v1  {suffix}  (no gates yet — permit per group on /analysis)")
            elif row["cache_suffix"] != suffix:
                newv = (row["current_version"] or 1) + 1
                cur.execute(
                    """UPDATE analysis_step SET cache_suffix = %s, current_version = %s,
                       description = %s, updated_at = NOW() WHERE name = %s""",
                    (suffix, newv, desc, name))
                print(f"updated  {name} -> v{newv}  {suffix}  (completed docs now re-runnable)")
            else:
                print(f"ok       {name} v{row['current_version']}  {suffix}")


def cmd_show(args):
    text = _db.get_document_transcription(args.doc)
    if not text:
        sys.exit("no transcription")
    registry = load_motif_registry()
    cached = load_cached_analysis(_cache_key_for(text, registry.get("version")))
    print(json.dumps(cached, indent=2) if cached else "not extracted")


def cmd_fixtures(args):
    """Compare cached extractions against ground-truth expectations."""
    fx_path = analysis._resolve_data_path("motifs_fixtures.yaml")
    fixtures = yaml.safe_load(Path(fx_path).read_text())["fixtures"]
    registry = load_motif_registry()
    ver = registry.get("version")
    passed = failed = missing = 0
    for f in fixtures:
        text = _db.get_document_transcription(f["doc"])
        cached = load_cached_analysis(_cache_key_for(text, ver)) if text else None
        if not cached:
            print(f"MISSING  {f['doc'][:8]}  {f.get('note','')}")
            missing += 1
            continue
        got = {a["motif_id"]: a for a in cached.get("assignments", [])}
        ok = True
        for exp in f.get("expect", []):
            a = got.get(exp["motif_id"])
            if not a:
                ok = False; print(f"FAIL     {f['doc'][:8]}  expected {exp['motif_id']} absent")
            elif exp.get("mode") and a["mode"] != exp["mode"]:
                ok = False; print(f"FAIL     {f['doc'][:8]}  {exp['motif_id']} mode {a['mode']} != {exp['mode']}")
        for absent in f.get("expect_absent", []):
            if absent in got:
                ok = False; print(f"FAIL     {f['doc'][:8]}  {absent} should be absent")
        if ok:
            passed += 1
            print(f"PASS     {f['doc'][:8]}  {f.get('note','')}")
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {missing} not yet extracted")
    sys.exit(1 if failed else 0)


def main():
    ap = argparse.ArgumentParser(description="Distinctive motifs runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    run = sub.add_parser("run")
    run.add_argument("--doc")
    run.add_argument("--docs-file", help="TSV file: doc_id<TAB>title per line")
    run.add_argument("--limit", type=int, default=5)
    run.add_argument("--source")
    run.add_argument("--provider", default=None)
    run.add_argument("--apply", action="store_true",
                     help="Actually call the LLM (default: dry listing)")
    run.set_defaults(fn=cmd_run)
    raw = sub.add_parser("raw", help="Registry-blind open-vocabulary extraction")
    raw.add_argument("--doc")
    raw.add_argument("--docs-file", help="TSV file: doc_id<TAB>title per line")
    raw.add_argument("--limit", type=int, default=5)
    raw.add_argument("--source")
    raw.add_argument("--provider", default=None)
    raw.add_argument("--apply", action="store_true",
                     help="Actually call the LLM (default: dry listing)")
    raw.set_defaults(fn=cmd_raw)
    diff = sub.add_parser("diff", help="Raw vs matched: recall gaps + registry misses")
    diff.add_argument("--doc")
    diff.add_argument("--docs-file")
    diff.add_argument("--limit", type=int, default=100)
    diff.add_argument("--source")
    diff.add_argument("--examples", type=int, default=3,
                      help="Example lessons shown per unmatched raw label")
    diff.set_defaults(fn=cmd_diff)
    axes = sub.add_parser("axes", help="Ordinal movement-axes extraction")
    axes.add_argument("--doc")
    axes.add_argument("--docs-file")
    axes.add_argument("--limit", type=int, default=5)
    axes.add_argument("--source")
    axes.add_argument("--provider", default=None)
    axes.add_argument("--apply", action="store_true",
                      help="Actually call the LLM (default: dry listing)")
    axes.set_defaults(fn=cmd_axes)
    axrep = sub.add_parser("axes-report", help="Lessons grouped by one axis value")
    axrep.add_argument("--axis", required=True)
    axrep.add_argument("--doc")
    axrep.add_argument("--docs-file")
    axrep.add_argument("--limit", type=int, default=100)
    axrep.add_argument("--source")
    axrep.add_argument("--evidence", action="store_true",
                       help="Show first evidence reason per lesson")
    axrep.set_defaults(fn=cmd_axes_report)
    show = sub.add_parser("show")
    show.add_argument("--doc", required=True)
    show.set_defaults(fn=cmd_show)
    sub.add_parser("sync-step",
                   help="Register/refresh motifs+axes steps in the analysis DAG"
                   ).set_defaults(fn=cmd_sync_step)
    sub.add_parser("fixtures").set_defaults(fn=cmd_fixtures)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
