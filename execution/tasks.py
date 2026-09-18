"""3a 的内部执行数据；不扩展 contracts_v0 的公开接口。"""
from typing import Literal, TypedDict

from contracts_v0 import Evidence, Issue, JsonValue, Listing


class ListingPage(TypedDict):
    query_id: str
    items: list[Listing]
    next_cursor: str | None
    pagination_known: bool
    truncated: bool
    applied_filters: list[str]
    unsupported_filters: list[str]
    issues: list[Issue]


class DetailFact(TypedDict):
    field: str
    value: JsonValue
    excerpt: str


class ListingDetail(TypedDict):
    source_listing_id: str
    source_url: str
    fetched_at: str
    raw_description: str | None
    raw_details: list[str]
    facts: list[DetailFact]


class LocationRequest(TypedDict):
    query: str
    address: str | None
    postal_code: str | None
    building: str | None
    source_url: str | None
    excerpt: str


class GeoCandidate(TypedDict):
    address: str
    postal_code: str | None
    block: str | None
    road: str | None
    building: str | None
    latitude: float
    longitude: float
    source_url: str
    observed_at: str


class GeocodeMatches(TypedDict):
    candidates: list[GeoCandidate]
    complete: bool


class LocationResult(TypedDict):
    status: Literal['resolved', 'ambiguous', 'not_found', 'insufficient', 'unavailable']
    standard_address: str | None
    latitude: float | None
    longitude: float | None
    precision: Literal['building', 'road', 'unknown']
    candidates: list[GeoCandidate]
    evidence: list[Evidence]
    gaps: list[str]


class ExecutionTask(TypedDict):
    task_id: str
    kind: Literal['search_page', 'read_detail', 'locate']
    query_id: str | None
    cursor: str | None
    listing_key: str | None
