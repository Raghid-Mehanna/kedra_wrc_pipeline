"""
Scrapy item pipelines.

The spider only discovers result metadata. Pipelines run afterwards and do the
repeated housekeeping work: validate fields, download files, and store data.
"""

import sys
from pathlib import Path
import re
import hashlib
from html import unescape
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

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
        self.session.headers.update({"User-Agent": "WrcScraper/1.0"})

    def process_item(self, item, spider):
        url = item["document_url"]
        if url.startswith("/"):
            url = urljoin(settings.wrc_base_url, url)
        url = canonicalize_url(url)

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
        document_url = item["document_url"]
        object_stem = self._object_stem(identifier)
        file_content = item["file_content"]
        file_hash = self.storage.sha256(file_content)

        existing = self.db.get_by_document_url(settings.landing_collection, document_url)
        if existing and existing.get("file_hash") == file_hash:
            object_name = existing["file_path"].split("/", 1)[1]
            file_path = existing["file_path"]
            colliding_document = self._path_collision(file_path, document_url)
            if colliding_document:
                object_name = self._unique_object_name(
                    item["partition_date"],
                    object_stem,
                    item["file_extension"],
                    document_url,
                    force_suffix=True,
                )
                file_hash = self.storage.upload_bytes(settings.landing_bucket, object_name, file_content)
                file_path = f"{settings.landing_bucket}/{object_name}"
                logger.info(
                    "file_relocated_after_path_collision",
                    extra={
                        "identifier": identifier,
                        "document_url": document_url,
                        "file_path": file_path,
                    },
                )
            else:
                logger.info(
                    "unchanged_file_skipped",
                    extra={"identifier": identifier, "document_url": document_url},
                )

            item["file_hash"] = file_hash
            item["file_path"] = file_path
        else:
            object_name = self._unique_object_name(
                item["partition_date"],
                object_stem,
                item["file_extension"],
                document_url,
            )
            file_hash = self.storage.upload_bytes(settings.landing_bucket, object_name, file_content)
            item["file_hash"] = file_hash
            item["file_path"] = f"{settings.landing_bucket}/{object_name}"
            logger.info(
                "file_uploaded",
                extra={
                    "identifier": identifier,
                    "document_url": document_url,
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
                "document_url": document_url,
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

    def _unique_object_name(
        self,
        partition_date: str,
        object_stem: str,
        extension: str,
        document_url: str,
        force_suffix: bool = False,
    ) -> str:
        object_name = f"{partition_date}/{object_stem}.{extension}"
        file_path = f"{settings.landing_bucket}/{object_name}"
        if force_suffix or self._path_collision(file_path, document_url):
            suffix = hashlib.sha1(document_url.encode("utf-8")).hexdigest()[:8]
            object_name = f"{partition_date}/{object_stem}--{suffix}.{extension}"
        return object_name

    def _path_collision(self, file_path: str, document_url: str):
        existing = self.db.get_by_file_path(settings.landing_collection, file_path)
        if existing and existing.get("document_url") != document_url:
            return existing
        return None

    def _object_stem(self, identifier: str) -> str:
        """
        Build the normal object-name stem from the visible WRC identifier.

        The assignment asks for files to be named identifier.ext. Duplicate
        identifiers are handled later by _unique_object_name, which only adds a
        short URL hash when another document already uses the same path.
        """
        return self._safe_name(identifier)

    def _safe_name(self, value: str) -> str:
        value = unescape(value or "").strip()
        value = re.sub(r"\s+", "-", value)
        value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
        value = re.sub(r"-+", "-", value).strip("-._")
        return value or "document"


def canonicalize_url(url: str) -> str:
    """Encode spaces and normalize URL paths before using them as keys."""
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
