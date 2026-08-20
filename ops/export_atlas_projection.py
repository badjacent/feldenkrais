#!/usr/bin/env python3
"""Export a public-safe analysis projection for Feldenkrais Atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from kb.database import Database
from kb.similarity.calibration import build_embedding_calibration
from kb.similarity.measures import similar_lessons
from kb.similarity.profiles import PROFILE_SCHEMA_VERSION, profile_from_payload

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


def public_lesson_key(private_lesson_key: str) -> str:
    """Hide private identifiers while preserving rendition identity."""
    return hashlib.sha256(
        f"atlas-analysis:{private_lesson_key}".encode()
    ).hexdigest()[:16]


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
    profile_rows = db.get_similarity_profiles(
        schema_version=PROFILE_SCHEMA_VERSION,
        extraction_model="pass0b-existing",
        weight_set="weights-v1",
    )
    profiles = [
        profile_from_payload(row["payload"], embedding=row["embedding"]).profile
        for row in profile_rows
    ]
    profiles_by_id = {profile.document_id: profile for profile in profiles}
    matched_profiles = [profiles_by_id[document_id] for document_id in matches if document_id in profiles_by_id]
    if len(matched_profiles) >= 2:
        calibration = build_embedding_calibration(
            profiles,
            schema_version=PROFILE_SCHEMA_VERSION,
            extraction_model="pass0b-existing",
        )
        ecdf = calibration.ecdf()
    else:
        ecdf = None
    entries = []
    for document_id, item in matches.items():
        profile = profiles_by_id.get(document_id)
        if profile is None:
            continue
        facets = {
            "body_regions": _public_labels(profile.regions),
            "movement_themes": _public_labels(profile.themes),
            "positions": _public_labels(profile.positions),
        }
        facets = {key: value for key, value in facets.items() if value}
        neighbors = []
        if ecdf is not None:
            neighbors = [
                {
                    "lesson_key": public_lesson_key(result_profile.lesson_key),
                    "score": round(result.similarity, 5),
                }
                for result in similar_lessons(
                    profile,
                    matched_profiles,
                    embedding_ecdf=ecdf,
                    limit=8,
                )
                if (result_profile := profiles_by_id[result.right_id])
            ]
        entries.append(
            {
                "resource_url": item["url"],
                "lesson_key": public_lesson_key(profile.lesson_key),
                "analysis_summary": build_summary(facets),
                "similarities": neighbors,
                **facets,
            }
        )
    entries.sort(key=lambda item: item["resource_url"])
    return {
        "schema_version": 1,
        "analysis_version": "similarity-profile-v1:weights-v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entries": entries,
    }


def _public_labels(values: dict[str, float]) -> list[str]:
    return [
        key.replace("_", "-")
        for key, _ in sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ][:8]


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
