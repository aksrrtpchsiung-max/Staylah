"""按查询和房源 ID 注入模拟来源响应，不连接浏览器。"""
from copy import deepcopy

from providers.base import ProviderError, issue


class MockListingProvider:
    source = "propertyguru"
    source_mode = "mock"

    def __init__(self, *, pages=None, details=None):
        self.pages = pages or {}
        self.details = details or {}

    async def search_page(self, query, *, intent, filters, limit, ctx):
        key = (query["text"], query["cursor"])
        response = self.pages.get(key)
        if isinstance(response, ProviderError):
            raise response
        if response is None:
            raise ProviderError(issue("SOURCE_UNAVAILABLE", "没有该查询的模拟响应"))
        page = deepcopy(response)
        page["query_id"] = query["query_id"]
        if len(page["items"]) > limit:
            page["items"] = page["items"][:limit]
            page["truncated"] = True
        return page

    async def read_detail(self, listing, *, ctx):
        response = self.details.get(listing["source_listing_id"])
        if isinstance(response, ProviderError):
            raise response
        if response is None:
            raise ProviderError(issue("SOURCE_UNAVAILABLE", "没有该房源的模拟详情"))
        return deepcopy(response)


class MockGeocodingProvider:
    source = 'onemap'
    source_mode = 'mock'

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def geocode(self, query, *, ctx):
        self.calls.append(query)
        response = self.responses.get(query)
        if isinstance(response, ProviderError):
            raise response
        if response is None:
            raise ProviderError(issue('SOURCE_UNAVAILABLE', '没有该地址的模拟响应', source=self.source))
        return deepcopy(response)
