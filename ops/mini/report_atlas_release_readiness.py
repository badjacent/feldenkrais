#!/usr/bin/env python3
"""Report the exact public lesson delta if Atlas were released now."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/kb"))

from atlas_projection import build_projection, validate_projection
from database import Database

DEFAULT_ATLAS = Path("/Users/hermes/src/feldenkrais-index")
DEFAULT_PRODUCTION_CONFIG = "https://feldenkraisatlas.com/site-config.json"


def release_counts(*, live_lessons: int, publishable_lessons: int,
                   projection_lessons: int, excluded_unrelated: int) -> dict:
    return {
        "live_lessons": live_lessons,
        "publishable_lessons": publishable_lessons,
        "lessons_added_if_released": max(0, publishable_lessons - live_lessons),
        "projection_lessons": projection_lessons,
        "projection_not_publishable": max(0, projection_lessons - publishable_lessons),
        "excluded_unrelated": excluded_unrelated,
    }


def _live_lessons(url: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "FeldenkraisMiniOps/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    value = payload.get("analysis_entries")
    if not isinstance(value, int) or value < 0:
        raise ValueError("production site config has no valid analysis_entries count")
    return value


def _publishable_lessons(atlas: Path, projection: Path) -> int:
    code = """
import json, sys
from pathlib import Path
from feldenkrais_index.analysis_projection import apply_analysis_projection
from feldenkrais_index.site_assets import derive_record
from scripts.build_site import gemini_complete_catalog
records = [derive_record(record) for record in json.loads(Path(sys.argv[1]).read_text())]
apply_analysis_projection(records, Path(sys.argv[2]))
records = gemini_complete_catalog([derive_record(record) for record in records])
print(sum(record.get('entry_kind') == 'resource' for record in records))
"""
    environment = {**os.environ, "PYTHONPATH": str(atlas / "src")}
    result = subprocess.run(
        [str(atlas / ".venv/bin/python"), "-c", code,
         str(atlas / "catalog/resources.json"), str(projection)],
        cwd=atlas, env=environment, capture_output=True, text=True,
        timeout=30, check=True,
    )
    return int(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--production-config", default=DEFAULT_PRODUCTION_CONFIG)
    args = parser.parse_args()

    db = Database(os.environ["DATABASE_URL"])
    try:
        projection, summary = build_projection(db)
    finally:
        db.close()
    errors = validate_projection(projection)
    if errors:
        raise SystemExit("invalid release projection: " + "; ".join(errors[:10]))

    with tempfile.TemporaryDirectory(prefix="atlas-release-readiness-") as directory:
        projection_path = Path(directory) / "gemini-lite-analysis.json"
        projection_path.write_text(json.dumps(projection), encoding="utf-8")
        publishable = _publishable_lessons(args.atlas.resolve(), projection_path)

    print(json.dumps(release_counts(
        live_lessons=_live_lessons(args.production_config),
        publishable_lessons=publishable,
        projection_lessons=len(projection.get("lessons", [])),
        excluded_unrelated=int(summary.get("excluded_unrelated", 0)),
    ), sort_keys=True))


if __name__ == "__main__":
    main()
