"""
MinIO helper functions.

MinIO behaves like AWS S3 but runs locally in Docker. The pipeline uses it as
object storage for landing and processed files.
"""

from io import BytesIO
from typing import Optional
import hashlib
import mimetypes

from minio import Minio

from config import settings


class ObjectStorage:
    def __init__(self):
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_user,
            secret_key=settings.minio_password,
            secure=settings.minio_secure,
        )

    def ensure_buckets(self) -> None:
        """Create landing and processed buckets if they do not exist yet."""
        for bucket in [settings.landing_bucket, settings.processed_bucket]:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    @staticmethod
    def sha256(content: bytes) -> str:
        """Create a stable fingerprint of a file's bytes."""
        return hashlib.sha256(content).hexdigest()

    def upload_bytes(self, bucket: str, object_name: str, content: bytes) -> str:
        """Upload bytes and return the file hash stored in metadata."""
        content_type = mimetypes.guess_type(object_name)[0] or "application/octet-stream"
        self.client.put_object(
            bucket,
            object_name,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        return self.sha256(content)

    def download_bytes(self, bucket: str, object_name: str) -> Optional[bytes]:
        """Download an object. Return None when the object cannot be read."""
        try:
            response = self.client.get_object(bucket, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception:
            return None
