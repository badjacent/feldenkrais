#!/usr/bin/env python3
"""Append bounded, secret-free queue and throughput evidence for tuning."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "var/reports/throughput-snapshots.jsonl"
TABLES = ("youtube_queue", "atlas_media_queue", "transcription_job")


def collect() -> dict:
    result = {"at": datetime.now(UTC).isoformat(), "queues": {}}
    connection = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            for table in TABLES:
                cursor.execute(f"SELECT status, count(*) FROM {table} GROUP BY status")
                row = {status: count for status, count in cursor.fetchall()}
                for label, interval in (("completed_1h", "1 hour"), ("completed_24h", "24 hours")):
                    cursor.execute(
                        f"SELECT count(*) FROM {table} WHERE status='completed' "
                        "AND updated_at >= NOW() - %s::interval", (interval,),
                    )
                    row[label] = cursor.fetchone()[0]
                cursor.execute(
                    f"SELECT extract(epoch FROM (NOW() - min(created_at))) / 3600 "
                    f"FROM {table} WHERE status IN ('pending', 'local_pending')"
                )
                age = cursor.fetchone()[0]
                row["oldest_pending_hours"] = round(float(age), 2) if age is not None else None
                result["queues"][table] = row
    finally:
        connection.close()
    return result


def append(snapshot: dict, *, retain: int = 1_344) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    existing = OUTPUT.read_text().splitlines()[-(retain - 1):] if OUTPUT.is_file() else []
    existing.append(json.dumps(snapshot, sort_keys=True))
    OUTPUT.write_text("\n".join(existing) + "\n")


if __name__ == "__main__":
    snapshot = collect()
    append(snapshot)
    print(json.dumps(snapshot, sort_keys=True))
