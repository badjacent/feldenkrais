"""The loader stays off the database when there is nothing to load."""

from unittest.mock import MagicMock, patch

from kb.ingest import loader


class _Storage:
    def __init__(self, keys):
        self.keys = set(keys)

    def list_objects(self, prefix):
        return [{"key": k} for k in sorted(self.keys) if k.startswith(prefix)]

    def object_exists(self, key):
        return key in self.keys

    def copy_object(self, source, dest):
        self.keys.add(dest)

    def delete_object(self, key):
        self.keys.discard(key)


def _run(storage):
    with patch.object(loader, "Config") as config, patch.object(loader, "Storage", return_value=storage), \
            patch.object(loader, "Database") as database, \
            patch.object(loader, "process_file", return_value={"status": "loaded"}):
        config.from_env.return_value = MagicMock(bucket="b", database_url="postgres://x")
        result = loader.handler({}, None)
    return result, database


def test_an_idle_run_never_connects_to_the_database():
    # Sidecars left behind without their main file do not count as work.
    result, database = _run(_Storage({"staging/", "staging/old.mp3.json", "failed/x.mp3"}))
    assert result["statusCode"] == 200
    assert "Nothing to load" in result["body"]
    database.assert_not_called()


def test_a_staged_file_still_connects():
    _, database = _run(_Storage({"staging/new.mp3", "staging/new.mp3.json"}))
    database.assert_called_once()


def test_a_permanent_failure_is_parked_in_failed():
    storage = _Storage({"errors/bad.mp3", "errors/bad.mp3.json"})
    db = MagicMock()
    db.count_events_for_filename.side_effect = lambda event, name: loader.MAX_LOAD_RETRIES if event == "load_error" else 1
    results = loader.retry_from_errors(storage, db)
    assert results == [{"filename": "bad.mp3", "status": "permanent_failure"}]
    assert storage.keys == {"failed/bad.mp3", "failed/bad.mp3.json"}
