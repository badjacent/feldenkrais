"""
Tests for Pass 0b somatic/language/concept analysis.

Phase 2 refactor target: analysis.py -> pass0b_*.py modules
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

    mock_llm_config = MagicMock()
    mock_llm_config.DEFAULT_LLM = "gemini"

    sys.modules["cache_helpers"] = mock_cache
    sys.modules["llm_config"] = mock_llm_config

    import importlib
    if "analysis" in sys.modules:
        importlib.reload(sys.modules["analysis"])
    import analysis
    return analysis


def _make_pass_a_result():
    """Helper to create a mock Pass A result."""
    return {
        "classifications": {"vl1_riser": 0.9},
        "primary_framework": "VL-1",
        "steps": [
            {"step_number": 1, "instruction": "Lie on your back", "segments": [1, 2], "tags": ["S", "P"]},
            {"step_number": 2, "instruction": "Bend your knees", "segments": [3], "tags": ["M"]},
        ],
        "groups": [
            {"group_number": 1, "label": "Setup", "description": "Initial position", "steps": [1, 2]},
        ],
        "pass_0": {
            "segments": [
                {"id": 1, "text": "Lie on your back on the floor."},
                {"id": 2, "text": "Feel the contact of your body."},
                {"id": 3, "text": "Bend your knees so your feet stand."},
            ],
            "original_length": 100,
        },
    }


class TestFormatStepsFor0b:
    def test_formats_steps_with_segments(self, analysis_module):
        """Should produce formatted text with step headers and segment content."""
        pass_a = _make_pass_a_result()
        result = analysis_module.format_steps_for_0b(pass_a)

        assert "## Group 1: Setup" in result
        assert "### Step 1" in result
        assert "### Step 2" in result
        assert "[1] Lie on your back on the floor." in result
        assert "[3] Bend your knees so your feet stand." in result

    def test_empty_steps_returns_empty(self, analysis_module):
        """Empty pass_a result should return minimal output."""
        pass_a = {"steps": [], "groups": [], "pass_0": {"segments": []}}
        result = analysis_module.format_steps_for_0b(pass_a)
        assert "### Step" not in result


class TestRunPass0b:
    def test_returns_cached_result(self, analysis_module):
        """Should return cached somatic result without calling LLM."""
        cached_data = {
            "enriched_steps": [{"step_number": 1, "differentiation": "pelvis-spine"}],
            "summary": {"key_patterns": "test"},
        }
        pass_a = _make_pass_a_result()

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "load_pass_0b", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b("test text")

        assert result["cached"] is True
        assert len(result["enriched_steps"]) == 1

    def test_calls_llm_with_formatted_input(self, tmp_path, analysis_module):
        """Should call LLM on cache miss."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_0b.md").write_text("Analyze somatic features:\n")
        (data_dir / "pass_0b.schema.json").write_text('{"type": "object"}')

        analysis_module._data_dir = data_dir
        pass_a = _make_pass_a_result()

        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "steps": [
                {"step_number": 1, "differentiation": "pelvis-spine",
                 "activation": "hip flexors", "inhibition": "lower back",
                 "transmission_route": "pelvis to spine", "ground_push": "feet"},
            ],
            "summary": {},
        }
        mock_llm.last_usage = {"tokens_in": 100, "tokens_out": 200}

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm):
            result = analysis_module.run_pass_0b("test text")

        mock_llm.complete_structured_json.assert_called_once()
        assert result["cached"] is False
        assert len(result["enriched_steps"]) == 1

    def test_somatic_fields_present(self, tmp_path, analysis_module):
        """Result should have differentiation, activation, inhibition, transmission_route, ground_push."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_0b.md").write_text("Analyze:\n")
        (data_dir / "pass_0b.schema.json").write_text('{"type": "object"}')

        analysis_module._data_dir = data_dir
        pass_a = _make_pass_a_result()

        somatic_fields = ["differentiation", "activation", "inhibition",
                          "transmission_route", "ground_push"]
        step_data = {"step_number": 1}
        for f in somatic_fields:
            step_data[f] = f"test_{f}"

        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "steps": [step_data],
            "summary": {},
        }
        mock_llm.last_usage = {}

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm):
            result = analysis_module.run_pass_0b("test text")

        enriched = result["enriched_steps"][0]
        for f in somatic_fields:
            assert f in enriched


class TestRunPass0bLang:
    def test_returns_cached_result(self, analysis_module):
        """Should return cached language result."""
        cached_data = {
            "enriched_steps": [{"step_number": 1, "language_type": "directive"}],
            "summary": {"speaker_principles": "economy of words"},
        }
        pass_a = _make_pass_a_result()

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "load_pass_0b_lang", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_lang("test text")

        assert result["cached"] is True
        assert result["enriched_steps"][0]["language_type"] == "directive"

    def test_language_analysis_output(self, tmp_path, analysis_module):
        """Should call LLM and return language analysis."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_0b_lang.md").write_text("Analyze language:\n")
        (data_dir / "pass_0b_lang.schema.json").write_text('{"type": "object"}')

        analysis_module._data_dir = data_dir
        pass_a = _make_pass_a_result()

        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "steps": [{"step_number": 1, "language_type": "invitational"}],
            "summary": {"speaker_principles": "indirect guidance"},
        }
        mock_llm.last_usage = {}

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm):
            result = analysis_module.run_pass_0b_lang("test text")

        assert result["cached"] is False
        assert "summary" in result


