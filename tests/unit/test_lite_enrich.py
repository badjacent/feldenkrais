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
