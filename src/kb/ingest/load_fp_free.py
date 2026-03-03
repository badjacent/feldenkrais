#!/usr/bin/env python3
"""
Load all free lessons from feldenkraisproject.com into a new source.

1. Creates FELDENKRAIS_PROJECT_FREE source (if not exists)
2. Deletes existing free FP lessons from the old source
3. Scrapes all free lesson pages WITHOUT cookies (so only truly free ones have audio)
4. Stages audio to S3 in batches of 5, waiting 15 minutes between batches

Usage:
    .venv/bin/python src/kb/ingest/load_fp_free.py                # full run
    .venv/bin/python src/kb/ingest/load_fp_free.py --dry-run      # just identify free lessons
    .venv/bin/python src/kb/ingest/load_fp_free.py --skip-delete   # don't delete old lessons
    .venv/bin/python src/kb/ingest/load_fp_free.py --free-list data/fp_free_lessons.json  # skip scan
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

# Add src/kb to path for infrastructure imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database
from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Source IDs
OLD_SOURCE_ID = 4   # FELDENKRAIS_PROJECT
NEW_SOURCE_NAME = "FELDENKRAIS_PROJECT_FREE"
NEW_SOURCE_CATEGORY = "Feldenkrais Project (Free)"
TYPE_AUDIO = 2

# All lesson slugs from the sitemap
ALL_LESSON_SLUGS = [
    "access-your-axis-workshop", "tcf-10-year-celebration",
    "driving-dynamic-sitting-1-floor", "stretch-without-strain-workshop",
    "arms-in-a-hoop", "perfecting-the-self-image",
    "spinal-support-powerful-pelvis-workshop", "connecting-arms-legs",
    "coordination-flexors-extensors", "stretch-without-strain",
    "easier-sitting-workshop-4-lesson-2", "spiraling-circling-arm-under",
    "advanced-folding", "folding-foundation-length",
    "basic-arching-folding", "lifting-and-pressing-short",
    "simple-twisting", "carriage-of-the-head",
    "spinal-support-powerful-pelvis", "dynamic-sitting-chair-clock",
    "lengthening-arms-freeing-scapulas", "sacral-clock",
    "skillful-scapulas-graceful-arms-hands", "twisting-on-belly",
    "activating-the-arches", "buttocks",
    "breathing-for-liftoff", "feet-torso-breath-earth",
    "light-as-a-finger", "spiral-length-power",
    "lifting-like-a-baby", "joyful-lifted-rolling",
    "morning-prayer", "what-is-good-posture",
    "effortless-arms-shoulders", "one-shoulder-lesson",
    "reaching-twisting-gazing-smoothly", "periscope-1",
    "periscope-2", "rib-basket-shoulder-cloak",
    "folding-arching-rolling", "secrets-seated-twist",
    "walking-with-your-sternum", "sliding-sternum",
    "balance-workshop-part-1", "balance-workshop-part-2",
    "whole-body-arms", "bending-sideways",
    "spine-chest-side-bending-lengthening-limbs", "breath-belly-back-hips-earth",
    "floating-toward-bridging", "side-bending-listening-hands",
    "dynamic-balance-stability-suppleness", "coordinates-of-the-head",
    "freeing-your-breath-and-spine", "freeing-your-breath-and-spine-plus-extra-content",
    "differentiation-breathing", "breathing-head-to-heels",
    "thinking-and-breathing", "holding-breath-awareness",
    "pelvic-floor-connections", "the-liminal-lesson",
    "finding-sensations-of-not-shortening", "two-sitbones-two-sides-pelvic-floor",
    "differentiating-pelvic-floor-part-1", "differentiating-pelvic-floor-part-2",
    "improving-pelvic-floor-control-and-ease", "simple-floor-clock",
    "drifting-simpler-turns-twists", "strength-without-excess",
    "calming-the-nervous-system-bell-hand", "length-without-effort",
    "buttocks-organizing-spine", "agile-hips-knees-feet",
    "power-of-one-foot", "one-two-bell-hands-feet",
    "ultimate-self-hug", "relaxing-your-neck-and-jaw",
    "softening-jaw-shoulders-chest", "easing-jaw-neck-shoulders",
    "tongue-and-spine", "easing-jaw-neck-short-edit",
    "chanukia-candle-holder", "beard-pull-pecking-chanukia",
    "arms-skeleton-neck-jaw-eyes", "freeing-neck-crawling-rolling",
    "easier-sitting-workshop-2-lesson-1", "pelvic-clock",
    "shoulder-clock-hip-intro", "hip-clock",
    "connecting-arms-legs-advanced", "connecting-legs-chest",
    "nodding-lengthening-heels", "rolling-and-hips-like-baby",
    "side-lying-twisting-rolling-recap", "rolling-with-length",
    "reaching-rolling-ribs", "rolling-to-sitting",
    "dynamic-diagonal-lengthening", "moving-head-legs-backward",
    "quiet-head-twisting-spine", "eyes-organize-body",
    "self-hug-embracing-differences", "improving-rotation-embracing-differences",
    "dynamic-core-lengthens-spine", "getting-free-with-bell-hand",
    "arms-skeleton-freeing-shoulders-neck", "driving-dynamic-sitting-2-chair",
    "effortless-legs-hips", "rotating-interlacing-hands-palms-overhead",
    "fundamental-properties-of-movement", "connecting-shoulders-hips-1",
    "3d-shoulder-hip-circles", "sitting-turning-length",
    "spine-chain-shoulder-girdle", "essence-whole-body-arms",
    "anti-gravity-lesson", "folding-foundation-feet",
    "supple-ankles-shifting-pelvis", "turning-spacious-center",
    "freeing-your-torso", "lifting-up-and-through",
    "freeing-shoulders-rolling-arms", "side-bent-stepping-down",
    "intro-to-lesson-6", "easier-sitting-workshop-1-talk-1",
    "easier-sitting-workshop-3-talk-2", "interview-mindbodyradio-nick-strauss-klein",
    "feldenkrais-for-self-empowerment", "intro-to-lesson-5",
    "intro-to-lesson-2", "intro-to-lesson-3-and-4",
    "intro-to-lesson-1", "what-action-is-good",
    "connecting-shoulders-hips-2", "softening-ribs",
    "simple-self-hug", "lblb-psoas-to-improve-walking",
    "spatial-relationships-coordinated-action", "gathering-spine-stepping-down",
    "making-peace", "advanced-twisting-1",
    "fundamentals-healthy-back", "hips-proximal-distal",
    "legs-free-as-babys", "more-precise-hips-spine",
    "your-navigational-pelvis", "breathing-floating-ribs-sternum",
    "essence-your-navigational-sternum-and-pelvis", "head-under-gap-full-length",
    "reaching-reorganized-eeao", "long-belly-strong-back-intro",
    "long-belly-strong-back-full-length", "lbsb-discussion-1",
    "lbsb-discussion-2", "reaching-distinctions-flex-ext",
    "head-under-gap-workshop-version", "essence-flexors-extensors-hips-waist",
    "lblb-psoas-to-lengthen-lighten-leg", "refining-shoulders-hips",
    "hip-shoulder-diagonals-walking", "generalize-your-skills",
    "sliding-sternum-comfortable-twisting", "pelvis-lifting-rotating-axis-1",
    "pelvis-lifting-rotating-axis-2", "imaging-spinal-rotation",
    "finding-using-spinal-bias-step-down", "your-naturally-asymmetrical-spine",
    "lamprey", "spine-chain-bias",
    "arms-like-skeleton-bias", "advanced-twisting-2",
    "stepping-down-with-bias",
]

# Document IDs of the 8 free lessons currently in source 4
OLD_FREE_DOC_IDS = [
    "0998e701-4ac5-471d-9bc7-948d3baacc58",  # Breath, Belly, Back, and Hips
    "3c2cd384-363f-4861-8c84-ce2a25eff572",  # Calming Your Nervous System
    "cf787d50-2757-4e7e-bc16-25582e9dc810",  # Chanukia
    "995dffa6-934e-4af6-9867-0c60b650d1d0",  # Folding, Foundation, and Feet
    "c0d50725-fc4a-4e7e-ab31-42a30ac70e07",  # Legs as Free as a Baby's
    "4e4d98d7-7af4-47a9-9a75-76997968100e",  # More Precise Hips and Spine
    "abfddaf1-e837-4ed5-9d59-6e53056eb759",  # The Anti-Gravity Lesson
    "bbbf731f-bc74-462f-b7eb-fdfac69dc61b",  # The Hip Joints: Proximal Around Distal
]

BATCH_SIZE = 5
BATCH_DELAY_SECONDS = 15 * 60  # 15 minutes

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get_or_create_source(db: Database) -> int:
    """Create the FELDENKRAIS_PROJECT_FREE source if it doesn't exist. Returns source ID."""
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id FROM document_source WHERE name = %s", (NEW_SOURCE_NAME,))
        row = cursor.fetchone()
        if row:
            logger.info("Source %s already exists (id=%d)", NEW_SOURCE_NAME, row["id"])
            return row["id"]
        # Fix sequence if it's behind the max id
        cursor.execute("SELECT setval('document_source_id_seq', GREATEST(nextval('document_source_id_seq'), (SELECT MAX(id) FROM document_source) + 1) - 1)")
        cursor.execute(
            "INSERT INTO document_source (name, category_name, visible, public) VALUES (%s, %s, TRUE, TRUE) RETURNING id",
            (NEW_SOURCE_NAME, NEW_SOURCE_CATEGORY),
        )
        new_id = cursor.fetchone()["id"]
    logger.info("Created source %s (id=%d)", NEW_SOURCE_NAME, new_id)
    return new_id


