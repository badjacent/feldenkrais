"""
Tests for Pass A decomposition and routing.

Phase 2 refactor target: analysis.py -> pass_a.py
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def analysis_module():
    """Import analysis module with mocked dependencies."""
    kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)

    mock_cache = MagicMock()
    mock_cache.get_cache_key = lambda text: "fake_key_" + text[:8]
    mock_cache.load_cached_analysis = MagicMock(return_value=None)
    mock_cache.save_cached_analysis = MagicMock()
    mock_cache.DEFAULT_LLM = "gemini"

    mock_llm_config = MagicMock()
    mock_llm_config.DEFAULT_LLM = "gemini"

    sys.modules["cache_helpers"] = mock_cache
    sys.modules["llm_config"] = mock_llm_config

    import importlib
    if "analysis" in sys.modules:
        importlib.reload(sys.modules["analysis"])
    import analysis
    return analysis


class TestLoadPassA:
    def test_loads_prompt_and_schema(self, tmp_path, analysis_module):
        """Should return (prompt_str, schema_dict) tuple."""
        prompts_dir = tmp_path / "frameworks" / "v1"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "pass_a.md").write_text("Analyze this: {CLASSIFICATION_TABLE}")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        schema = {"type": "object", "properties": {
            "classifications": {"type": "object", "properties": {}, "required": []},
            "primary_framework": {"type": "string"},
            "secondary_frameworks": {"type": "array", "items": {"type": "string"}},
        }}
        (data_dir / "pass_a.schema.json").write_text(
            '{"type":"object","properties":{"classifications":{"type":"object","properties":{},"required":[]},"primary_framework":{"type":"string"},"secondary_frameworks":{"type":"array","items":{"type":"string"}}}}'
        )

        analysis_module._data_dir = data_dir
        analysis_module.FRAMEWORK_VERSION = "v1"

        mock_frameworks = [
            {"id": "VL-1", "name": "The Riser", "role": "primary",
             "focus": "verticality", "indicators": "standing"},
        ]
        with patch.object(analysis_module, "_resolve_prompts_dir", return_value=(prompts_dir, "v1")), \
             patch.object(analysis_module, "get_active_frameworks_config", return_value=mock_frameworks):
            prompt, result_schema = analysis_module.load_pass_a("v1")

        assert isinstance(prompt, str)
        assert isinstance(result_schema, dict)
        # The classification table should have been substituted in
        assert "VL-1" in prompt

    def test_returns_empty_if_missing(self, tmp_path, analysis_module):
        """Should return empty prompt if prompt file doesn't exist."""
        prompts_dir = tmp_path / "frameworks" / "v1"
        prompts_dir.mkdir(parents=True)
        # No pass_a.md file

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_a.schema.json").write_text(
            '{"type":"object","properties":{"classifications":{"type":"object","properties":{},"required":[]},"primary_framework":{"type":"string"},"secondary_frameworks":{"type":"array","items":{"type":"string"}}}}'
        )

        analysis_module._data_dir = data_dir
        analysis_module.FRAMEWORK_VERSION = "v1"

        with patch.object(analysis_module, "_resolve_prompts_dir", return_value=(prompts_dir, "v1")), \
             patch.object(analysis_module, "get_active_frameworks_config", return_value=[]):
            prompt, schema = analysis_module.load_pass_a("v1")

        assert prompt == ""


