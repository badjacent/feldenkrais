"""Configuration management for the knowledge base."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Knowledge base configuration."""

    bucket: str
    environment: str
    database_url: str
    temp_dir: str
    grace_period_seconds: int = 300  # 5 minutes

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            bucket=os.environ.get("KB_BUCKET", ""),
            environment=os.environ.get("KB_ENV", "dev"),
            database_url=os.environ.get("DATABASE_URL", ""),
            temp_dir=os.environ.get("TEMP_DIR", "/tmp"),
            grace_period_seconds=int(os.environ.get("KB_GRACE_PERIOD", "300")),
        )
