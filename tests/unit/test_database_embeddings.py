"""
Tests for database embedding/search operations.

Phase 1 refactor target: database.py -> db/embeddings.py
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


class TestUpsertEmbedding:
    def test_inserts_new_embedding(self, mock_db):
        mock_db.upsert_embedding(
            document_id="doc-1",
            embedding=[0.1, 0.2, 0.3],
            summary_text="A test summary",
            summary_hash="abc123",
        )
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO lesson_embedding" in sql
        assert "ON CONFLICT (document_id, profile_key, method) DO UPDATE" in sql
        # No profile given: the canonical profile is written.
        assert "SELECT key FROM analysis_profile WHERE canonical" in sql

    def test_updates_existing_embedding(self, mock_db):
        """Upsert should update embedding, summary_text, summary_hash on conflict."""
        mock_db.upsert_embedding(
            document_id="doc-1",
            embedding=[0.4, 0.5],
            summary_text="Updated summary",
            summary_hash="def456",
            method="custom",
            profile_key="astra-low",
        )
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        params = mock_db._mock_cursor.execute.call_args[0][1]
        assert "embedding = EXCLUDED.embedding" in sql
        assert params[1] == "astra-low"
        assert params[2] == "custom"


class TestSearchLessons:
    def test_returns_ranked_results(self, mock_db):
        """Should return results ordered by cosine similarity."""
        mock_db._mock_cursor.fetchall.return_value = [
            {"document_id": "d1", "title": "Lesson 1", "similarity": 0.95,
             "summary_text": "summary", "source_id": 1, "category_name": None},
        ]
        results = mock_db.search_lessons([0.1, 0.2], limit=5)
        assert len(results) == 1
        assert results[0]["similarity"] == 0.95
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ORDER BY le.embedding <=>" in sql

    def test_respects_source_filter(self, mock_db):
        """Should only search accessible sources."""
        mock_db.allowed_source_ids = {1, 2}
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.search_lessons([0.1], limit=5)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ds.id = ANY(%s)" in sql

    def test_limit_parameter(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []
        mock_db.search_lessons([0.1], limit=3)
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "LIMIT %s" in sql

    def test_query_document_enforces_rendition_gate(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = []

        mock_db.search_lessons([0.1], query_document_id="query-doc")

        sql, params = mock_db._mock_cursor.execute.call_args[0]
        assert "candidate_rendition" in sql
        assert "query_rendition" in sql
        assert "candidate_rendition.lesson_key IS DISTINCT FROM query_rendition.lesson_key" in sql
        assert params.count("query-doc") == 2


class TestUpsertConcepts:
    def test_inserts_concept_tags(self, mock_db):
        with patch("psycopg2.extras.execute_values") as mock_ev:
            mock_db.upsert_concepts("doc-1", [
                {"category": "body_region", "value": "pelvis"},
                {"category": "movement_pattern", "value": "rolling"},
            ], profile_key="astra-low")
        # Should delete that profile's existing concepts first, leaving other profiles' rows
        first_call = mock_db._mock_cursor.execute.call_args_list[0]
        assert "DELETE FROM lesson_concept" in first_call[0][0]
        assert first_call[0][1] == ("doc-1", "astra-low")
        # Then batch insert via execute_values
        mock_ev.assert_called_once()


class TestSearchByConcepts:
    def test_concept_search(self, mock_db):
        mock_db._mock_cursor.fetchall.return_value = [
            {"document_id": "d1", "title": "L1", "match_count": 2,
             "matched_concepts": ["body_region:pelvis"], "source_id": 1,
             "category_name": None},
        ]
        results = mock_db.search_by_concepts(["pelvis"])
        assert len(results) == 1
        sql = mock_db._mock_cursor.execute.call_args[0][0]
        assert "ILIKE" in sql
        # Ranked by match count, with full-phrase matches weighted
        assert "ORDER BY (COUNT(*) +" in sql and ") DESC" in sql
