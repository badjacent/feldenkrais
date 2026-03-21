"""
Tests for search routes.

Phase 3 refactor target: web.py → routes/search.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestSearchAPI:
    def test_search_returns_results(self):
        """POST /api/search should return ranked lesson results."""
        pytest.skip("TODO")

    def test_search_empty_query(self):
        """Should handle empty query gracefully."""
        pytest.skip("TODO")

    def test_search_respects_source_access(self):
        """Should only return results from allowed sources."""
        pytest.skip("TODO")


class TestSearchPage:
    def test_search_page_renders(self):
        """GET / on search subdomain should render search UI."""
        pytest.skip("TODO")
