"""OpenStreetMap 的学校/超市点位；只读 Overpass，有界查询，不把缺失当不存在。"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import math

import httpx

from execution.budget import remaining_seconds
from providers.base import ProviderError, issue


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
            raise ProviderError(issue('INVALID_INPUT', 'OSM 仅承接学校和超市查询', source=self.source))
        lat, lon = origin['latitude'], origin['longitude']
        scope = tuple(ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode'))
        key = (scope, lat, lon, radius_m)
        # 同一个房源一次查询两个类别，避免重复请求公共服务。
        delta_lat = radius_m / 110500
        delta_lon = radius_m / (110500 * math.cos(math.radians(lat)))
        bbox = f'{lat-delta_lat},{lon-delta_lon},{lat+delta_lat},{lon+delta_lon}'
        # 边界框走空间索引，避免 around 对复杂校园多边形做昂贵全局距离计算。
        # 后面按来源代表点精确过滤圆形半径，证据仍明确点位口径。
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
                            raise ProviderError(issue('RATE_LIMITED', 'OSM 查询频率受限', source=self.source, retryable=True))
                        if response.is_error:
                            raise ProviderError(issue('SOURCE_UNAVAILABLE', f'OSM 返回 HTTP {response.status_code}',
                                                      source=self.source, retryable=response.status_code >= 500))
                        data = response.json()
                        if not isinstance(data, dict) or not isinstance(data.get('elements'), list):
                            raise ValueError
                        if data.get('remark'):
                            raise ProviderError(issue('SOURCE_UNAVAILABLE', 'OSM 查询未完整执行，不能作为完整设施列表',
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
                    continue  # 区域边界进入查询圈但中心不在圈内时不当作圈内点位。
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
            raise ProviderError(issue('TIMEOUT', 'OSM 周边查询超时', source=self.source, retryable=True)) from None
        except httpx.HTTPError:
            raise ProviderError(issue('SOURCE_UNAVAILABLE', '无法访问 OSM 设施来源', source=self.source, retryable=True)) from None
        except (KeyError, TypeError, ValueError):
            raise ProviderError(issue('PARSE_ERROR', 'OSM 设施响应缺少有效字段', source=self.source)) from None


class NeighborhoodProvider:
    """来源分工：OneMap 查交通/公园，OSM 查学校/超市。"""
    source = 'neighborhood'
    source_mode = 'live'

    def __init__(self, onemap, osm=None):
        self.onemap, self.osm = onemap, osm or OSMPlacesProvider()

    async def nearby(self, origin, category, radius_m, *, ctx):
        provider = self.osm if category in ('supermarket', 'school') else self.onemap
        return await provider.nearby(origin, category, radius_m, ctx=ctx)