def delete_old_free_lessons(db: Database):
    """Delete the old free lessons from the FELDENKRAIS_PROJECT source."""
    for doc_id in OLD_FREE_DOC_IDS:
        with db.get_cursor() as cursor:
            # Check if it still exists
            cursor.execute("SELECT id FROM document WHERE id = %s", (doc_id,))
            if not cursor.fetchone():
                logger.info("Already deleted: %s", doc_id)
                continue
            # Delete in dependency order
            cursor.execute("DELETE FROM lesson_embedding WHERE document_id = %s", (doc_id,))
            cursor.execute("DELETE FROM transcription_job WHERE document_id = %s", (doc_id,))
            cursor.execute("DELETE FROM analysis WHERE document_id = %s", (doc_id,))
            cursor.execute("DELETE FROM document WHERE id = %s", (doc_id,))
        logger.info("Deleted old document %s", doc_id)
    # Also clear any related cache entries
    with db.get_cursor() as cursor:
        for doc_id in OLD_FREE_DOC_IDS:
            cursor.execute("DELETE FROM cache WHERE key LIKE %s", (f"%{doc_id}%",))
    logger.info("Cleaned up old free lessons from source %d", OLD_SOURCE_ID)


def extract_audio_url(html_text: str):
    """Extract audio URL from Podlove player config."""
    pattern = r'"audio"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"'
    match = re.search(pattern, html_text)
    if match:
        url = match.group(1).replace(r"\\/", "/").replace("\\/", "/")
        return url
    return None


