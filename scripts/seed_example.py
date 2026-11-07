"""Seed one fictional document into an explicitly supplied local database."""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "kb"))
from database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    if urlparse(args.database_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Example seeding is restricted to a local database")
    lesson = json.loads((ROOT / "data/examples/lesson.json").read_text())
    db = Database(args.database_url)
    db.initialize()
    # A disabled canonical profile makes the UI usable without enabling model jobs.
    with db.get_cursor() as cursor:
        cursor.execute("""INSERT INTO analysis_profile
            (key, provider, model, enabled, canonical)
            SELECT 'example', 'gemini', 'example-disabled', FALSE, TRUE
            WHERE NOT EXISTS (SELECT 1 FROM analysis_profile WHERE canonical)
            ON CONFLICT DO NOTHING""")
    from cache_helpers import profile_result_key
    fixture = json.loads((ROOT / "data/examples/analysis.json").read_text())
    profile = db.get_canonical_profile()
    db.set_cache(profile_result_key(lesson["text"], ":decompose_v3", profile), fixture)
    if db.document_exists(lesson["title"], lesson["source_url"]):
        print("Synthetic example already exists")
        return
    doc_id = db.create_document(title=lesson["title"], s3_key="", source_id=1,
        type_id=1, source_url=lesson["source_url"],
        metadata=json.dumps({"synthetic": True, "provenance": lesson["provenance"]}))
    db.create_analysis(doc_id, 1, json.dumps({key: lesson[key] for key in
        ("text", "segments", "language")}))
    print(f"Created synthetic example: /lesson/{doc_id}")


if __name__ == "__main__":
    main()
