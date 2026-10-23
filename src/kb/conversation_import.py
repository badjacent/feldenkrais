"""Conversations about a lesson held in an outside LLM, saved back into its Q&A log.

A lesson's context export (``lesson_context``) carries an import token. The
token names the lesson, the account that exported it, the analysis profile and
one export, and is signed with the site's cookie secret, so whoever holds the
exported document can add conversations to that lesson, as that account, and to
nothing else, until it expires.

A conversation arrives one of two ways, both ending in ``store``:

- ``POST /api/conversations/import`` (``routes/conversations.py``);
- a file dropped under ``conversations/inbox/`` in the site bucket, which S3
  hands to the web Lambda (``handle_s3_event``).

Either way the body is the JSON form the export asks the outside LLM to produce,
or a plain-text copy of the chat with speaker labels. Exchanges are stored in
``lesson_chat_log`` beside in-app Q&A, marked with their origin, and every
submission is kept verbatim in ``external_conversation``. Sending a longer copy
of the same conversation replaces the exchanges saved from the shorter one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote_plus

TOKEN_PREFIX = "fkc1"
TOKEN_TTL_SECONDS = 90 * 24 * 3600
MAX_BYTES = 2_000_000
IMPORT_PATH = "/api/conversations/import"

S3_INBOX_PREFIX = "conversations/inbox/"
S3_IMPORTED_PREFIX = "conversations/imported/"
S3_REJECTED_PREFIX = "conversations/rejected/"

# The export's reference line. A user turn holding it is the export itself,
# pasted to start the chat; it is stored as a placeholder, not in full.
_EXPORT_MARKER = re.compile(r"Reference: lesson [0-9a-fA-F-]{36} · profile ")
EXPORT_PLACEHOLDER = "(the lesson context export)"

# The example turns in the export's description of the JSON form. A chat that
# quotes the export quotes this example too; it is never a conversation.
EXAMPLE_TURNS = [
    {"role": "user", "content": "<the user's message, word for word>"},
    {"role": "assistant", "content": "<your reply, word for word>"},
]

_TOKEN_RE = re.compile(rf"\b{TOKEN_PREFIX}\.[A-Za-z0-9_-]+\.[0-9a-f]{{64}}\b")

# Speaker labels that open a turn. Deliberately few: a pasted export holds a
# whole lesson text, and a looser set ("A:", "Q:") would split it into turns.
_USER_LABELS = {"user", "you", "human"}
_ASSISTANT_LABELS = {"assistant", "claude", "chatgpt", "gpt", "gemini", "ai"}
_NAMED_ASSISTANTS = {"claude": "Claude", "chatgpt": "ChatGPT", "gpt": "ChatGPT", "gemini": "Gemini"}
_LABEL = "|".join(sorted(_USER_LABELS | _ASSISTANT_LABELS, key=len, reverse=True))
# A label alone on its line ("## User", "**ChatGPT said:**") ...
_LABEL_LINE = re.compile(
    rf"^\s*(?:#{{1,6}}\s*)?(?:\*\*|__)?\s*(?P<who>{_LABEL})(?:\s+said)?\s*:?\s*(?:\*\*|__)?\s*:?\s*$",
    re.IGNORECASE)
# ... or a label with a colon, followed by the turn's first line ("User: why ...").
_LABEL_INLINE = re.compile(
    rf"^\s*(?:\*\*|__)?(?P<who>{_LABEL})(?:\s+said)?\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<rest>\S.*)$",
    re.IGNORECASE)


class ConversationError(ValueError):
    """A submission that cannot be stored; the message says why and how to fix it."""


class TokenError(ConversationError):
    """A missing, forged or expired import token."""


# ── Tokens ───────────────────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), f"conversation-import:{payload}".encode(), hashlib.sha256).hexdigest()


def issue_token(secret: str, *, document_id: str, user_id: int | None, profile_key: str,
                now: float | None = None) -> tuple[str, datetime]:
    """Return (token, expiry) for one export of one lesson."""
    if not secret:
        raise ConversationError("Conversation import is not configured on this server")
    now = time.time() if now is None else now
    expires = int(now + TOKEN_TTL_SECONDS)
    claims = {"d": document_id, "u": user_id, "p": profile_key,
              "c": secrets.token_hex(4), "exp": expires}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    token = f"{TOKEN_PREFIX}.{payload}.{_signature(secret, payload)}"
    return token, datetime.fromtimestamp(expires, timezone.utc)


def read_token(secret: str, token: str, now: float | None = None) -> dict:
    """Return the token's claims, or raise ConversationError."""
    parts = (token or "").strip().split(".")
    if not secret or len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise TokenError("Not a conversation import token")
    if not hmac.compare_digest(parts[2], _signature(secret, parts[1])):
        raise TokenError("The import token is not valid on this server")
    try:
        claims = json.loads(_unb64(parts[1]))
    except Exception:
        raise TokenError("Not a conversation import token")
    if claims.get("exp", 0) < (time.time() if now is None else now):
        raise TokenError("The import token has expired; export the lesson again for a new one")
    return claims


