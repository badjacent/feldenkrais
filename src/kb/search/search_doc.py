"""Search document generation and embedding.

Generates LLM-written "search documents" from condensed lesson cards,
designed for high-discrimination semantic search. Each search doc emphasizes
what's distinctive about a lesson, minimizing shared Feldenkrais language.

Usage:
    from search_doc import generate_search_doc, generate_all_search_docs, embed_search_docs
"""

import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from database import Database
from llm import LLM, get_embeddings

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
PROMPTS_DIR = DATA_DIR / "search_doc_prompts"


def load_prompt(version: int) -> str:
    """Load the search doc prompt for a given version."""
    path = PROMPTS_DIR / f"v{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text()


def format_condensed_for_search_doc(condensed: dict, title: str) -> str:
    """Format all condensed card fields into input for the search doc LLM.

    Includes ALL fields (action, body, attention, intention, voice, setting)
    so the LLM can decide what's distinctive.
    """
    parts = [f"# {title}\n"]

    # Setting card
    setting = condensed.get("setting", {})
    if setting:
        parts.append("## Setting")
        if setting.get("somatic_intent"):
            parts.append(f"Somatic intent: {setting['somatic_intent']}")
        if setting.get("space"):
            parts.append(f"Space: {setting['space']}")
        if setting.get("teacher_presence"):
            parts.append(f"Teacher presence: {setting['teacher_presence']}")
        if setting.get("arc"):
            parts.append(f"Arc: {setting['arc']}")
        parts.append("")

    # Group cards
    for card in condensed.get("group_cards", []):
        label = card.get("group_label", "")
        card_title = card.get("title", "")
        header = f"{label}: {card_title}".strip(": ")
        parts.append(f"## Group {card.get('group_number', '?')}: {header}")

        if card.get("the_action"):
            parts.append(f"Action: {card['the_action']}")
        if card.get("the_body"):
            parts.append(f"Body: {card['the_body']}")
        if card.get("the_attention"):
            parts.append(f"Attention: {card['the_attention']}")
        if card.get("the_intention"):
            parts.append(f"Intention: {card['the_intention']}")
        if card.get("the_voice"):
            parts.append(f"Voice: {card['the_voice']}")
        parts.append("")

    return "\n".join(parts)


def generate_search_doc(condensed: dict, title: str, prompt_text: str) -> str:
    """Generate a search document for a single lesson via LLM.

    Uses gemini-2.0-flash for speed and cost efficiency.
    """
    formatted_input = format_condensed_for_search_doc(condensed, title)

    llm = LLM(
        provider="gemini",
        model="gemini-2.0-flash",
        api_key=os.environ.get("GOOGLE_API_KEY"),
        default_max_tokens=2048,
    )

    user_prompt = f"{formatted_input}\n\n---\n\nWrite the search-optimized description for this lesson."
    return llm.complete(user_prompt, system=prompt_text)


def _cache_key_for_search_doc(doc_id: str, version: int) -> str:
    """Cache key for a search doc: search_doc:v{N}:{doc_id}."""
    return f"search_doc:v{version}:{doc_id}"


