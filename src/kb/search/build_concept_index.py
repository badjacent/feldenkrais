"""
Build concept index for multi-method search.

Reads pass_0b_concepts results from cache, populates the lesson_concept table,
and builds concept-based embeddings (method='concepts') from distinctive_summary
+ search_terms.

Usage:
    .venv/bin/python src/kb/search/build_concept_index.py --dry-run
    .venv/bin/python src/kb/search/build_concept_index.py --apply
    .venv/bin/python src/kb/search/build_concept_index.py --apply --force
    .venv/bin/python src/kb/search/build_concept_index.py --apply --limit 5
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import get_embeddings
from cache_helpers import get_cache_key


def build_concept_summary(concepts_result: dict, title: str) -> str:
    """Build embedding text from the distinctive summary only.

    Keeps the embedding focused on what makes this lesson unique,
    without diluting with generic concept tags.
    """
    parts = [title]

    summary = concepts_result.get("distinctive_summary", "")
    if summary:
        parts.append(summary)

    return "\n".join(parts)


def build_concept_rows(concepts_result: dict) -> list[dict]:
    """Build concept rows including search_terms as a category.

    Returns list of {category, value} dicts for lesson_concept table.
    """
    rows = list(concepts_result.get("concepts", []))
    for term in concepts_result.get("search_terms", []):
        rows.append({"category": "search_term", "value": term})
    return rows


def find_docs_with_concepts(db: Database) -> list[dict]:
    """Find all documents that have pass_0b_concepts cached.

    Returns list of {doc_id, title, cache_key, concepts_data}.
    """
    # Get all transcripts to compute cache keys
    public_join = "\nJOIN document_source ds ON ds.id = d.source_id" if db.public_only else ""
    public_filter = " AND ds.public = TRUE" if db.public_only else ""

    with db.get_cursor(commit=False) as cursor:
        cursor.execute(f"""
            SELECT a.document_id::text as doc_id,
                   d.title,
                   a.data->>'text' as text
            FROM analysis a
            JOIN document d ON d.id = a.document_id
            JOIN analysis_type at ON at.id = a.type_id AND at.name = 'TRANSCRIPTION'{public_join}
            WHERE a.data->>'text' IS NOT NULL{public_filter}
        """)
        docs = [dict(row) for row in cursor.fetchall()]

    results = []
    cache_suffix = ":pass_0b_concepts_v1"

    for doc in docs:
        text = doc["text"].strip()
        if not text:
            continue
        cache_key = get_cache_key(text + cache_suffix)
        cached = db.get_cache(cache_key)
        if cached and cached.get("concepts"):
            results.append({
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "cache_key": cache_key,
                "concepts_data": cached,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Build concept index for multi-method search")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be indexed without making changes")
    group.add_argument("--apply", action="store_true", help="Actually build index and embed")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--force", action="store_true", help="Re-embed even if summary_hash unchanged")
    parser.add_argument("--concepts-only", action="store_true", help="Only update lesson_concept table, skip embeddings")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if args.apply and not args.concepts_only and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    # Find docs with concept extraction cached
    docs = find_docs_with_concepts(db)
    print(f"Found {len(docs)} documents with concept extraction")

    if args.limit:
        docs = docs[:args.limit]

    if not docs:
        print("Nothing to do.")
        db.close()
        return

    # Get existing concept embedding hashes
    existing_hashes = db.get_embedding_hashes(method="concepts")
    print(f"Found {len(existing_hashes)} existing concept embeddings")

    # Process
    to_embed = []
    concepts_updated = 0
    skipped = 0

    for doc in docs:
        concepts_data = doc["concepts_data"]
        concepts = concepts_data.get("concepts", [])

        # Build concept embedding text
        summary = build_concept_summary(concepts_data, doc["title"])
        summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

        if args.dry_run:
            print(f"\n  [{doc['title']}] ({doc['doc_id']})")
            print(f"    concepts: {len(concepts)}")
            cats = {}
            for c in concepts:
                cats.setdefault(c["category"], []).append(c["value"])
            for cat, vals in sorted(cats.items()):
                print(f"      {cat}: {', '.join(vals)}")
            distinctive = concepts_data.get("distinctive_summary", "")
            if distinctive:
                print(f"    summary: {distinctive[:150]}...")
            search_terms = concepts_data.get("search_terms", [])
            if search_terms:
                print(f"    search_terms: {', '.join(search_terms[:5])}...")
            print(f"    hash: {summary_hash}")
            continue

        # Upsert concepts + search terms to lesson_concept table
        all_rows = build_concept_rows(concepts_data)
        db.upsert_concepts(doc["doc_id"], all_rows)
        concepts_updated += 1

        # Check if embedding needs update
        if not args.concepts_only:
            if not args.force and existing_hashes.get(doc["doc_id"]) == summary_hash:
                skipped += 1
            else:
                to_embed.append((doc["doc_id"], summary, summary_hash, doc["title"]))

    if args.dry_run:
        print(f"\nDRY RUN: {len(docs)} lessons would be indexed")
        db.close()
        return

    print(f"\nConcepts updated: {concepts_updated}")

    if args.concepts_only:
        print("Skipping embeddings (--concepts-only)")
        db.close()
        return

    print(f"To embed: {len(to_embed)}, skipped (unchanged): {skipped}")

    if not to_embed:
        print("No embeddings to update.")
        db.close()
        return

    # Batch embed in chunks of 100
    CHUNK_SIZE = 100
    embedded = 0
    errors = 0
    total_chars = 0

    for i in range(0, len(to_embed), CHUNK_SIZE):
        chunk = to_embed[i:i + CHUNK_SIZE]
        texts = [summary for _, summary, _, _ in chunk]
        batch_chars = sum(len(t) for t in texts)
        total_chars += batch_chars

        print(f"Embedding batch {i // CHUNK_SIZE + 1} ({len(chunk)} texts, {batch_chars:,} chars)...", end=" ", flush=True)
        try:
            embeddings, usage = get_embeddings(texts)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += len(chunk)
            continue

        for (doc_id, summary, summary_hash, title), embedding in zip(chunk, embeddings):
            try:
                db.upsert_embedding(doc_id, embedding, summary, summary_hash, method="concepts")
                embedded += 1
            except Exception as e:
                print(f"\n  ERROR upserting {title}: {e}", file=sys.stderr)
                errors += 1

        print(f"done ({embedded} total)")

    print(f"\nDone: {embedded} embedded, {concepts_updated} concepts updated, {skipped} skipped, {errors} errors")
    print(f"Total chars: {total_chars:,}")
    db.close()


if __name__ == "__main__":
    main()