class TestRunPass0bWordLang:
    def test_returns_cached_result(self, analysis_module):
        """Should return cached word-level annotations."""
        cached_data = {
            "word_annotations": [{"step_number": 1, "phrases": []}],
        }
        pass_a = _make_pass_a_result()
        lang_result = {
            "pass_a": pass_a,
            "enriched_steps": [{"step_number": 1}],
            "cached": True,
        }

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "run_pass_0b_lang", return_value=lang_result), \
             patch.object(analysis_module, "load_pass_0b_word_lang", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_word_lang("test text")

        assert result["cached"] is True

    def test_word_language_patterns(self, tmp_path, analysis_module):
        """Should call LLM for word-level annotation on cache miss."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_0b_word_lang.md").write_text("Annotate words:\n")
        (data_dir / "pass_0b_word_lang.schema.json").write_text('{"type": "object"}')

        analysis_module._data_dir = data_dir
        pass_a = _make_pass_a_result()
        lang_result = {
            "pass_a": pass_a,
            "enriched_steps": [{"step_number": 1}],
            "cached": True,
        }

        mock_llm = MagicMock()
        mock_llm.complete_structured_json.return_value = {
            "steps": [{"step_number": 1, "phrases": [{"text": "lie", "category": "action"}]}],
        }
        mock_llm.last_usage = {}

        with patch.object(analysis_module, "run_pass_a", return_value=pass_a), \
             patch.object(analysis_module, "run_pass_0b_lang", return_value=lang_result), \
             patch.object(analysis_module, "load_cached_analysis", return_value=None), \
             patch.object(analysis_module, "save_cached_analysis"), \
             patch.object(analysis_module, "_get_llm", return_value=mock_llm):
            result = analysis_module.run_pass_0b_word_lang("test text")

        assert result["cached"] is False


class TestRunPass0bCondensed:
    def test_requires_somatic_and_lang_inputs(self, analysis_module):
        """Should handle missing upstream results gracefully by using empty defaults."""
        # run_pass_0b_condensed takes somatic_result and lang_result as params
        somatic_result = {"pass_a": {"steps": [], "groups": []}, "enriched_steps": [], "summary": {}}
        lang_result = {"enriched_steps": [], "summary": {}}

        cached_data = {"segments": [{"segment_number": 1, "group_cards": []}]}

        with patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_condensed(
                somatic_result, lang_result, "test text"
            )
        assert result["cached"] is True
        assert "segments" in result

    def test_merges_somatic_and_lang(self, analysis_module):
        """Should use both somatic and lang results."""
        somatic_result = {
            "pass_a": _make_pass_a_result(),
            "enriched_steps": [{"step_number": 1, "differentiation": "pelvis"}],
            "summary": {"pattern": "test"},
        }
        lang_result = {
            "enriched_steps": [{"step_number": 1, "language_type": "directive"}],
            "summary": {"speaker_principles": "economy"},
        }

        cached_data = {"segments": [{"segment_number": 1, "group_cards": [{"title": "test"}]}]}

        with patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_condensed(
                somatic_result, lang_result, "test text"
            )
        assert result["cached"] is True


class TestRunPass0bConcepts:
    def test_extracts_concept_tags(self, analysis_module):
        """Should return concepts from cache."""
        cached_data = {
            "concepts": [{"category": "body_part", "value": "pelvis"}],
            "distinctive_summary": "A lesson about pelvis",
            "search_terms": ["pelvis", "floor"],
        }

        somatic_result = {
            "pass_a": _make_pass_a_result(),
            "enriched_steps": [{"step_number": 1}],
        }

        with patch.object(analysis_module, "run_pass_0b", return_value=somatic_result), \
             patch.object(analysis_module, "load_pass_0b_concepts", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_concepts("test text")

        assert result["cached"] is True
        assert len(result["concepts"]) == 1

    def test_concept_format(self, analysis_module):
        """Each concept should have category and value."""
        cached_data = {
            "concepts": [
                {"category": "body_part", "value": "pelvis"},
                {"category": "movement", "value": "rolling"},
            ],
            "distinctive_summary": "test",
            "search_terms": [],
        }

        somatic_result = {
            "pass_a": _make_pass_a_result(),
            "enriched_steps": [],
        }

        with patch.object(analysis_module, "run_pass_0b", return_value=somatic_result), \
             patch.object(analysis_module, "load_pass_0b_concepts", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_concepts("test text")

        for concept in result["concepts"]:
            assert "category" in concept
            assert "value" in concept


class TestRunPass0bSpatial:
    def test_extracts_spatial_concepts(self, analysis_module):
        """Should return spatial groups from cache."""
        cached_data = {
            "groups": [{"group_number": 1, "position": "supine"}],
        }

        somatic_result = {
            "pass_a": _make_pass_a_result(),
            "enriched_steps": [],
        }

        with patch.object(analysis_module, "run_pass_0b", return_value=somatic_result), \
             patch.object(analysis_module, "load_pass_0b_spatial", return_value=("Prompt", {"type": "object"})), \
             patch.object(analysis_module, "load_cached_analysis", return_value=cached_data):
            result = analysis_module.run_pass_0b_spatial("test text")

        assert result["cached"] is True
        assert result["groups"][0]["position"] == "supine"


class TestFormatInputForConcepts:
    def test_formats_for_concept_extraction(self, analysis_module):
        """Should include group structure, steps, and somatic fields."""
        pass_a = _make_pass_a_result()
        somatic_steps = [
            {"step_number": 1, "differentiation": "pelvis-spine",
             "activation": "hip flexors", "inhibition": "none",
             "transmission_route": "pelvis to spine", "ground_push": "feet"},
        ]

        result = analysis_module.format_input_for_concepts(pass_a, somatic_steps)

        assert "## Group 1: Setup" in result
        assert "### Step 1" in result
        assert "differentiation" in result
        assert "pelvis-spine" in result
