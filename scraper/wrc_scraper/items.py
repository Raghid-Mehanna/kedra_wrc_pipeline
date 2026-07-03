import scrapy


class WrcDocumentItem(scrapy.Item):
    """The metadata fields we want to store for every WRC result."""

    identifier = scrapy.Field()
    description = scrapy.Field()
    published_date = scrapy.Field()
    document_url = scrapy.Field()
    body = scrapy.Field()
    partition_date = scrapy.Field()
    file_extension = scrapy.Field()
    file_content = scrapy.Field()
    file_hash = scrapy.Field()
    file_path = scrapy.Field()
