"""
MongoDB helper functions.

MongoDB stores metadata, while MinIO stores the actual files. The identifier
field is unique so rerunning the same scrape updates the existing record
instead of creating duplicates.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient

from config import settings


class MetadataStore:
    def __init__(self):
        self.client = MongoClient(settings.mongo_uri)
        self.db = self.client[settings.mongo_database]

    def ensure_indexes(self) -> None:
        """Indexes make lookups fast and enforce one row per identifier."""
        for collection_name in [settings.landing_collection, settings.processed_collection]:
            collection = self.db[collection_name]
            collection.create_index([("identifier", ASCENDING)], unique=True)
            collection.create_index([("partition_date", DESCENDING)])
            collection.create_index([("file_hash", ASCENDING)])

    def upsert(self, collection_name: str, document: Dict[str, Any]) -> None:
        """Insert new metadata or update the existing row for this identifier."""
        now = datetime.now(timezone.utc)
        document = dict(document)
        document["updated_at"] = now
        self.db[collection_name].update_one(
            {"identifier": document["identifier"]},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    def get_by_identifier(self, collection_name: str, identifier: str) -> Optional[Dict[str, Any]]:
        return self.db[collection_name].find_one({"identifier": identifier})

    def find_by_partition_range(
        self,
        collection_name: str,
        start_partition: str,
        end_partition: str,
    ) -> List[Dict[str, Any]]:
        query = {"partition_date": {"$gte": start_partition, "$lte": end_partition}}
        return list(self.db[collection_name].find(query))

    def count_by_partition_range(self, collection_name: str, start_partition: str, end_partition: str) -> int:
        query = {"partition_date": {"$gte": start_partition, "$lte": end_partition}}
        return self.db[collection_name].count_documents(query)

    def close(self) -> None:
        self.client.close()
