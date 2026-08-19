"""
Tests for database pipeline state, lesson groups, gates, and dashboard.

Phase 1 refactor target: database.py -> db/pipeline_state.py, db/lesson_groups.py, db/dashboard.py
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_db():
    with patch("kb.db.core.psycopg2") as mock_pg:
        from kb.database import Database

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        db = Database.__new__(Database)
        db._pool = mock_pool
        db._min_connections = 1
        db._max_connections = 10
        db.database_url = "postgres://test"
        db.public_only = False
        db.allowed_source_ids = None
        db._mock_cursor = mock_cursor
        yield db


class TestAnalysisSteps:
    def test_get_analysis_steps(self, mock_db):
        """Should return list of step dicts with name, version, etc."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"name": "decompose", "current_version": 3, "cache_suffix": ":decompose_v3",
             "depends_on": [], "description": "Decomposition"},
            {"name": "somatic", "current_version": 2, "cache_suffix": ":somatic_v2",
             "depends_on": ["decompose"], "description": "Somatic features"},
        ]
        result = mock_db.get_analysis_steps()
        assert len(result) == 2
        assert result[0]["name"] == "decompose"
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SELECT * FROM analysis_step ORDER BY name" in sql

    def test_update_step_version(self, mock_db):
        mock_db.update_analysis_step_version("decompose", 4)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert "UPDATE analysis_step SET current_version" in sql
        assert params == (4, "decompose")

    def test_advisory_lock_holds_dedicated_connection(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = {"acquired": True}

        with mock_db.advisory_lock("analysis") as acquired:
            assert acquired is True

        calls = mock_db._mock_cursor.execute.call_args_list
        assert "pg_try_advisory_lock" in calls[0][0][0]
        mock_db._pool.putconn.assert_called_once()


class TestDocumentAnalysisState:
    def test_mark_step_completed(self, mock_db):
        mock_db.mark_step_completed("doc-1", "decompose", version=3)
        calls = mock_db._mock_cursor.execute.call_args_list
        # First call: INSERT/UPDATE the state
        sql1 = calls[0][0][0]
        assert "INSERT INTO document_analysis_state" in sql1
        assert "completed_version = EXCLUDED.completed_version" in sql1
        # Second call: mark downstream as stale
        sql2 = calls[1][0][0]
        assert "SET stale = TRUE" in sql2

    def test_mark_step_error(self, mock_db):
        mock_db.mark_step_error("doc-1", "somatic", "LLM timeout")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert "INSERT INTO document_analysis_state" in sql
        assert "error = EXCLUDED.error" in sql
        assert params == ("doc-1", "somatic", "LLM timeout")

    def test_clear_step_error(self, mock_db):
        mock_db.clear_step_error("doc-1", "somatic")
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "SET error = NULL" in sql
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params == ("doc-1", "somatic")

    def test_get_runnable_steps(self, mock_db):
        """Should return steps where permitted=True and not yet completed at current version."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"document_id": "d1", "document_title": "L1", "step_name": "decompose",
             "current_version": 3, "depends_on": [], "cache_suffix": ":v3",
             "completed_version": None, "stale": False},
        ]
        result = mock_db.get_runnable_steps()
        assert len(result) == 1
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "group_analysis_gate" in sql
        assert "gag.permitted = TRUE" in sql

    def test_count_runnable_steps(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = {"count": 42}
        result = mock_db.count_runnable_steps()
        assert result == 42


class TestLessonGroups:
    def test_create_lesson_group(self, mock_db):
        mock_db._mock_cursor.fetchone.return_value = {"id": 5}
        result = mock_db.create_lesson_group("Test Group", description="A test group")
        assert result == 5
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO lesson_group" in sql

    def test_add_group_members(self, mock_db):
        mock_db.add_group_members(5, ["doc-1", "doc-2"])
        calls = mock_db._mock_cursor.execute.call_args_list
        assert len(calls) == 2
        for c in calls:
            assert "INSERT INTO lesson_group_member" in c[0][0]
            assert "ON CONFLICT DO NOTHING" in c[0][0]

    def test_get_group_members(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"document_id": "d1", "title": "Lesson 1", "created_at": None, "source_name": "YT"},
        ]
        result = mock_db.get_group_members(5)
        assert len(result) == 1
        assert result[0]["title"] == "Lesson 1"

    def test_sync_auto_groups(self, mock_db):
        """Should create/update groups based on source_id."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "YOUTUBE"},
        ]
        mock_db._mock_cursor.fetchone.return_value = {"id": 10}
        mock_db.sync_auto_groups()
        calls = [c[0][0] for c in mock_db._mock_cursor.execute.call_args_list]
        # Should query sources, create group, sync members
        assert any("SELECT DISTINCT ds.id" in c for c in calls)
        assert any("INSERT INTO lesson_group" in c for c in calls)
        assert any("INSERT INTO lesson_group_member" in c for c in calls)


class TestGroupGates:
    def test_set_gate_permitted(self, mock_db):
        mock_db.set_group_gate(5, "decompose", True)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO group_analysis_gate" in sql
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params == (5, "decompose", True)

    def test_set_gate_not_permitted(self, mock_db):
        mock_db.set_group_gate(5, "decompose", False)
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert params == (5, "decompose", False)

    def test_get_group_gates(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"group_id": 5, "step_name": "decompose", "permitted": True,
             "current_version": 3, "depends_on": [], "description": "test",
             "updated_at": None},
        ]
        result = mock_db.get_group_gates(5)
        assert len(result) == 1
        assert result[0]["permitted"] is True


class TestDashboard:
    def test_get_group_dashboard(self, mock_db):
        """Should return progress stats per group."""
        # get_analysis_steps
        mock_db._mock_cursor.fetchall.side_effect = [
            # get_analysis_steps
            [{"name": "decompose", "current_version": 3, "cache_suffix": ":v3",
              "depends_on": [], "description": "D", "updated_at": None}],
            # get_lesson_groups
            [{"id": 1, "name": "G1", "doc_count": 2, "description": None,
              "auto_source_id": None, "created_at": None}],
            # gate query
            [{"group_id": 1, "step_name": "decompose", "permitted": True}],
        ]
        result = mock_db.get_group_dashboard()
        assert len(result) == 1
        assert result[0]["gates"]["decompose"] is True

    def test_get_pipeline_progress(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"group_id": 1, "group_name": "G1", "step_name": "decompose",
             "current_version": 3, "total": 10, "completed": 8, "stale": 1, "errors": 1},
        ]
        result = mock_db.get_pipeline_progress()
        assert len(result) == 1
        assert result[0]["completed"] == 8

    def test_get_pipeline_errors(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"document_id": "d1", "title": "L1", "step_name": "somatic",
             "error": "timeout", "updated_at": None, "source_name": "YT"},
        ]
        result = mock_db.get_pipeline_errors()
        assert len(result) == 1
        assert result[0]["error"] == "timeout"
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "WHERE das.error IS NOT NULL" in sql
