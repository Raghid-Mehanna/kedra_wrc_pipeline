from dagster import define_asset_job


full_pipeline_job = define_asset_job(
    name="full_pipeline_job",
    selection=["scraped_documents", "processed_documents"],
)