def generate_all_search_docs(
    db: Database,
    version: int,
    prompt_text: str | None = None,
    workers: int = 5,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate search docs for all lessons with condensed data.

    Args:
        db: Database instance.
        version: Prompt version number.
        prompt_text: Override prompt text (default: load from file).
        workers: Number of parallel LLM workers.
        limit: Max lessons to process (for testing).
        dry_run: If True, print docs instead of caching.

    Returns:
        Dict with counts: {generated, cached, errors, total}.
    """
    if prompt_text is None:
        prompt_text = load_prompt(version)

    docs_with_condensed = db.get_docs_with_condensed()
    print(f"Found {len(docs_with_condensed)} lessons with condensed data")

    if limit:
        docs_with_condensed = docs_with_condensed[:limit]

    # Check which already have search docs cached for this version
    existing_keys = db.get_cache_keys()
    to_generate = []
    cached = 0

    for doc_info in docs_with_condensed:
        cache_key = _cache_key_for_search_doc(doc_info["doc_id"], version)
        if cache_key in existing_keys:
            cached += 1
            continue
        # Load condensed data
        condensed = db.get_cache(doc_info["cache_key"])
        if not condensed or not condensed.get("group_cards"):
            # Try segments key (older format)
            if condensed and condensed.get("segments"):
                # Flatten segments into group_cards + setting
                flat = _flatten_segments(condensed)
                to_generate.append((doc_info, flat))
            continue
        to_generate.append((doc_info, condensed))

    print(f"To generate: {len(to_generate)}, already cached: {cached}")

    if not to_generate:
        return {"generated": 0, "cached": cached, "errors": 0, "total": len(docs_with_condensed)}

    if dry_run:
        # Generate a few and print them
        for doc_info, condensed in to_generate:
            print(f"\n{'='*70}")
            print(f"LESSON: {doc_info['title']}")
            print(f"{'='*70}")
            search_doc = generate_search_doc(condensed, doc_info["title"], prompt_text)
            print(search_doc)
            print()
        return {"generated": len(to_generate), "cached": cached, "errors": 0, "total": len(docs_with_condensed)}

    # Parallel generation
    generated = 0
    errors = 0
    t0 = time.time()

    def _generate_one(doc_info, condensed):
        search_doc = generate_search_doc(condensed, doc_info["title"], prompt_text)
        return doc_info, search_doc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate_one, doc_info, condensed): doc_info
            for doc_info, condensed in to_generate
        }
        for future in as_completed(futures):
            doc_info = futures[future]
            try:
                _, search_doc = future.result()
                cache_key = _cache_key_for_search_doc(doc_info["doc_id"], version)
                db.set_cache(cache_key, {"text": search_doc, "doc_id": doc_info["doc_id"], "title": doc_info["title"]})
                generated += 1
                if generated % 50 == 0:
                    elapsed = time.time() - t0
                    rate = generated / elapsed
                    print(f"  Generated {generated}/{len(to_generate)} ({rate:.1f}/s)")
            except Exception as e:
                print(f"  ERROR generating for {doc_info['title']}: {e}", file=sys.stderr)
                errors += 1

    elapsed = time.time() - t0
    print(f"Done: {generated} generated, {errors} errors in {elapsed:.1f}s")
    return {"generated": generated, "cached": cached, "errors": errors, "total": len(docs_with_condensed)}


def _flatten_segments(condensed: dict) -> dict:
    """Flatten segments-format condensed data into group_cards + setting format."""
    group_cards = []
    setting = {}
    for seg in condensed.get("segments", []):
        if seg.get("setting"):
            setting = seg["setting"]
        for card in seg.get("group_cards", []):
            group_cards.append(card)
    return {"group_cards": group_cards, "setting": setting}


def embed_search_docs(db: Database, version: int) -> dict:
    """Embed all cached search docs for a version and upsert into lesson_embedding.

    Overwrites existing embeddings in the same table.

    Returns:
        Dict with counts: {embedded, errors, total}.
    """
    # Collect all search docs for this version
    prefix = f"search_doc:v{version}:"
    all_keys = db.get_cache_keys()
    version_keys = sorted(k for k in all_keys if k.startswith(prefix))

    print(f"Found {len(version_keys)} search docs for v{version}")
    if not version_keys:
        return {"embedded": 0, "errors": 0, "total": 0}

    # Load all search docs
    docs = []  # (doc_id, text, title)
    for key in version_keys:
        data = db.get_cache(key)
        if data and data.get("text"):
            docs.append((data["doc_id"], data["text"], data.get("title", "")))

    print(f"Loaded {len(docs)} search doc texts")

    # Embed in chunks
    CHUNK_SIZE = 100
    embedded = 0
    errors = 0
    t0 = time.time()

    for i in range(0, len(docs), CHUNK_SIZE):
        chunk = docs[i:i + CHUNK_SIZE]
        texts = [text for _, text, _ in chunk]
        batch_chars = sum(len(t) for t in texts)

        print(f"  Embedding batch {i // CHUNK_SIZE + 1} ({len(chunk)} texts, {batch_chars:,} chars)...", end=" ", flush=True)
        try:
            embeddings, usage = get_embeddings(texts)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            errors += len(chunk)
            continue

        for (doc_id, text, title), embedding in zip(chunk, embeddings):
            summary_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            try:
                db.upsert_embedding(doc_id, embedding, text, summary_hash)
                embedded += 1
            except Exception as e:
                print(f"\n    ERROR upserting {title}: {e}", file=sys.stderr)
                errors += 1

        print(f"done ({embedded} total)")

    elapsed = time.time() - t0
    print(f"Embedded {embedded} docs in {elapsed:.1f}s, {errors} errors")
    return {"embedded": embedded, "errors": errors, "total": len(docs)}


def load_search_doc(db: Database, doc_id: str, version: int) -> str | None:
    """Load a single cached search doc by doc_id and version."""
    key = _cache_key_for_search_doc(doc_id, version)
    data = db.get_cache(key)
    if data and data.get("text"):
        return data["text"]
    return None
