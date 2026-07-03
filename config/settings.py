"""
Central configuration for the whole project.

The assessment asks for no hardcoded connection strings or scraping values.
This file reads those values from environment variables, with local defaults
that work with docker-compose.
"""

from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str, cast=str):
    """Read an environment variable and convert it to the requested type."""
    value = os.getenv(name, default)
    if cast is bool:
        return value.lower() in {"1", "true", "yes", "on"}
    return cast(value)


@dataclass(frozen=True)
class Settings:
    # Website settings.
    wrc_base_url: str = "https://www.workplacerelations.ie"
    wrc_search_url: str = "https://www.workplacerelations.ie/en/search/"

    # MongoDB settings.
    mongo_host: str = env("MONGO_HOST", "localhost")
    mongo_port: int = env("MONGO_PORT", "27017", int)
    mongo_username: str = env("MONGO_USERNAME", "admin")
    mongo_password: str = env("MONGO_PASSWORD", "password123")
    mongo_database: str = env("MONGO_DATABASE", "wrc_pipeline")
    landing_collection: str = env("LANDING_COLLECTION", "landing_documents")
    processed_collection: str = env("PROCESSED_COLLECTION", "processed_documents")

    # MinIO settings.
    minio_host: str = env("MINIO_HOST", "localhost")
    minio_port: int = env("MINIO_PORT", "9000", int)
    minio_user: str = env("MINIO_ROOT_USER", "minioadmin")
    minio_password: str = env("MINIO_ROOT_PASSWORD", "minioadmin123")
    minio_secure: bool = env("MINIO_SECURE", "false", bool)
    landing_bucket: str = env("LANDING_BUCKET", "landing-zone")
    processed_bucket: str = env("PROCESSED_BUCKET", "processed-zone")

    # Scraping settings.
    partition_size_days: int = env("PARTITION_SIZE_DAYS", "30", int)
    concurrent_requests: int = env("CONCURRENT_REQUESTS", "4", int)
    download_delay: float = env("DOWNLOAD_DELAY", "1.0", float)
    autothrottle_enabled: bool = env("AUTOTHROTTLE_ENABLED", "true", bool)
    retry_times: int = env("RETRY_TIMES", "3", int)

    @property
    def mongo_uri(self) -> str:
        return (
            f"mongodb://{self.mongo_username}:{self.mongo_password}"
            f"@{self.mongo_host}:{self.mongo_port}/"
        )

    @property
    def minio_endpoint(self) -> str:
        return f"{self.minio_host}:{self.minio_port}"


settings = Settings()
