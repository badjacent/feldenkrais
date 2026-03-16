"""
Migration: document_analysis_gate → group-level gates + document_analysis_state.

This script:
1. Creates new tables: lesson_group, lesson_group_member, group_analysis_gate, document_analysis_state
2. Auto-creates lesson_groups from document_source (for sources with transcribed docs)
3. Populates lesson_group_member from document.source_id
4. Copies completion state (completed_version, completed_at, upstream_hash, stale, error)
   from document_analysis_gate → document_analysis_state
5. Drops document_analysis_gate (and its indexes)

All group gates start at permitted=FALSE (user wants to start fresh).

Usage:
    .venv/bin/python scripts/migrate_gates_to_groups.py [--dry-run]
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


def migrate(db_url: str, dry_run: bool = False):
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. Create new tables
        print("Creating new tables...")
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

            CREATE TABLE IF NOT EXISTS group_analysis_gate (
                group_id INTEGER NOT NULL REFERENCES lesson_group(id) ON DELETE CASCADE,
                step_name TEXT NOT NULL REFERENCES analysis_step(name),
                permitted BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (group_id, step_name)
            );

            CREATE TABLE IF NOT EXISTS document_analysis_state (
                document_id UUID NOT NULL REFERENCES document(id) ON DELETE CASCADE,
                step_name TEXT NOT NULL REFERENCES analysis_step(name),
                completed_version INTEGER,
                completed_at TIMESTAMPTZ,
                upstream_hash TEXT,
                stale BOOLEAN NOT NULL DEFAULT FALSE,
                error TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (document_id, step_name)
            );
        """)

        # Create indexes
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_lgm_document ON lesson_group_member (document_id)",
            "CREATE INDEX IF NOT EXISTS idx_gag_permitted ON group_analysis_gate (step_name) WHERE permitted = TRUE",
            "CREATE INDEX IF NOT EXISTS idx_das_document ON document_analysis_state (document_id)",
            "CREATE INDEX IF NOT EXISTS idx_das_step ON document_analysis_state (step_name)",
        ]:
            cur.execute(idx)

        # 2. Auto-create lesson_groups from document_source
        print("Creating lesson groups from document sources...")
        cur.execute("""
            SELECT DISTINCT ds.id, ds.name
            FROM document_source ds
            JOIN document d ON d.source_id = ds.id
            JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
        """)
        sources = cur.fetchall()
        print(f"  Found {len(sources)} sources with transcribed documents")

        for src in sources:
            cur.execute("""
                INSERT INTO lesson_group (name, auto_source_id)
                VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE SET auto_source_id = EXCLUDED.auto_source_id
                RETURNING id
            """, (src["name"], src["id"]))
            group_id = cur.fetchone()["id"]

            # 3. Populate members
            cur.execute("""
                INSERT INTO lesson_group_member (group_id, document_id)
                SELECT %s, d.id
                FROM document d
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE d.source_id = %s
                ON CONFLICT DO NOTHING
            """, (group_id, src["id"]))
            print(f"  {src['name']}: group_id={group_id}, members added")

        # 4. Copy completion state from document_analysis_gate → document_analysis_state
        print("Migrating completion state...")
        cur.execute("""
            SELECT COUNT(*) as cnt FROM information_schema.tables
            WHERE table_name = 'document_analysis_gate'
        """)
        if cur.fetchone()["cnt"] > 0:
            cur.execute("""
                INSERT INTO document_analysis_state
                    (document_id, step_name, completed_version, completed_at, upstream_hash, stale, error, updated_at)
                SELECT document_id, step_name, completed_version, completed_at, upstream_hash, stale, error, updated_at
                FROM document_analysis_gate
                WHERE completed_version IS NOT NULL OR error IS NOT NULL
                ON CONFLICT (document_id, step_name) DO NOTHING
            """)
            migrated = cur.rowcount
            print(f"  Migrated {migrated} state rows")

            # 5. Drop old table and indexes
            print("Dropping document_analysis_gate...")
            cur.execute("DROP INDEX IF EXISTS idx_dag_document")
            cur.execute("DROP INDEX IF EXISTS idx_dag_step")
            cur.execute("DROP INDEX IF EXISTS idx_dag_runnable")
            cur.execute("DROP TABLE IF EXISTS document_analysis_gate")
        else:
            print("  document_analysis_gate not found, skipping migration")

        # Summary
        cur.execute("SELECT COUNT(*) as cnt FROM lesson_group")
        group_count = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM lesson_group_member")
        member_count = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM document_analysis_state")
        state_count = cur.fetchone()["cnt"]

        print(f"\nSummary:")
        print(f"  Groups: {group_count}")
        print(f"  Members: {member_count}")
        print(f"  State rows: {state_count}")
        print(f"  Group gates: all start at permitted=FALSE")

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
    parser = argparse.ArgumentParser(description="Migrate gates to groups")
    parser.add_argument("--dry-run", action="store_true", help="Preview without committing")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    migrate(db_url, dry_run=args.dry_run)
