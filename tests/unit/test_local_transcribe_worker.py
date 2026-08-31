import subprocess
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from kb.ingest.local_transcribe import (
    _claim,
    _find_candidates,
    _transcribe_in_subprocess,
)


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


def test_transcription_subprocess_returns_child_json():
    child = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"document_id":"doc-1","words":12}\n', stderr=""
    )
    db = MagicMock()

    with patch("kb.ingest.local_transcribe.subprocess.run", return_value=child) as run:
        result = _transcribe_in_subprocess(
            db,
            {"id": "doc-1"},
            model="test-model",
            language=None,
            dry_run=False,
            max_retries=3,
            stale_minutes=360,
            timeout_seconds=45,
        )

    assert result == {"document_id": "doc-1", "words": 12}
    assert run.call_args.kwargs["timeout"] == 45
    assert "one" in run.call_args.args[0]


def test_transcription_subprocess_fails_claim_on_timeout():
    db = MagicMock()

    with (
        patch(
            "kb.ingest.local_transcribe.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="worker", timeout=45),
        ),
        patch("kb.ingest.local_transcribe._fail_timed_out_job") as fail_job,
    ):
        result = _transcribe_in_subprocess(
            db,
            {"id": "doc-1"},
            model="test-model",
            language=None,
            dry_run=False,
            max_retries=3,
            stale_minutes=360,
            timeout_seconds=45,
        )

    assert result["timed_out"] is True
    fail_job.assert_called_once_with(db, "doc-1", 45)
