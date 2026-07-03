# Kedra WRC Scraping Pipeline

This project is a data pipeline for Workplace Relations Commission decisions and
determinations. It uses Scrapy to collect documents, MongoDB to store metadata,
MinIO to store files, BeautifulSoup to transform HTML files, and Dagster to
orchestrate the scrape and transform steps.

## What The Pipeline Does

1. Splits the requested date range into monthly partitions.
2. Scrapes WRC search results for each partition.
3. Extracts metadata: `identifier`, `description`, `published_date`, `document_url`, `body`, and `partition_date`.
4. Downloads each document.
5. Stores raw files in the MinIO landing bucket.
6. Stores raw metadata in the MongoDB landing collection.
7. Transforms landing files into processed files.
8. Stores processed files and processed metadata separately.

The landing zone is never modified by the transformation step.

## Project Structure

```text
kedra_wrc_pipeline/
  config/              environment-based settings
  scraper/             Scrapy project
  transform/           transformation script
  orchestration/       Dagster assets and job
  utils/               MongoDB, MinIO, and logging helpers
  docker-compose.yml   local MongoDB and MinIO
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
docker-compose up -d
```

MinIO console: http://localhost:9001

Default login:

```text
username: minioadmin
password: minioadmin123
```

## Run With Scrapy And Python

Run the scraper:

```bash
cd scraper
scrapy crawl wrc -a start_date=2024-01-01 -a end_date=2024-01-31
cd ..
```

Run the transformation:

```bash
python -m transform.transformer --start-date 2024-01-01 --end-date 2024-01-31
```

## Run With Dagster

```bash
dagster dev -f orchestration/definitions.py
```

Open http://localhost:3000, choose `full_pipeline_job`, and provide config:

```yaml
ops:
  scraped_documents:
    config:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
  processed_documents:
    config:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
```

## Idempotency

The pipeline calculates a SHA256 hash for every downloaded file. If a document
already exists in MongoDB and the hash did not change, the file is not uploaded
again. MongoDB also has a unique index on `identifier`, so rerunning the same
date range updates the same metadata row instead of creating duplicates.

## Design Overview

Think of the project as four layers:

1. `config/`: reads settings from `.env`.
2. `scraper/`: finds documents and downloads raw files.
3. `utils/`: talks to MongoDB and MinIO.
4. `transform/`: reads raw files and creates cleaned processed files.

The main design idea is separation of responsibility. Each file has one clear
job, which keeps the pipeline easier to maintain, test, and extend.
