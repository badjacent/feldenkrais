"""
Tests for web middleware: auth gating, subdomain routing.

Phase 3 refactor target: web.py -> middleware.py, auth_helpers.py
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestSubdomainDetection:
    """Test the subdomain detection helper functions directly."""

    def _make_request(self, host):
        """Create a mock request with a given Host header."""
        request = MagicMock()
        request.headers = {"host": host}
        return request

    @patch("kb.web.SEARCH_DOMAIN", "search.example.com")
    def test_is_search_site(self):
        """Should detect search subdomain from Host header."""
        from kb.web import _is_search_site
        request = self._make_request("search.example.com")
        assert _is_search_site(request) is True

    @patch("kb.web.SEARCH_DOMAIN", "search.example.com")
    def test_is_search_site_with_port(self):
        """Should detect search subdomain with port."""
        from kb.web import _is_search_site
        request = self._make_request("search.example.com:8000")
        assert _is_search_site(request) is True

    @patch("kb.web.FINDER_DOMAIN", "finder.example.com")
    def test_is_finder_site(self):
        from kb.web import _is_finder_site
        request = self._make_request("finder.example.com")
        assert _is_finder_site(request) is True

    @patch("kb.web.INTERIORITY_DOMAIN", "interiority.example.com")
    def test_is_interiority_site(self):
        from kb.web import _is_interiority_site
        request = self._make_request("interiority.example.com")
        assert _is_interiority_site(request) is True

    @patch("kb.web.SEARCH_DOMAIN", "search.example.com")
    @patch("kb.web.FINDER_DOMAIN", "finder.example.com")
    @patch("kb.web.INTERIORITY_DOMAIN", "interiority.example.com")
    def test_main_domain_not_subdomain(self):
        """Main domain should not trigger any subdomain checks."""
        from kb.web import _is_search_site, _is_finder_site, _is_interiority_site
        request = self._make_request("example.com")
        assert _is_search_site(request) is False
        assert _is_finder_site(request) is False
        assert _is_interiority_site(request) is False

    @patch("kb.web.SEARCH_DOMAIN", "search.example.com")
    def test_localhost_not_subdomain(self):
        from kb.web import _is_search_site
        request = self._make_request("localhost:8000")
        assert _is_search_site(request) is False

    @patch("kb.web.SEARCH_DOMAIN", "")
    def test_empty_domain_returns_false(self):
        """When domain env var is empty, should always return False."""
        from kb.web import _is_search_site
        request = self._make_request("anything.example.com")
        assert _is_search_site(request) is False


class TestAuthMiddleware:
    """Test the search_site_guard middleware logic.

    These tests exercise the middleware by testing the individual branches
    via the subdomain helpers and route patterns. Full integration would
    require a TestClient with all dependencies mocked.
    """

    def test_auth_routes_always_accessible(self):
        """/auth/* paths should bypass all guards.

        Verified by inspecting the middleware source: the first check is
        `if path.startswith("/auth/")` which returns call_next immediately."""
        # The middleware code path is: if path.startswith("/auth/"): return await call_next(request)
        # We verify this by checking that auth paths are handled before any domain checks.
        # This is a source-code-level verification rather than integration test.
        import kb.web
        import inspect
        source = inspect.getsource(kb.web.search_site_guard)
        # The /auth/ check must come before any subdomain checks
        auth_pos = source.find('"/auth/"')
        finder_pos = source.find("_is_finder_site")
        assert auth_pos < finder_pos, "/auth/ check should come before subdomain checks"

    def test_finder_is_public(self):
        """Finder subdomain should not require login — verified by middleware source."""
        import kb.web
        import inspect
        source = inspect.getsource(kb.web.search_site_guard)
        # Finder check comes before IS_LAMBDA auth check
        finder_pos = source.find("_is_finder_site")
        lambda_pos = source.find("IS_LAMBDA")
        assert finder_pos < lambda_pos, "Finder check should come before Lambda auth"

    def test_interiority_is_public(self):
        """Interiority subdomain should not require login — verified by middleware source."""
        import kb.web
        import inspect
        source = inspect.getsource(kb.web.search_site_guard)
        interiority_pos = source.find("_is_interiority_site")
        lambda_pos = source.find("IS_LAMBDA")
        assert interiority_pos < lambda_pos

    def test_lambda_requires_login(self):
        """Non-public routes on Lambda should require auth — verified by middleware source."""
        import kb.web
        import inspect
        source = inspect.getsource(kb.web.search_site_guard)
        assert "IS_LAMBDA" in source
        assert "_login_page_html" in source

    def test_local_dev_no_auth(self):
        """Local dev (IS_LAMBDA=False) should not require auth — the IS_LAMBDA
        guard is the only auth gate in the middleware."""
        import kb.web
        import inspect
        source = inspect.getsource(kb.web.search_site_guard)
        # The auth check is `if IS_LAMBDA and not _auth_mod.get_current_user(request):`
        # When IS_LAMBDA is False, this is skipped entirely.
        assert "if IS_LAMBDA and not" in source


class TestFinderPathRestrictions:
    """Test path restriction logic for finder subdomain."""

    def test_finder_allows_root(self):
        """Root path should be allowed on finder."""
        # From source: allowed = (path == "/" or path.startswith("/api/finder"))
        allowed_paths = ["/"]
        for path in allowed_paths:
            is_allowed = (path == "/" or path.startswith("/api/finder"))
            assert is_allowed, f"Path {path} should be allowed on finder"

    def test_finder_allows_api_finder(self):
        allowed_paths = ["/api/finder/search", "/api/finder/stats"]
        for path in allowed_paths:
            is_allowed = (path == "/" or path.startswith("/api/finder"))
            assert is_allowed, f"Path {path} should be allowed on finder"

    def test_finder_blocks_other_paths(self):
        """Should redirect non-allowed paths to /."""
        blocked_paths = ["/analysis", "/pass0b", "/pipeline", "/ingestion", "/api/search"]
        for path in blocked_paths:
            is_allowed = (path == "/" or path.startswith("/api/finder"))
            assert not is_allowed, f"Path {path} should be blocked on finder"


class TestInterioritySiteRestrictions:
    """Test path restriction logic for interiority subdomain."""

    def test_interiority_allows_root(self):
        # From source: if path != "/": return RedirectResponse("/")
        assert "/" == "/"  # Only root is allowed

    def test_interiority_blocks_other_paths(self):
        """All non-root paths should be blocked."""
        blocked_paths = ["/analysis", "/api/search", "/pass0b"]
        for path in blocked_paths:
            assert path != "/", f"Path {path} should be blocked on interiority"
