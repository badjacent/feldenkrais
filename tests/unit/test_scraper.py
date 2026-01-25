"""
Unit tests for the scraper module.

Tests scraping logic with mocked HTTP - no AWS required.
"""

import pytest
import responses
from bs4 import BeautifulSoup

from kb.scraper import (
    find_squarespace_audio,
    find_html5_media,
    find_direct_links,
    get_filename_from_url,
    extract_page_title,
    scrape_page,
)


class TestFilenameExtraction:
    """Tests for get_filename_from_url."""

    def test_simple_url(self):
        url = "https://example.com/audio/track.mp3"
        assert get_filename_from_url(url) == "track.mp3"

    def test_squarespace_url(self):
        url = "https://static1.squarespace.com/static/abc/t/def/123/file.mp3/original/file.mp3"
        assert get_filename_from_url(url) == "file.mp3"

    def test_url_encoded_filename(self):
        url = "https://example.com/audio/my%20track%20name.mp3"
        assert get_filename_from_url(url) == "my_track_name.mp3"

    def test_special_characters_sanitized(self):
        url = "https://example.com/audio/track (1) [final].mp3"
        filename = get_filename_from_url(url)
        assert "(" not in filename
        assert "[" not in filename
        assert filename.endswith(".mp3")


class TestPageTitleExtraction:
    """Tests for extract_page_title."""

    def test_og_title(self):
        html = '<html><head><meta property="og:title" content="OG Title"></head></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert extract_page_title(soup) == "OG Title"

    def test_title_tag(self):
        html = "<html><head><title>Page Title</title></head></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert extract_page_title(soup) == "Page Title"

    def test_h1_fallback(self):
        html = "<html><body><h1>Heading Title</h1></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert extract_page_title(soup) == "Heading Title"

    def test_og_title_preferred(self):
        html = """
        <html>
        <head>
            <meta property="og:title" content="OG Title">
            <title>Page Title</title>
        </head>
        <body><h1>Heading</h1></body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert extract_page_title(soup) == "OG Title"

    def test_no_title(self):
        html = "<html><body><p>No title here</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert extract_page_title(soup) is None


class TestSquarespaceAudio:
    """Tests for find_squarespace_audio."""

    def test_data_url_attribute(self):
        html = """
        <div class="sqs-audio-embed"
             data-url="https://static1.squarespace.com/audio.mp3"
             data-title="Test Track">
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        media = find_squarespace_audio(soup)

        assert len(media) == 1
        assert media[0].url == "https://static1.squarespace.com/audio.mp3"
        assert media[0].title == "Test Track"

    def test_multiple_embeds(self):
        html = """
        <div class="sqs-audio-embed" data-url="https://example.com/track1.mp3" data-title="Track 1"></div>
        <div class="sqs-audio-embed" data-url="https://example.com/track2.mp3" data-title="Track 2"></div>
        <div class="sqs-audio-embed" data-url="https://example.com/track3.mp3" data-title="Track 3"></div>
        """
        soup = BeautifulSoup(html, "html.parser")
        media = find_squarespace_audio(soup)

        assert len(media) == 3
        assert media[0].title == "Track 1"
        assert media[2].title == "Track 3"

    def test_no_audio_embeds(self):
        html = "<html><body><p>No audio here</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        media = find_squarespace_audio(soup)

        assert len(media) == 0

    def test_embed_without_url(self):
        html = '<div class="sqs-audio-embed" data-title="No URL"></div>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_squarespace_audio(soup)

        assert len(media) == 0


class TestHtml5Media:
    """Tests for find_html5_media."""

    def test_audio_tag(self):
        html = '<audio src="https://example.com/audio.mp3"></audio>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_html5_media(soup)

        assert len(media) == 1
        assert media[0].url == "https://example.com/audio.mp3"
        assert media[0].media_type == "audio"

    def test_video_tag(self):
        html = '<video src="https://example.com/video.mp4"></video>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_html5_media(soup)

        assert len(media) == 1
        assert media[0].url == "https://example.com/video.mp4"
        assert media[0].media_type == "video"

    def test_source_children(self):
        html = """
        <audio>
            <source src="https://example.com/audio.mp3" type="audio/mpeg">
            <source src="https://example.com/audio.ogg" type="audio/ogg">
        </audio>
        """
        soup = BeautifulSoup(html, "html.parser")
        media = find_html5_media(soup)

        assert len(media) == 2

    def test_no_media_tags(self):
        html = "<html><body><p>No media</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        media = find_html5_media(soup)

        assert len(media) == 0


class TestDirectLinks:
    """Tests for find_direct_links."""

    def test_mp3_link(self):
        html = '<a href="https://example.com/file.mp3">Download MP3</a>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_direct_links(soup, "https://example.com")

        assert len(media) == 1
        assert media[0].url == "https://example.com/file.mp3"
        assert media[0].media_type == "audio"

    def test_video_link(self):
        html = '<a href="https://example.com/file.mp4">Download Video</a>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_direct_links(soup, "https://example.com")

        assert len(media) == 1
        assert media[0].media_type == "video"

    def test_relative_url(self):
        html = '<a href="/files/audio.mp3">Download</a>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_direct_links(soup, "https://example.com/page")

        assert len(media) == 1
        assert media[0].url == "https://example.com/files/audio.mp3"

    def test_non_media_links_ignored(self):
        html = """
        <a href="https://example.com/page.html">Page</a>
        <a href="https://example.com/doc.pdf">PDF</a>
        <a href="https://example.com/audio.mp3">Audio</a>
        """
        soup = BeautifulSoup(html, "html.parser")
        media = find_direct_links(soup, "https://example.com")

        assert len(media) == 1
        assert media[0].filename == "audio.mp3"

    def test_link_text_as_title(self):
        html = '<a href="https://example.com/audio.mp3">My Great Song</a>'
        soup = BeautifulSoup(html, "html.parser")
        media = find_direct_links(soup, "https://example.com")

        assert media[0].title == "My Great Song"


class TestScrapePage:
    """Tests for scrape_page with mocked HTTP."""

    @responses.activate
    def test_full_page_scrape(self):
        test_url = "https://example.com/recordings"
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Recordings</title></head>
        <body>
            <div class="sqs-audio-embed"
                 data-url="https://cdn.example.com/track1.mp3"
                 data-title="Track 1">
            </div>
            <audio src="https://example.com/bonus.mp3"></audio>
        </body>
        </html>
        """

        responses.add(responses.GET, test_url, body=html, status=200)

        media, title = scrape_page(test_url)

        assert title == "Test Recordings"
        assert len(media) == 2

    @responses.activate
    def test_deduplication(self):
        test_url = "https://example.com/page"
        html = """
        <div class="sqs-audio-embed" data-url="https://example.com/same.mp3"></div>
        <audio src="https://example.com/same.mp3"></audio>
        <a href="https://example.com/same.mp3">Download</a>
        """

        responses.add(responses.GET, test_url, body=html, status=200)

        media, _ = scrape_page(test_url)

        # Same URL should only appear once
        assert len(media) == 1

    @responses.activate
    def test_http_error(self):
        test_url = "https://example.com/not-found"
        responses.add(responses.GET, test_url, status=404)

        with pytest.raises(Exception):
            scrape_page(test_url)
