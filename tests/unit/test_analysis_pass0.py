"""
Tests for Pass 0 segmentation.

Phase 2 refactor target: analysis.py -> pass0.py
"""

import sys
import pytest
from unittest.mock import MagicMock
from pathlib import Path


@pytest.fixture
def analysis_module():
    """Import analysis module with mocked dependencies."""
    kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)

    mock_cache = MagicMock()
    mock_cache.get_cache_key = lambda text: "fake_key"
    mock_cache.load_cached_analysis = lambda key: None
    mock_cache.save_cached_analysis = MagicMock()
    mock_llm_config = MagicMock()
    mock_llm_config.DEFAULT_LLM = "gemini"
    sys.modules["cache_helpers"] = mock_cache
    sys.modules["llm_config"] = mock_llm_config

    import importlib
    if "analysis" in sys.modules:
        importlib.reload(sys.modules["analysis"])
    import analysis
    return analysis


class TestRunPass0:
    def test_segments_lesson_text(self, analysis_module):
        """Should split lesson text into numbered segments."""
        text = (
            "Lie on your back on the floor. "
            "Bend your knees so your feet are standing. "
            "Feel the contact of your body with the surface."
        )
        result = analysis_module.run_pass_0(text)
        assert "segments" in result
        assert "original_length" in result
        assert len(result["segments"]) > 0

    def test_empty_text_returns_empty_segments(self, analysis_module):
        """Empty or very short text returns no segments."""
        result = analysis_module.run_pass_0("")
        assert result["segments"] == []
        assert result["original_length"] == 0

    def test_segment_ids_are_sequential(self, analysis_module):
        """Segment IDs should be sequential starting from 1."""
        text = (
            "Lie on your back on the floor. "
            "Bend your knees so your feet are standing. "
            "Feel the contact of your body with the surface. "
            "Now slowly tilt your knees to the right."
        )
        result = analysis_module.run_pass_0(text)
        ids = [s["id"] for s in result["segments"]]
        assert ids == list(range(1, len(ids) + 1))

    def test_segments_have_required_fields(self, analysis_module):
        """Each segment should have id, text, start, end."""
        text = "Lie on your back on the floor. Feel the contact."
        result = analysis_module.run_pass_0(text)
        for seg in result["segments"]:
            assert "id" in seg
            assert "text" in seg
            assert "start" in seg
            assert "end" in seg

    def test_original_length_matches(self, analysis_module):
        """original_length should match the stripped text length."""
        text = "  Lie on your back on the floor.  "
        result = analysis_module.run_pass_0(text)
        assert result["original_length"] == len(text.strip())


class TestFormatTextWithSegments:
    def test_formats_segments_with_ids(self, analysis_module):
        """Should produce text with [1], [2] markers."""
        pass_0_result = {
            "segments": [
                {"id": 1, "text": "Lie on your back."},
                {"id": 2, "text": "Bend your knees."},
            ]
        }
        formatted = analysis_module.format_text_with_segments(pass_0_result)
        assert "[1] Lie on your back." in formatted
        assert "[2] Bend your knees." in formatted

    def test_roundtrip_preserves_content(self, analysis_module):
        """Segment -> format should preserve all original text content."""
        pass_0_result = {
            "segments": [
                {"id": 1, "text": "First sentence here."},
                {"id": 2, "text": "Second sentence here."},
                {"id": 3, "text": "Third sentence here."},
            ]
        }
        formatted = analysis_module.format_text_with_segments(pass_0_result)
        for seg in pass_0_result["segments"]:
            assert seg["text"] in formatted

    def test_empty_segments_returns_empty(self, analysis_module):
        """No segments should produce empty string."""
        result = analysis_module.format_text_with_segments({"segments": []})
        assert result == ""

    def test_missing_segments_key(self, analysis_module):
        """Should handle missing segments key gracefully."""
        result = analysis_module.format_text_with_segments({})
        assert result == ""
