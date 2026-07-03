from dagster import Definitions

from orchestration.assets import processed_documents, scraped_documents
from orchestration.jobs import full_pipeline_job


defs = Definitions(
    assets=[scraped_documents, processed_documents],
    jobs=[full_pipeline_job],
)
