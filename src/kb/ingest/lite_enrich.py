"""Independent Gemini lite-enrichment worker for public transcribed lessons.

This is intentionally outside the analysis DAG. It writes only content-hash
cache artifacts and never touches analysis steps, gates, or worker leases.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

from database import Database  # noqa: E402
import analysis  # noqa: E402
import cache_helpers  # noqa: E402
import llm_config  # noqa: E402
from atlas_projection import PUBLIC_SOURCES  # noqa: E402
from analysis.axes import AXES_PROMPT_VERSION, LITE_PROMPT_VERSION as AXES_LITE_VERSION  # noqa: E402
from analysis.lesson_card import PROMPT_VERSION as CARD_VERSION  # noqa: E402
from analysis.motifs import (  # noqa: E402
    LITE_PROMPT_VERSION as MOTIFS_LITE_VERSION,
    PROMPT_VERSION as MOTIFS_PROMPT_VERSION,
)

PROVIDER = "gemini"


def _key(text: str, suffix: str) -> str:
    return cache_helpers.get_cache_key(text.strip() + suffix)


def artifact_keys(text: str, registry_version: str) -> dict[str, str]:
    return {
        "full_motifs": _key(text, f":distinctive_motifs:{registry_version}:{MOTIFS_PROMPT_VERSION}"),
        "full_axes": _key(text, f":movement_axes:{AXES_PROMPT_VERSION}"),
        "lite_motifs": _key(text, f":distinctive_motifs_lite:{registry_version}:{MOTIFS_LITE_VERSION}"),
        "lite_axes": _key(text, f":movement_axes_lite:{AXES_LITE_VERSION}"),
        "card": _key(text, f":lesson_card:{CARD_VERSION}"),
    }


def _documents(db: Database, limit: int, source: str | None, doc: str | None) -> list[dict]:
    sql = """
        SELECT d.id, d.title, ds.name AS source_name, d.origin,
               d.source_url, d.metadata, d.presenter, d.category_name,
               d.lesson_date, d.tags, a.data->>'text' AS text
        FROM document d
        JOIN document_source ds ON ds.id = d.source_id
        JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
        WHERE (ds.name = ANY(%s) OR d.origin = 'feldenkrais_atlas')
          AND d.type_id IN (2, 3)
          AND COALESCE(a.data->>'text', '') <> ''
    """
    params: list = [PUBLIC_SOURCES]
    if source:
        sql += " AND ds.name = %s"
        params.append(source)
    if doc:
        sql += " AND d.id = %s"
        params.append(doc)
    sql += " ORDER BY (d.origin = 'feldenkrais_atlas') DESC NULLS LAST, d.created_at, d.id LIMIT %s"
    params.append(limit)
    with db.get_cursor(commit=False) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def _hosting_page_context(document: dict) -> dict:
    """Return bounded, public source metadata as secondary Gemini context."""
    metadata = document.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {"raw": metadata[:2000]}
    if not isinstance(metadata, dict):
        metadata = {"raw": str(metadata)[:2000]}
    return {
        "page_url": document.get("source_url"),
        "page_title": document.get("title"),
        "source_name": document.get("source_name"),
        "presenter": document.get("presenter"),
        "category": document.get("category_name"),
        "publication_date": document.get("lesson_date"),
        "tags": document.get("tags"),
        "captured_metadata": metadata,
    }


def _status(db: Database, text: str, registry_version: str) -> tuple[str, dict]:
    keys = artifact_keys(text, registry_version)
    present = {name: bool(db.get_cache(key)) for name, key in keys.items()}
    if present["lite_motifs"] and present["lite_axes"] and present["card"]:
        return "lite", present
    if any(present.values()):
        return "partial", present
    return "missing", present


def _coverage(db: Database, documents: list[dict], registry_version: str) -> dict:
    keyed = [(document, artifact_keys(document["text"], registry_version)) for document in documents]
    all_keys = [key for _, keys in keyed for key in keys.values()]
    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT key FROM cache WHERE key = ANY(%s)", (all_keys,))
        existing = {row["key"] for row in cur.fetchall()}
    counts = {"eligible": len(documents), "complete": 0, "partial": 0, "missing": 0}
    artifacts = {"axes": 0, "motifs": 0, "cards": 0}
    for _, keys in keyed:
        present = {name: key in existing for name, key in keys.items()}
        artifacts["axes"] += int(present["lite_axes"])
        artifacts["motifs"] += int(present["lite_motifs"])
        artifacts["cards"] += int(present["card"])
        have = sum((present["lite_axes"], present["lite_motifs"], present["card"]))
        counts["complete" if have == 3 else "missing" if have == 0 else "partial"] += 1
    return {"worker": "gemini-lite", **counts, "artifacts": artifacts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini transcript-direct enrichment worker")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run", "status", "export"):
        command = sub.add_parser(name)
        command.add_argument("--limit", type=int, default=250)
        command.add_argument("--source", choices=PUBLIC_SOURCES)
        command.add_argument("--doc")
        if name == "run":
            command.add_argument("--apply", action="store_true")
        if name == "export":
            command.add_argument("--out", required=True)
    args = parser.parse_args()

    db = Database(os.environ["DATABASE_URL"])
    if os.environ.get("WORKER_INITIALIZE_DATABASE") == "1":
        db.initialize()
    cache_helpers.init(db)
    llm_config.init(db)
    analysis.init(db, ROOT / "data", llm_config.get_llm)
    registry_version = analysis.load_motif_registry().get("version", "motifs-v0")
    try:
        if args.command == "export":
            from atlas_projection import build_projection, validate_projection

            projection, summary = build_projection(db)
            errors = validate_projection(projection)
            if errors:
                raise SystemExit("invalid projection: " + "; ".join(errors[:10]))
            Path(args.out).write_text(json.dumps(projection, ensure_ascii=False, sort_keys=True))
            print(json.dumps({**summary, "out": args.out}, sort_keys=True))
            return
        documents = _documents(db, args.limit, args.source, args.doc)
        if args.command == "status":
            print(json.dumps(_coverage(db, documents, registry_version), sort_keys=True))
            return
        for document in documents:
            text = document["text"]
            status, present = _status(db, text, registry_version)
            if args.command == "list":
                print(json.dumps({
                    "document_id": str(document["id"]), "title": document["title"],
                    "source": document["source_name"], "status": status,
                    "artifacts": present,
                }, sort_keys=True))
                continue
            if not args.apply:
                print(json.dumps({"document_id": str(document["id"]), "status": status, "would_enrich": True}))
                continue
            try:
                generated = []
                if not present["lite_axes"]:
                    analysis.run_axes_lite(text, provider_key=PROVIDER)
                    generated.append("axes")
                if not present["lite_motifs"]:
                    analysis.run_motifs_lite(text, provider_key=PROVIDER)
                    generated.append("motifs")
                if not present["card"]:
                    analysis.run_lesson_card(
                        text,
                        provider_key=PROVIDER,
                        source_title=document.get("title"),
                        hosting_page_context=_hosting_page_context(document),
                    )
                    generated.append("card")
                print(json.dumps({"document_id": str(document["id"]), "generated": generated}))
            except Exception as exc:  # one bad artifact cannot stop the resumable batch
                print(json.dumps({"document_id": str(document["id"]), "error": str(exc)[:500]}), file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    main()
