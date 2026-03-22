"""Shared test fixtures for the feldenkrais project."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure src/kb is on the path for all tests
KB_DIR = str(Path(__file__).resolve().parent.parent / "src" / "kb")
if KB_DIR not in sys.path:
    sys.path.insert(0, KB_DIR)


@pytest.fixture
def mock_db():
    """Create a Database instance with mocked connection pool.

    Usage:
        def test_something(self, mock_db):
            mock_db._mock_cursor.fetchone.return_value = {"id": 1}
            result = mock_db.some_method()
            sql = mock_db._mock_cursor.execute.call_args[0][0]
    """
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


@pytest.fixture
def analysis_module():
    """Import analysis module with mocked dependencies.

    Mocks cache_helpers and llm_config to avoid requiring
    database connections or API keys during tests.
    """
    import importlib

    mock_cache = MagicMock()
    mock_cache.get_cache_key = lambda text: "fake_key_" + text[:8]
    mock_cache.load_cached_analysis = MagicMock(return_value=None)
    mock_cache.save_cached_analysis = MagicMock()
    mock_cache.DEFAULT_LLM = "gemini"

    mock_llm_config = MagicMock()
    mock_llm_config.DEFAULT_LLM = "gemini"

    sys.modules["cache_helpers"] = mock_cache
    sys.modules["llm_config"] = mock_llm_config

    if "analysis" in sys.modules:
        importlib.reload(sys.modules["analysis"])
    import analysis
    return analysis


@pytest.fixture
def llm_module():
    """Import the LLM module."""
    from llm import LLM
    return LLM


@pytest.fixture
def engine_module():
    """Import the pipeline engine module."""
    pipeline_dir = str(Path(__file__).resolve().parent.parent / "src" / "kb" / "pipeline")
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from kb.pipeline.engine import PipelineGraph, NodeKind, NodeStatus, NodeState, PipelineExecutor, PipelineStateStore
    return {
        "PipelineGraph": PipelineGraph,
        "NodeKind": NodeKind,
        "NodeStatus": NodeStatus,
        "NodeState": NodeState,
        "PipelineExecutor": PipelineExecutor,
        "PipelineStateStore": PipelineStateStore,
    }
