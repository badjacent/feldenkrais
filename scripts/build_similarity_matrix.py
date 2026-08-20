#!/usr/bin/env python3
"""Build the versioned ECDF and float16 lesson-similarity matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from kb.database import Database
from kb.similarity.calibration import build_embedding_calibration
from kb.similarity.matrix import SimilarityMatrix, build_similarity_matrix
from kb.similarity.profiles import PROFILE_SCHEMA_VERSION, profile_from_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extraction-model", default="pass0b-existing")
    parser.add_argument("--weight-set", default="weights-v2-language")
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")

    db = Database(database_url)
    try:
        rows = db.get_similarity_profiles(
            schema_version=PROFILE_SCHEMA_VERSION,
            extraction_model=args.extraction_model,
            weight_set=args.weight_set,
        )
        projected = [
            profile_from_payload(row["payload"], embedding=row["embedding"])
            for row in rows
        ]
        profiles = [item.profile for item in projected]
        hashes = {}
        for item, row in zip(projected, rows, strict=True):
            embedding_hash = hashlib.sha256(
                json.dumps(row["embedding"], separators=(",", ":")).encode()
            ).hexdigest()
            hashes[item.profile.document_id] = hashlib.sha256(
                f'{row["content_hash"]}:{embedding_hash}'.encode()
            ).hexdigest()
        calibration = build_embedding_calibration(
            profiles,
            schema_version=PROFILE_SCHEMA_VERSION,
            extraction_model=args.extraction_model,
        )
        previous = None
        if args.incremental and (args.output / "manifest.json").exists():
            previous = SimilarityMatrix.load(args.output)
        matrix, recomputed = build_similarity_matrix(
            profiles,
            content_hashes=hashes,
            embedding_ecdf=calibration.ecdf(),
            schema_version=PROFILE_SCHEMA_VERSION,
            extraction_model=args.extraction_model,
            weight_set=args.weight_set,
            previous=previous,
        )
        calibration.write(args.output / "embedding_ecdf.json")
        matrix.save(args.output)
        print(json.dumps({
            "profiles": len(profiles),
            "pairs_recomputed": recomputed,
            "matrix_shape": list(matrix.values.shape),
            "ecdf_pairs": calibration.sampled_pairs,
            "output": str(args.output),
        }, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
