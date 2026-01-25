"""
Integration test for the scraper.

Uses mocked HTTP responses with realistic HTML fixtures,
but real S3 for staging verification.
"""

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import boto3
import pytest
import responses

# Import after responses to avoid issues
from kb.scraper import scrape
from kb.storage import Storage

TERRAFORM_DIR = Path(__file__).parent.parent.parent / "terraform"
TEST_ENV = "test"
TEST_GRACE_PERIOD = 0

# Realistic Squarespace HTML with audio embeds
MOCK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Test Recording — Test Site</title>
    <meta property="og:title" content="Test Recording Session">
</head>
<body>
    <h1>Test Recording Session</h1>

    <div class="sqs-audio-embed"
         data-url="https://static1.squarespace.com/static/abc123/t/def456/1234567890/track1.mp3/original/track1.mp3"
         data-title="Track 1 - Test">
    </div>

    <div class="sqs-audio-embed"
         data-url="https://static1.squarespace.com/static/abc123/t/ghi789/1234567891/track2.mp3/original/track2.mp3"
         data-title="Track 2 - Test">
    </div>

    <audio src="https://example.com/audio/bonus.mp3"></audio>

    <a href="https://example.com/files/document.pdf">Download PDF</a>
</body>
</html>
"""

# Fake audio content (just bytes for testing)
MOCK_AUDIO_CONTENT = b"fake audio content for testing - " + b"x" * 1000


@pytest.fixture(scope="module")
def aws_infrastructure():
    """Set up and tear down AWS infrastructure for tests."""
    subprocess.run(
        ["terraform", "init"],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "terraform", "apply", "-auto-approve",
            f"-var=environment={TEST_ENV}",
            f"-var=grace_period_seconds={TEST_GRACE_PERIOD}",
        ],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = json.loads(result.stdout)

    yield {
        "bucket_name": outputs["bucket_name"]["value"],
        "lambda_function_name": outputs["lambda_function_name"]["value"],
    }

    # Teardown
    bucket_name = outputs["bucket_name"]["value"]
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    bucket.objects.all().delete()
    bucket.object_versions.all().delete()

    subprocess.run(
        [
            "terraform", "destroy", "-auto-approve",
            f"-var=environment={TEST_ENV}",
            f"-var=grace_period_seconds={TEST_GRACE_PERIOD}",
        ],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )


class TestScraperIntegration:
    """Integration tests for the scraper."""

    @responses.activate
    def test_scrape_and_stage(self, aws_infrastructure):
        """Test scraping a page and staging files to S3."""
        bucket_name = aws_infrastructure["bucket_name"]
        storage = Storage(bucket_name)
        s3 = boto3.client("s3")

        test_url = "https://example.com/recordings/test-session"

        # Mock the page request
        responses.add(
            responses.GET,
            test_url,
            body=MOCK_HTML,
            status=200,
            content_type="text/html",
        )

        # Mock the audio file downloads
        responses.add(
            responses.GET,
            "https://static1.squarespace.com/static/abc123/t/def456/1234567890/track1.mp3/original/track1.mp3",
            body=MOCK_AUDIO_CONTENT,
            status=200,
            content_type="audio/mpeg",
        )
        responses.add(
            responses.GET,
            "https://static1.squarespace.com/static/abc123/t/ghi789/1234567891/track2.mp3/original/track2.mp3",
            body=MOCK_AUDIO_CONTENT,
            status=200,
            content_type="audio/mpeg",
        )
        responses.add(
            responses.GET,
            "https://example.com/audio/bonus.mp3",
            body=MOCK_AUDIO_CONTENT,
            status=200,
            content_type="audio/mpeg",
        )

        # Run scraper
        results = scrape(
            url=test_url,
            source_id=1,
            type_id=2,
            storage=storage,
            tags=["test", "integration"],
            dry_run=False,
        )

        # Verify results
        assert results["media_found"] == 3  # 2 squarespace + 1 html5 audio
        assert len(results["staged"]) == 3
        assert len(results["errors"]) == 0

        # Verify files in S3 staging/
        staging_objects = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix="staging/",
        )
        staging_keys = [obj["Key"] for obj in staging_objects.get("Contents", [])]

        # Should have 3 audio files + 3 sidecars = 6 objects (plus folder marker)
        staging_files = [k for k in staging_keys if not k.endswith("/")]
        assert len(staging_files) == 6, f"Expected 6 files, got: {staging_files}"

        # Check one sidecar content
        sidecar_response = s3.get_object(
            Bucket=bucket_name,
            Key="staging/track1.mp3.json",
        )
        sidecar = json.loads(sidecar_response["Body"].read().decode())

        assert sidecar["title"] == "Track 1 - Test"
        assert sidecar["source_url"] == test_url
        assert sidecar["source_id"] == 1
        assert sidecar["type_id"] == 2
        assert sidecar["tags"] == ["test", "integration"]
        assert "scraper_metadata" in sidecar

    @responses.activate
    def test_scrape_dry_run(self, aws_infrastructure):
        """Test dry run mode doesn't stage anything."""
        bucket_name = aws_infrastructure["bucket_name"]
        s3 = boto3.client("s3")

        test_url = "https://example.com/recordings/dry-run-test"

        responses.add(
            responses.GET,
            test_url,
            body=MOCK_HTML,
            status=200,
            content_type="text/html",
        )

        # Run scraper in dry-run mode
        results = scrape(
            url=test_url,
            source_id=1,
            type_id=2,
            tags=["test"],
            dry_run=True,
        )

        # Should find media but not stage
        assert results["media_found"] == 3
        assert "media" in results
        assert len(results["media"]) == 3
        assert len(results["staged"]) == 0

    @responses.activate
    def test_scrape_no_media(self, aws_infrastructure):
        """Test handling page with no media."""
        bucket_name = aws_infrastructure["bucket_name"]
        storage = Storage(bucket_name)

        test_url = "https://example.com/text-only-page"
        empty_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Text Only Page</title></head>
        <body><p>No media here.</p></body>
        </html>
        """

        responses.add(
            responses.GET,
            test_url,
            body=empty_html,
            status=200,
            content_type="text/html",
        )

        results = scrape(
            url=test_url,
            source_id=1,
            type_id=2,
            storage=storage,
            dry_run=False,
        )

        assert results["media_found"] == 0
        assert len(results["staged"]) == 0
        assert len(results["errors"]) == 0

    @responses.activate
    def test_scrape_with_download_error(self, aws_infrastructure):
        """Test handling download failures gracefully."""
        bucket_name = aws_infrastructure["bucket_name"]
        storage = Storage(bucket_name)

        test_url = "https://example.com/recordings/error-test"
        html_with_bad_audio = """
        <!DOCTYPE html>
        <html>
        <head><title>Error Test</title></head>
        <body>
            <div class="sqs-audio-embed"
                 data-url="https://example.com/audio/good.mp3"
                 data-title="Good Track">
            </div>
            <div class="sqs-audio-embed"
                 data-url="https://example.com/audio/bad.mp3"
                 data-title="Bad Track">
            </div>
        </body>
        </html>
        """

        responses.add(responses.GET, test_url, body=html_with_bad_audio, status=200)
        responses.add(
            responses.GET,
            "https://example.com/audio/good.mp3",
            body=MOCK_AUDIO_CONTENT,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://example.com/audio/bad.mp3",
            body="Not Found",
            status=404,
        )

        results = scrape(
            url=test_url,
            source_id=1,
            type_id=2,
            storage=storage,
            dry_run=False,
        )

        assert results["media_found"] == 2
        assert len(results["staged"]) == 1  # Only the good one
        assert len(results["errors"]) == 1  # The bad one failed
        assert "bad.mp3" in results["errors"][0]["filename"]
