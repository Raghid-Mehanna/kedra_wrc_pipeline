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
from urllib.parse import urlencode

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


def extract_expected_count(soup: BeautifulSoup) -> int:
    """Read text like 'Shows 1 to 10 of 628 results' from the WRC page."""
    text = soup.get_text(" ", strip=True)
    match = re.search(r"of\s+([\d,]+)\s+results", text, flags=re.I)
    return int(match.group(1).replace(",", "")) if match else 0


def fetch_wrc_identifiers(
    start_date: str,
    end_date: str,
) -> Tuple[int, Set[str], Dict[str, int], Dict[str, List[str]]]:
    """Fetch every search page and collect the identifiers shown by WRC."""
    session = requests.Session()
    session.headers.update({"User-Agent": "KedraAssessmentWrcAudit/1.0"})
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))

    expected_count = 0
    identifiers: Set[str] = set()
    duplicate_counts: Dict[str, int] = {}
    identifier_urls: Dict[str, List[str]] = {}
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
                href = title.get("href", "")
                duplicate_counts[identifier] = duplicate_counts.get(identifier, 0) + 1
                identifier_urls.setdefault(identifier, []).append(href)
                identifiers.add(identifier)

        if page >= max_pages:
            break

        page += 1

    duplicates = {
        identifier: count
        for identifier, count in duplicate_counts.items()
        if count > 1
    }
    return expected_count, identifiers, duplicates, identifier_urls


def fetch_mongo_identifiers(start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    """Read identifiers stored in the landing metadata collection."""
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

    return {clean_identifier(document["identifier"]): document for document in documents}


def audit_completeness(start_date: str, end_date: str) -> Dict[str, Any]:
    """Compare WRC search identifiers with MongoDB landing metadata."""
    expected_count, website_ids, duplicate_ids, identifier_urls = fetch_wrc_identifiers(
        start_date,
        end_date,
    )
    mongo_documents = fetch_mongo_identifiers(start_date, end_date)
    mongo_ids = set(mongo_documents)

    missing_in_mongo = sorted(website_ids - mongo_ids)
    extra_in_mongo = sorted(mongo_ids - website_ids)

    return {
        "wrc_expected_count": expected_count,
        "wrc_unique_identifiers": len(website_ids),
        "duplicate_identifiers": duplicate_ids,
        "mongo_landing_identifiers": len(mongo_ids),
        "missing_in_mongo": missing_in_mongo,
        "missing_urls": {
            identifier: [
                f"{settings.wrc_base_url}{url}"
                for url in identifier_urls.get(identifier, [])
            ]
            for identifier in missing_in_mongo
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
    print(f"Duplicate identifiers on WRC pages: {len(audit['duplicate_identifiers'])}")
    for identifier, count in sorted(audit["duplicate_identifiers"].items()):
        print(f"  - {identifier}: {count} records")
    print(f"Mongo landing identifiers: {audit['mongo_landing_identifiers']}")
    print(f"Missing in Mongo: {len(audit['missing_in_mongo'])}")
    for identifier in audit["missing_in_mongo"]:
        print(f"  - {identifier}")
        for url in audit["missing_urls"].get(identifier, []):
            print(f"    {url}")

    print(f"Extra in Mongo for same months: {len(audit['extra_in_mongo'])}")
    mongo_documents = fetch_mongo_identifiers(args.start_date, args.end_date)
    for identifier in audit["extra_in_mongo"][:20]:
        document = mongo_documents[identifier]
        print(
            "  - "
            f"{identifier} "
            f"(published_date={document.get('published_date', '')}, "
            f"partition_date={document.get('partition_date', '')})"
        )


if __name__ == "__main__":
    main()
