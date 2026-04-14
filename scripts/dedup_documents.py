"""
Delete duplicate documents based on audio_fingerprint.

For each group of documents sharing a fingerprint:
- Count how many documents each source_id has OVERALL in the database
- Keep the document whose source_id has the most documents overall (bigger collection)
- If tied, keep the oldest (earliest created_at)
- Delete the rest: related rows in child tables + S3 files + document row
"""

import json
import os
import sys

import boto3
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
S3_BUCKET = os.environ.get("KB_BUCKET", "feldenkrais-prod")

DRY_RUN = "--dry-run" in sys.argv

s3 = boto3.client("s3")

# Tables with document_id FK (in deletion order to avoid FK violations)
CHILD_TABLES = [
    "lesson_embedding",
    "lesson_concept",
    "analysis",
    "document_analysis_state",
    "document_analysis_gate",
    "pipeline_log",
    "transcription_job",
    "lesson_group_member",
]


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Get source_id document counts (for determining "bigger collection")
    cur.execute("SELECT source_id, count(*) FROM document GROUP BY source_id")
    source_counts = dict(cur.fetchall())
    print("Source document counts:")
    # Get source names
    cur.execute("SELECT id, name FROM document_source ORDER BY id")
    source_names = dict(cur.fetchall())
    for sid, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"  {source_names.get(sid, sid)}: {count}")

    # Find duplicate groups
    cur.execute("""
        SELECT audio_fingerprint, array_agg(id ORDER BY created_at) as doc_ids
        FROM document
        WHERE audio_fingerprint IS NOT NULL
        GROUP BY audio_fingerprint
        HAVING count(*) > 1
        ORDER BY count(*) DESC
    """)
    dup_groups = cur.fetchall()
    print(f"\nDuplicate groups: {len(dup_groups)}")

    if not dup_groups:
        print("No duplicates found!")
        conn.close()
        return

    total_to_delete = 0
    deletions = []

    for fingerprint, doc_ids in dup_groups:
        # Get doc details
        cur.execute("""
            SELECT id, title, source_id, s3_key, created_at
            FROM document WHERE id = ANY(%s)
            ORDER BY created_at
        """, (doc_ids,))
        docs = cur.fetchall()

        # Pick keeper: source with most docs overall, then oldest
        docs_ranked = sorted(docs, key=lambda d: (-source_counts.get(d[2], 0), d[4]))
        keeper = docs_ranked[0]
        to_delete = docs_ranked[1:]

        print(f"\n  Fingerprint: {fingerprint[:20]}...")
        print(f"    KEEP: {keeper[1][:60]} (source={source_names.get(keeper[2], keeper[2])}, id={keeper[0]})")
        for d in to_delete:
            print(f"    DEL:  {d[1][:60]} (source={source_names.get(d[2], d[2])}, id={d[0]})")
            deletions.append({
                "doc_id": str(d[0]),
                "title": d[1],
                "source": source_names.get(d[2], str(d[2])),
                "s3_key": d[3],
                "keeper_id": str(keeper[0]),
                "keeper_title": keeper[1],
            })

        total_to_delete += len(to_delete)

    print(f"\nTotal documents to delete: {total_to_delete}")

    if DRY_RUN:
        print("\n*** DRY RUN — no changes made ***")
        conn.close()
        return

    # Confirm
    answer = input(f"\nDelete {total_to_delete} duplicate documents? (yes/no): ")
    if answer.strip().lower() != "yes":
        print("Aborted.")
        conn.close()
        return

    # Delete
    deleted = 0
    s3_deleted = 0
    s3_errors = 0

    for d in deletions:
        doc_id = d["doc_id"]
        s3_key = d["s3_key"]

        # Delete from child tables
        for table in CHILD_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE document_id = %s", (doc_id,))
            if cur.rowcount > 0:
                print(f"    Deleted {cur.rowcount} rows from {table}")

        # Delete document row
        cur.execute("DELETE FROM document WHERE id = %s", (doc_id,))
        deleted += 1

        # Delete S3 file
        if s3_key and s3_key.strip():
            try:
                s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
                # Also try to delete sidecar
                s3.delete_object(Bucket=S3_BUCKET, Key=s3_key + ".json")
                s3_deleted += 1
            except Exception as e:
                print(f"    S3 delete error for {s3_key}: {e}")
                s3_errors += 1

        if deleted % 10 == 0:
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone. Deleted {deleted} document rows, {s3_deleted} S3 files, {s3_errors} S3 errors")

    # Save deletion log
    log_path = "scripts/dedup_log.json"
    with open(log_path, "w") as f:
        json.dump(deletions, f, indent=2)
    print(f"Deletion log saved to {log_path}")


if __name__ == "__main__":
    main()
