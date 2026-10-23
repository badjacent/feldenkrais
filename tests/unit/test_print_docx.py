import io

import pytest

docx = pytest.importorskip("docx")

from kb.analysis.print_prep import build_print_model  # noqa: E402
from kb.print_docx import (  # noqa: E402
    IncompleteTranscriptError, build_annotated_transcript, reflow, step_text,
)


def _pass_a():
    return {
        "groups": [{"group_number": 1, "label": "Rolling the head", "steps": [1, 2]}],
        "steps": [
            {"step_number": 1, "instruction": "Lie down.", "segments": [1, 2]},
            {"step_number": 2, "instruction": "Roll the head.", "segments": [3]},
        ],
        "pass_0": {"segments": [
            {"id": 1, "text": "Lie on your back.  Feel the floor."},
            {"id": 2, "text": "Is it the same on both sides? Notice. Rest."},
            {"id": 3, "text": "Roll the head to the right, \"slowly.\" Then left."},
        ]},
    }


def _model():
    answer = {"steps": [
        {"step_number": 1, "position": "On back", "brief_summary": "Lie down and sense the floor.",
         "notable_quotes": [{"segment_id": 2, "text": "Is it the same on both sides?"}]},
        {"step_number": 2, "position": "On back", "brief_summary": "Roll the head right, then left.",
         "notable_quotes": []},
    ], "editorial_notes": []}
    return build_print_model(_pass_a(), answer)


def test_reflow_keeps_the_text_exactly_and_groups_three_sentences():
    text = "One. Two? Three! Four. Five."
    paragraphs = reflow(text)
    assert paragraphs == ["One. Two? Three!", "Four. Five."]
    assert " ".join(paragraphs) == text


def test_reflow_breaks_after_closing_quotes_without_losing_them():
    text = 'Roll the head to the right, "slowly." Then left. Rest. Again.'
    paragraphs = reflow(text)
    assert paragraphs[0].endswith('Rest.') and '"slowly."' in paragraphs[0]
    assert " ".join(paragraphs) == text


def test_step_text_joins_segments_and_collapses_whitespace_only():
    seg = {s["id"]: s["text"] for s in _pass_a()["pass_0"]["segments"]}
    assert step_text([1, 2], seg) == "Lie on your back. Feel the floor. Is it the same on both sides? Notice. Rest."


def test_document_contains_every_segment_once_in_order():
    data = build_annotated_transcript(_model(), _pass_a(), "Rolling", "Test source")
    doc = docx.Document(io.BytesIO(data))
    texts = [p.text for p in doc.paragraphs]

    assert "Step 1" in texts and "Step 2" in texts
    assert "“Is it the same on both sides?”" in texts
    assert "Lie down and sense the floor." in texts
    body = " ".join(t for t in texts if t.startswith(("Lie on", "Is it the same on both sides? Notice", "Rest.", "Roll the head to the right,")))
    assert "Lie on your back. Feel the floor. Is it the same on both sides?" in body
    assert texts.index("Step 1") < texts.index("Step 2")


def test_a_lesson_with_segment_gaps_is_refused():
    pass_a = _pass_a()
    model = _model()
    model["validation"]["coverage_errors"] = ["1 segments belong to no step (first: [4])"]
    with pytest.raises(IncompleteTranscriptError):
        build_annotated_transcript(model, pass_a, "Rolling")


def test_quotes_lose_segment_line_breaks_but_keep_their_words():
    pass_a = _pass_a()
    pass_a["pass_0"]["segments"][1]["text"] = "Is it the same\non both sides? Notice. Rest."
    answer = {"steps": [
        {"step_number": 1, "position": "", "brief_summary": "Sense.",
         "notable_quotes": [{"segment_id": 2, "text": "Is it the same\non both sides?"}]},
        {"step_number": 2, "position": "", "brief_summary": "Roll.", "notable_quotes": []},
    ], "editorial_notes": []}
    data = build_annotated_transcript(build_print_model(pass_a, answer), pass_a, "Rolling")
    texts = [p.text for p in docx.Document(io.BytesIO(data)).paragraphs]
    assert "“Is it the same on both sides?”" in texts
