"""
Transform landing-zone files into processed-zone files.

This script follows the assessment exactly:
- read metadata from MongoDB for a date range
- fetch files from object storage
- clean HTML files with BeautifulSoup
- leave PDF/DOC/DOCX files unchanged
- rename every output file to identifier.ext
- write processed metadata to a new MongoDB collection
"""

import argparse
from pathlib import Path
import sys
from typing import Dict

from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from config import settings
from utils.database import MetadataStore
from utils.logging_config import get_logger, setup_logging
from utils.storage import ObjectStorage

setup_logging()
logger = get_logger(__name__)


class HtmlCleaner:
    """Remove website chrome and keep the legal decision content."""

    remove_selectors = [
        "script",
        "style",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        ".breadcrumb",
        ".pagination",
        ".cookie",
        ".return-to-search",
    ]

    def clean(self, html: str) -> bytes:
        soup = BeautifulSoup(html, "lxml")

        for selector in self.remove_selectors:
            for element in soup.select(selector):
                element.decompose()

        # Prefer semantic containers first. If the site changes, the fallback
        # still gives us the largest text-heavy block.
        main = soup.select_one("main, article, .content, #content")
        if main is None:
            main = self._largest_text_block(soup)
        if main is None:
            main = soup.body or soup

        self._remove_empty_tags(main)
        return str(main).encode("utf-8")

    def _largest_text_block(self, soup):
        candidates = soup.find_all(["div", "section"])
        if not candidates:
            return None
        return max(candidates, key=lambda tag: len(tag.get_text(strip=True)), default=None)

    def _remove_empty_tags(self, root) -> None:
        for tag in root.find_all(["div", "span", "p"]):
            if not tag.get_text(strip=True) and not tag.find(["table", "img"]):
                tag.decompose()


class Transformer:
    def __init__(self):
        self.db = MetadataStore()
        self.storage = ObjectStorage()
        self.storage.ensure_buckets()
        self.cleaner = HtmlCleaner()

    def run(self, start_date: str, end_date: str) -> Dict[str, int]:
        """Transform all landing documents in the requested date range."""
        start_partition = start_date[:7]
        end_partition = end_date[:7]
        documents = self.db.find_by_partition_range(
            settings.landing_collection,
            start_partition,
            end_partition,
        )

        stats = {"processed": 0, "skipped": 0, "failed": 0, "html_cleaned": 0, "binary_copied": 0}
        logger.info(
            "transformation_started",
            extra={
                "start_date": start_date,
                "end_date": end_date,
                "documents_found": len(documents),
            },
        )

        for document in documents:
            try:
                action = self._process_one(document)
                stats[action] += 1
                if action == "processed" and document.get("file_extension") == "html":
                    stats["html_cleaned"] += 1
                elif action == "processed":
                    stats["binary_copied"] += 1
            except Exception as exc:
                stats["failed"] += 1
                logger.error(
                    "transformation_failed",
                    extra={"identifier": document.get("identifier"), "error": str(exc)},
                )

        logger.info("transformation_finished", extra=stats)
        return stats

    def _process_one(self, document) -> str:
        identifier = document["identifier"]
        source_hash = document["file_hash"]

        existing = self.db.get_by_identifier(settings.processed_collection, identifier)
        if existing and existing.get("source_hash") == source_hash:
            return "skipped"

        bucket, object_name = document["file_path"].split("/", 1)
        content = self.storage.download_bytes(bucket, object_name)
        if content is None:
            raise RuntimeError(f"Could not download {document['file_path']}")

        extension = document.get("file_extension", "html")
        if extension == "html":
            content = self.cleaner.clean(content.decode("utf-8", errors="replace"))

        processed_object = f"{document['partition_date']}/{identifier}.{extension}"
        new_hash = self.storage.upload_bytes(settings.processed_bucket, processed_object, content)

        processed_metadata = {
            "identifier": identifier,
            "description": document.get("description", ""),
            "published_date": document.get("published_date", ""),
            "document_url": document.get("document_url", ""),
            "body": document.get("body", ""),
            "partition_date": document["partition_date"],
            "file_extension": extension,
            "file_path": f"{settings.processed_bucket}/{processed_object}",
            "file_hash": new_hash,
            "source_path": document["file_path"],
            "source_hash": source_hash,
        }
        self.db.upsert(settings.processed_collection, processed_metadata)
        return "processed"

    def close(self):
        self.db.close()


def run_transformation(start_date: str, end_date: str) -> Dict[str, int]:
    transformer = Transformer()
    try:
        return transformer.run(start_date, end_date)
    finally:
        transformer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    stats = run_transformation(args.start_date, args.end_date)
    print(stats)


if __name__ == "__main__":
    main()
