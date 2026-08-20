#!/usr/bin/env python3
"""Build the pre-scoring rendition gate from existing corpus data."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from kb.database import Database
from kb.similarity.renditions import RenditionDocument, build_rendition_groups


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="replace persisted rendition assignments after building",
    )
    args = parser.parse_args()

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")

    db = Database(database_url)
    db.initialize()
    try:
        inputs = [RenditionDocument(**row) for row in db.get_rendition_inputs()]
        build = build_rendition_groups(
            inputs,
            existing_lesson_keys=db.get_existing_lesson_keys(),
        )
        if args.apply:
            db.replace_rendition_gate(build)
        rendition_groups = [members for members in build.groups.values() if len(members) > 1]
        print(
            json.dumps(
                {
                    "applied": args.apply,
                    "detection_version": build.detection_version,
                    "documents_read": len(inputs),
                    "eligible_atms": len(build.lesson_keys),
                    "excluded": len(build.excluded),
                    "rendition_groups": len(rendition_groups),
                    "rendition_members": sum(len(members) for members in rendition_groups),
                    "evidence_edges": len(build.evidence),
                    "raw_embeddings_available": sum(
                        item.raw_embedding is not None for item in inputs
                    ),
                },
                sort_keys=True,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
