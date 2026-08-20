from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from kb.ingest.local_transcribe import _claim, _find_candidates


class FakeDb:
    def __init__(self, cursor):
        self.cursor = cursor

    @contextmanager
    def get_cursor(self, commit=True):
        yield self.cursor


def test_find_candidates_includes_bounded_local_retries():
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    _find_candidates(FakeDb(cursor), 7, None, max_retries=4, stale_minutes=90)

    sql, params = cursor.execute.call_args.args
    assert "j.status = 'failed'" in sql
    assert "updated_at < NOW()" in sql
    assert "d.origin = 'feldenkrais_atlas'" in sql
    assert params == [1, "local-mlx", 4, 90, 7]


def test_claim_reuses_failed_local_job_before_inserting():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": "job-retry"}

    result = _claim(FakeDb(cursor), "doc-1", max_retries=3, stale_minutes=60)

    assert result == "job-retry"
    assert cursor.execute.call_count == 1
    sql, params = cursor.execute.call_args.args
    assert "attempts=attempts + 1" in sql
    assert params == ("doc-1", "local-mlx", 3, 60)


def test_claim_inserts_new_processing_job_when_no_retry_exists():
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.rowcount = 1

    with patch("kb.ingest.local_transcribe.uuid.uuid4", return_value="job-new"):
        result = _claim(FakeDb(cursor), "doc-1")

    assert result == "job-new"
    assert cursor.execute.call_count == 2
    assert "'processing'" in cursor.execute.call_args.args[0]
