"""外部服务协议。依赖在构造时注入，业务参数不携带连接。"""
from typing import Literal, Protocol

from contracts_v0 import HardConstraints, Issue, Listing, ListingConstraint, RunContext, SearchQuery
from execution.tasks import GeocodeMatches, ListingDetail, ListingPage, PlaceMatches, RouteFact


def issue(code: str, message: str, *, field_path: str | None = None,
          source: str | None = "propertyguru", retryable: bool = False) -> Issue:
    return dict(code=code, message=message, field_path=field_path, source=source,
                retryable=retryable, retry_after_seconds=None)


class ProviderError(Exception):
    def __init__(self, problem: Issue):
        self.issue = problem
        super().__init__(problem["message"])


class ListingProvider(Protocol):
    source: str
    source_mode: Literal["mock", "live"]

    async def search_page(self, query: SearchQuery, *, intent: str,
                          filters: HardConstraints, limit: int,
                          ctx: RunContext, constraints: list[ListingConstraint] | None = None) -> ListingPage: ...

    async def read_detail(self, listing: Listing, *, ctx: RunContext) -> ListingDetail: ...


class GeocodingProvider(Protocol):
    source: str
    source_mode: Literal['mock', 'live']

    async def geocode(self, query: str, *, ctx: RunContext) -> GeocodeMatches: ...


class RoutingProvider(Protocol):
    source: str
    source_mode: Literal['mock', 'live']

    async def route(self, origin, destination, *, mode: str, transit_mode: str,
                    departure_at: str, ctx: RunContext) -> list[RouteFact]: ...


class PlacesProvider(Protocol):
    source: str
    source_mode: Literal['mock', 'live']

    async def nearby(self, origin, category: str, radius_m: int, *, ctx: RunContext) -> PlaceMatches: ...
