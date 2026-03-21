"""
Tests for ingestion dashboard routes and retry APIs.

Phase 3 refactor target: web.py → routes/ingestion.py
"""

import pytest
from unittest.mock import patch, MagicMock


class TestIngestionDashboard:
    def test_renders_dashboard(self):
        """GET /ingestion should render pipeline monitoring UI."""
        pytest.skip("TODO")

    def test_shows_pending_jobs(self):
        pytest.skip("TODO")

    def test_shows_failed_jobs(self):
        pytest.skip("TODO")


class TestRetryAPI:
    def test_retry_single_document(self):
        """POST /api/retry/{document_id} should resubmit."""
        pytest.skip("TODO")

    def test_retry_all_failed(self):
        """POST /api/retry-all-failed should resubmit all failed jobs."""
        pytest.skip("TODO")
