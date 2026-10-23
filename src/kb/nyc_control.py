"""Expedited crawl-check contract: the site → S3 → the Mini.

The site must not poll Neon to wake the crawler and must never shell into the
Mini. Instead it drops one small request object under a control prefix; the Mini
polls that prefix every five minutes, and moves the object to ``completed/``,
``failed/``, ``rejected/``, or ``dead/`` when it acts. Durable status lands in
``atlas_nyc.nyc_crawl_request``, which the console reads when that table exists.

This module owns the wire format only — the payload, the key, and which statuses
count as finished. The caller supplies the S3 client and bucket, so there is one
credential path in the app rather than two.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

# Version-1 payload contract, fixed by docs/NYC_CRAWLER_ADMIN_SPEC.md.
PAYLOAD_VERSION = 1
ACTION_CRAWL_PROVIDER = "crawl_provider"

REQUEST_PREFIX = "control/atlas-nyc/requests/"

# SSE-S3 with AES-256, so the object is encrypted at rest without the site and
# the Mini sharing a key.
SERVER_SIDE_ENCRYPTION = "AES256"

# Statuses the Mini treats as finished, from its nyc_crawl_request vocabulary
# (pending, running, succeeded, failed, dead, rejected). "failed" is retryable
# and the request object stays in place, so it is NOT terminal; "completed" is
# accepted as an alias for the completed/ result prefix. Anything unrecognised
# counts as in flight, which errs toward not sending a duplicate request.
TERMINAL_REQUEST_STATUSES = frozenset({"succeeded", "completed", "rejected", "dead"})


def build_request(provider_id: str, actor: str, *,
                  now: datetime | None = None,
                  request_id: str | None = None) -> tuple[str, dict]:
    """Return (object key, version-1 payload).

    ``provider_id`` must come from a server-loaded ``atlas_nyc.nyc_provider``
    row — never from the browser — and no URL or command is ever carried.
    """
    now = now or datetime.now(timezone.utc)
    request_id = request_id or str(uuid.uuid4())
    requested_at = now.isoformat()
    payload = {
        "version": PAYLOAD_VERSION,
        "action": ACTION_CRAWL_PROVIDER,
        "provider_id": str(provider_id),
        "requested_by": str(actor),
        "requested_at": requested_at,
        "request_id": request_id,
    }
    stamp = requested_at.replace(":", "").replace("+00:00", "Z")
    return f"{REQUEST_PREFIX}{stamp}-{request_id}.json", payload


def put_request(client, bucket: str, provider_id: str, actor: str, *,
                now: datetime | None = None) -> dict:
    """Write one expedited-check request. Returns the key and request id."""
    key, payload = build_request(provider_id, actor, now=now)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        ContentType="application/json",
        ServerSideEncryption=SERVER_SIDE_ENCRYPTION,
    )
    return {"key": key, "request_id": payload["request_id"],
            "requested_at": payload["requested_at"]}


def is_terminal(status: str | None) -> bool:
    return str(status or "").strip().lower() in TERMINAL_REQUEST_STATUSES
