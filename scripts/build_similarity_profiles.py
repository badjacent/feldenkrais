#!/usr/bin/env python3
"""Project existing analysis caches into durable similarity profiles."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from kb.database import Database
from kb.similarity.profiles import profile_content_hash, project_existing_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="replace the persisted profile version")
    parser.add_argument(
        "--skip-initialize",
        action="store_true",
        help="do not run broad database initialization (use when workers are active)",
    )
    parser.add_argument("--weight-set", default="weights-v1")
    args = parser.parse_args()
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")

    db = Database(database_url)
    if not args.skip_initialize:
        db.initialize()
    try:
        rows = db.get_similarity_projection_inputs()
        projected = [
            project_existing_analysis(
                document_id=row["document_id"],
                lesson_key=row["lesson_key"],
                concepts=row["concepts"],
                decompose=row["decompose"],
                somatic=row["somatic"],
                language=row["language"],
                spatial=row["spatial"],
                embedding=row["embedding"],
                duration_seconds=row["duration_seconds"],
            )
            for row in rows
        ]
        hashes = {item.profile.document_id: profile_content_hash(item) for item in projected}
        if args.apply:
            db.replace_similarity_profiles(projected, weight_set=args.weight_set, content_hashes=hashes)
        print(json.dumps({
            "applied": args.apply,
            "profiles": len(projected),
            "embedded": sum(item.profile.embedding is not None for item in projected),
            "with_structured_data": sum(bool(item.profile.regions or item.profile.themes) for item in projected),
            "schema_version": projected[0].schema_version if projected else None,
            "extraction_model": projected[0].extraction_model if projected else None,
            "weight_set": args.weight_set,
        }, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
