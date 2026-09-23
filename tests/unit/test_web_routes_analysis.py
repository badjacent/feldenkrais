"""
Tests for analysis dashboard routes and APIs.

Phase 3 refactor target: web.py -> routes/analysis.py

The analysis routes are tightly coupled to the web.py module globals (_db, _auth_mod,
_build_analysis_page, etc.). Testing route handlers directly requires the full FastAPI
app with all dependencies mocked. These tests verify the route structure and API contracts.
"""

import pytest
from unittest.mock import patch, MagicMock
from tests.unit.route_helpers import app_route_map


class TestAnalysisDashboardPage:
    def test_renders_dashboard_html(self):
        """GET /analysis should return HTML with group cards.

        This route requires _db, _auth_mod, and _build_analysis_page to be properly
        initialized. Testing the route handler in isolation requires mocking the
        entire web module state."""
        # Verify the route exists and has the expected signature
        from kb.web import app
        routes = app_route_map(app)
        assert "/analysis" in routes

    def test_shows_step_progress(self):
        """Dashboard should show completed/total for each step.

        The _build_analysis_html helper queries the database for group-level
        gate states and step completion counts. Full testing requires an
        integration test with a mocked database."""
        # Verify the AJAX refresh endpoint exists
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/dashboard" in routes


class TestAnalysisGateAPI:
    def test_set_gate_permitted(self):
        """POST /api/analysis/gate should update gate.

        Requires _db.set_group_gate(). Verify the route exists and accepts JSON body."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/gate" in routes

    def test_get_group_gates(self):
        """GET /api/analysis/group/{group_id} should return gate states."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/group/{group_id}" in routes


class TestAnalysisPipelineRun:
    def test_web_app_no_longer_runs_the_queue(self):
        """Analysis runs in the dedicated worker; the web app has no run endpoint."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/run" not in routes
        assert "/api/analysis/pipeline-status" not in routes

    def test_each_model_has_a_switch(self):
        """POST /api/analysis/profile turns a model's global switch on or off."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/profile" in routes

    def test_pipeline_is_hidden_from_the_production_site(self):
        """Under Lambda every analysis route answers 404."""
        import pytest
        from fastapi import HTTPException
        import kb.routes.analysis_routes as routes_mod

        with patch.object(routes_mod._web, "PIPELINE_LOCAL_CONTROLS", False):
            with pytest.raises(HTTPException) as err:
                routes_mod._local_deployment_only()
        assert err.value.status_code == 404
        with patch.object(routes_mod._web, "PIPELINE_LOCAL_CONTROLS", True):
            routes_mod._local_deployment_only()


class TestAnalysisStatusAPI:
    def test_pipeline_progress(self):
        """GET /api/analysis/status should return step completion counts."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/status" in routes

    def test_pipeline_errors(self):
        """Error clearing endpoints should exist."""
        from kb.web import app
        routes = app_route_map(app)
        assert "/api/analysis/clear-error" in routes
        assert "/api/analysis/clear-all-errors" in routes
