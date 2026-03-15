"""Run spatial extraction on the spatial_configuration reference lessons only."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database import Database
import cache_helpers
import llm_config
import analysis
from cache_helpers import get_cache_key

# Load reference doc_ids
data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"
data = json.load(open(data_dir / "search_eval_questions.json"))
spatial_qs = [q for q in data["questions"] if "spatial_configuration" in q.get("tags", [])]
ref_doc_ids = set(q["expected_doc_id"] for q in spatial_qs)
print(f"Reference lessons: {len(ref_doc_ids)}")

db = Database(os.environ["DATABASE_URL"])
db.initialize()
cache_helpers.init(db)
llm_config.init(db)
analysis.init(db, data_dir, llm_config.get_llm)

# Get transcripts for reference lessons
with db.get_cursor(commit=False) as cur:
    cur.execute("""
        SELECT a.document_id::text as doc_id, d.title, a.data->>'text' as text
        FROM analysis a
        JOIN document d ON d.id = a.document_id
        JOIN analysis_type at ON at.id = a.type_id AND at.name = 'TRANSCRIPTION'
        WHERE a.data->>'text' IS NOT NULL
    """)
    all_docs = [dict(row) for row in cur.fetchall()]

lessons = [d for d in all_docs if d["doc_id"] in ref_doc_ids]
print(f"Found transcripts: {len(lessons)}")

# Check which already cached
cache_suffix = ":spatial_v2"
to_extract = []
already = 0
for doc in lessons:
    text = doc["text"].strip()
    if not text:
        continue
    key = get_cache_key(text + cache_suffix)
    cached = db.get_cache(key)
    if cached and cached.get("groups"):
        already += 1
    else:
        to_extract.append(doc)

print(f"Already cached: {already}, to extract: {len(to_extract)}")

successes = 0
errors = 0
for i, lesson in enumerate(to_extract, 1):
    print(f"[{i}/{len(to_extract)}] {lesson['title'][:60]}...", end=" ", flush=True)
    t0 = time.time()
    try:
        result = analysis.run_pass_0b_spatial(lesson["text"])
        elapsed = round(time.time() - t0, 1)
        cached = result.get("cached", False)
        n_groups = len(result.get("groups", []))
        print(f"{'(cached)' if cached else f'{elapsed}s'} — {n_groups} groups")
        successes += 1
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"ERROR ({elapsed}s): {e}")
        errors += 1

print(f"\nDone: {successes} extracted, {errors} errors, {already} already cached")
db.close()
