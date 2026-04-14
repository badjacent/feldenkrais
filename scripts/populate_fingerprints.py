"""
Populate audio_fingerprint column for all documents with S3 keys.

Fingerprint = SHA256(first 100KB) + "_" + file_size
"""

import hashlib
import os
import sys

import boto3
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
S3_BUCKET = os.environ.get("KB_BUCKET", "feldenkrais-prod")

s3 = boto3.client("s3")


def compute_fingerprint(bucket: str, key: str) -> str | None:
    """Fetch first 100KB from S3 and compute fingerprint."""
    try:
        # Get file size via HEAD
        head = s3.head_object(Bucket=bucket, Key=key)
        file_size = head["ContentLength"]

        # Get first 100KB
        range_end = min(102400, file_size) - 1
        if range_end < 0:
            return None
        resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{range_end}")
        data = resp["Body"].read()

        sha = hashlib.sha256(data).hexdigest()
        return f"{sha}_{file_size}"
    except Exception as e:
        print(f"  ERROR fetching {key}: {e}")
        return None


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Get all docs without fingerprint that have s3_key
    cur.execute("""
        SELECT id, s3_key FROM document
        WHERE s3_key IS NOT NULL AND s3_key != ''
          AND audio_fingerprint IS NULL
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    print(f"Documents to fingerprint: {len(rows)}")

    updated = 0
    errors = 0
    for i, (doc_id, s3_key) in enumerate(rows):
        fp = compute_fingerprint(S3_BUCKET, s3_key)
        if fp:
            cur.execute(
                "UPDATE document SET audio_fingerprint = %s WHERE id = %s",
                (fp, str(doc_id)),
            )
            updated += 1
        else:
            errors += 1

        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  Progress: {i+1}/{len(rows)} (updated={updated}, errors={errors})")

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Updated: {updated}, Errors: {errors}")


if __name__ == "__main__":
    main()
