"""OpenStreetMap school/supermarket points; read-only Overpass, bounded queries, do not treat missing as nonexistent."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import math

import httpx

from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.providers.base import ProviderError, issue


def distance_m(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


class OSMPlacesProvider:
    source = 'openstreetmap'
    source_mode = 'live'

    def __init__(self, *, endpoint='https://maps.mail.ru/osm/tools/overpass/api/interpreter', transport=None):
        self.endpoint, self.transport = endpoint, transport
        self._cache = {}
        self._lock = asyncio.Lock()

    async def nearby(self, origin, category, radius_m, *, ctx):
        if category not in ('supermarket', 'school'):
            raise ProviderError(issue('INVALID_INPUT', 'OSM only handles school and supermarket queries', source=self.source))
        lat, lon = origin['latitude'], origin['longitude']
        scope = tuple(ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode'))
        key = (scope, lat, lon, radius_m)
        # Query both categories in one pass per listing to avoid duplicate requests to public services.
        delta_lat = radius_m / 110500
        delta_lon = radius_m / (110500 * math.cos(math.radians(lat)))
        bbox = f'{lat-delta_lat},{lon-delta_lon},{lat+delta_lat},{lon+delta_lon}'
        # Use the spatial index for the bounding box to avoid expensive global distance computations with around on complex campus polygons.
        # Then filter precisely by circular radius using the source representative point; the evidence still clearly reflects the point-based scope.
        query = (f'[out:json][timeout:25];('
                 f'nwr["shop"="supermarket"]({bbox});'
                 f'nwr["amenity"~"^(school|kindergarten|college|university)$"]({bbox});'
                 ');out center tags;')
        try:
            async with asyncio.timeout(remaining_seconds(ctx)):
                async with self._lock:
                    if key not in self._cache:
                        async with httpx.AsyncClient(transport=self.transport, timeout=min(55, remaining_seconds(ctx))) as client:
                            response = await client.get(self.endpoint, params={'data': query},
                                headers={'User-Agent': 'HousingResearch/1.0 (read-only amenity lookup)'})
                        if response.status_code == 429:
                            raise ProviderError(issue('RATE_LIMITED', 'OSM query rate limited', source=self.source, retryable=True))
                        if response.is_error:
                            raise ProviderError(issue('SOURCE_UNAVAILABLE', f'OSM returned HTTP {response.status_code}',
                                                      source=self.source, retryable=response.status_code >= 500))
                        data = response.json()
                        if not isinstance(data, dict) or not isinstance(data.get('elements'), list):
                            raise ValueError
                        if data.get('remark'):
                            raise ProviderError(issue('SOURCE_UNAVAILABLE', 'OSM query did not complete fully and cannot serve as a complete facility list',
                                                      source=self.source, retryable=True))
                        self._cache[key] = (data, str(response.url), datetime.now(timezone.utc).isoformat())
                    data, url, observed = self._cache[key]
            items = []
            for row in data['elements']:
                tags = row['tags']
                actual_category = 'supermarket' if tags.get('shop') == 'supermarket' else 'school'
                if actual_category != category:
                    continue
                point = row.get('center', row)
                plat, plon = float(point['lat']), float(point['lon'])
                if not (1.1 <= plat <= 1.6 and 103.5 <= plon <= 104.2):
                    raise ValueError
                distance = distance_m(lat, lon, plat, plon)
                if distance > radius_m:
                    continue  # When an area boundary enters the query circle but its center is outside the circle, do not treat it as a point inside the circle.
                name = tags.get('name:en') or tags.get('name') or tags.get('brand')
                if not name:
                    name = 'Unnamed ' + category + ' (OSM ' + str(row['id']) + ')'
                address = ' '.join(str(tags[k]) for k in ('addr:housenumber', 'addr:street', 'addr:postcode') if tags.get(k))
                source_url = f"https://www.openstreetmap.org/{row['type']}/{row['id']}"
                items.append(dict(place_id=f"osm:{row['type']}:{row['id']}", category=category, name=name,
                    address=address or None, latitude=plat, longitude=plon,
                    coordinate_kind='representative_center' if 'center' in row else 'point',
                    straight_line_distance_m=distance, source_url=source_url, observed_at=observed))
            return deepcopy(dict(items=sorted(items, key=lambda item: item['straight_line_distance_m']),
                                 complete=True, source_url=url, observed_at=observed))
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError(issue('TIMEOUT', 'OSM nearby query timed out', source=self.source, retryable=True)) from None
        except httpx.HTTPError:
            raise ProviderError(issue('SOURCE_UNAVAILABLE', 'Unable to access OSM facility source', source=self.source, retryable=True)) from None
        except (KeyError, TypeError, ValueError):
            raise ProviderError(issue('PARSE_ERROR', 'OSM facility response is missing valid fields', source=self.source)) from None


class NeighborhoodProvider:
    """Source division of labor: OneMap for transport/parks, OSM for schools/supermarkets."""
    source = 'neighborhood'
    source_mode = 'live'

    def __init__(self, onemap, osm=None):
        self.onemap, self.osm = onemap, osm or OSMPlacesProvider()

    async def nearby(self, origin, category, radius_m, *, ctx):
        provider = self.osm if category in ('supermarket', 'school') else self.onemap
        return await provider.nearby(origin, category, radius_m, ctx=ctx)
