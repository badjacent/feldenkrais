"""Dead pooled connections are discarded instead of reused."""

from unittest.mock import MagicMock

import pytest

from kb.database import Database


class _Conn:
    def __init__(self, closed=0, fail=False):
        self.closed = closed
        self.fail = fail
        self.rolled_back = False

    def cursor(self, cursor_factory=None):
        conn = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                if conn.fail:
                    conn.closed = 2
                    raise RuntimeError("SSL connection has been closed unexpectedly")

        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        if self.closed:
            raise RuntimeError("connection already closed")
        self.rolled_back = True


class _Pool:
    def __init__(self, conns):
        self.conns = list(conns)
        self.returned = []

    def getconn(self):
        return self.conns.pop(0)

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


def _db(pool):
    db = Database.__new__(Database)
    db._get_pool = lambda: pool
    return db


def test_a_connection_closed_in_the_pool_is_replaced():
    dead, live = _Conn(closed=1), _Conn()
    pool = _Pool([dead, live])
    with _db(pool).get_cursor() as cursor:
        cursor.execute("SELECT 1")
    assert pool.returned == [(dead, True), (live, False)]


def test_a_connection_that_dies_mid_statement_is_not_returned_for_reuse():
    dying = _Conn(fail=True)
    pool = _Pool([dying])
    with pytest.raises(RuntimeError, match="closed unexpectedly"):
        with _db(pool).get_cursor() as cursor:
            cursor.execute("SELECT 1")
    assert pool.returned == [(dying, True)]


def test_transcriber_reconnects_after_a_dropped_connection():
    from kb.ingest import local_transcribe
    pool = _Pool([_Conn(fail=True), _Conn()])
    local_transcribe._reconnect(_db(pool))
    assert [close for _, close in pool.returned] == [True, False]
