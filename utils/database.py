"""
MongoDB helper functions.

MongoDB stores metadata, while MinIO stores the actual files. The identifier
field is useful metadata, but WRC can publish multiple result cards with the
same identifier and different document URLs. The document URL is therefore the
unique key used for idempotent upserts.
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
        """Indexes make lookups fast and enforce one row per document URL."""
        for collection_name in [settings.landing_collection, settings.processed_collection]:
            collection = self.db[collection_name]
            for index in collection.list_indexes():
                is_identifier_index = index.get("key") == {"identifier": 1}
                if is_identifier_index and index.get("unique"):
                    collection.drop_index(index["name"])

            collection.create_index([("document_url", ASCENDING)], unique=True)
            collection.create_index([("identifier", ASCENDING)])
            collection.create_index([("partition_date", DESCENDING)])
            collection.create_index([("file_hash", ASCENDING)])

    def upsert(self, collection_name: str, document: Dict[str, Any], key_field: str = "document_url") -> None:
        """Insert new metadata or update the existing row for this document."""
        now = datetime.now(timezone.utc)
        document = dict(document)
        document["updated_at"] = now
        self.db[collection_name].update_one(
            {key_field: document[key_field]},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    def get_by_identifier(self, collection_name: str, identifier: str) -> Optional[Dict[str, Any]]:
        return self.db[collection_name].find_one({"identifier": identifier})

    def get_by_document_url(self, collection_name: str, document_url: str) -> Optional[Dict[str, Any]]:
        return self.db[collection_name].find_one({"document_url": document_url})

    def get_by_file_path(self, collection_name: str, file_path: str) -> Optional[Dict[str, Any]]:
        return self.db[collection_name].find_one({"file_path": file_path})

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
