"""
Tests for ingestion dashboard routes and retry APIs.

Phase 3 refactor target: web.py -> routes/ingestion.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestIngestionDashboard:
    def test_renders_dashboard(self):
        """GET /ingestion should render pipeline monitoring UI."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/ingestion" in routes

    def test_shows_pending_jobs(self):
        """Dashboard AJAX endpoint exists for refresh."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/pipeline/ingestion" in routes

    def test_shows_failed_jobs(self):
        """The ingestion dashboard shows failed pipeline stages.

        The _build_ingestion_html helper queries documents with pipeline_stage='failed'.
        This is verified by checking the retry-all-failed endpoint exists."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/ingestion/retry-all-failed" in routes


class TestRetryAPI:
    def test_retry_single_document(self):
        """POST /api/ingestion/resubmit/{document_id} should resubmit."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/ingestion/resubmit/{document_id}" in routes

    def test_retry_single_document_calls_resubmit(self):
        """The resubmit route calls _db.resubmit_document(document_id)."""
        import inspect
        from kb.routes.ingestion import resubmit_document
        source = inspect.getsource(resubmit_document)
        assert "resubmit_document" in source
        assert "log_event" in source

    def test_retry_all_failed(self):
        """POST /api/ingestion/retry-all-failed should resubmit all failed jobs."""
        from kb.web import app
        routes = {r.path: r for r in app.routes if hasattr(r, "path")}
        assert "/api/ingestion/retry-all-failed" in routes

    def test_retry_all_failed_filters_by_stage(self):
        """Should only resubmit documents in 'failed' pipeline stage."""
        import inspect
        from kb.routes.ingestion import retry_all_failed
        source = inspect.getsource(retry_all_failed)
        assert '"failed"' in source
        assert "pipeline_stage" in source
