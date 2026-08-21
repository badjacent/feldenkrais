from contextlib import contextmanager

from kb.db.core import DatabaseCore


class Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return {"exists": True}


def test_initialize_takes_transaction_advisory_lock_before_schema(monkeypatch):
    database = object.__new__(DatabaseCore)
    cursor = Cursor()

    @contextmanager
    def get_cursor(commit=True):
        yield cursor

    monkeypatch.setattr(database, "get_cursor", get_cursor)
    monkeypatch.setattr(database, "sync_auto_groups", lambda: None, raising=False)

    database.initialize()

    first_query, first_params = cursor.calls[0]
    assert "pg_advisory_xact_lock" in first_query
    assert first_params == ("feldenkrais:database-initialize",)
