#!/usr/bin/env python3
"""Export Whisper transcription segments as sidecar JSON files for lesson timing."""

import json
import os
import sys

import psycopg2

# Title fragments (from DB) -> lesson filename (without extension)
TITLE_TO_FILENAME = {
    "What Is Good Posture?": "01_what_is_good_posture",
    "What Action Is Good?": "02_what_action_is_good",
    "Some Fundamental Properties of Movement": "03_fundamental_properties_of_movement",
    "Differentiation of Parts and Functions in Breathing": "04_differentiation_in_breathing",
    "Coordination of the Flexor Muscles and of the Extensors": "05_coordination_flexors_extensors",
    "Differentiation of Pelvic Movements by Means of an Imaginary Clock": "06_pelvic_clock",
    "Carriage of the Head Affects the State of the Musculature": "07_carriage_of_the_head",
    "Perfecting the Self-Image": "08_perfecting_the_self_image",
    "Spatial Relationships as a Means to Coordinated Action": "09_spatial_relationships",
    "Movement of the Eyes Organizes the Movement of the Body": "10_eyes_organize_body",
    "Generalize Your Skills": "11_generalize_your_skills",
    "Thinking and Breathing": "12_thinking_and_breathing",
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "lessons", "atm_book")


def match_title(db_title: str) -> str | None:
    """Match a DB document title to a lesson filename."""
    for fragment, filename in TITLE_TO_FILENAME.items():
        if fragment.lower() in db_title.lower():
            return filename
    return None


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Try .env file
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("DATABASE_URL="):
                        db_url = line.strip().split("=", 1)[1]
                        break
    if not db_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Get all ATM book documents (source_id=1) with their transcription segments
    cur.execute('''
        SELECT d.title, a.data
        FROM document d
        JOIN analysis a ON d.id = a.document_id AND a.type_id = 1
        WHERE d.source_id = 1
        ORDER BY d.created_at
    ''')

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    exported = 0
    for title, data in cur.fetchall():
        if isinstance(data, str):
            data = json.loads(data)

        filename = match_title(title)
        if not filename:
            print(f"  SKIP: No filename match for '{title}'")
            continue

        segments = data.get("segments", [])
        if not segments:
            print(f"  SKIP: No segments for '{title}'")
            continue

        # Write only the fields we need: id, start, end, text
        clean_segments = []
        for seg in segments:
            clean_segments.append({
                "id": seg.get("id"),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "text": seg.get("text", ""),
            })

        output_path = os.path.join(OUTPUT_DIR, f"{filename}.whisper.json")
        with open(output_path, "w") as f:
            json.dump(clean_segments, f, indent=2)

        duration = clean_segments[-1]["end"] if clean_segments else 0
        print(f"  OK: {filename}.whisper.json ({len(clean_segments)} segments, {duration:.0f}s)")
        exported += 1

    conn.close()
    print(f"\nExported {exported} whisper files to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
