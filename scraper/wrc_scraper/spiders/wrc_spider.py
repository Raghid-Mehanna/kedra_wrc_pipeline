"""
Scrapy spider for Workplace Relations decisions.

Run it from the scraper folder:
    scrapy crawl wrc -a start_date=2024-01-01 -a end_date=2024-03-31
"""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import scrapy

ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR))

from config import settings
from utils.logging_config import get_logger, setup_logging
from wrc_scraper.items import WrcDocumentItem

setup_logging()
logger = get_logger(__name__)


class WrcSpider(scrapy.Spider):
    name = "wrc"
    allowed_domains = ["workplacerelations.ie"]

    # These bodies are visible in the website filter. We still detect the body
    # from the identifier because the WRC website's body filter can be unreliable.
    bodies = [
        "Employment Appeals Tribunal",
        "Equality Tribunal",
        "Labour Court",
        "Workplace Relations Commission",
    ]

    def __init__(self, start_date=None, end_date=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not start_date or not end_date:
            raise ValueError("Please pass start_date and end_date in YYYY-MM-DD format.")

        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date.")

        self.stats = {
            "records_found": 0,
            "records_scraped": 0,
            "failed_downloads": 0,
            "failed_urls": [],
        }

    def start_requests(self) -> Iterable[scrapy.Request]:
        """Create one search request per date partition."""
        for partition_start, partition_end in self._date_partitions():
            partition_date = partition_start.strftime("%Y-%m")
            logger.info(
                "partition_started",
                extra={
                    "partition_date": partition_date,
                    "start_date": partition_start.date().isoformat(),
                    "end_date": partition_end.date().isoformat(),
                },
            )

            yield scrapy.Request(
                self._build_search_url(partition_start, partition_end, page=1),
                callback=self.parse_search_results,
                meta={
                    "partition_date": partition_date,
                    "partition_start": partition_start,
                    "partition_end": partition_end,
                    "page": 1,
                },
            )

    async def start(self):
        """
        Compatibility with newer Scrapy versions.

        Older Scrapy versions call start_requests(). Newer versions prefer
        async start(). Yielding from start_requests() keeps the beginner-friendly
        method above while still working on recent Scrapy releases.
        """
        for request in self.start_requests():
            yield request

    def _date_partitions(self):
        """Split a large date range into smaller chunks."""
        current = self.start_date
        while current <= self.end_date:
            partition_end = min(
                current + timedelta(days=settings.partition_size_days - 1),
                self.end_date,
            )
            yield current, partition_end
            current = partition_end + timedelta(days=1)

    def _build_search_url(self, start: datetime, end: datetime, page: int) -> str:
        """Build the same search URL a user would create in the browser."""
        params = {
            "decisions": "1",
            "from": f"{start.day}/{start.month}/{start.year}",
            "to": f"{end.day}/{end.month}/{end.year}",
            "pageNumber": str(page),
        }
        return f"{settings.wrc_search_url}?{urlencode(params)}"

    def parse_search_results(self, response):
        """Read one search result page and follow pagination."""
        partition_date = response.meta["partition_date"]
        page = response.meta["page"]

        if page == 1:
            total = self._extract_total_results(response)
            self.stats["records_found"] += total
            logger.info(
                "partition_result_count",
                extra={"partition_date": partition_date, "records_found": total},
            )

        # The current WRC HTML wraps each search result in li.each-item.
        # Reading the card as a unit avoids accidentally treating the
        # "View Page" button text as the identifier.
        seen = set()
        for card in response.css("li.each-item"):
            title_link = card.css("h2.title a")
            href = title_link.attrib.get("href") if title_link else None
            identifier = self._clean_text(
                title_link.attrib.get("title") if title_link else ""
            ) or self._clean_text(title_link.css("::text").get() if title_link else "")

            if not href or not identifier or identifier in seen:
                continue
            seen.add(identifier)

            description = self._clean_text(card.css(".description::text").get())
            published_date = self._clean_text(card.css(".date::text").get())

            item = WrcDocumentItem()
            item["identifier"] = identifier
            item["description"] = description
            item["published_date"] = published_date
            item["document_url"] = response.urljoin(href)
            item["body"] = self._detect_body(identifier)
            item["partition_date"] = partition_date
            yield item

        next_page = page + 1
        next_href = response.css(f'a[href*="pageNumber={next_page}"]::attr(href)').get()
        if next_href:
            yield scrapy.Request(
                response.urljoin(next_href),
                callback=self.parse_search_results,
                meta={**response.meta, "page": next_page},
            )

    def _extract_total_results(self, response) -> int:
        text = " ".join(response.css("body ::text").getall())
        match = re.search(r"of\s+([\d,]+)\s+results", text, flags=re.I)
        return int(match.group(1).replace(",", "")) if match else 0

    def _extract_date(self, text: str) -> str:
        match = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
        return match.group(0) if match else ""

    def _extract_description(self, text: str, identifier: str) -> str:
        """Find the title/case description near the identifier."""
        text = text.replace(identifier, " ")
        text = re.sub(r"\bRef no:.*", " ", text, flags=re.I)
        text = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", " ", text)
        text = self._clean_text(text)
        return text[:500]

    def _detect_body(self, identifier: str) -> str:
        upper = identifier.upper()
        if upper.startswith(("ADJ-", "IR")):
            return "Workplace Relations Commission"
        if upper.startswith(("LCR", "UDD", "DWT", "EDA", "PWD")):
            return "Labour Court"
        if upper.startswith("DEC-"):
            return "Equality Tribunal"
        if upper.startswith(("UD", "MN", "RP", "TE")):
            return "Employment Appeals Tribunal"
        return "Unknown"

    def _clean_text(self, value) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def closed(self, reason):
        logger.info(
            "spider_finished",
            extra={
                "reason": reason,
                "records_found": self.stats["records_found"],
                "records_scraped": self.stats["records_scraped"],
                "failed_downloads": self.stats["failed_downloads"],
                "failed_urls": self.stats["failed_urls"][:20],
            },
        )
