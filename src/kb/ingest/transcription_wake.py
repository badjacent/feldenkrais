"""Wake signal for the Mini's local transcription worker.

New audio reaches transcription through the loader Lambda, which creates the
document row in Neon. Instead of the worker polling Neon every few minutes to
find out, the loader also writes an empty marker object to S3. The worker's
runner checks S3 first (no Neon query), removes the marker, and only then asks
Neon for work. Removing it before the query means a document created during a
run leaves a fresh marker for the next one. The worker writes the marker back
when it must run again anyway: failures still due a retry, a full batch, or a
run cut short by its time limit. The runner does the same after a crash.

The same pattern as the NYC requests job, which checks an S3 folder and only
connects to Neon when a request is there.

    python -m ingest.transcription_wake take   # exit 0 and remove the marker if present; 3 if absent
    python -m ingest.transcription_wake arm    # write the marker
"""

from __future__ import annotations

import os
import sys

MARKER_KEY = "control/transcription/pending"
EXIT_ABSENT = 3


def _client():
    import boto3

    return boto3.client("s3")


def _bucket(bucket: str | None) -> str:
    name = bucket or os.environ.get("KB_BUCKET")
    if not name:
        raise RuntimeError("KB_BUCKET is not set")
    return name


def arm(bucket: str | None = None, client=None) -> None:
    """Ask for a transcription run (overwrites any existing marker)."""
    (client or _client()).put_object(Bucket=_bucket(bucket), Key=MARKER_KEY, Body=b"")


def take(bucket: str | None = None, client=None) -> bool:
    """Remove the marker. True if it was there, False if not."""
    from botocore.exceptions import ClientError

    client = client or _client()
    name = _bucket(bucket)
    try:
        client.head_object(Bucket=name, Key=MARKER_KEY)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    client.delete_object(Bucket=name, Key=MARKER_KEY)
    return True


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "take":
        return 0 if take() else EXIT_ABSENT
    if command == "arm":
        arm()
        return 0
    print("usage: python -m ingest.transcription_wake take|arm", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
