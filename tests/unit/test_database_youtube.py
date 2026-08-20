from contextlib import contextmanager
from unittest.mock import MagicMock

from kb.db.youtube import YouTubeMixin


class FakeDb(YouTubeMixin):
    def __init__(self):
        self.cursor = MagicMock()

    @contextmanager
    def get_cursor(self, commit=True):
        yield self.cursor


def test_claim_increments_persisted_attempt_count():
    db = FakeDb()
    db.cursor.rowcount = 1

    assert db.claim_youtube_url("queue-1") is True

    sql, params = db.cursor.execute.call_args.args
    assert "attempts = attempts + 1" in sql
    assert params == ("queue-1",)


def test_pending_query_respects_retry_time():
    db = FakeDb()
    db.cursor.fetchall.return_value = []

    db.get_pending_youtube_urls(limit=4)

    sql, params = db.cursor.execute.call_args.args
    assert "next_attempt_at <= NOW()" in sql
    assert params == (4,)


def test_requeue_is_bounded_by_attempt_count_and_delay():
    db = FakeDb()
    db.cursor.rowcount = 2

    assert db.requeue_retryable_youtube_urls(max_attempts=3) == 2

    sql, params = db.cursor.execute.call_args.args
    assert "attempts < %s" in sql
    assert "COALESCE(next_attempt_at, updated_at) <= NOW()" in sql
    assert params == (3,)


def test_failure_records_delayed_retry_without_losing_error():
    db = FakeDb()

    db.fail_youtube_url("queue-1", "temporary failure", retry_delay_minutes=15)

    sql, params = db.cursor.execute.call_args.args
    assert "status = 'failed'" in sql
    assert "next_attempt_at" in sql
    assert params == ("temporary failure", 15, "queue-1")


def test_completion_clears_stale_retry_diagnostics():
    db = FakeDb()

    db.complete_youtube_url("queue-1")

    sql, params = db.cursor.execute.call_args.args
    assert "status = 'completed'" in sql
    assert "error = NULL" in sql
    assert "next_attempt_at = NULL" in sql
    assert params == ("queue-1",)
