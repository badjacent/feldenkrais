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


def test_media_already_ingested_under_another_signature_is_skipped():
    """A re-signed URL for a file that already has a document is not downloaded."""
    from kb.media_identity import media_identity

    url = "https://ams3.digitaloceanspaces.com/eumov/a/lesson.mp3"
    db = MagicMock()
    cursor = db.get_cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"id": "doc-1", "source_url": url + "?AWSAccessKeyId=K&Signature=a&Expires=1"}]
    item = {"id": "item-2", "url": url + "?AWSAccessKeyId=K&Signature=b&Expires=2", "title": "Lesson"}
    with patch("kb.ingest.atlas_media._extract_audio") as extract, \
         patch("kb.ingest.atlas_media._finish") as finish:
        result = process(db, MagicMock(), item)
    assert result["status"] == "ineligible"
    extract.assert_not_called()
    finish.assert_called_once_with(db, "item-2", "ineligible", "already ingested as document doc-1")
    assert media_identity(item["url"]) == url


def test_media_identity_keeps_ordinary_queries():
    from kb.media_identity import is_signed, media_identity

    assert media_identity("HTTPS://Example.com/a.mp3?X-Amz-Signature=abc&X-Amz-Expires=60") == "https://example.com/a.mp3"
    assert media_identity("https://example.com/play.mp3?track=2") == "https://example.com/play.mp3?track=2"
    assert not is_signed("https://example.com/play.mp3?track=2")
