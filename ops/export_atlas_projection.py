#!/usr/bin/env python3
"""Export a public-safe analysis projection for Feldenkrais Atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

TRANSCRIPT_PATTERNS = {
    "body_regions": {
        "spine": (r"\bspine\b", r"\bvertebra(?:e|l)?\b"),
        "ribs": (r"\bribs?\b", r"\bsternum\b", r"\bchest\b"),
        "shoulders": (r"\bshoulders?\b", r"\bscapula(?:e|r)?\b"),
        "pelvis": (r"\bpelvis\b", r"\bpelvic\b", r"\bsit(?:ting)? bones?\b"),
        "hips": (r"\bhips?\b",),
        "legs": (r"\blegs?\b", r"\bknees?\b", r"\bshins?\b"),
        "feet": (r"\bfeet\b", r"\bfoot\b", r"\bankles?\b", r"\btoes?\b"),
        "arms": (r"\barms?\b", r"\belbows?\b", r"\bhands?\b", r"\bwrists?\b"),
        "head": (r"\bhead\b", r"\bskull\b"),
        "neck": (r"\bneck\b", r"\bcervical\b"),
        "eyes": (r"\beyes?\b", r"\bgaze\b"),
        "jaw": (r"\bjaw\b", r"\bmouth\b", r"\btongue\b"),
    },
    "positions": {
        "side-lying": (r"\bside[ -]lying\b", r"\blie on your (?:left|right) side\b"),
        "lying": (r"\bsupine\b", r"\blie on your back\b", r"\blying on your back\b"),
        "prone": (r"\bprone\b", r"\b(?:lie|lying) on your (?:belly|stomach)\b"),
        "sitting": (r"\bsitting\b", r"\bseated\b", r"\bsit on\b"),
        "standing": (r"\bstanding\b", r"\bstand (?:up|on)\b"),
        "kneeling": (r"\bkneeling\b", r"\ball fours\b", r"\bhands and knees\b"),
    },
    "movement_themes": {
        "breathing": (r"\bbreath(?:e|ing|s)?\b", r"\binhal(?:e|ing)\b", r"\bexhal(?:e|ing)\b"),
        "rolling": (r"\broll(?:ing|ed|s)?\b",),
        "rotation": (r"\brotat(?:e|ing|ion)\b", r"\bturn(?:ing|ed|s)?\b", r"\btwist(?:ing|ed|s)?\b"),
        "reaching": (r"\breach(?:ing|ed|es)?\b", r"\blengthen(?:ing|ed|s)?\b"),
        "weight shifting": (r"\bshift(?:ing)? (?:your )?weight\b", r"\bweight transfer\b"),
        "flexion and extension": (r"\bflex(?:ion|ing)?\b", r"\bextend(?:ing|ed)?\b", r"\barch(?:ing|ed)?\b"),
        "walking": (r"\bwalk(?:ing|ed|s)?\b", r"\bstepp(?:ing|ed)\b"),
        "balance": (r"\bbalanc(?:e|ing)\b",),
        "circling": (r"\bcircl(?:e|ing|ed)\b",),
        "lifting and lowering": (r"\blift(?:ing|ed)?\b", r"\blower(?:ing|ed)?\b"),
        "pressing and pushing": (r"\bpress(?:ing|ed)?\b", r"\bpush(?:ing|ed)?\b"),
    },
    "teaching_strategies": {
        "variation": (r"\bvariation\b", r"\banother way\b", r"\bdifferent way\b"),
        "comparison": (r"\bcompar(?:e|ing|ison)\b", r"\bnotice the difference\b"),
        "reference movement": (r"\btry that again\b", r"\breturn to\b", r"\bonce again\b"),
        "sensing": (r"\bsens(?:e|ing)\b", r"\bnotice\b", r"\bfeel(?:ing)?\b"),
        "imagination": (r"\bimagin(?:e|ing|ation)\b",),
        "rest and integration": (r"\btake a rest\b", r"\brest for a moment\b", r"\bpause\b"),
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
            cursor.execute(
                """SELECT DISTINCT ON (document_id) document_id::text,
                          data->>'text' AS text
                   FROM analysis
                   WHERE type_id = 1 AND document_id = ANY(%s::uuid[])
                   ORDER BY document_id, updated_at DESC""",
                (list(matches),),
            )
            transcripts = {
                row["document_id"]: row["text"]
                for row in cursor.fetchall()
                if row.get("text")
            }
        if not matches:
            transcripts = {}
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
        for field, values in derive_transcript_facets(
            transcripts.get(document_id, "")
        ).items():
            facets.setdefault(field, values)
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
        "analysis_version": "public-transcript-facets-v1",
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


def derive_transcript_facets(text: str) -> dict[str, list[str]]:
    """Derive a deliberately coarse, non-quoting public projection."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return {}
    result = {}
    for field, labels in TRANSCRIPT_PATTERNS.items():
        matches = [
            label
            for label, patterns in labels.items()
            if any(re.search(pattern, normalized) for pattern in patterns)
        ]
        if matches:
            result[field] = matches[:8]
    return result


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
