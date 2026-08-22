from unittest.mock import MagicMock, patch

from kb.ingest.atlas_media import MediaTimeoutError, _embedded_media_url, process


def test_embedded_media_url_finds_podcast_mp3():
    page = '''<script>window.audio="https://cdn.example.test/show/lesson.mp3?token=x"</script>'''

    assert _embedded_media_url(page, "https://example.test/episode") == (
        "https://cdn.example.test/show/lesson.mp3?token=x"
    )


def test_embedded_media_url_supports_protocol_relative_audio():
    page = '<meta content="//cdn.example.test/lesson.m4a">'

    assert _embedded_media_url(page, "https://example.test/episode") == (
        "https://cdn.example.test/lesson.m4a"
    )


def test_media_timeout_is_persisted_as_failed(monkeypatch):
    monkeypatch.setenv("ATLAS_MEDIA_ITEM_TIMEOUT_SECONDS", "1")
    db = MagicMock()
    storage = MagicMock()
    item = {"id": "item-1", "url": "https://example.test/lesson.mp3", "title": "Lesson"}
    with patch("kb.ingest.atlas_media._extract_audio", side_effect=MediaTimeoutError("bounded")), \
         patch("kb.ingest.atlas_media._finish") as finish:
        result = process(db, storage, item)
    assert result["status"] == "failed"
    finish.assert_called_once_with(db, "item-1", "failed", "bounded")


def test_soundcloud_collection_is_marked_ineligible_without_download():
    db = MagicMock()
    storage = MagicMock()
    item = {
        "id": "item-set",
        "url": "https://soundcloud.com/feldenbear/sets/atm-ger",
        "title": "ATM German",
    }
    with patch("kb.ingest.atlas_media._extract_audio") as extract, \
         patch("kb.ingest.atlas_media._finish") as finish:
        result = process(db, storage, item)
    assert result["status"] == "ineligible"
    extract.assert_not_called()
    storage.upload_file.assert_not_called()
    finish.assert_called_once_with(
        db, "item-set", "ineligible", "collection URL is not a single lesson recording"
    )
