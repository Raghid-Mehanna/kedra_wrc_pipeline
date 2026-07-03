"""
Scrapy settings.

Scrapy reads this file automatically when commands are run from the scraper
folder. We import our project settings so values stay configurable.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from config import settings

BOT_NAME = "wrc_scraper"
SPIDER_MODULES = ["wrc_scraper.spiders"]
NEWSPIDER_MODULE = "wrc_scraper.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = settings.concurrent_requests
DOWNLOAD_DELAY = settings.download_delay
AUTOTHROTTLE_ENABLED = settings.autothrottle_enabled
AUTOTHROTTLE_START_DELAY = settings.download_delay
AUTOTHROTTLE_MAX_DELAY = 10
RETRY_ENABLED = True
RETRY_TIMES = settings.retry_times
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504]

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": "WrcScraper/1.0",
}

ITEM_PIPELINES = {
    "wrc_scraper.pipelines.ValidationPipeline": 100,
    "wrc_scraper.pipelines.FileDownloadPipeline": 200,
    "wrc_scraper.pipelines.StoragePipeline": 300,
}
