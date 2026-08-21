from kb.ingest.atlas_media import _embedded_media_url


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
