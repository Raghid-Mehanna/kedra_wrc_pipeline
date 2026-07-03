"""
Dagster assets.

Dagster lets us express dependency handling clearly:
processed_documents depends on scraped_documents.
"""

import subprocess
import sys
from pathlib import Path

from dagster import AssetExecutionContext, Config, MaterializeResult, MetadataValue, asset

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from scripts.audit_completeness import audit_completeness
from transform.transformer import run_transformation


class DateRangeConfig(Config):
    start_date: str
    end_date: str


@asset(group_name="ingestion", compute_kind="scrapy")
def scraped_documents(context: AssetExecutionContext, config: DateRangeConfig) -> MaterializeResult:
    scraper_dir = ROOT_DIR / "scraper"
    command = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "wrc",
        "-a",
        f"start_date={config.start_date}",
        "-a",
        f"end_date={config.end_date}",
    ]

    context.log.info("Running Scrapy spider")
    result = subprocess.run(command, cwd=scraper_dir, text=True, capture_output=True, timeout=3600)
    if result.returncode != 0:
        context.log.error(result.stderr)
        raise RuntimeError("Scrapy failed")

    audit = audit_completeness(config.start_date, config.end_date)
    duplicate_lines = [
        f"{identifier}: {duplicate_count} records"
        for identifier, duplicate_count in sorted(audit["duplicate_identifiers"].items())
    ]

    return MaterializeResult(
        metadata={
            "landing_documents": MetadataValue.int(audit["mongo_landing_documents"]),
            "wrc_expected_result_cards": MetadataValue.int(audit["wrc_expected_count"]),
            "wrc_unique_identifiers": MetadataValue.int(audit["wrc_unique_identifiers"]),
            "wrc_unique_documents": MetadataValue.int(audit["wrc_unique_documents"]),
            "wrc_duplicate_identifier_count": MetadataValue.int(
                len(audit["duplicate_identifiers"])
            ),
            "wrc_duplicate_identifiers": MetadataValue.md(
                "\n".join(f"- {line}" for line in duplicate_lines) or "None"
            ),
            "mongo_landing_documents": MetadataValue.int(audit["mongo_landing_documents"]),
            "missing_in_mongo_count": MetadataValue.int(len(audit["missing_in_mongo"])),
            "missing_in_mongo": MetadataValue.md(
                "\n".join(
                    f"- {audit['missing_identifiers'].get(url, 'Unknown identifier')}: {url}"
                    for url in audit["missing_in_mongo"]
                )
                or "None"
            ),
            "extra_in_mongo_count": MetadataValue.int(len(audit["extra_in_mongo"])),
        }
    )


@asset(deps=[scraped_documents], group_name="transformation", compute_kind="python")
def processed_documents(context: AssetExecutionContext, config: DateRangeConfig) -> MaterializeResult:
    context.log.info("Running transformation")
    stats = run_transformation(config.start_date, config.end_date)
    return MaterializeResult(
        metadata={key: MetadataValue.int(value) for key, value in stats.items()}
    )
