import json

from kb.ingest.lite_enrich import _hosting_page_context


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
