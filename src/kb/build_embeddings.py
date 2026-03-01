"""
Build lesson embeddings for semantic search.

Reads condensed analysis cards from the cache, builds summary text,
embeds via OpenAI, and stores in the lesson_embedding table (pgvector).

Usage:
    .venv/bin/python src/kb/build_embeddings.py --dry-run
    .venv/bin/python src/kb/build_embeddings.py --apply
    .venv/bin/python src/kb/build_embeddings.py --apply --force
    .venv/bin/python src/kb/build_embeddings.py --apply --limit 5
"""

import argparse
import hashlib
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import get_embeddings


def get_cache_key(text: str) -> str:
    """Generate a cache key from lesson text (matches web.py)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_summary_from_condensed(condensed: dict, title: str) -> str:
    """Build a summary string from condensed card data for embedding."""
    parts = [title]

    for seg in condensed.get("segments", []):
        setting = seg.get("setting", {})
        if setting:
            if setting.get("title"):
                parts.append(f"Setting: {setting['title']}")
            if setting.get("somatic_intent"):
                parts.append(setting["somatic_intent"])

        for card in seg.get("group_cards", []):
            label = card.get("group_label", "")
            card_title = card.get("title", "")
            if label or card_title:
                parts.append(f"{label}: {card_title}".strip(": "))
            if card.get("the_action"):
                parts.append(card["the_action"])
            if card.get("the_body"):
                parts.append(card["the_body"])

    return "\n".join(parts)




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

    if args.apply and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    db = Database(database_url)
    db.initialize()

    # Get all transcribed docs: {doc_id: text_hash}
    text_hashes = db.get_all_transcription_hashes()
    print(f"Found {len(text_hashes)} transcribed documents")

    # Get existing embedding hashes: {doc_id: summary_hash}
    existing_hashes = db.get_embedding_hashes()
    print(f"Found {len(existing_hashes)} existing embeddings")

    # Build summaries for each document
    to_embed = []  # list of (doc_id, summary_text, summary_hash)
    skipped = 0
    no_condensed = 0

    for doc_id, text_hash in text_hashes.items():
        # Load transcript to compute condensed cache key (matches web.py's get_cache_key)
        transcript = db.get_document_transcription(doc_id)
        if not transcript or not transcript.strip():
            continue

        condensed_cache_key = get_cache_key(transcript.strip() + ":pass_0b_condensed_v4")
        condensed = db.get_cache(condensed_cache_key)

        # Get document title
        doc = db.get_document(doc_id)
        title = doc["title"] if doc else "Unknown"

        if condensed and condensed.get("segments"):
            summary = build_summary_from_condensed(condensed, title)
        else:
            no_condensed += 1
            continue

        summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:16]

        # Skip if unchanged
        if not args.force and existing_hashes.get(doc_id) == summary_hash:
            skipped += 1
            continue

        to_embed.append((doc_id, summary, summary_hash, title))

    if args.limit:
        to_embed = to_embed[:args.limit]

    print(f"\nTo embed: {len(to_embed)}, skipped (unchanged): {skipped}, no condensed (using transcript): {no_condensed}")

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
    # Gemini embedding: free tier up to 1500 RPM; paid at ~$0.00001/1K chars
    if embedded > 0 and len(text_hashes) > embedded:
        projected_chars = total_chars * len(text_hashes) / embedded
        print(f"Projected for all {len(text_hashes)} docs: ~{int(projected_chars):,} billable chars")
    db.close()


if __name__ == "__main__":
    main()