def scrape_free_lesson(url: str) -> dict | None:
    """Scrape a lesson page WITHOUT cookies. Returns info dict if free, None if locked."""
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()

    # Check for paywall text
    if "just for Patrons" in response.text or "just for Members" in response.text:
        return None

    audio_url = extract_audio_url(response.text)
    if not audio_url:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    # Extract title
    h1 = soup.find("h1", class_="entry-title")
    if h1:
        title = h1.get_text(strip=True)
        # Clean up title suffixes
        title = re.sub(r'\s*-\s*Feldenkrais Project\s*$', '', title)
        title = re.sub(r'\s*\(Patrons\)\s*$', '', title)
    else:
        title = soup.title.string.strip() if soup.title else "Untitled"

    parsed = urlparse(audio_url)
    filename = unquote(Path(parsed.path).name)
    filename = re.sub(r'[^\w\-_.]', '_', filename)

    return {
        "url": url,
        "title": title,
        "audio_url": audio_url,
        "filename": filename,
    }


def identify_free_lessons() -> list[dict]:
    """Check each lesson page WITHOUT auth to identify truly free ones."""
    free_lessons = []
    total = len(ALL_LESSON_SLUGS)
    for i, slug in enumerate(ALL_LESSON_SLUGS, 1):
        url = f"https://feldenkraisproject.com/lesson/{slug}/"
        try:
            result = scrape_free_lesson(url)
            if result:
                free_lessons.append({"slug": slug, **result})
                logger.info("[%d/%d] FREE: %s", i, total, result["title"])
            else:
                logger.debug("[%d/%d] locked: %s", i, total, slug)
        except Exception as e:
            logger.warning("[%d/%d] ERROR %s: %s", i, total, slug, e)
        # Be polite — 1 second between requests
        time.sleep(1)
    return free_lessons


