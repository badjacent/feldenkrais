"""
Tests for analysis dashboard routes and APIs.

Phase 3 refactor target: web.py → routes/analysis.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAnalysisDashboardPage:
    def test_renders_dashboard_html(self):
        """GET /analysis should return HTML with group cards."""
        pytest.skip("TODO: TestClient with mocked db")

    def test_shows_step_progress(self):
        """Dashboard should show completed/total for each step."""
        pytest.skip("TODO")


class TestAnalysisGateAPI:
    def test_set_gate_permitted(self):
        """POST /api/analysis/gate should update gate."""
        pytest.skip("TODO")

    def test_get_group_gates(self):
        """GET /api/analysis/gates/{group_id} should return gate states."""
        pytest.skip("TODO")


class TestAnalysisPipelineRun:
    def test_run_pipeline_starts_worker(self):
        """POST /api/analysis/run should start background worker."""
        pytest.skip("TODO")

    def test_run_pipeline_respects_gates(self):
        """Should only run permitted steps."""
        pytest.skip("TODO")


class TestAnalysisStatusAPI:
    def test_pipeline_progress(self):
        """GET /api/analysis/progress should return step completion counts."""
        pytest.skip("TODO")

    def test_pipeline_errors(self):
        """GET /api/analysis/errors should return recent errors."""
        pytest.skip("TODO")
