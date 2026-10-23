"""Outside conversations: import tokens, parsing, the REST endpoint and the S3 drop point."""

import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest

import conversation_import as ci

SECRET = "test-secret"
DOC = "3f043c17-25fc-46eb-9635-a2d283cc5c75"
EXPORT = f"# Lesson\n\n- Reference: lesson {DOC} · profile astra-low · exported 2026-09-23T10:00Z\n\n...long..."


def _token(**kw):
    args = {"document_id": DOC, "user_id": 7, "profile_key": "astra-low"} | kw
    return ci.issue_token(SECRET, **args)[0]


# ── Tokens ───────────────────────────────────────────────────────────────────

def test_token_round_trips_its_claims():
    claims = ci.read_token(SECRET, _token())
    assert (claims["d"], claims["u"], claims["p"]) == (DOC, 7, "astra-low")
    assert claims["c"]


def test_token_signed_with_another_secret_is_refused():
    with pytest.raises(ci.TokenError):
        ci.read_token("other-secret", _token())


def test_tampered_token_is_refused():
    prefix, payload, sig = _token().split(".")
    forged = ci._b64(json.dumps({"d": DOC, "u": 1, "p": "x", "c": "a", "exp": 2**40}).encode())
    with pytest.raises(ci.TokenError):
        ci.read_token(SECRET, f"{prefix}.{forged}.{sig}")


def test_expired_token_is_refused():
    token = _token(now=time.time() - ci.TOKEN_TTL_SECONDS - 10)
    with pytest.raises(ci.TokenError, match="expired"):
        ci.read_token(SECRET, token)


def test_token_cannot_be_issued_without_a_secret():
    with pytest.raises(ci.ConversationError):
        ci.issue_token("", document_id=DOC, user_id=None, profile_key="p")


def test_token_is_found_inside_a_pasted_chat():
    token = _token()
    assert ci.find_token(f"User: here is the lesson\n...Authorization: Bearer {token} ...") == token


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_json_form_from_the_export():
    body = json.dumps({"token": "t", "assistant": "Claude Opus 5", "turns": [
        {"role": "user", "content": EXPORT},
        {"role": "assistant", "content": "I have read it."},
        {"role": "user", "content": "Why the pause at step 4?"},
        {"role": "assistant", "content": "Because..."},
    ]})
    sub = ci.parse_submission(body.encode())
    assert sub.format == "json" and sub.token == "t" and sub.assistant == "Claude Opus 5"
    assert sub.exchanges == [(ci.EXPORT_PLACEHOLDER, "I have read it."),
                             ("Why the pause at step 4?", "Because...")]


def test_json_in_a_fenced_chat_reply():
    token = _token()
    body = ("Here you go:\n\n```json\n" + json.dumps({"token": token, "turns": [
        {"role": "user", "content": "Q1"}, {"role": "assistant", "content": [{"type": "text", "text": "A1"}]},
    ]}) + "\n```\n")
    sub = ci.parse_submission(body)
    assert sub.token == token and sub.exchanges == [("Q1", "A1")]


def test_plain_text_chat_with_speaker_labels():
    text = ("You said:\nWhat does the lesson want from the ribs?\n\n"
            "ChatGPT said:\nIt asks them to soften.\nA: a line of the answer, not a label\n"
            "**User:** And the head?\n\n## Assistant\nThe head follows.\n")
    turns, assistant = ci.parse_transcript(text)
    assert assistant == "ChatGPT"
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[1]["content"].endswith("A: a line of the answer, not a label")
    sub = ci.parse_submission(text)
    assert sub.format == "text"
    assert sub.exchanges[0][0] == "What does the lesson want from the ribs?"
    assert sub.exchanges[-1] == ("And the head?", "The head follows.")


def test_ordinary_prose_is_not_mistaken_for_a_label():
    turns, _ = ci.parse_transcript("User: hi\nYou can feel the ribs.\nA quiet pause.\nAssistant: ok")
    assert turns == [{"role": "user", "content": "hi\nYou can feel the ribs.\nA quiet pause."},
                     {"role": "assistant", "content": "ok"}]


def test_a_chat_without_turns_is_refused_with_guidance():
    with pytest.raises(ci.ConversationError, match="speaker labels"):
        ci.parse_submission("just some notes about the lesson")


