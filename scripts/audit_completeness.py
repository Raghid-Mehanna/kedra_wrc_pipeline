"""
Audit whether MongoDB has every WRC result for a date range.

This script is separate from the scraper on purpose:
- the scraper downloads and stores documents
- this script only checks completeness and reports missing identifiers

Run from the project root:
    python scripts/audit_completeness.py --start-date 2024-01-01 --end-date 2024-02-29
"""

import argparse
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from config import settings
from utils.database import MetadataStore


def clean_identifier(value: str) -> str:
    """Clean identifier text the same way the Scrapy spider does."""
    return re.sub(r"\s+", " ", value or "").strip()


def format_wrc_date(date_text: str) -> str:
    """Convert YYYY-MM-DD into the D/M/YYYY format used by the WRC website."""
    value = datetime.strptime(date_text, "%Y-%m-%d")
    return f"{value.day}/{value.month}/{value.year}"


def build_search_url(start_date: str, end_date: str, page: int) -> str:
    """Build the same search URL used by the Scrapy spider."""
    params = {
        "decisions": "1",
        "from": format_wrc_date(start_date),
        "to": format_wrc_date(end_date),
        "pageNumber": str(page),
    }
    return f"{settings.wrc_search_url}?{urlencode(params)}"


def canonicalize_url(url: str) -> str:
    """Encode spaces and normalize URL paths before comparing URLs."""
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def extract_expected_count(soup: BeautifulSoup) -> int:
    """Read text like 'Shows 1 to 10 of 628 results' from the WRC page."""
    text = soup.get_text(" ", strip=True)
    match = re.search(r"of\s+([\d,]+)\s+results", text, flags=re.I)
    return int(match.group(1).replace(",", "")) if match else 0


def fetch_wrc_records(
    start_date: str,
    end_date: str,
) -> Tuple[int, Dict[str, str], Dict[str, int]]:
    """Fetch every search page and map document URLs to WRC identifiers."""
    session = requests.Session()
    session.headers.update({"User-Agent": "WrcCompletenessAudit/1.0"})
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))

    expected_count = 0
    records: Dict[str, str] = {}
    duplicate_counts: Dict[str, int] = {}
    page = 1
    max_pages = 1

    while True:
        response = session.get(build_search_url(start_date, end_date, page), timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        if page == 1:
            expected_count = extract_expected_count(soup)
            max_pages = max(1, math.ceil(expected_count / 10))

        cards = soup.select("li.each-item")
        if not cards:
            break

        for card in cards:
            title = card.select_one("h2.title a")
            if title:
                identifier = clean_identifier(title.get("title") or title.get_text(strip=True))
                href = canonicalize_url(urljoin(settings.wrc_base_url, title.get("href", "")))
                duplicate_counts[identifier] = duplicate_counts.get(identifier, 0) + 1
                records[href] = identifier

        if page >= max_pages:
            break

        page += 1

    duplicates = {
        identifier: count
        for identifier, count in duplicate_counts.items()
        if count > 1
    }
    return expected_count, records, duplicates


def fetch_mongo_documents(start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    """Read landing metadata keyed by document URL."""
    start_partition = start_date[:7]
    end_partition = end_date[:7]

    db = MetadataStore()
    try:
        documents = db.find_by_partition_range(
            settings.landing_collection,
            start_partition,
            end_partition,
        )
    finally:
        db.close()

    return {canonicalize_url(document["document_url"]): document for document in documents}


def audit_completeness(start_date: str, end_date: str) -> Dict[str, Any]:
    """Compare WRC search document URLs with MongoDB landing metadata."""
    expected_count, website_records, duplicate_ids = fetch_wrc_records(
        start_date,
        end_date,
    )
    mongo_documents = fetch_mongo_documents(start_date, end_date)
    website_urls = set(website_records)
    mongo_urls = set(mongo_documents)
    website_identifiers = {clean_identifier(identifier) for identifier in website_records.values()}

    missing_in_mongo = sorted(website_urls - mongo_urls)
    extra_in_mongo = sorted(mongo_urls - website_urls)

    return {
        "wrc_expected_count": expected_count,
        "wrc_unique_identifiers": len(website_identifiers),
        "wrc_unique_documents": len(website_urls),
        "duplicate_identifiers": duplicate_ids,
        "mongo_landing_identifiers": len(
            {clean_identifier(document["identifier"]) for document in mongo_documents.values()}
        ),
        "mongo_landing_documents": len(mongo_urls),
        "missing_in_mongo": missing_in_mongo,
        "missing_identifiers": {
            url: website_records[url]
            for url in missing_in_mongo
        },
        "extra_in_mongo": extra_in_mongo,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    audit = audit_completeness(
        args.start_date,
        args.end_date,
    )

    print(f"WRC expected count: {audit['wrc_expected_count']}")
    print(f"WRC identifiers fetched: {audit['wrc_unique_identifiers']}")
    print(f"WRC document URLs fetched: {audit['wrc_unique_documents']}")
    print(f"Duplicate identifiers on WRC pages: {len(audit['duplicate_identifiers'])}")
    for identifier, count in sorted(audit["duplicate_identifiers"].items()):
        print(f"  - {identifier}: {count} records")
    print(f"Mongo landing identifiers: {audit['mongo_landing_identifiers']}")
    print(f"Mongo landing documents: {audit['mongo_landing_documents']}")
    print(f"Missing in Mongo: {len(audit['missing_in_mongo'])}")
    for url in audit["missing_in_mongo"]:
        print(f"  - {audit['missing_identifiers'].get(url, 'Unknown identifier')}: {url}")

    print(f"Extra in Mongo for same months: {len(audit['extra_in_mongo'])}")
    mongo_documents = fetch_mongo_documents(args.start_date, args.end_date)
    for url in audit["extra_in_mongo"][:20]:
        document = mongo_documents[url]
        print(
            "  - "
            f"{document.get('identifier', '')} "
            f"(published_date={document.get('published_date', '')}, "
            f"partition_date={document.get('partition_date', '')}, "
            f"url={url})"
        )


if __name__ == "__main__":
    main()