def find_token(text: str) -> str | None:
    """The first import token in a piece of text, such as a chat that quotes the export."""
    match = _TOKEN_RE.search(text or "")
    return match.group(0) if match else None


# ── Parsing ──────────────────────────────────────────────────────────────────

@dataclass
class Submission:
    turns: list[dict]
    format: str
    token: str | None = None
    assistant: str | None = None
    conversation_id: str | None = None
    document_id: str | None = None
    profile_key: str | None = None
    exchanges: list[tuple[str, str]] = field(default_factory=list)


def _content(value) -> str:
    """Message content as text; accepts provider block lists."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(_content(v) for v in value if _content(v))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return "" if value is None else str(value)


def _json_turns(data) -> list[dict]:
    messages = data if isinstance(data, list) else (data.get("turns") or data.get("messages") or [])
    turns = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or message.get("author") or "").lower()
        if role in ("user", "human"):
            role = "user"
        elif role in ("assistant", "model", "ai", "bot"):
            role = "assistant"
        else:
            continue
        turns.append({"role": role, "content": _content(message.get("content", message.get("text")))})
    return turns


def _embedded_json(text: str):
    """The JSON object in a chat reply that wraps it in a code fence, if any."""
    for block in sorted(re.findall(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL), key=len, reverse=True):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and (data.get("turns") or data.get("messages")) \
                and data.get("turns") != EXAMPLE_TURNS:
            return data
    return None


def parse_transcript(text: str) -> tuple[list[dict], str | None]:
    """Split a plain-text chat on its speaker labels. Returns (turns, assistant name)."""
    turns: list[dict] = []
    assistant = None
    current = None
    for line in text.splitlines():
        match = _LABEL_LINE.match(line) or _LABEL_INLINE.match(line)
        if match:
            who = match.group("who").lower()
            role = "user" if who in _USER_LABELS else "assistant"
            if role == "assistant" and who in _NAMED_ASSISTANTS:
                assistant = assistant or _NAMED_ASSISTANTS[who]
            current = {"role": role, "lines": []}
            turns.append(current)
            rest = match.groupdict().get("rest")
            if rest:
                current["lines"].append(rest)
        elif current is not None:
            current["lines"].append(line)
    return [{"role": t["role"], "content": "\n".join(t["lines"]).strip()} for t in turns], assistant


def parse_submission(body: bytes | str) -> Submission:
    """Read a submission: the export's JSON form, a chat reply holding it, or a plain-text chat."""
    if isinstance(body, bytes):
        if len(body) > MAX_BYTES:
            raise ConversationError(f"The conversation is larger than {MAX_BYTES // 1_000_000} MB")
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            raise ConversationError("The conversation must be UTF-8 text")
    text = body.strip()
    if not text:
        raise ConversationError("The conversation is empty")

    data = None
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if data is None:
        data = _embedded_json(text)

    if data is not None:
        fields = data if isinstance(data, dict) else {}
        submission = Submission(
            turns=_json_turns(data), format="json",
            token=fields.get("token") or find_token(text),
            assistant=fields.get("assistant") or fields.get("model"),
            conversation_id=fields.get("conversation_id"),
            document_id=fields.get("document_id"),
            profile_key=fields.get("profile_key") or fields.get("profile"),
        )
    else:
        turns, assistant = parse_transcript(text)
        submission = Submission(turns=turns, format="text", token=find_token(text), assistant=assistant)

    submission.exchanges = exchanges(submission.turns)
    if not submission.exchanges:
        raise ConversationError(
            "No conversation turns found. Send the JSON form the export describes, or a chat copy "
            "whose turns start with speaker labels such as 'User:' and 'Assistant:'.")
    return submission


def exchanges(turns: list[dict]) -> list[tuple[str, str]]:
    """Pair turns into (question, answer) exchanges, the shape of the Q&A log.

    Consecutive turns from one side are joined. The pasted export becomes a
    placeholder. A conversation with no answer at all has no exchanges.
    """
    merged: list[dict] = []
    for turn in turns:
        content = (turn.get("content") or "").strip()
        if turn["role"] == "user" and _EXPORT_MARKER.search(content):
            content = EXPORT_PLACEHOLDER
        if not content:
            continue
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["content"] += "\n\n" + content
        else:
            merged.append({"role": turn["role"], "content": content})
    if not any(t["role"] == "assistant" for t in merged):
        return []
    pairs: list[tuple[str, str]] = []
    question = None
    for turn in merged:
        if turn["role"] == "user":
            question = turn["content"]
        else:
            pairs.append((question or "", turn["content"]))
            question = None
    if question is not None:
        pairs.append((question, ""))
    return pairs


