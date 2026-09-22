from contextlib import contextmanager
from unittest.mock import patch


class _Cursor:
    def __init__(self, source_name):
        self.source_name = source_name

    def execute(self, query, params):
        pass

    def fetchone(self):
        return {"name": self.source_name}


class _DB:
    def __init__(self, source_name):
        self.source_name = source_name

    @contextmanager
    def get_cursor(self, commit=False):
        yield _Cursor(self.source_name)


def test_astra_document_prefers_provider_qualified_cache_key():
    from kb import cache_helpers

    db = _DB("New Material-Astra")
    astra_key = cache_helpers.get_cache_key("lesson:decompose_v3:codex-astra")
    cache = {astra_key: {"steps": [{"step_number": 1}]}}

    with patch.object(cache_helpers, "_db", db), patch.object(
        cache_helpers, "load_cached_analysis", side_effect=cache.get
    ):
        key, result = cache_helpers.load_document_cached_analysis("doc-1", "lesson", ":decompose_v3")

    assert key == astra_key
    assert result["steps"][0]["step_number"] == 1


def test_gemini_document_uses_historical_cache_key():
    from kb import cache_helpers

    db = _DB("Existing Material")
    gemini_key = cache_helpers.get_cache_key("lesson:decompose_v3")
    cache = {gemini_key: {"steps": [{"step_number": 2}]}}

    with patch.object(cache_helpers, "_db", db), patch.object(
        cache_helpers, "load_cached_analysis", side_effect=cache.get
    ):
        key, result = cache_helpers.load_document_cached_analysis("doc-2", "lesson", ":decompose_v3")

    assert key == gemini_key
    assert result["steps"][0]["step_number"] == 2


def test_astra_document_falls_back_to_legacy_cache():
    from kb import cache_helpers

    db = _DB("New Material-Astra")
    legacy_key = cache_helpers.get_cache_key("lesson:decompose_v3")
    cache = {legacy_key: {"steps": [{"step_number": 3}]}}

    with patch.object(cache_helpers, "_db", db), patch.object(
        cache_helpers, "load_cached_analysis", side_effect=cache.get
    ):
        key, result = cache_helpers.load_document_cached_analysis("doc-3", "lesson", ":decompose_v3")

    assert key == legacy_key
    assert result["steps"][0]["step_number"] == 3
