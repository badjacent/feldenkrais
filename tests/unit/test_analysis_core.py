"""
Tests for analysis core: framework loading, schema building, data path resolution.

Phase 2 refactor target: analysis.py -> analysis_core.py, framework_builders.py
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def analysis_module():
    """Import and initialize the analysis module with mocked dependencies."""
    import sys
    # Ensure src/kb is on the path
    kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)

    # Mock the cache_helpers and llm_config modules before importing analysis
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


class TestResolveDataPath:
    def test_configured_path_preferred(self, tmp_path, analysis_module, monkeypatch):
        """Should check configured resource directory first, fall back to data/."""
        # Set up directory structure: project/configured resource directory and project/data/
        project = tmp_path / "project"
        data_dir = project / "data"
        data_dir.mkdir(parents=True)
        private_data = tmp_path / "resources"
        private_data.mkdir(parents=True)
        monkeypatch.setenv("KB_RESOURCE_DIRS", str(private_data))

        # Create file in both locations
        (data_dir / "test.md").write_text("public")
        (private_data / "test.md").write_text("private")

        analysis_module._data_dir = data_dir
        result = analysis_module._resolve_data_path("test.md")
        assert result == private_data / "test.md"

    def test_falls_back_to_public(self, tmp_path, analysis_module):
        """Should return public path when private missing."""
        project = tmp_path / "project"
        data_dir = project / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "test.md").write_text("public")

        analysis_module._data_dir = data_dir
        result = analysis_module._resolve_data_path("test.md")
        assert result == data_dir / "test.md"

    def test_returns_public_path_when_missing(self, tmp_path, analysis_module):
        """Should return public data path even when file doesn't exist there
        (caller checks existence)."""
        project = tmp_path / "project"
        data_dir = project / "data"
        data_dir.mkdir(parents=True)

        analysis_module._data_dir = data_dir
        result = analysis_module._resolve_data_path("nonexistent.md")
        # Returns the public data path (file doesn't exist, but path is returned)
        assert result == data_dir / "nonexistent.md"


class TestLoadFrameworks:
    def test_loads_framework_config(self, tmp_path, analysis_module):
        """Should return list of framework dicts."""
        prompts_dir = tmp_path / "frameworks" / "v1"
        prompts_dir.mkdir(parents=True)
        frameworks_data = {
            "characters": [
                {"id": "VL-1", "name": "The Riser", "role": "primary", "focus": "verticality"},
                {"id": "VL-2", "name": "The Cartographer", "role": "primary", "focus": "mapping"},
            ]
        }
        (prompts_dir / "frameworks.json").write_text(json.dumps(frameworks_data))

        # Clear cache
        analysis_module._frameworks_cache.clear()
        # Mock _resolve_prompts_dir to return our tmp dir
        with patch.object(analysis_module, "_resolve_prompts_dir", return_value=(prompts_dir, "v1")):
            result = analysis_module.load_frameworks("v1")

        assert len(result) == 2
        assert result[0]["id"] == "VL-1"
        assert result[1]["name"] == "The Cartographer"

    def test_framework_has_required_fields(self, tmp_path, analysis_module):
        """Each framework should have id and name."""
        prompts_dir = tmp_path / "frameworks" / "v1"
        prompts_dir.mkdir(parents=True)
        frameworks_data = {
            "characters": [
                {"id": "VL-1", "name": "The Riser", "role": "primary", "focus": "verticality"},
            ]
        }
        (prompts_dir / "frameworks.json").write_text(json.dumps(frameworks_data))

        analysis_module._frameworks_cache.clear()
        with patch.object(analysis_module, "_resolve_prompts_dir", return_value=(prompts_dir, "v1")):
            result = analysis_module.load_frameworks("v1")

        for f in result:
            assert "id" in f
            assert "name" in f


class TestBuildClassificationTable:
    def test_builds_table_from_frameworks(self, analysis_module):
        """Should build a markdown table with headers and rows."""
        mock_frameworks = [
            {"id": "VL-1", "name": "The Riser", "role": "primary",
             "focus": "verticality", "indicators": "standing, balance"},
            {"id": "VL-2", "name": "The Cartographer", "role": "primary",
             "focus": "mapping", "indicators": "exploration, scanning"},
        ]
        with patch.object(analysis_module, "get_active_frameworks_config", return_value=mock_frameworks):
            table = analysis_module.build_classification_table("v1")

        assert "| Framework | Focus | Key Indicators |" in table
        assert "VL-1" in table
        assert "VL-2" in table
        assert "The Riser" in table
        assert "verticality" in table


class TestDeriveKey:
    def test_derives_key_from_framework(self, analysis_module):
        """Should produce vl1_riser from VL-1 + The Riser."""
        char = {"id": "VL-1", "name": "The Riser"}
        result = analysis_module._derive_key(char)
        assert result == "vl1_riser"

    def test_uses_explicit_key_if_present(self, analysis_module):
        """Should use explicit 'key' field if present."""
        char = {"id": "VL-1", "name": "The Riser", "key": "custom_key"}
        result = analysis_module._derive_key(char)
        assert result == "custom_key"

    def test_handles_multi_word_names(self, analysis_module):
        """Should handle names with multiple words."""
        char = {"id": "VL-3", "name": "The Ground Explorer"}
        result = analysis_module._derive_key(char)
        assert result == "vl3_ground_explorer"


class TestBuildPassASchema:
    def test_schema_matches_frameworks(self, tmp_path, analysis_module):
        """Generated schema should have properties for each active framework."""
        # Create a minimal pass_a.schema.json
        base_schema = {
            "type": "object",
            "properties": {
                "classifications": {"type": "object", "properties": {}, "required": []},
                "primary_framework": {"type": "string"},
                "secondary_frameworks": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array"},
            },
        }
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_a.schema.json").write_text(json.dumps(base_schema))

        analysis_module._data_dir = data_dir

        mock_frameworks = [
            {"id": "VL-1", "name": "The Riser", "role": "primary"},
            {"id": "VL-2", "name": "The Cartographer", "role": "primary"},
        ]
        with patch.object(analysis_module, "get_active_frameworks_config", return_value=mock_frameworks):
            schema = analysis_module.build_pass_a_schema("v1")

        cls_props = schema["properties"]["classifications"]["properties"]
        assert "vl1_riser" in cls_props
        assert "vl2_cartographer" in cls_props
        assert cls_props["vl1_riser"]["type"] == "number"

        # Check enum for primary_framework
        enum_vals = schema["properties"]["primary_framework"]["enum"]
        assert "VL-1" in enum_vals
        assert "VL-2" in enum_vals

    def test_schema_is_valid_json_schema(self, tmp_path, analysis_module):
        """Should be parseable as a JSON schema (has correct structure)."""
        base_schema = {
            "type": "object",
            "properties": {
                "classifications": {"type": "object", "properties": {}, "required": []},
                "primary_framework": {"type": "string"},
                "secondary_frameworks": {"type": "array", "items": {"type": "string"}},
            },
        }
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "pass_a.schema.json").write_text(json.dumps(base_schema))

        analysis_module._data_dir = data_dir

        mock_frameworks = [
            {"id": "VL-1", "name": "The Riser", "role": "primary"},
        ]
        with patch.object(analysis_module, "get_active_frameworks_config", return_value=mock_frameworks):
            schema = analysis_module.build_pass_a_schema("v1")

        # Verify it's valid JSON (can be serialized/deserialized)
        roundtripped = json.loads(json.dumps(schema))
        assert roundtripped == schema
        assert "type" in schema
        assert schema["type"] == "object"
