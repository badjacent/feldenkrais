#!/usr/bin/env python3
"""Export a public-safe analysis projection for Feldenkrais Atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from kb.database import Database

PRACTICE_KINDS = {"audio_lesson", "video_lesson"}
CATEGORY_FIELDS = {
    "body_region": "body_regions",
    "movement_pattern": "movement_themes",
    "theme": "themes",
    "position": "positions",
    "working_position": "positions",
    "teaching_strategy": "teaching_strategies",
}
CANONICAL_TERMS = {
    "body_regions": {
        "spine": ("spine", "vertebra"),
        "ribs": ("rib", "sternum"),
        "shoulders": ("shoulder", "scapula"),
        "pelvis": ("pelvis", "pelvic", "sit bones"),
        "hips": ("hip",),
        "legs": ("leg", "knee", "fibular"),
        "feet": ("foot", "feet", "ankle", "metatarsal"),
        "arms": ("arm", "elbow", "hand"),
        "head": ("head", "skull"),
        "neck": ("neck", "cervical"),
        "eyes": ("eye", "gaze"),
        "jaw": ("jaw", "mouth", "tongue"),
    },
    "positions": {
        "side-lying": ("side-lying", "side_lying", "side lying"),
        "lying": ("supine", "lying", "on back"),
        "prone": ("prone", "on belly", "on stomach"),
        "sitting": ("sitting", "seated"),
        "standing": ("standing",),
        "kneeling": ("kneeling", "all fours"),
    },
}


def normalize_resource_url(url: str) -> str:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").casefold().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    query = dict(parse_qsl(parts.query))
    if host in {"youtube.com", "youtu.be"}:
        video_id = query.get("v") or (path.strip("/") if host == "youtu.be" else None)
        if video_id:
            return f"youtube:{video_id}"
    return f"{host}{path}"


def public_lesson_key(document_id: str) -> str:
    return hashlib.sha256(f"atlas-analysis:{document_id}".encode()).hexdigest()[:16]


def build_summary(facets: dict[str, list[str]]) -> str:
    parts = []
    if facets.get("body_regions"):
        parts.append("attention to " + _join(facets["body_regions"][:3]))
    movements = facets.get("movement_themes", [])
    if movements:
        parts.append("exploring " + _join(movements[:3]))
    positions = facets.get("positions", [])
    if positions:
        parts.append("often in " + _join(positions[:2]))
    if not parts and facets.get("themes"):
        parts.append("exploring " + _join(facets["themes"][:3]))
    return ("A Feldenkrais lesson with " + ", ".join(parts) + ".") if parts else ""


def export_projection(db: Database, catalog: list[dict]) -> dict:
    eligible = {
        normalize_resource_url(item["url"]): item
        for item in catalog
        if item.get("entry_kind") == "resource"
        and item.get("access") == "free"
        and item.get("content_kind") in PRACTICE_KINDS
        and item.get("status") != "dead"
    }
    with db.get_cursor(commit=False) as cursor:
        cursor.execute("SELECT id::text, source_url FROM document")
        documents = cursor.fetchall()
        matches = {
            row["id"]: eligible[normalize_resource_url(row["source_url"])]
            for row in documents
            if normalize_resource_url(row["source_url"]) in eligible
        }
        if not matches:
            concept_rows = []
        else:
            cursor.execute(
                """SELECT document_id::text, category, value
                   FROM lesson_concept WHERE document_id = ANY(%s::uuid[])""",
                (list(matches),),
            )
            concept_rows = cursor.fetchall()
    by_document: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in concept_rows:
        field = CATEGORY_FIELDS.get(row["category"])
        if field and row["value"]:
            by_document[row["document_id"]][field].add(str(row["value"]).strip())
    entries = []
    for document_id, item in matches.items():
        facets = {
            field: normalize_values(field, values)
            for field, values in by_document.get(document_id, {}).items()
            if values
        }
        if not facets:
            continue
        entries.append(
            {
                "resource_url": item["url"],
                "lesson_key": public_lesson_key(document_id),
                "analysis_summary": build_summary(facets),
                **facets,
            }
        )
    entries.sort(key=lambda item: item["resource_url"])
    return {
        "schema_version": 1,
        "analysis_version": "structured-concepts-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entries": entries,
    }


def _join(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + f" and {values[-1]}"


def normalize_values(field: str, values: set[str]) -> list[str]:
    vocabulary = CANONICAL_TERMS.get(field)
    if vocabulary is None:
        return sorted(values)[:8]
    lowered_values = [value.casefold() for value in values]
    return [
        label
        for label, aliases in vocabulary.items()
        if any(
            alias in value and not (label == "standing" and value != "standing")
            for value in lowered_values
            for alias in aliases
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    db = Database(database_url)
    try:
        projection = export_projection(db, catalog)
    finally:
        db.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(projection, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"entries": len(projection["entries"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
