"""
Integration test for the loader Lambda.

Spins up AWS infrastructure, uploads a test file, verifies processing,
and tears down.
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

TERRAFORM_DIR = Path(__file__).parent.parent.parent / "terraform"
TEST_ENV = "test"
# Use a short grace period for testing (0 seconds)
TEST_GRACE_PERIOD = 0


@pytest.fixture(scope="module")
def aws_infrastructure():
    """Set up and tear down AWS infrastructure for tests."""
    # Apply Terraform
    subprocess.run(
        ["terraform", "init"],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
    )
    apply_result = subprocess.run(
        [
            "terraform", "apply", "-auto-approve",
            f"-var=environment={TEST_ENV}",
            f"-var=grace_period_seconds={TEST_GRACE_PERIOD}",
        ],
        cwd=TERRAFORM_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    print(apply_result.stdout)
    print(apply_result.stderr)

    # Get outputs
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

    # Teardown: empty bucket first (required before deletion)
    bucket_name = outputs["bucket_name"]["value"]
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    bucket.objects.all().delete()
    # Also delete any object versions (for versioned bucket)
    bucket.object_versions.all().delete()

    # Destroy infrastructure
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


def wait_for_processing(
    s3_client,
    bucket: str,
    staging_key: str,
    timeout: int = 60,
    interval: int = 5,
) -> bool:
    """Wait for a file to be processed (removed from staging/)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            s3_client.head_object(Bucket=bucket, Key=staging_key)
            # File still exists, keep waiting
            time.sleep(interval)
        except s3_client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                # File removed from staging - processing complete
                return True
            raise
    return False


def download_db(s3_client, bucket: str, local_path: str) -> bool:
    """Download the knowledge.db from S3. Returns True if successful."""
    try:
        s3_client.download_file(bucket, "db/knowledge.db", local_path)
        return True
    except Exception:
        return False


class TestLoaderIntegration:
    """Integration tests for the loader Lambda."""

    def test_environment_setup(self, aws_infrastructure):
        """Verify AWS infrastructure is set up correctly."""
        bucket_name = aws_infrastructure["bucket_name"]
        lambda_name = aws_infrastructure["lambda_function_name"]

        # Check bucket exists
        s3 = boto3.client("s3")
        response = s3.head_bucket(Bucket=bucket_name)
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

        # Check Lambda exists
        lambda_client = boto3.client("lambda")
        response = lambda_client.get_function(FunctionName=lambda_name)
        assert response["Configuration"]["FunctionName"] == lambda_name
        assert response["Configuration"]["Runtime"] == "python3.12"
        # Note: ReservedConcurrentExecutions removed due to account quota limits

    def test_file_processing(self, aws_infrastructure):
        """Test that a file uploaded to staging/ gets processed."""
        bucket_name = aws_infrastructure["bucket_name"]
        s3 = boto3.client("s3")

        # Create test file content
        test_filename = "test-document.txt"
        test_content = b"This is a test document for integration testing."
        staging_key = f"staging/{test_filename}"
        sidecar_key = f"staging/{test_filename}.json"

        # Create sidecar metadata
        sidecar_content = json.dumps({
            "title": "Test Document",
            "source_url": "https://example.com/test",
            "source_id": 1,
            "type_id": 1,
            "tags": ["test"],
        })

        # Upload sidecar first, then file
        s3.put_object(
            Bucket=bucket_name,
            Key=sidecar_key,
            Body=sidecar_content.encode(),
        )
        # Small delay to ensure sidecar is there
        time.sleep(1)
        s3.put_object(
            Bucket=bucket_name,
            Key=staging_key,
            Body=test_content,
        )

        # Wait for processing
        processed = wait_for_processing(s3, bucket_name, staging_key, timeout=60)
        assert processed, "File was not processed within timeout"

        # Verify file is in archive/
        archive_objects = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix="archive/",
        )
        archive_keys = [obj["Key"] for obj in archive_objects.get("Contents", [])]

        # Should have at least one file in archive (excluding the folder marker)
        archive_files = [k for k in archive_keys if not k.endswith("/")]
        assert len(archive_files) >= 1, f"Expected file in archive/, got: {archive_keys}"

        # Verify staging/ is empty (except folder marker)
        staging_objects = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix="staging/",
        )
        staging_keys = [obj["Key"] for obj in staging_objects.get("Contents", [])]
        staging_files = [k for k in staging_keys if not k.endswith("/")]
        assert len(staging_files) == 0, f"Expected staging/ to be empty, got: {staging_files}"

        # Verify DB record exists
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            assert download_db(s3, bucket_name, db_path), "Failed to download DB"

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM document WHERE title = ?", ("Test Document",))
            row = cursor.fetchone()
            conn.close()

            assert row is not None, "Document record not found in DB"
            assert row["title"] == "Test Document"
            assert row["source_url"] == "https://example.com/test"
            assert row["s3_key"].startswith("archive/")
            assert row["source_id"] == 1
            assert row["type_id"] == 1
        finally:
            os.unlink(db_path)
