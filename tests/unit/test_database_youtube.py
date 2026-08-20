from contextlib import contextmanager
from unittest.mock import MagicMock

from kb.db.youtube import YouTubeMixin


class FakeDb(YouTubeMixin):
    def __init__(self):
        self.cursor = MagicMock()

    @contextmanager
    def get_cursor(self, commit=True):
        yield self.cursor


def test_atlas_urls_enter_cloud_invisible_local_pending_state():
    db = FakeDb()

    assert db.add_youtube_url(
        "https://youtube.com/watch?v=atlas", origin="feldenkrais_atlas"
    )

    sql, params = db.cursor.execute.call_args.args
    assert "status" in sql
    assert params[-2:] == ("feldenkrais_atlas", "local_pending")


def test_claim_increments_persisted_attempt_count():
    db = FakeDb()
    db.cursor.rowcount = 1

    assert db.claim_youtube_url("queue-1") is True

    sql, params = db.cursor.execute.call_args.args
    assert "attempts = attempts + 1" in sql
    assert "local_pending" in sql
    assert params == ("queue-1",)


def test_pending_query_respects_retry_time():
    db = FakeDb()
    db.cursor.fetchall.return_value = []

    db.get_pending_youtube_urls(limit=4)

    sql, params = db.cursor.execute.call_args.args
    assert "next_attempt_at <= NOW()" in sql
    assert params == [4]


def test_pending_query_can_reserve_an_origin_for_one_worker():
    db = FakeDb()
    db.cursor.fetchall.return_value = []

    db.get_pending_youtube_urls(limit=4, origin="feldenkrais_atlas")

    sql, params = db.cursor.execute.call_args.args
    assert "origin = %s" in sql
    assert params == ["local_pending", "feldenkrais_atlas", 4]


def test_pending_query_can_exclude_a_reserved_origin():
    db = FakeDb()
    db.cursor.fetchall.return_value = []

    db.get_pending_youtube_urls(limit=4, exclude_origin="feldenkrais_atlas")

    sql, params = db.cursor.execute.call_args.args
    assert "COALESCE(origin, '') <> %s" in sql
    assert params == ["feldenkrais_atlas", 4]


def test_requeue_is_bounded_by_attempt_count_delay_and_processing_lease():
    db = FakeDb()
    db.cursor.rowcount = 2

    assert db.requeue_retryable_youtube_urls(
        max_attempts=3, stale_processing_minutes=240
    ) == 2

    sql, params = db.cursor.execute.call_args.args
    assert "attempts < %s" in sql
    assert "COALESCE(next_attempt_at, updated_at) <= NOW()" in sql
    assert "status = 'processing'" in sql
    assert "local_pending" in sql
    assert "updated_at <= NOW()" in sql
    assert params == (3, 240)


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
