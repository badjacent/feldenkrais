"""
Consolidate fragmented document sources into single sources with groups.

1. Merge FELDENKRAIS_PROJECT_FREE, FELDENKRAIS_PROJECT_FREE_EXTRA, FELDENKRAIS_PROJECT_TALKS
   into FELDENKRAIS_PROJECT. Create groups: "FP Free", "FP Talks".
2. Merge OPENATM_REFERENCE into OPENATM. Create group: "OpenATM Reference".

Run BEFORE migrate_gates_to_groups.py so auto-groups are created correctly.

Usage:
    .venv/bin/python scripts/consolidate_sources.py [--dry-run]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor

# Source merges: (target_source_id, [(donor_source_id, group_name), ...])
MERGES = [
    (4, [  # FELDENKRAIS_PROJECT
        (12, "FP Free"),          # FELDENKRAIS_PROJECT_FREE
        (15, "FP Free"),          # FELDENKRAIS_PROJECT_FREE_EXTRA (same group as FREE)
        (14, "FP Talks"),         # FELDENKRAIS_PROJECT_TALKS
    ]),
    (6, [  # OPENATM
        (13, "OpenATM Reference"),  # OPENATM_REFERENCE
    ]),
]


def consolidate(db_url: str, dry_run: bool = False):
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Ensure lesson_group tables exist (may run before gates migration)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lesson_group (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                auto_source_id INTEGER REFERENCES document_source(id),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS lesson_group_member (
                group_id INTEGER NOT NULL REFERENCES lesson_group(id) ON DELETE CASCADE,
                document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
                PRIMARY KEY (group_id, document_id)
            );
        """)

        for target_id, donors in MERGES:
            # Get target source name
            cur.execute("SELECT name FROM document_source WHERE id = %s", (target_id,))
            target_name = cur.fetchone()["name"]
            print(f"\n=== Merging into {target_name} (id={target_id}) ===")

            # Collect doc IDs per group before moving
            group_docs = {}  # group_name -> [doc_ids]
            for donor_id, group_name in donors:
                cur.execute("SELECT name FROM document_source WHERE id = %s", (donor_id,))
                row = cur.fetchone()
                if not row:
                    print(f"  WARNING: source id={donor_id} not found, skipping")
                    continue
                donor_name = row["name"]

                cur.execute(
                    "SELECT id FROM document WHERE source_id = %s",
                    (donor_id,),
                )
                doc_ids = [str(r["id"]) for r in cur.fetchall()]
                print(f"  {donor_name} (id={donor_id}): {len(doc_ids)} docs → group '{group_name}'")

                group_docs.setdefault(group_name, []).extend(doc_ids)

                # Move documents to target source
                cur.execute(
                    "UPDATE document SET source_id = %s WHERE source_id = %s",
                    (target_id, donor_id),
                )

            # Create lesson groups and populate members
            for group_name, doc_ids in group_docs.items():
                if not doc_ids:
                    continue
                cur.execute("""
                    INSERT INTO lesson_group (name)
                    VALUES (%s)
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (group_name,))
                group_id = cur.fetchone()["id"]
                for doc_id in doc_ids:
                    cur.execute("""
                        INSERT INTO lesson_group_member (group_id, document_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (group_id, doc_id))
                print(f"  Created group '{group_name}' (id={group_id}) with {len(doc_ids)} members")

            # Delete empty donor sources (only if no docs remain)
            for donor_id, _ in donors:
                cur.execute("SELECT COUNT(*) as cnt FROM document WHERE source_id = %s", (donor_id,))
                remaining = cur.fetchone()["cnt"]
                if remaining == 0:
                    # Check for foreign key refs in other tables
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM group_source_access WHERE source_id = %s",
                        (donor_id,),
                    )
                    gsa = cur.fetchone()["cnt"]
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM user_source_access WHERE source_id = %s",
                        (donor_id,),
                    )
                    usa = cur.fetchone()["cnt"]
                    if gsa > 0 or usa > 0:
                        print(f"  Source id={donor_id} has access entries, hiding instead of deleting")
                        cur.execute(
                            "UPDATE document_source SET visible = FALSE WHERE id = %s",
                            (donor_id,),
                        )
                    else:
                        # Clear any lesson_group auto_source_id references
                        cur.execute(
                            "UPDATE lesson_group SET auto_source_id = NULL WHERE auto_source_id = %s",
                            (donor_id,),
                        )
                        # Delete any empty auto-groups that were for this source
                        cur.execute("""
                            DELETE FROM lesson_group lg
                            WHERE NOT EXISTS (
                                SELECT 1 FROM lesson_group_member lgm WHERE lgm.group_id = lg.id
                            )
                            AND lg.name = (SELECT name FROM document_source WHERE id = %s)
                        """, (donor_id,))
                        cur.execute("DELETE FROM document_source WHERE id = %s", (donor_id,))
                        print(f"  Deleted empty source id={donor_id}")
                else:
                    print(f"  WARNING: source id={donor_id} still has {remaining} docs, not deleting")

        # Summary
        print("\n=== Final source counts ===")
        cur.execute("""
            SELECT ds.id, ds.name, COUNT(d.id) as docs
            FROM document_source ds
            LEFT JOIN document d ON d.source_id = ds.id
            GROUP BY ds.id, ds.name
            ORDER BY ds.name
        """)
        for r in cur.fetchall():
            print(f"  {r['id']:3d} | {r['name']:40s} | {r['docs']} docs")

        print("\n=== Lesson groups ===")
        cur.execute("""
            SELECT lg.name, COUNT(lgm.document_id) as members
            FROM lesson_group lg
            LEFT JOIN lesson_group_member lgm ON lgm.group_id = lg.id
            GROUP BY lg.id, lg.name
            ORDER BY lg.name
        """)
        for r in cur.fetchall():
            print(f"  {r['name']:30s} | {r['members']} members")

        if dry_run:
            print("\n[DRY RUN] Rolling back...")
            conn.rollback()
        else:
            print("\nCommitting...")
            conn.commit()
            print("Done!")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate document sources")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    consolidate(db_url, dry_run=args.dry_run)
