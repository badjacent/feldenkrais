"""Configuration management for the knowledge base."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Knowledge base configuration."""

    bucket: str
    environment: str
    db_mode: str  # "s3" or "local"
    db_path: str  # local path for DB (used in both modes as working copy)
    temp_dir: str
    grace_period_seconds: int = 300  # 5 minutes

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            bucket=os.environ.get("KB_BUCKET", ""),
            environment=os.environ.get("KB_ENV", "dev"),
            db_mode=os.environ.get("KB_DB_MODE", "s3"),
            db_path=os.environ.get("KB_DB_PATH", "/tmp/knowledge.db"),
            temp_dir=os.environ.get("TEMP_DIR", "/tmp"),
            grace_period_seconds=int(os.environ.get("KB_GRACE_PERIOD", "300")),
        )
