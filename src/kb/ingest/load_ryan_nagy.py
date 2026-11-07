#!/usr/bin/env python3
"""
Load Ryan Nagy's free Feldenkrais lessons (ryannagy.com) into a RYAN_NAGY source.

The free-MP3 category on ryannagy.com is a WordPress archive of posts, each with a
wp-audio-shortcode player pointing at an MP3 on S3. The lesson list below was taken
from that archive on 2026-09-13; podcast interviews and short quote clips from the
same category are deliberately left out.

1. Creates the RYAN_NAGY source (if not exists)
2. Downloads each MP3 and stages it to S3 with a sidecar, for the loader Lambda

Usage:
    .venv/bin/python src/kb/ingest/load_ryan_nagy.py            # stage everything not yet loaded
    .venv/bin/python src/kb/ingest/load_ryan_nagy.py --dry-run  # list what would be staged
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from database import Database
from storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SOURCE_NAME = "RYAN_NAGY"
SOURCE_CATEGORY = "Ryan Nagy (Emergent Somatics)"
TYPE_AUDIO = 2
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ARCHIVE_URL = "https://www.ryannagy.com/feldenkrais/free-feldenkrais-mp3/"

# (title, post url, audio url, staging filename)
LESSONS = [
    ("Body Scan: Reset Your Focus and Connect (Basic Orientation)",
     "https://www.ryannagy.com/2015/body-scan-reset-your-focus-and-connect/",
     "https://s3.amazonaws.com/psychconfs/Basic-Orientation-With-Intro.mp3",
     "nagy_basic_orientation.mp3"),
    ("Breathing In All Directions",
     "https://www.ryannagy.com/2017/breathing-in-all-directions-a-free-feldenkrais-session/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/shorts-lying/BreathingAllDirWeld.mp3",
     "nagy_breathing_all_directions.mp3"),
    ("Imaginary Ball In Front Of The Eyes",
     "https://www.ryannagy.com/2017/imaginary-ball-eyes-feldenkrais/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/eyes/BallInFrontEyeShorter.mp3",
     "nagy_ball_in_front_of_eyes.mp3"),
    ("Legs and Arms in Circles (Alexander Yanai 61)",
     "https://www.ryannagy.com/2018/favorite-alexander-yanai-ay-61/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/yanai/AY61LegsArmsInCircles.mp3",
     "nagy_ay61_legs_arms_circles.mp3"),
    ("Seeing Your Heels (Alexander Yanai 2)",
     "https://www.ryannagy.com/2019/alexander-yanai-3-with-video/",
     "https://ryannagy.s3.amazonaws.com/easyfeldenkrais/yanai/AY2SeeingTheHeels.mp3",
     "nagy_ay2_seeing_the_heels.mp3"),
    ("Bear Walking: Four Points, Standing Version (Esalen)",
     "https://www.ryannagy.com/2019/bear-walking-feldenkrais-at-esalan/",
     "https://s3.amazonaws.com/feldnkrais/classics/volume2/Classic_Four_Points_Standing_Version.mp3",
     "nagy_bear_walking_four_points.mp3"),
    ("A Pretzel In Sitting (Alexander Yanai 150, Part 1)",
     "https://www.ryannagy.com/2019/feldenkrais-mp3-pretzel-sitting/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/yanai/AY150PretzelSittingPart1.mp3",
     "nagy_ay150_pretzel_sitting.mp3"),
    ("Rhythmic Tapping (Esalen)",
     "https://www.ryannagy.com/2019/rhythmic-tapping-feldenkrais-at-esalen/",
     "https://s3.amazonaws.com/feldnkrais/classics/volume2/2.+Rhythmic_Tapping.mp3",
     "nagy_rhythmic_tapping.mp3"),
    ("Circles in Standing (Alexander Yanai 88)",
     "https://www.ryannagy.com/2020/happy-new-year-2020/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/yanai/AY88CirclesStanding.mp3",
     "nagy_ay88_circles_standing.mp3"),
    ("Relaxing Jaw, Face and Tongue (Alexander Yanai 451)",
     "https://www.ryannagy.com/2021/feldenkrais-session-jaw-face-tongue/",
     "https://ryannagy.com/AY451HeadPelvisTongue.mp3",
     "nagy_ay451_head_pelvis_tongue.mp3"),
    ("Covering The Eyes",
     "https://www.ryannagy.com/2022/feldenkrais-for-the-eyes/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/eyes/CoveringTheEyes.mp3",
     "nagy_covering_the_eyes.mp3"),
    ("What Is Flexion: Pain Relief Step-By-Step",
     "https://www.ryannagy.com/2023/feldenkrais-pain-relief-session/",
     "https://s3.amazonaws.com/ryannagy/easyfeldenkrais/v1/6.WhatIsFlexion.mp3",
     "nagy_what_is_flexion.mp3"),
]
# The 2024 post "bathe your eyes in healing darkness" serves the same file
# (identical ETag) as "Covering The Eyes" above, so it is not listed twice.


def get_or_create_source(db: Database) -> int:
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id FROM document_source WHERE name = %s", (SOURCE_NAME,))
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            "SELECT setval('document_source_id_seq', GREATEST(nextval('document_source_id_seq'), "
            "(SELECT MAX(id) FROM document_source) + 1) - 1)"
        )
        cursor.execute(
            "INSERT INTO document_source (name, category_name, visible, public) "
            "VALUES (%s, %s, TRUE, TRUE) RETURNING id",
            (SOURCE_NAME, SOURCE_CATEGORY),
        )
        new_id = cursor.fetchone()["id"]
    logger.info("Created source %s (id=%d)", SOURCE_NAME, new_id)
    return new_id


def main():
    dry_run = "--dry-run" in sys.argv
    db_url = os.environ.get("DATABASE_URL")
    bucket = os.environ.get("KB_BUCKET")
    if not db_url or not bucket:
        sys.exit("DATABASE_URL and KB_BUCKET are required")

    db = Database(db_url)
    db.initialize()
    source_id = None if dry_run else get_or_create_source(db)
    pending = [l for l in LESSONS if not db.document_exists(l[0], l[1])]
    db.close()

    logger.info("%d lessons listed, %d not yet loaded", len(LESSONS), len(pending))
    for title, url, audio_url, filename in pending:
        logger.info("  %s  <- %s", title, audio_url)
    if dry_run:
        return

    storage = Storage(bucket)
    for title, url, audio_url, filename in pending:
        try:
            response = requests.get(audio_url, headers={"User-Agent": USER_AGENT}, timeout=600)
            response.raise_for_status()
            content = response.content
            staging_key = f"staging/{filename}"
            sidecar = {
                "title": title,
                "source_url": url,
                "source_id": source_id,
                "type_id": TYPE_AUDIO,
                "tags": ["ryan-nagy", "free"],
                "scraper_metadata": {
                    "audio_url": audio_url,
                    "original_filename": audio_url.rsplit("/", 1)[-1],
                    "file_size": len(content),
                    "source": "ryannagy.com",
                    "archive_url": ARCHIVE_URL,
                    "teacher": "Ryan Nagy",
                },
            }
            storage.put_object(f"{staging_key}.json", json.dumps(sidecar, indent=2).encode())
            storage.put_object(staging_key, content, content_type="audio/mpeg")
            logger.info("STAGED: %s (%.1f MB)", title, len(content) / 1024 / 1024)
            time.sleep(2)
        except Exception as e:
            logger.error("FAILED: %s - %s", title, e)


if __name__ == "__main__":
    main()
