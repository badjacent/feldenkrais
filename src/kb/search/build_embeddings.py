"""
Build lesson embeddings for semantic search.

Reads condensed analysis cards from the cache, builds summary text,
embeds via Gemini, and stores in the lesson_embedding table (pgvector).

Usage:
    .venv/bin/python src/kb/search/build_embeddings.py --dry-run
    .venv/bin/python src/kb/search/build_embeddings.py --apply
    .venv/bin/python src/kb/search/build_embeddings.py --apply --force
    .venv/bin/python src/kb/search/build_embeddings.py --apply --limit 5
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
from cache_helpers import build_summary_from_condensed


def main():
    parser = argparse.ArgumentParser(description="Build lesson embeddings for semantic search")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be embedded without calling API")
    group.add_argument("--apply", action="store_true", help="Actually embed and store")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N lessons")
    parser.add_argument("--force", action="store_true", help="Re-embed even if summary_hash unchanged")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    if args.apply and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    # Find docs with condensed analysis (2 DB queries, no per-doc loading)
    docs_with_condensed = db.get_docs_with_condensed()
    print(f"Found {len(docs_with_condensed)} documents with condensed analysis")

    # Get existing embedding hashes: {doc_id: summary_hash}
    existing_hashes = db.get_embedding_hashes()
    print(f"Found {len(existing_hashes)} existing embeddings")

    # Build summaries from condensed cache data
    to_embed = []  # list of (doc_id, summary_text, summary_hash, title)
    skipped = 0

    for doc_info in docs_with_condensed:
        condensed = db.get_cache(doc_info["cache_key"])
        if not condensed or not condensed.get("segments"):
            continue

        summary = build_summary_from_condensed(condensed, doc_info["title"])
        summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

        # Skip if unchanged
        if not args.force and existing_hashes.get(doc_info["doc_id"]) == summary_hash:
            skipped += 1
            continue

        to_embed.append((doc_info["doc_id"], summary, summary_hash, doc_info["title"]))

    if args.limit:
        to_embed = to_embed[:args.limit]

    print(f"\nTo embed: {len(to_embed)}, skipped (unchanged): {skipped}")

    if not to_embed:
        print("Nothing to do.")
        db.close()
        return

    if args.dry_run:
        for doc_id, summary, summary_hash, title in to_embed:
            print(f"\n  [{title}] ({doc_id})")
            print(f"    hash: {summary_hash}")
            preview = summary[:200].replace("\n", " ")
            print(f"    summary: {preview}...")
        print(f"\nDRY RUN: {len(to_embed)} lessons would be embedded")
        db.close()
        return

    # Batch embed in chunks of 100
    CHUNK_SIZE = 100
    embedded = 0
    errors = 0
    total_chars = 0
    total_input_chars = 0

    for i in range(0, len(to_embed), CHUNK_SIZE):
        chunk = to_embed[i:i + CHUNK_SIZE]
        texts = [summary for _, summary, _, _ in chunk]
        batch_input_chars = sum(len(t) for t in texts)
        total_input_chars += batch_input_chars

        print(f"Embedding batch {i // CHUNK_SIZE + 1} ({len(chunk)} texts, {batch_input_chars:,} chars)...", end=" ", flush=True)
        try:
            embeddings, usage = get_embeddings(texts)
            batch_chars = usage.get("billable_characters", batch_input_chars)
            total_chars += batch_chars
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += len(chunk)
            continue

        for (doc_id, summary, summary_hash, title), embedding in zip(chunk, embeddings):
            try:
                db.upsert_embedding(doc_id, embedding, summary, summary_hash)
                embedded += 1
            except Exception as e:
                print(f"\n  ERROR upserting {title}: {e}", file=sys.stderr)
                errors += 1

        print(f"done ({embedded} total)")

    print(f"\nDone: {embedded} embedded, {skipped} skipped, {errors} errors")
    print(f"Input chars: {total_input_chars:,}, billable chars: {total_chars:,}")
    db.close()


if __name__ == "__main__":
    main()