def load_free_lessons(
    db_url: str,
    storage: Storage,
    source_id: int,
    free_lessons: list[dict],
    batch_size: int = BATCH_SIZE,
    batch_delay: int = BATCH_DELAY_SECONDS,
):
    """Download and stage free lessons in batches."""
    total = len(free_lessons)
    logger.info("Loading %d free lessons in batches of %d (delay: %ds)", total, batch_size, batch_delay)

    for batch_start in range(0, total, batch_size):
        batch = free_lessons[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        logger.info("=== Batch %d/%d (%d lessons) ===", batch_num, total_batches, len(batch))

        for lesson_info in batch:
            title = lesson_info["title"]
            url = lesson_info["url"]
            audio_url = lesson_info["audio_url"]
            filename = lesson_info["filename"]

            # Fresh DB connection per lesson (Neon drops idle connections during downloads)
            db = Database(db_url)
            db.initialize()
            already_exists = db.document_exists(title, url)
            db.close()

            if already_exists:
                logger.info("SKIP (exists): %s", title)
                continue

            try:
                logger.info("Downloading: %s", filename)
                response = requests.get(audio_url, headers={"User-Agent": USER_AGENT}, timeout=300)
                response.raise_for_status()
                content = response.content

                # Stage to S3
                staging_key = f"staging/{filename}"
                sidecar_key = f"{staging_key}.json"
                sidecar = {
                    "title": title,
                    "source_url": url,
                    "source_id": source_id,
                    "type_id": TYPE_AUDIO,
                    "tags": [],
                    "scraper_metadata": {
                        "audio_url": audio_url,
                        "original_filename": filename,
                        "file_size": len(content),
                        "source": "feldenkraisproject.com",
                    },
                }
                storage.put_object(sidecar_key, json.dumps(sidecar, indent=2).encode())
                storage.put_object(staging_key, content)
                logger.info("STAGED: %s (%.1f MB)", title, len(content) / 1024 / 1024)
                time.sleep(2)

            except Exception as e:
                logger.error("FAILED: %s — %s", title, e)

        # Wait between batches (except after the last one)
        if batch_start + batch_size < total:
            logger.info("Batch %d complete. Waiting %d minutes...", batch_num, batch_delay // 60)
            time.sleep(batch_delay)

    logger.info("=== All %d batches complete ===", (total + batch_size - 1) // batch_size)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Load free FP lessons")
    parser.add_argument("--dry-run", action="store_true", help="Identify free lessons only, no DB changes")
    parser.add_argument("--skip-delete", action="store_true", help="Don't delete old lessons")
    parser.add_argument("--free-list", help="Path to cached free lessons JSON (skip identification)")
    args = parser.parse_args()

    db = Database(os.environ["DATABASE_URL"])
    db.initialize()

    bucket = os.environ.get("KB_BUCKET")

    if args.dry_run:
        # Just scan and list free lessons — no DB changes
        if args.free_list:
            with open(args.free_list) as f:
                free_lessons = json.load(f)
        else:
            logger.info("Scanning %d lesson pages (no cookies — only truly free)...", len(ALL_LESSON_SLUGS))
            free_lessons = identify_free_lessons()
            cache_path = Path(__file__).parent.parent.parent / "data" / "fp_free_lessons.json"
            with open(cache_path, "w") as f:
                json.dump(free_lessons, f, indent=2)
            logger.info("Saved to %s", cache_path)

        logger.info("\n=== %d FREE LESSONS ===", len(free_lessons))
        for i, lesson in enumerate(free_lessons, 1):
            print(f"{i:>3}. {lesson['title']}")
            print(f"     {lesson['url']}")
        return

    # Full run
    if not bucket:
        logger.error("KB_BUCKET env var required for staging")
        sys.exit(1)

    # Step 1: Create new source
    source_id = get_or_create_source(db)

    # Step 2: Delete old free lessons
    if not args.skip_delete:
        delete_old_free_lessons(db)

    # Step 3: Identify free lessons
    if args.free_list:
        with open(args.free_list) as f:
            free_lessons = json.load(f)
        logger.info("Loaded %d free lessons from %s", len(free_lessons), args.free_list)
    else:
        logger.info("Scanning %d lesson pages (no cookies — only truly free)...", len(ALL_LESSON_SLUGS))
        free_lessons = identify_free_lessons()
        cache_path = Path(__file__).parent.parent.parent / "data" / "fp_free_lessons.json"
        with open(cache_path, "w") as f:
            json.dump(free_lessons, f, indent=2)
        logger.info("Saved %d free lessons to %s", len(free_lessons), cache_path)

    logger.info("Found %d free lessons to load", len(free_lessons))

    # Step 4: Stage in batches
    storage = Storage(bucket)
    load_free_lessons(os.environ["DATABASE_URL"], storage, source_id, free_lessons)


if __name__ == "__main__":
    main()
