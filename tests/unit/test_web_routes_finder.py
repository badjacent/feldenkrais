"""
Tests for finder subdomain routes.

Phase 3 refactor target: web.py → routes/finder.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestFinderPage:
    def test_renders_finder_html(self):
        """GET / on finder subdomain should render finder UI."""
        pytest.skip("TODO")


class TestFinderSearchAPI:
    def test_search_external_lessons(self):
        """POST /api/finder/search should search external lessons."""
        pytest.skip("TODO")

    def test_search_returns_ranked_results(self):
        pytest.skip("TODO")

    def test_search_empty_query(self):
        pytest.skip("TODO")


class TestFinderStatsAPI:
    def test_returns_total_and_sources(self):
        """GET /api/finder/stats should return counts."""
        pytest.skip("TODO")
