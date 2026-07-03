# Architecture Write-Up

## Date Partition Size

I chose 30-day partitions. This is small enough that a failed run only needs to
repeat one month, but large enough that the pipeline does not create too many
tiny requests. The partition size is configurable with `PARTITION_SIZE_DAYS`.

## Retries And Rate Limiting

The scraper uses Scrapy's built-in retry middleware for temporary errors such as
`429`, `500`, `502`, `503`, and `504`. It also uses AutoThrottle and a small
download delay so the scraper adapts to the website instead of sending requests
too aggressively.

This is a simple and reliable first approach. If this ran at much larger scale,
I would add per-domain rate limits, monitoring, and alerting.

## Deduplication Strategy

Each downloaded file gets a SHA256 hash. Before uploading a file, the pipeline
checks MongoDB for the same document identifier. If the identifier exists and
the hash is unchanged, the pipeline skips the upload.

MongoDB also has a unique index on `identifier`, so rerunning the same date range
does not create duplicate metadata records.

## Landing Zone And Processed Zone

The landing zone stores the original files exactly as downloaded. The processed
zone stores cleaned files. This means transformation logic can improve later
without scraping the website again.

For HTML files, the transformer removes navigation, buttons, headers, footers,
scripts, styles, and other page chrome. For PDF/DOC/DOCX files, it copies the
file as-is because those formats are already the document itself.

## Scaling To 50+ Sources

To support 50+ sources, I would keep the same pattern but make it more generic:

- Move website-specific selectors and URL patterns into source config files.
- Create one spider class or plugin per source.
- Run workers in parallel using Dagster or another orchestrator.
- Add monitoring for failed downloads and unusual result counts.
- Store source-specific parser versions so old data can be reproduced.
- Use cloud object storage and a managed MongoDB cluster for higher volume.

The current project is intentionally simple enough to understand, but the
landing/processed storage pattern, hashes, and orchestration structure are the
same ideas I would keep in a larger system.
