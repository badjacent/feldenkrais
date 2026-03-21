"""
Tests for web middleware: auth gating, subdomain routing.

Phase 3 refactor target: web.py → middleware.py, auth_helpers.py
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from starlette.testclient import TestClient


class TestSubdomainDetection:
    def test_is_search_site(self):
        """Should detect search subdomain from Host header."""
        pytest.skip("TODO: mock request with search domain host")

    def test_is_finder_site(self):
        pytest.skip("TODO")

    def test_is_interiority_site(self):
        pytest.skip("TODO")

    def test_main_domain_not_subdomain(self):
        """Main domain should not trigger any subdomain checks."""
        pytest.skip("TODO")

    def test_localhost_not_subdomain(self):
        pytest.skip("TODO")


class TestAuthMiddleware:
    def test_auth_routes_always_accessible(self):
        """/auth/* should bypass all guards."""
        pytest.skip("TODO")

    def test_finder_is_public(self):
        """Finder subdomain should not require login."""
        pytest.skip("TODO")

    def test_interiority_is_public(self):
        """Interiority subdomain should not require login."""
        pytest.skip("TODO")

    def test_lambda_requires_login(self):
        """Non-public routes on Lambda should require auth."""
        pytest.skip("TODO: mock IS_LAMBDA=True")

    def test_local_dev_no_auth(self):
        """Local dev (IS_LAMBDA=False) should not require auth."""
        pytest.skip("TODO")


class TestFinderPathRestrictions:
    def test_finder_allows_root(self):
        pytest.skip("TODO")

    def test_finder_allows_api_finder(self):
        pytest.skip("TODO")

    def test_finder_blocks_other_paths(self):
        """Should redirect non-allowed paths to /."""
        pytest.skip("TODO")


class TestInterioritySiteRestrictions:
    def test_interiority_allows_root(self):
        pytest.skip("TODO")

    def test_interiority_blocks_other_paths(self):
        pytest.skip("TODO")
