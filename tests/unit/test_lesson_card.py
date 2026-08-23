from pathlib import Path
from unittest.mock import MagicMock

import kb.analysis as analysis
from kb.analysis import core
from kb.analysis.lesson_card import run_lesson_card


def test_lesson_card_prompt_defines_primary_position_without_brief_standing():
    prompt = (Path(__file__).parents[2] / "data" / "lesson_card.md").read_text()

    assert "`primary_positions`" in prompt
    assert "most of the substantive movement exploration" in prompt
    assert "brief orientation or closing check" in prompt


def test_lesson_card_validates_and_caches(tmp_path, monkeypatch):
    (tmp_path / "lesson_card.md").write_text(
        "Title: {source_title}\nContext: {hosting_page_context}\nTranscript: {lesson_text}"
    )
    schema_source = __import__("pathlib").Path(__file__).parents[2] / "data" / "lesson_card.schema.json"
    (tmp_path / "lesson_card.schema.json").write_text(schema_source.read_text())
    llm = MagicMock(model="gemini-3-flash-preview", last_usage={"input_tokens": 10})
    llm.complete_structured_json.return_value = {
        "summary": "A quiet lesson explores rolling and the relationship between the eyes and pelvis.",
        "suggested_title": None,
        "is_feldenkrais_related": True,
        "feldenkrais_relevance_confidence": 0.99,
        "relevance_reason": "The transcript guides an Awareness Through Movement lesson.",
        "lesson_type": "atm",
        "starting_position": "lying_on_back",
        "primary_positions": ["lying_on_back", "lying_on_side"],
        "positions": ["lying_on_back", "sitting"],
        "themes": ["rolling", "eyes"],
        "support_context": ["floor"],
        "equipment": [],
        "primary_language": "en",
        "notable_feature": "The eyes repeatedly initiate rolling.",
        "audience_notes": None,
        "presenter_intent": "Explore how the eyes participate in rolling.",
        "intent_evidence": ["notice what your eyes do as you roll"],
        "applications": [
            {
                "id": app_id,
                "strength": "intended" if app_id == "everyday_movement" else "absent",
                "evidence": ["make rolling easier"] if app_id == "everyday_movement" else [],
                "reason": "Explicit purpose." if app_id == "everyday_movement" else "Not framed.",
            }
            for app_id in (
                "pain_comfort_recovery", "everyday_movement",
                "music_voice_performance", "sport_skilled_activity",
                "stress_rest_self_regulation", "learning_across_life",
                "creativity_meditation_self_knowledge",
            )
        ],
    }
    cache = {}
    monkeypatch.setitem(core._state, "_data_dir", tmp_path)
    monkeypatch.setitem(core._state, "_get_llm", lambda _: llm)
    monkeypatch.setattr(analysis, "get_cache_key", lambda value: value)
    monkeypatch.setattr(analysis, "load_cached_analysis", cache.get)
    monkeypatch.setattr(analysis, "save_cached_analysis", cache.__setitem__)

    result = run_lesson_card(
        "lesson transcript",
        provider_key="gemini",
        source_title="Lesson 12",
        hosting_page_context={"page_url": "https://example.test/lesson", "description": "Audio lesson"},
    )

    assert result["primary_language"] == "en"
    assert result["primary_positions"] == ["lying_on_back", "lying_on_side"]
    assert result["positions"] == ["lying_on_back", "lying_on_side", "sitting"]
    assert result["notable_feature"] == "The eyes repeatedly initiate rolling."
    assert result["is_feldenkrais_related"] is True
    assert result["feldenkrais_relevance_confidence"] == 0.99
    assert result["presenter_intent"] == "Explore how the eyes participate in rolling."
    assert [item["id"] for item in result["applications"] if item["strength"] == "intended"] == [
        "everyday_movement"
    ]
    assert cache
    prompt = llm.complete_structured_json.call_args.kwargs["prompt"]
    assert "Lesson 12" in prompt
    assert "https://example.test/lesson" in prompt
    assert "Audio lesson" in prompt
