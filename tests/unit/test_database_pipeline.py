"""
Tests for database pipeline state, lesson groups, gates, and dashboard.

Phase 1 refactor target: database.py → db_pipeline_state.py, db_lesson_groups.py, db_dashboard.py
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_db():
    with patch("kb.database.psycopg2") as mock_pg:
        from kb.database import Database

        mock_pool = MagicMock()
        mock_pg.pool.SimpleConnectionPool.return_value = mock_pool
        db = Database.__new__(Database)
        db._pool = mock_pool
        db.public_only = False
        yield db


class TestAnalysisSteps:
    def test_get_analysis_steps(self, mock_db):
        """Should return list of step dicts with name, version, etc."""
        pytest.skip("TODO")

    def test_update_step_version(self, mock_db):
        pytest.skip("TODO")


class TestDocumentAnalysisState:
    def test_mark_step_completed(self, mock_db):
        pytest.skip("TODO")

    def test_mark_step_error(self, mock_db):
        pytest.skip("TODO")

    def test_clear_step_error(self, mock_db):
        pytest.skip("TODO")

    def test_get_runnable_steps(self, mock_db):
        """Should return steps where permitted=True and not yet completed at current version."""
        pytest.skip("TODO")

    def test_count_runnable_steps(self, mock_db):
        pytest.skip("TODO")


class TestLessonGroups:
    def test_create_lesson_group(self, mock_db):
        pytest.skip("TODO")

    def test_add_group_members(self, mock_db):
        pytest.skip("TODO")

    def test_get_group_members(self, mock_db):
        pytest.skip("TODO")

    def test_sync_auto_groups(self, mock_db):
        """Should create/update groups based on source_id."""
        pytest.skip("TODO")


class TestGroupGates:
    def test_set_gate_permitted(self, mock_db):
        pytest.skip("TODO")

    def test_set_gate_not_permitted(self, mock_db):
        pytest.skip("TODO")

    def test_get_group_gates(self, mock_db):
        pytest.skip("TODO")


class TestDashboard:
    def test_get_group_dashboard(self, mock_db):
        """Should return progress stats per group."""
        pytest.skip("TODO")

    def test_get_pipeline_progress(self, mock_db):
        pytest.skip("TODO")

    def test_get_pipeline_errors(self, mock_db):
        pytest.skip("TODO")
