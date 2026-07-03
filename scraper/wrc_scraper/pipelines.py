"""
Scrapy item pipelines.

The spider only discovers result metadata. Pipelines run afterwards and do the
repeated housekeeping work: validate fields, download files, and store data.
"""

import sys
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import requests
from scrapy.exceptions import DropItem

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from config import settings
from utils.database import MetadataStore
from utils.logging_config import get_logger, setup_logging
from utils.storage import ObjectStorage

setup_logging()
logger = get_logger(__name__)


class ValidationPipeline:
    """Stop incomplete records before they reach storage."""

    required_fields = ["identifier", "document_url", "body", "partition_date"]

    def process_item(self, item, spider):
        missing = [field for field in self.required_fields if not item.get(field)]
        if missing:
            raise DropItem(f"Missing required fields: {missing}")
        return item


class FileDownloadPipeline:
    """Download the real document bytes from the result link."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "KedraAssessmentWrcScraper/1.0"})

    def process_item(self, item, spider):
        url = item["document_url"]
        if url.startswith("/"):
            url = urljoin(settings.wrc_base_url, url)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            spider.stats["failed_downloads"] += 1
            spider.stats["failed_urls"].append({"url": url, "reason": str(exc)})
            logger.error(
                "download_failed",
                extra={
                    "identifier": item["identifier"],
                    "url": url,
                    "error": str(exc),
                    "status_code": getattr(exc.response, "status_code", None),
                },
            )
            raise DropItem(f"Download failed: {url}") from exc

        item["document_url"] = url
        item["file_extension"] = self._guess_extension(url, response.headers.get("Content-Type", ""))

        # WRC pages include small volatile comments such as elapsed render time.
        # Removing those makes the hash stable when the legal document itself
        # has not changed.
        if item["file_extension"] == "html":
            item["file_content"] = self._normalize_html_bytes(response.content)
        else:
            item["file_content"] = response.content
        return item

    def _guess_extension(self, url: str, content_type: str) -> str:
        """Prefer the real URL extension, then fall back to the Content-Type."""
        path = urlparse(url).path.lower()
        for extension in [".pdf", ".docx", ".doc", ".html", ".htm"]:
            if path.endswith(extension):
                return "html" if extension == ".htm" else extension.lstrip(".")

        if "pdf" in content_type:
            return "pdf"
        if "wordprocessingml" in content_type:
            return "docx"
        if "msword" in content_type:
            return "doc"
        return "html"

    def _normalize_html_bytes(self, content: bytes) -> bytes:
        html = content.decode("utf-8", errors="replace")
        html = re.sub(r"<!--\s*Elapsed time:.*?-->", "", html, flags=re.I | re.S)
        html = re.sub(
            r"<!--\s*cached or not being index\.aspx page\s*-->",
            "",
            html,
            flags=re.I,
        )
        return html.encode("utf-8")


class StoragePipeline:
    """Store file bytes in MinIO and metadata in MongoDB."""

    def open_spider(self, spider):
        self.db = MetadataStore()
        self.db.ensure_indexes()
        self.storage = ObjectStorage()
        self.storage.ensure_buckets()

    def close_spider(self, spider):
        self.db.close()

    def process_item(self, item, spider):
        identifier = item["identifier"]
        file_content = item["file_content"]
        file_hash = self.storage.sha256(file_content)

        existing = self.db.get_by_identifier(settings.landing_collection, identifier)
        if existing and existing.get("file_hash") == file_hash:
            item["file_hash"] = file_hash
            item["file_path"] = existing["file_path"]
            logger.info("unchanged_file_skipped", extra={"identifier": identifier})
        else:
            object_name = f"{item['partition_date']}/{identifier}.{item['file_extension']}"
            file_hash = self.storage.upload_bytes(settings.landing_bucket, object_name, file_content)
            item["file_hash"] = file_hash
            item["file_path"] = f"{settings.landing_bucket}/{object_name}"
            logger.info(
                "file_uploaded",
                extra={
                    "identifier": identifier,
                    "partition_date": item["partition_date"],
                    "file_path": item["file_path"],
                },
            )

        self.db.upsert(
            settings.landing_collection,
            {
                "identifier": identifier,
                "description": item.get("description", ""),
                "published_date": item.get("published_date", ""),
                "document_url": item["document_url"],
                "body": item["body"],
                "partition_date": item["partition_date"],
                "file_extension": item["file_extension"],
                "file_path": item["file_path"],
                "file_hash": item["file_hash"],
            },
        )

        spider.stats["records_scraped"] += 1
        del item["file_content"]
        return item
