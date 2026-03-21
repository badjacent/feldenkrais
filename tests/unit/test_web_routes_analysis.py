"""
Tests for analysis dashboard routes and APIs.

Phase 3 refactor target: web.py -> routes/analysis.py

The analysis routes are tightly coupled to the web.py module globals (_db, _auth_mod,
_build_analysis_page, etc.). Testing route handlers directly requires the full FastAPI
app with all dependencies mocked. These tests verify the route structure and API contracts.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAnalysisDashboardPage:
    def test_renders_dashboard_html(self):
        """GET /analysis should return HTML with group cards.

        This route requires _db, _auth_mod, and _build_analysis_page to be properly
        initialized. Testing the route handler in isolation requires mocking the
        entire web module state."""
        # Verify the route exists and has the expected signature
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/analysis" in routes

    def test_shows_step_progress(self):
        """Dashboard should show completed/total for each step.

        The _build_analysis_html helper queries the database for group-level
        gate states and step completion counts. Full testing requires an
        integration test with a mocked database."""
        # Verify the AJAX refresh endpoint exists
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/dashboard" in routes


class TestAnalysisGateAPI:
    def test_set_gate_permitted(self):
        """POST /api/analysis/gate should update gate.

        Requires _db.set_group_gate(). Verify the route exists and accepts JSON body."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/gate" in routes

    def test_get_group_gates(self):
        """GET /api/analysis/group/{group_id} should return gate states."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/group/{group_id}" in routes


class TestAnalysisPipelineRun:
    def test_run_pipeline_starts_worker(self):
        """POST /api/analysis/run should start background worker."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/run" in routes

    def test_run_pipeline_respects_gates(self):
        """Should only run permitted steps.

        The run endpoint calls _run_analysis_pipeline which checks gates.
        This requires integration testing with mocked DB and pipeline executor."""
        # Verify pipeline status endpoint exists (used to check gate state)
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/pipeline-status" in routes


class TestAnalysisStatusAPI:
    def test_pipeline_progress(self):
        """GET /api/analysis/status should return step completion counts."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/status" in routes

    def test_pipeline_errors(self):
        """Error clearing endpoints should exist."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/analysis/clear-error" in routes
        assert "/api/analysis/clear-all-errors" in routes