def conversation_key(export_id: str, submission: Submission) -> str:
    """Stable id for one conversation: the export plus its opening exchange.

    Two chats started from one export differ in their opening and are kept
    apart; a longer copy of the same chat keeps its id and replaces the shorter.
    """
    if submission.conversation_id:
        own = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(submission.conversation_id))[:64]
    else:
        question, answer = submission.exchanges[0]
        if question == EXPORT_PLACEHOLDER and len(submission.exchanges) > 1:
            question, answer = submission.exchanges[1]
        own = hashlib.sha1(f"{question}\n{answer}".encode()).hexdigest()[:10]
    return f"{export_id}-{own}"


# ── Storing ──────────────────────────────────────────────────────────────────

def store(db, *, document_id: str, user_id: int | None, profile_key: str | None,
          export_id: str, submission: Submission, channel: str, raw: str) -> dict:
    """Save one submission. Returns what was saved."""
    if not db.get_document(document_id):
        raise ConversationError(f"Lesson {document_id} does not exist")
    key = conversation_key(export_id, submission)
    replaced = db.save_external_conversation(
        document_id=document_id, user_id=user_id, conversation_id=key,
        profile_key=profile_key, assistant=(submission.assistant or "")[:80] or None,
        channel=channel, fmt=submission.format, raw=raw, exchanges=submission.exchanges,
    )
    return {"document_id": document_id, "conversation_id": key,
            "saved": len(submission.exchanges), "replaced": replaced}


def store_with_token(db, secret: str, *, submission: Submission, raw: str, channel: str,
                     token: str | None = None) -> dict:
    """Save a submission authorized by its import token."""
    token = token or submission.token
    if not token:
        raise TokenError(
            "No import token. Keep the token from the lesson export in the conversation, "
            "or send it as an 'Authorization: Bearer' header.")
    claims = read_token(secret, token)
    return store(db, document_id=claims["d"], user_id=claims.get("u"), profile_key=claims.get("p"),
                 export_id=claims["c"], submission=submission, channel=channel, raw=raw)


# ── S3 drop point ────────────────────────────────────────────────────────────

def is_s3_event(event) -> bool:
    records = event.get("Records") if isinstance(event, dict) else None
    return bool(records) and all(r.get("eventSource") == "aws:s3" for r in records)


def handle_s3_event(event: dict, db, s3, secret: str) -> list[dict]:
    """Import every conversation dropped in the inbox, then file it as imported or rejected.

    A rejected drop is filed with a ``.error.txt`` note next to it saying why.
    """
    outcomes = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith(S3_INBOX_PREFIX) or key.endswith("/"):
            continue
        name = key[len(S3_INBOX_PREFIX):]
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            if obj.get("ContentLength", 0) > MAX_BYTES:
                raise ConversationError(f"The conversation is larger than {MAX_BYTES // 1_000_000} MB")
            raw_bytes = obj["Body"].read()
            submission = parse_submission(raw_bytes)
            result = store_with_token(db, secret, submission=submission, channel="s3",
                                      raw=raw_bytes.decode("utf-8"))
            destination = f"{S3_IMPORTED_PREFIX}{stamp}/{name}"
            outcome = {"key": key, "status": "imported", **result}
        except Exception as e:
            destination = f"{S3_REJECTED_PREFIX}{stamp}/{name}"
            reason = str(e) if isinstance(e, ConversationError) else f"{type(e).__name__}: {e}"
            outcome = {"key": key, "status": "rejected", "reason": reason}
            try:
                s3.put_object(Bucket=bucket, Key=destination + ".error.txt",
                              Body=(reason + "\n").encode(), ContentType="text/plain")
            except Exception as note_error:
                print(f"[conversation-import] could not write rejection note for {key}: {note_error}")
        try:
            s3.copy_object(Bucket=bucket, Key=destination, CopySource={"Bucket": bucket, "Key": key})
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as move_error:
            print(f"[conversation-import] could not file {key}: {move_error}")
        print(f"[conversation-import] {json.dumps(outcome, default=str)}")
        outcomes.append(outcome)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    """Import conversation files from disk: ``python conversation_import.py FILE...``."""
    import argparse
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    from database import Database

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    db = Database(os.environ["DATABASE_URL"])
    secret = os.environ.get("COOKIE_SECRET", "")
    failed = 0
    for path in args.files:
        raw = path.read_bytes()
        try:
            result = store_with_token(db, secret, submission=parse_submission(raw),
                                      raw=raw.decode("utf-8"), channel="file")
            print(f"{path}: saved {result['saved']} exchanges as {result['conversation_id']}")
        except ConversationError as e:
            failed += 1
            print(f"{path}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
