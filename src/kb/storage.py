"""S3 storage operations for the knowledge base."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class Storage:
    """S3 storage wrapper for the knowledge base."""

    def __init__(self, bucket: str):
        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def get_object(self, key: str) -> Optional[bytes]:
        """Get an object from S3. Returns None if not found."""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    def get_object_metadata(self, key: str) -> Optional[dict]:
        """Get object metadata. Returns None if not found."""
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
            return {
                "last_modified": response["LastModified"],
                "content_length": response["ContentLength"],
                "content_type": response.get("ContentType"),
            }
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    def put_object(self, key: str, body: bytes, content_type: Optional[str] = None):
        """Put an object to S3."""
        kwargs = {"Bucket": self.bucket, "Key": key, "Body": body}
        if content_type:
            kwargs["ContentType"] = content_type
        self.s3.put_object(**kwargs)

    def copy_object(self, source_key: str, dest_key: str):
        """Copy an object within the bucket."""
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": source_key},
            Key=dest_key,
        )

    def delete_object(self, key: str):
        """Delete an object from S3."""
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def list_objects(self, prefix: str) -> list[dict]:
        """List objects with a given prefix."""
        response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        objects = []
        for obj in response.get("Contents", []):
            # Skip folder markers
            if obj["Key"].endswith("/"):
                continue
            objects.append({
                "key": obj["Key"],
                "last_modified": obj["LastModified"],
                "size": obj["Size"],
            })
        return objects

    def object_exists(self, key: str) -> bool:
        """Check if an object exists."""
        return self.get_object_metadata(key) is not None

    def get_object_age_seconds(self, key: str) -> Optional[float]:
        """Get the age of an object in seconds. Returns None if not found."""
        metadata = self.get_object_metadata(key)
        if metadata is None:
            return None
        last_modified = metadata["last_modified"]
        now = datetime.now(timezone.utc)
        return (now - last_modified).total_seconds()

    def download_file(self, key: str, local_path: str):
        """Download a file from S3 to local path."""
        self.s3.download_file(self.bucket, key, local_path)

    def upload_file(self, local_path: str, key: str):
        """Upload a local file to S3."""
        self.s3.upload_file(local_path, self.bucket, key)

    def parse_sidecar(self, key: str) -> Optional[dict]:
        """Parse a sidecar JSON file. Returns None if not found or invalid."""
        content = self.get_object(key)
        if content is None:
            return None
        try:
            return json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to parse sidecar {key}: {e}")
            return None
