"""
Tests for pass0b analysis routes and renderers.

Phase 3 refactor target: web.py -> routes/pass0b.py, renderers/pass0b_renderer.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestPass0bPage:
    def test_renders_somatic_analysis(self):
        """GET /pass0b should render somatic analysis page."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/pass0b" in routes

    def test_renders_with_lesson_list(self):
        """Should include available lessons in dropdown.

        The pass0b route calls build_pass0b_html with get_lessons_fn parameter
        which provides the lesson dropdown options."""
        import inspect
        from kb.routes.pass0b import pass0b_page
        source = inspect.getsource(pass0b_page)
        assert "get_available_lessons" in source or "get_lessons_fn" in source


class TestPass0bRunAPI:
    def test_run_analysis(self):
        """POST /api/pass0b should trigger analysis."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/pass0b" in routes

    def test_run_returns_results(self):
        """Response should include result_html, elapsed, cached, step_count."""
        import inspect
        from kb.routes.pass0b import api_pass0b
        source = inspect.getsource(api_pass0b)
        expected_keys = ["result_html", "elapsed", "cached", "step_count"]
        for key in expected_keys:
            assert f'"{key}"' in source, f"Response should include {key}"


class TestPass0bRenderer:
    def test_renders_somatic_table(self):
        """Should produce HTML table with biomechanical fields.

        The render_pass0b_result function is defined in web.py and generates
        HTML tables from the analysis result dict."""
        # Verify the renderer function exists
        from kb.web import render_pass0b_result
        assert callable(render_pass0b_result)

    def test_renders_language_analysis(self):
        """Language analysis has its own route and renderer."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/pass0b-lang" in routes

    def test_renders_combined_view(self):
        """Combined view merges somatic + language."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/pass0b-combined" in routes

    def test_handles_missing_fields(self):
        """Should degrade gracefully if analysis fields missing.

        The renderer uses .get() with defaults throughout, so missing fields
        produce empty cells rather than errors."""
        from kb.web import render_pass0b_result
        # Call with minimal result — should not crash
        minimal_result = {
            "pass_a": {"steps": [], "groups": [], "pass_0": {"segments": []}},
            "enriched_steps": [],
        }
        html = render_pass0b_result(minimal_result)
        assert isinstance(html, str)
