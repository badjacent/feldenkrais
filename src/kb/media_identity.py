"""The identity of a media file behind a URL, for spotting files already ingested.

Some hosts publish media as signed URLs (S3-style presigned links, as the Eumov
audio on DigitalOcean Spaces does). The query string carries a key, an expiry
and a signature that change every time the link is issued, so the same file
arrives under a new URL on every Atlas crawl. Comparing full URLs then treats
it as new each time, and it is downloaded and transcribed again.

``media_identity`` drops the query string when it is a signature and keeps it
otherwise, since an ordinary query can select a different file.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Query parameters that only authorize access to a file, never select it.
SIGNATURE_PARAMS = frozenset({
    "awsaccesskeyid", "signature", "expires",
    "x-amz-algorithm", "x-amz-credential", "x-amz-date", "x-amz-expires",
    "x-amz-signature", "x-amz-signedheaders", "x-amz-security-token",
    "x-goog-algorithm", "x-goog-credential", "x-goog-date", "x-goog-expires",
    "x-goog-signature", "x-goog-signedheaders",
})


def is_signed(url: str) -> bool:
    """True when the URL's query string carries an access signature."""
    keys = {key.casefold() for key, _ in parse_qsl(urlsplit(url or "").query, keep_blank_values=True)}
    return bool(keys & SIGNATURE_PARAMS)


def media_identity(url: str) -> str:
    """The URL with any signature removed and the host lower-cased."""
    parts = urlsplit((url or "").strip())
    query = "" if is_signed(url) else parts.query
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, query, ""))