def test_unanswered_question_is_kept():
    assert ci.exchanges([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                         {"role": "user", "content": "c"}]) == [("a", "b"), ("c", "")]


def test_a_longer_copy_keeps_the_conversation_id():
    turns = [{"role": "user", "content": EXPORT}, {"role": "assistant", "content": "Read."},
             {"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]
    short = ci.parse_submission(json.dumps({"turns": turns}))
    longer = ci.parse_submission(json.dumps({"turns": turns + [
        {"role": "user", "content": "Q2"}, {"role": "assistant", "content": "A2"}]}))
    other = ci.parse_submission(json.dumps({"turns": turns[:2] + [
        {"role": "user", "content": "Different"}, {"role": "assistant", "content": "A"}]}))
    assert ci.conversation_key("abc", short) == ci.conversation_key("abc", longer)
    assert ci.conversation_key("abc", short) != ci.conversation_key("abc", other)


# ── Storing ──────────────────────────────────────────────────────────────────

def _fake_db():
    db = MagicMock()
    db.get_document.return_value = {"id": DOC}
    db.save_external_conversation.return_value = 0
    return db


def test_store_with_token_saves_under_the_tokens_lesson_and_user():
    db = _fake_db()
    sub = ci.parse_submission("User: q\nAssistant: a")
    result = ci.store_with_token(db, SECRET, submission=sub, raw="raw", channel="rest", token=_token())
    kwargs = db.save_external_conversation.call_args.kwargs
    assert kwargs["document_id"] == DOC and kwargs["user_id"] == 7
    assert kwargs["profile_key"] == "astra-low" and kwargs["exchanges"] == [("q", "a")]
    assert result["saved"] == 1


def test_store_without_a_token_is_refused():
    with pytest.raises(ci.TokenError):
        ci.store_with_token(_fake_db(), SECRET, submission=ci.parse_submission("User: q\nAssistant: a"),
                            raw="", channel="rest")


def test_save_replaces_earlier_copy_in_one_transaction(mock_db):
    mock_db._mock_cursor.rowcount = 2
    replaced = mock_db.save_external_conversation(
        document_id=DOC, user_id=7, conversation_id="c-1", profile_key="p", assistant="Claude",
        channel="rest", fmt="json", raw="{}", exchanges=[("q1", "a1"), ("q2", "a2")])
    sql = [c.args[0] for c in mock_db._mock_cursor.execute.call_args_list]
    assert "INSERT INTO external_conversation" in sql[0]
    assert "DELETE FROM lesson_chat_log" in sql[1] and "origin = 'external'" in sql[1]
    assert sum("INSERT INTO lesson_chat_log" in s for s in sql) == 2
    assert replaced == 2


# ── S3 drop point ────────────────────────────────────────────────────────────

def _s3_event(key):
    return {"Records": [{"eventSource": "aws:s3",
                         "s3": {"bucket": {"name": "b"}, "object": {"key": key}}}]}


def _s3_with(body: bytes):
    s3 = MagicMock()
    s3.get_object.return_value = {"ContentLength": len(body), "Body": io.BytesIO(body)}
    return s3


def test_dropped_conversation_is_imported_and_filed():
    body = json.dumps({"token": _token(), "turns": [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}).encode()
    s3, db = _s3_with(body), _fake_db()
    [outcome] = ci.handle_s3_event(_s3_event("conversations/inbox/my+chat.json"), db, s3, SECRET)
    assert outcome["status"] == "imported"
    s3.get_object.assert_called_with(Bucket="b", Key="conversations/inbox/my chat.json")
    destination = s3.copy_object.call_args.kwargs["Key"]
    assert destination.startswith(ci.S3_IMPORTED_PREFIX) and destination.endswith("/my chat.json")
    s3.delete_object.assert_called_with(Bucket="b", Key="conversations/inbox/my chat.json")
    assert db.save_external_conversation.call_args.kwargs["channel"] == "s3"


def test_dropped_file_without_token_is_rejected_with_a_note():
    s3, db = _s3_with(b"User: q\nAssistant: a"), _fake_db()
    [outcome] = ci.handle_s3_event(_s3_event("conversations/inbox/x.txt"), db, s3, SECRET)
    assert outcome["status"] == "rejected" and "token" in outcome["reason"]
    note = s3.put_object.call_args.kwargs
    assert note["Key"].startswith(ci.S3_REJECTED_PREFIX) and note["Key"].endswith("x.txt.error.txt")
    assert s3.copy_object.call_args.kwargs["Key"].startswith(ci.S3_REJECTED_PREFIX)
    db.save_external_conversation.assert_not_called()


def test_only_inbox_objects_are_touched():
    s3 = MagicMock()
    assert ci.handle_s3_event(_s3_event("conversations/imported/x.json"), _fake_db(), s3, SECRET) == []
    s3.get_object.assert_not_called()


def test_lambda_handler_routes_s3_events_to_the_importer():
    import web
    with patch.object(ci, "handle_s3_event", return_value=[{"status": "imported"}]) as handle, \
         patch("boto3.client"):
        result = web.handler(_s3_event("conversations/inbox/x.json"), None)
    handle.assert_called_once()
    assert result["statusCode"] == 200


# ── REST endpoint ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import auth
    import web

    fake_db = _fake_db()
    with patch.object(web, "_db", fake_db), patch.object(auth, "COOKIE_SECRET", SECRET), \
         patch.object(web, "IS_LAMBDA", True):
        with TestClient(web.app, base_url="https://aionyourside.net", follow_redirects=False) as c:
            c.fake_db = fake_db
            yield c


def test_import_endpoint_takes_a_token_without_a_session(client):
    body = {"token": _token(), "assistant": "Claude",
            "turns": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}
    resp = client.post(ci.IMPORT_PATH, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["saved"] == 1
    assert resp.json()["lesson_url"] == f"https://aionyourside.net/lesson/{DOC}"


def test_import_endpoint_takes_a_bearer_token_with_plain_text(client):
    resp = client.post(ci.IMPORT_PATH, content="User: q\nAssistant: a",
                       headers={"Authorization": f"Bearer {_token()}", "Content-Type": "text/plain"})
    assert resp.status_code == 200, resp.text


def test_import_endpoint_refuses_anonymous_imports_without_a_token(client):
    resp = client.post(ci.IMPORT_PATH, json={"document_id": DOC, "turns": [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]})
    assert resp.status_code == 401
    client.fake_db.save_external_conversation.assert_not_called()


def test_import_endpoint_refuses_a_forged_token(client):
    resp = client.post(ci.IMPORT_PATH, content="User: q\nAssistant: a",
                       headers={"Authorization": "Bearer fkc1.e30." + "0" * 64})
    assert resp.status_code == 401


def test_login_gate_still_covers_other_api_routes(client):
    resp = client.get(f"/api/lesson/{DOC}/context")
    assert "Sign in" in resp.text


# ── The export describes the way back ────────────────────────────────────────

def test_context_export_explains_how_to_save_the_conversation():
    import lesson_context

    token = _token()
    doc = lesson_context.build_lesson_context(
        document={"id": DOC, "title": "Lesson"}, source_name="", lesson_url="u",
        profile={"key": "astra-low", "model": "gpt-6-astra"}, transcript="text", whisper_segments=None,
        pass_a=None, somatic=None, language=None, condensed=None, concepts=None, positions=None,
        field_descriptions={}, exported_at="2026-09-23T10:00Z",
        saving={"endpoint": "https://aionyourside.net" + ci.IMPORT_PATH, "token": token,
                "expires": "2026-12-22", "bucket": "feldenkrais-prod"},
    )
    assert "## Saving this conversation" in doc
    assert f"curl -X POST https://aionyourside.net{ci.IMPORT_PATH}" in doc
    assert "s3://feldenkrais-prod/conversations/inbox/" in doc
    assert ci.find_token(doc) == token
    assert doc.index("## Saving this conversation") < doc.index("## Lesson text")
    # A chat that starts by pasting the export carries its own token back.
    sub = ci.parse_submission(f"User:\n{doc}\n\nAssistant:\nRead it.\n\nUser: q\n\nAssistant: a")
    assert sub.token == token
    assert sub.exchanges == [(ci.EXPORT_PLACEHOLDER, "Read it."), ("q", "a")]
