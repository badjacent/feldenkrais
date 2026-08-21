from unittest.mock import MagicMock

import kb.analysis as analysis
from kb.analysis import core
from kb.analysis.lesson_card import run_lesson_card


def test_lesson_card_validates_and_caches(tmp_path, monkeypatch):
    (tmp_path / "lesson_card.md").write_text("Transcript: {lesson_text}")
    schema_source = __import__("pathlib").Path(__file__).parents[2] / "data" / "lesson_card.schema.json"
    (tmp_path / "lesson_card.schema.json").write_text(schema_source.read_text())
    llm = MagicMock(model="gemini-3-flash-preview", last_usage={"input_tokens": 10})
    llm.complete_structured_json.return_value = {
        "summary": "A quiet lesson explores rolling and the relationship between the eyes and pelvis.",
        "suggested_title": None,
        "lesson_type": "atm",
        "starting_position": "lying_on_back",
        "positions": ["lying_on_back", "sitting"],
        "themes": ["rolling", "eyes"],
        "support_context": ["floor"],
        "equipment": [],
        "primary_language": "en",
        "notable_feature": "The eyes repeatedly initiate rolling.",
        "audience_notes": None,
    }
    cache = {}
    monkeypatch.setitem(core._state, "_data_dir", tmp_path)
    monkeypatch.setitem(core._state, "_get_llm", lambda _: llm)
    monkeypatch.setattr(analysis, "get_cache_key", lambda value: value)
    monkeypatch.setattr(analysis, "load_cached_analysis", cache.get)
    monkeypatch.setattr(analysis, "save_cached_analysis", cache.__setitem__)

    result = run_lesson_card("lesson transcript", provider_key="gemini")

    assert result["primary_language"] == "en"
    assert result["notable_feature"] == "The eyes repeatedly initiate rolling."
    assert cache