class TestRunPassA:
    def test_returns_cached_result(self, analysis_module):
        """Should return cached result without calling LLM."""
        cached_data = {
            "classifications": {"vl1_riser": 0.8},
            "primary_framework": "VL-1",
            "steps": [{"step_number": 1, "instruction": "Lie down"}],
            "pass_0": {"segments": []},
        }

        import cache_helpers
        cache_helpers.load_cached_analysis = MagicMock(return_value=cached_data)

        # Reload analysis to pick up the mock
        import importlib
        importlib.reload(analysis_module)

        # Since cache_helpers is mocked at module level, the analysis module
        # imports it — we need to patch it on the analysis module's reference
        with patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_a("some lesson text")

        assert result == cached_data

    def test_calls_llm_on_cache_miss(self, tmp_path, analysis_module):
        """Should call LLM and cache result."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_a.schema.json").write_text(
            '{"type":"object","properties":{"classifications":{"type":"object","properties":{},"required":[]},"primary_framework":{"type":"string"},"secondary_frameworks":{"type":"array","items":{"type":"string"}}}}'
        )
        analysis_module._data_dir = data_dir

        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "classifications": {"vl1_riser": 0.9},
            "primary_framework": "VL-1",
            "steps": [{"step_number": 1, "instruction": "Lie down", "segments": [1]}],
        }

        with patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis") as mock_save, \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm), \
             patch.object(analysis_module, "load_pass_a", return_value=("Analyze:", {"type": "object"})):
            result = analysis_module.run_pass_a("Lie on your back. Feel the floor.")

        mock_llm.complete_structured_json.assert_called_once()
        mock_save.assert_called_once()
        assert "classifications" in result

    def test_normalizes_segment_ranges(self, analysis_module):
        """Should convert segment_start/segment_end to segments array."""
        # The normalization logic is in run_pass_a after the LLM call
        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "classifications": {"vl1_riser": 0.9},
            "primary_framework": "VL-1",
            "steps": [
                {"step_number": 1, "instruction": "Lie down",
                 "segment_start": 1, "segment_end": 3},
            ],
        }

        mock_pass_0 = {
            "segments": [
                {"id": 1, "text": "a", "start": 0, "end": 10},
                {"id": 2, "text": "b", "start": 10, "end": 20},
                {"id": 3, "text": "c", "start": 20, "end": 30},
            ],
            "original_length": 30,
        }

        with patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm), \
             patch.object(analysis_module, "load_pass_a", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "run_pass_0", return_value=mock_pass_0):
            result = analysis_module.run_pass_a("test text with enough content.")

        step = result["steps"][0]
        assert "segments" in step
        assert step["segments"] == [1, 2, 3]

    def test_detects_segment_gaps(self, analysis_module):
        """Should set segment_gaps for uncovered segments."""
        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "classifications": {"vl1_riser": 0.9},
            "primary_framework": "VL-1",
            "steps": [
                {"step_number": 1, "instruction": "Lie down", "segments": [1, 3]},
            ],
        }

        mock_pass_0 = {
            "segments": [
                {"id": 1, "text": "a", "start": 0, "end": 10},
                {"id": 2, "text": "b", "start": 10, "end": 20},
                {"id": 3, "text": "c", "start": 20, "end": 30},
            ],
            "original_length": 30,
        }

        with patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm), \
             patch.object(analysis_module, "load_pass_a", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "run_pass_0", return_value=mock_pass_0):
            result = analysis_module.run_pass_a("test text with enough content.")

        assert "segment_gaps" in result
        assert 2 in result["segment_gaps"]

    def test_default_classification_when_not_configured(self, analysis_module):
        """Should return default classification if pass_a not configured."""
        mock_frameworks = [
            {"id": "VL-1", "name": "The Riser", "role": "primary"},
        ]

        with patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "load_pass_a", return_value=("", {})), \
             patch.object(analysis_module, "get_active_frameworks_config", return_value=mock_frameworks), \
             patch.object(analysis_module, "run_pass_0", return_value={"segments": []}):
            result = analysis_module.run_pass_a("test text")

        assert result["primary_framework"] == "VL-1"
        assert "Pass A not configured" in result["rationale"]


class TestPassACacheKey:
    def test_cache_key_deterministic(self, analysis_module):
        """Same input should produce same cache key."""
        analysis_module.FRAMEWORK_VERSION = "v1"
        key1 = analysis_module._pass_a_cache_key("test text")
        key2 = analysis_module._pass_a_cache_key("test text")
        assert key1 == key2

    def test_different_providers_different_keys(self, analysis_module):
        """Different provider_key should produce different cache key."""
        import hashlib
        # Use a real hash function so suffixes produce different keys
        real_get_cache_key = lambda text: hashlib.sha256(text.encode()).hexdigest()[:16]
        analysis_module.FRAMEWORK_VERSION = "v1"
        with patch.object(analysis_module, "get_cache_key", real_get_cache_key):
            key1 = analysis_module._pass_a_cache_key("test text", "gemini")
            key2 = analysis_module._pass_a_cache_key("test text", "anthropic")
        assert key1 != key2
