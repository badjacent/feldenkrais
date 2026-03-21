"""
Tests for analysis core: framework loading, schema building, data path resolution.

Phase 2 refactor target: analysis.py → analysis_core.py, framework_builders.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestResolveDataPath:
    def test_private_path_preferred(self):
        """Should check private/data/ first, fall back to data/."""
        pytest.skip("TODO")

    def test_falls_back_to_public(self):
        """Should return public path when private missing."""
        pytest.skip("TODO")

    def test_returns_none_when_missing(self):
        pytest.skip("TODO")


class TestLoadFrameworks:
    def test_loads_framework_config(self):
        """Should return list of framework dicts."""
        pytest.skip("TODO: mock file reads")

    def test_framework_has_required_fields(self):
        """Each framework should have id, name, focus_areas."""
        pytest.skip("TODO")


class TestBuildClassificationTable:
    def test_builds_table_from_frameworks(self):
        pytest.skip("TODO")


class TestDeriveKey:
    def test_derives_key_from_framework(self):
        """Should produce VL-1, VL-2 etc from framework config."""
        pytest.skip("TODO")


class TestBuildPassASchema:
    def test_schema_matches_frameworks(self):
        """Generated schema should have properties for each active framework."""
        pytest.skip("TODO")

    def test_schema_is_valid_json_schema(self):
        """Should be parseable as a JSON schema."""
        pytest.skip("TODO")
