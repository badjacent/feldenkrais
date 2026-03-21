"""
Tests for Pass A decomposition and routing.

Phase 2 refactor target: analysis.py → pass_a.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestLoadPassA:
    def test_loads_prompt_and_schema(self):
        """Should return (prompt_str, schema_dict) tuple."""
        pytest.skip("TODO: mock _resolve_data_path")

    def test_returns_none_if_missing(self):
        pytest.skip("TODO")


class TestRunPassA:
    def test_returns_cached_result(self):
        """Should return cached result without calling LLM."""
        pytest.skip("TODO: mock load_cached_analysis")

    def test_calls_llm_on_cache_miss(self):
        """Should call LLM and cache result."""
        pytest.skip("TODO: patch kb.analysis._get_llm")

    def test_normalizes_segment_ranges(self):
        """Should convert segment_start/segment_end to segments array."""
        pytest.skip("TODO: provide mock result with ranges")

    def test_detects_segment_gaps(self):
        """Should log warning and set segment_gaps for uncovered segments."""
        pytest.skip("TODO")

    def test_default_classification_when_not_configured(self):
        """Should return default classification if pass_a not configured."""
        pytest.skip("TODO")


class TestPassACacheKey:
    def test_cache_key_deterministic(self):
        """Same input should produce same cache key."""
        pytest.skip("TODO")

    def test_different_providers_different_keys(self):
        """Different provider_key should produce different cache key."""
        pytest.skip("TODO")
