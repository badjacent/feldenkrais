"""Lesson Step Table page and Annotated Step Transcript download."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import kb.web  # noqa: F401 - loads the app before its route modules
from tests.unit.test_print_docx import _model, _pass_a

PROFILES = [{"key": "gemini-3-flash"}, {"key": "astra-low"}]


def _db(prepared_under=("gemini-3-flash",)):
    db = MagicMock()
    db.is_document_accessible.return_value = True
    db.get_document.return_value = {"title": "Rolling <the> head", "presenter": "M. F."}
    db.get_document_transcription.return_value = "lesson text"
    db.default_profile_for_document.return_value = "gemini-3-flash"
    db.resolve_analysis_profile.side_effect = lambda key: {"key": key or "gemini-3-flash"}
    db.get_analysis_profiles.return_value = PROFILES
    db.get_document_state.return_value = {"stale": False}
    return db


def _results(prepared_under=("gemini-3-flash",)):
    def load(text, suffix, profile):
        if profile["key"] not in prepared_under:
            return "key", None
        return "key", _model() if suffix == ":print_prep_v1" else _pass_a()
    return load


def _request(profile=None):
    request = MagicMock()
    request.query_params = {"profile": profile} if profile else {}
    return request


def _call(route, db, load, profile=None):
    import sys

    import kb.web  # noqa: F401 - loads the app the routes belong to
    web = sys.modules["web"]
    with patch.object(web, "_db", db), patch.object(web, "_set_source_access"), \
            patch("routes.search.load_profile_result", side_effect=load):
        return asyncio.run(route(_request(profile), "doc-1"))


def test_step_table_shows_each_step_once_with_position_and_summary():
    from routes.search import lesson_print_page
    response = _call(lesson_print_page, _db(), _results())
    page = response.body.decode()
    assert response.status_code == 200
    assert "Group 1: Rolling the head" in page
    assert page.count('<td class="num">') == 2
    assert "Roll the head right, then left." in page
    assert "Rolling &lt;the&gt; head" in page  # escaped
    assert "/lesson/doc-1/print.docx?profile=gemini-3-flash" in page
    assert "fetch(" not in page  # prints offline


def test_step_table_falls_back_to_a_profile_that_prepared_the_lesson():
    from routes.search import lesson_print_page
    page = _call(lesson_print_page, _db(), _results(("astra-low",))).body.decode()
    assert "Roll the head right, then left." in page
    assert "print.docx?profile=astra-low" in page


def test_a_requested_profile_without_a_model_says_what_is_missing():
    from routes.search import lesson_print_page
    page = _call(lesson_print_page, _db(), _results(("astra-low",)), profile="gemini-3-flash").body.decode()
    assert "no step table yet" in page
    assert "<code>print_prep</code>" in page
    assert '/lesson/doc-1/print?profile=astra-low' in page
    assert "Download Word" not in page


def test_stale_model_is_labelled():
    from routes.search import lesson_print_page
    db = _db()
    db.get_document_state.return_value = {"stale": True}
    assert "Out of date" in _call(lesson_print_page, db, _results()).body.decode()


def test_inaccessible_lesson_is_not_found():
    from routes.search import lesson_print_docx, lesson_print_page
    db = _db()
    db.is_document_accessible.return_value = False
    assert _call(lesson_print_page, db, _results()).status_code == 404
    assert _call(lesson_print_docx, db, _results()).status_code == 404


def test_word_download_is_the_annotated_transcript():
    pytest.importorskip("docx")
    from routes.search import lesson_print_docx
    response = _call(lesson_print_docx, _db(), _results())
    assert response.status_code == 200
    assert 'filename="rolling-the-head-annotated-step-transcript.docx"' in response.headers["content-disposition"]
    import io
    import docx
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(response.body)).paragraphs)
    assert "Roll the head to the right" in text


def test_word_download_without_a_model_reports_what_is_needed():
    from routes.search import lesson_print_docx
    response = _call(lesson_print_docx, _db(), _results(()), profile="gemini-3-flash")
    assert response.status_code == 404
    assert b"print_prep" in response.body
