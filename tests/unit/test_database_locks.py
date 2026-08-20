from unittest.mock import MagicMock

from kb.db.locks import LocksMixin


class LockDatabase(LocksMixin):
    def __init__(self):
        self._pool = MagicMock()
        self.conn = MagicMock()
        self.cursor = MagicMock()
        self._pool.getconn.return_value = self.conn
        self.conn.cursor.return_value = self.cursor
        self._legacy_lock_connections = {}

    def _get_pool(self):
        return self._pool


def test_legacy_lock_is_released_on_the_connection_that_acquired_it():
    db = LockDatabase()
    db.cursor.fetchone.side_effect = [
        {"pg_try_advisory_lock": True},
        {"pg_advisory_unlock": True},
    ]

    assert db.try_acquire_lock(3) is True
    assert db.release_lock(3) is True

    assert db._pool.getconn.call_count == 1
    db._pool.putconn.assert_called_once_with(db.conn)
    assert "pg_try_advisory_lock" in db.cursor.execute.call_args_list[0].args[0]
    assert "pg_advisory_unlock" in db.cursor.execute.call_args_list[1].args[0]


def test_failed_lock_attempt_returns_connection_to_pool():
    db = LockDatabase()
    db.cursor.fetchone.return_value = {"pg_try_advisory_lock": False}

    assert db.try_acquire_lock(3) is False

    db._pool.putconn.assert_called_once_with(db.conn)
