import json
from contextlib import contextmanager

from kb.ingest.lite_enrich import _hosting_page_context, _pending_documents, artifact_keys


def test_hosting_page_context_includes_page_and_captured_metadata():
    context = _hosting_page_context(
        {
            "title": "Rolling lesson",
            "source_name": "Example teacher",
            "source_url": "https://example.test/rolling",
            "presenter": "A. Teacher",
            "category_name": "ATM",
            "lesson_date": "2025-01-02",
            "tags": ["rolling"],
            "metadata": json.dumps(
                {"youtube": {"description": "Explore rolling on the floor."}}
            ),
        }
    )

    assert context["page_url"] == "https://example.test/rolling"
    assert context["source_name"] == "Example teacher"
    assert context["captured_metadata"]["youtube"]["description"].startswith("Explore")


def test_hosting_page_context_tolerates_non_json_metadata():
    context = _hosting_page_context({"metadata": "not-json"})

    assert context["captured_metadata"] == {"raw": "not-json"}


class _CacheCursor:
    def __init__(self, existing):
        self.existing = existing

    def execute(self, _sql, params):
        self.rows = [{"key": key} for key in params[0] if key in self.existing]

    def fetchall(self):
        return self.rows


class _CacheDb:
    def __init__(self, existing):
        self.existing = existing

    @contextmanager
    def get_cursor(self, commit=False):
        yield _CacheCursor(self.existing)


def test_pending_documents_excludes_complete_lessons_before_limit():
    documents = [{"id": index, "text": f"lesson {index}"} for index in range(4)]
    complete = set()
    for document in documents[:2]:
        keys = artifact_keys(document["text"], "motifs-v6")
        complete.update(keys[name] for name in ("lite_axes", "lite_motifs", "card"))

    pending = _pending_documents(_CacheDb(complete), documents, "motifs-v6", limit=2)

    assert [document["id"] for document in pending] == [2, 3]


def test_enrichment_batch_with_new_artifacts_publishes_the_atlas_projection():
    from unittest.mock import MagicMock, patch

    import atlas_projection

    db = MagicMock()
    with patch.object(atlas_projection, "publish", return_value={"status": "unchanged"}) as publish:
        assert atlas_projection.maybe_publish_after_enrichment(db, 0) is None
        publish.assert_not_called()

        result = atlas_projection.maybe_publish_after_enrichment(db, 3)
    publish.assert_called_once_with(db)
    assert result == {"status": "unchanged"}


def test_a_failed_publish_does_not_stop_the_enrichment_worker():
    from unittest.mock import MagicMock, patch

    import atlas_projection

    with patch.object(atlas_projection, "publish", side_effect=RuntimeError("s3 down")):
        result = atlas_projection.maybe_publish_after_enrichment(MagicMock(), 1)
    assert result == {"status": "error", "error": "s3 down"}
