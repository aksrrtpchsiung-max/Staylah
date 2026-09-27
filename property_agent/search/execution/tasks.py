"""Internal execution data for 3a; does not extend the public interface of property_agent.contracts."""
from typing import Literal, TypedDict

from property_agent.contracts import Evidence, Issue, JsonValue, Listing


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
    precision: Literal['building', 'road', 'point', 'unknown']
    candidates: list[GeoCandidate]
    evidence: list[Evidence]
    gaps: list[str]


class ExecutionTask(TypedDict):
    task_id: str
    kind: Literal['search_page', 'read_detail', 'locate', 'amenities', 'travel']
    query_id: str | None
    cursor: str | None
    listing_key: str | None
    requirement_id: str | None


class RouteLeg(TypedDict):
    mode: str
    start: str
    end: str
    service: str | None
    duration_seconds: float
    distance_m: float


class RouteFact(TypedDict):
    duration_seconds: float
    distance_m: float
    legs: list[RouteLeg]
    departure_at: str | None
    arrival_at: str | None
    time_dependent: bool
    source_url: str
    observed_at: str


class TravelRequest(TypedDict):
    origin: LocationRequest
    destination: LocationRequest
    origin_location: LocationResult | None
    destination_location: LocationResult | None
    mode: Literal['pt', 'walk', 'drive', 'cycle']
    transit_mode: Literal['TRANSIT', 'BUS', 'RAIL']
    departure_at: str
    assumptions: list[str]


class TravelResult(TypedDict):
    status: Literal['resolved', 'unverified']
    routes: list[RouteFact]
    evidence: list[Evidence]
    gaps: list[str]


AmenityCategory = Literal['mrt', 'bus', 'supermarket', 'school', 'park']


class PlaceFact(TypedDict):
    place_id: str
    category: AmenityCategory
    name: str
    address: str | None
    latitude: float
    longitude: float
    coordinate_kind: Literal['point', 'representative_center']
    straight_line_distance_m: float
    source_url: str
    observed_at: str


class PlaceMatches(TypedDict):
    items: list[PlaceFact]
    complete: bool  # Whether the data source response is complete; does not guarantee that real-world facilities are exhaustive
    source_url: str
    observed_at: str


class AmenityRequest(TypedDict):
    origin: LocationRequest
    origin_location: LocationResult | None
    categories: list[AmenityCategory]
    radius_m: int


class AmenityResult(TypedDict):
    places: list[PlaceFact]
    completed_categories: list[AmenityCategory]
    radius_m: int
    evidence: list[Evidence]
    gaps: list[str]


class InvestigationResult(TypedDict):
    evidence: list[Evidence]
    gaps: list[str]
    investigation_status: Literal['fulfilled', 'unverified']
    check: Literal['pass', 'fail', 'unknown']
