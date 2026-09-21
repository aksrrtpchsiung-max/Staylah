"""OneMap Search API 适配；Token/凭据留在 Provider，绝不进入图状态。

官方接口：https://www.onemap.gov.sg/apidocs/search
鉴权：https://www.onemap.gov.sg/apidocs/authentication
运行真实查询检查：.venv/bin/python -m providers.onemap
"""
import asyncio
from datetime import datetime, timezone
import math
import os
import re
from pathlib import Path
import sys
from time import monotonic, time
from zoneinfo import ZoneInfo

import httpx

if __name__ == '__main__' and __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts_v0 import RunContext
from execution.budget import remaining_seconds
from execution.tasks import GeocodeMatches
from providers.base import ProviderError, issue

BASE_URL = 'https://www.onemap.gov.sg'


class OneMapProvider:
    source = 'onemap'
    source_mode = 'live'

    def __init__(self, *, token='', email='', password='', transport=None,
                 timeout_seconds=15.0, max_pages=3, request_interval=0.25):
        if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or type(max_pages) is not int or max_pages < 1
                or not math.isfinite(request_interval) or request_interval < 0.2):
            raise ValueError('需要正超时、正分页上限，调用间隔至少 0.2 秒')
        self._token, self._email, self._password = token.strip(), email.strip(), password
        self._expires_at = 0.0
        self._transport = transport
        self.timeout_seconds, self.max_pages = timeout_seconds, max_pages
        self.request_interval = request_interval
        self._next_request = 0.0
        self._lock = asyncio.Lock()

    def _error(self, code, message, retryable=False):
        return ProviderError(issue(code, message, source=self.source, retryable=retryable))

    @classmethod
    def from_env(cls, env_file=None):
        from dotenv import dotenv_values
        path = Path(env_file) if env_file is not None else Path(__file__).resolve().parents[1] / '.env'
        values = dict(dotenv_values(path, interpolate=False))
        values.update(os.environ)
        return cls(token=values.get('ONEMAP_TOKEN') or '', email=values.get('ONEMAP_EMAIL') or '',
                   password=values.get('ONEMAP_PASSWORD') or '')

    def check_configuration(self):
        if not self._token and not (self._email and self._password):
            raise self._error('AUTH_REQUIRED', '真实定位缺少配置：请在 .env 填写 ONEMAP_TOKEN 或 ONEMAP_EMAIL / ONEMAP_PASSWORD')

    async def _request(self, client, method, path, ctx, *, allow_list=False, **kwargs):
        delay = max(0, self._next_request - monotonic())
        if delay:
            await asyncio.sleep(delay)
        self._next_request = monotonic() + self.request_interval
        try:
            response = await client.request(method, path,
                timeout=min(self.timeout_seconds, remaining_seconds(ctx)), **kwargs)
        except httpx.TimeoutException:
            raise self._error('TIMEOUT', 'OneMap 请求超时', True) from None
        except httpx.HTTPError:
            raise self._error('SOURCE_UNAVAILABLE', '无法连接 OneMap', True) from None
        if response.status_code == 429:
            problem = issue('RATE_LIMITED', 'OneMap 调用频率受限', source=self.source, retryable=True)
            retry_after = response.headers.get('Retry-After', '')
            if retry_after.isdigit():
                problem['retry_after_seconds'] = int(retry_after)
            raise ProviderError(problem)
        if response.status_code in (401, 403):
            raise self._error('AUTH_REQUIRED', 'OneMap Token 无效或无访问权限')
        if response.status_code >= 500:
            raise self._error('SOURCE_UNAVAILABLE', 'OneMap 服务暂时不可用', True)
        if response.is_error:
            raise self._error('SOURCE_UNAVAILABLE', f'OneMap 返回 HTTP {response.status_code}')
        try:
            payload = response.json()
        except ValueError:
            raise self._error('PARSE_ERROR', 'OneMap 响应不是 JSON') from None
        if allow_list and isinstance(payload, list):
            return payload, str(response.url)
        if not isinstance(payload, dict):
            raise self._error('PARSE_ERROR', 'OneMap 响应不是对象')
        # 官方 Search 鉴权失败可能仍返回 HTTP 200，不能当成没有匹配。
        if payload.get('error'):
            description = str(payload['error']).lower()
            code = 'AUTH_REQUIRED' if any(s in description for s in ('token', 'auth', 'credential')) else 'SOURCE_UNAVAILABLE'
            raise self._error(code, f'OneMap 请求失败：{code}')
        return payload, str(response.url)

    async def query(self, path, params, *, ctx, allow_list=False):
        """地图能力共用鉴权/串行限速；刷新只处理一次实际鉴权失败。"""
        self.check_configuration()
        try:
            async with asyncio.timeout(remaining_seconds(ctx)):
                async with self._lock:
                    async with httpx.AsyncClient(base_url=BASE_URL, transport=self._transport,
                                                  follow_redirects=False) as client:
                        if not self._token or (self._expires_at and self._expires_at <= time() + 30):
                            await self._authenticate(client, ctx)
                        for attempt in range(2):
                            try:
                                return await self._request(client, 'GET', path, ctx, params=params,
                                    headers={'Authorization': self._token}, allow_list=allow_list)
                            except ProviderError as exc:
                                if attempt or exc.issue['code'] != 'AUTH_REQUIRED' or not (self._email and self._password):
                                    raise
                                await self._authenticate(client, ctx)
        except TimeoutError:
            raise self._error('TIMEOUT', '地图请求达到截止时间', True) from None

    async def route(self, origin, destination, *, mode, transit_mode, departure_at, ctx):
        """保留线路各段；公共交通总时长包含等待与换乘，不用距离推算耗时。"""
        start = f"{origin['latitude']},{origin['longitude']}"
        end = f"{destination['latitude']},{destination['longitude']}"
        params = dict(start=start, end=end, routeType=mode)
        if mode == 'pt':
            departure = datetime.fromisoformat(departure_at).astimezone(ZoneInfo('Asia/Singapore'))
            params.update(date=departure.strftime('%m-%d-%Y'), time=departure.strftime('%H:%M:%S'),
                          mode=transit_mode, numItineraries=3)
        data, url = await self.query('/api/public/routingsvc/route', params, ctx=ctx)
        observed = datetime.now(timezone.utc).isoformat()

        def number(value):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError
            return float(value)

        def instant(value):
            return datetime.fromtimestamp(number(value) / 1000, timezone.utc).isoformat()

        try:
            routes = []
            if mode == 'pt':
                itineraries = data['plan']['itineraries']
                if not isinstance(itineraries, list):
                    raise ValueError
                for itinerary in itineraries:
                    legs = [dict(mode=leg['mode'], start=leg['from']['name'], end=leg['to']['name'],
                        service=leg.get('route') or None, duration_seconds=number(leg['duration']),
                        distance_m=number(leg['distance'])) for leg in itinerary['legs']]
                    if not legs:
                        raise ValueError
                    routes.append(dict(duration_seconds=number(itinerary['duration']),
                        distance_m=sum(leg['distance_m'] for leg in legs), legs=legs,
                        departure_at=instant(itinerary['startTime']), arrival_at=instant(itinerary['endTime']),
                        time_dependent=True, source_url=url, observed_at=observed))
            else:
                if data['status'] != 0:
                    raise ValueError
                summary = data['route_summary']
                routes.append(dict(duration_seconds=number(summary['total_time']),
                    distance_m=number(summary['total_distance']), legs=[dict(mode=mode,
                        start=origin['standard_address'], end=destination['standard_address'], service=None,
                        duration_seconds=number(summary['total_time']), distance_m=number(summary['total_distance']))],
                    departure_at=None, arrival_at=None, time_dependent=False, source_url=url, observed_at=observed))
            return sorted(routes, key=lambda route: route['duration_seconds'])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise self._error('PARSE_ERROR', 'OneMap 路线缺少有效时间、距离或线路信息') from None

    async def nearby(self, origin, category, radius_m, *, ctx):
        from providers.osm import distance_m
        observed = datetime.now(timezone.utc).isoformat()
        if category in ('mrt', 'bus'):
            endpoint = 'getNearestMrtStops' if category == 'mrt' else 'getNearestBusStops'
            rows, url = await self.query('/api/public/nearbysvc/' + endpoint,
                dict(latitude=origin['latitude'], longitude=origin['longitude'], radius_in_meters=radius_m),
                ctx=ctx, allow_list=True)
            if not isinstance(rows, list):
                raise self._error('PARSE_ERROR', 'OneMap 交通设施响应不是数组')
        elif category == 'park':
            # 全国点位列表很小；读取完整列表再按圆形半径过滤，不把矩形范围当圆形。
            data, url = await self.query('/api/public/themesvc/retrieveTheme', {'queryName': 'nationalparks'}, ctx=ctx)
            try:
                meta, *features = data['SrchResults']
                if meta['FeatCount'] != len(features):
                    raise ValueError
                rows = []
                for feature in features:
                    if feature['Type'] != 'Point':
                        raise ValueError
                    lat, lon = map(float, feature['LatLng'].split(','))
                    if re.search(r'\bPG\b|PLAYGROUND|FITNESS CORNER', feature['NAME'], re.I):
                        continue  # Parks 主题也收录游乐场，不能代替用户要求的公园。
                    rows.append(dict(id=feature['NAME'] + ':' + feature['LatLng'], name=feature['NAME'],
                                     lat=lat, lon=lon, road=None))
            except (KeyError, ValueError, TypeError):
                raise self._error('PARSE_ERROR', 'OneMap 公园点位数据不完整') from None
        else:
            raise self._error('INVALID_INPUT', '该类别应交给学校/超市来源')
        try:
            items = []
            for row in rows:
                lat, lon = float(row['lat']), float(row['lon'])
                if not (1.1 <= lat <= 1.6 and 103.5 <= lon <= 104.2) or not row['name']:
                    raise ValueError
                distance = distance_m(origin['latitude'], origin['longitude'], lat, lon)
                if distance <= radius_m:
                    items.append(dict(place_id='onemap:' + category + ':' + str(row['id']), category=category,
                        name=row['name'], address=row.get('road'), latitude=lat, longitude=lon,
                        coordinate_kind='point', straight_line_distance_m=distance,
                        source_url=url, observed_at=observed))
            return dict(items=sorted(items, key=lambda item: item['straight_line_distance_m']),
                        complete=True, source_url=url, observed_at=observed)
        except (KeyError, ValueError, TypeError):
            raise self._error('PARSE_ERROR', 'OneMap 设施名称或坐标无效') from None

    async def _authenticate(self, client, ctx):
        if not self._email or not self._password:
            raise self._error('AUTH_REQUIRED', '请配置 ONEMAP_TOKEN 或 OneMap 邮箱和密码')
        data, _ = await self._request(client, 'POST', '/api/auth/post/getToken', ctx,
                                     json={'email': self._email, 'password': self._password})
        try:
            token = data['access_token']
            expiry = float(data['expiry_timestamp'])
            if not isinstance(token, str) or not token.strip() or not math.isfinite(expiry) or expiry <= time():
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise self._error('PARSE_ERROR', 'OneMap 未返回有效 Token 或到期时间') from None
        self._token, self._expires_at = token, expiry

    async def geocode(self, query: str, *, ctx: RunContext) -> GeocodeMatches:
        if not isinstance(query, str) or not query.strip():
            raise self._error('INVALID_INPUT', '定位查询不能为空')
        self.check_configuration()
        try:
            async with asyncio.timeout(remaining_seconds(ctx)):
                async with self._lock:
                    async with httpx.AsyncClient(base_url=BASE_URL, transport=self._transport,
                                                  follow_redirects=False) as client:
                        if not self._token or (self._expires_at and self._expires_at <= time() + 30):
                            await self._authenticate(client, ctx)
                        candidates, complete, refreshed = [], False, False
                        for page in range(1, self.max_pages + 1):
                            params = dict(searchVal=query.strip(), returnGeom='Y', getAddrDetails='Y', pageNum=page)
                            try:
                                data, url = await self._request(client, 'GET', '/api/common/elastic/search', ctx,
                                    params=params, headers={'Authorization': self._token})
                            except ProviderError as exc:
                                if exc.issue['code'] != 'AUTH_REQUIRED' or refreshed or not (self._email and self._password):
                                    raise
                                await self._authenticate(client, ctx)
                                refreshed = True
                                data, url = await self._request(client, 'GET', '/api/common/elastic/search', ctx,
                                    params=params, headers={'Authorization': self._token})
                            try:
                                total, found, rows = data['totalNumPages'], data['found'], data['results']
                                if (type(total) is not int or total < 0 or type(found) is not int or found < 0
                                        or type(rows) is not list or len(rows) > found
                                        or (found == 0) != (total == 0)
                                        or (found > 0 and (not rows or data.get('pageNum') != page))):
                                    raise ValueError
                                observed_at = datetime.now(timezone.utc).isoformat()
                                for row in rows:
                                    def field(name):
                                        value = row.get(name)
                                        if value is None or str(value).strip().upper() in ('', 'NIL', 'NULL'):
                                            return None
                                        if not isinstance(value, str):
                                            raise ValueError
                                        return value.strip()
                                    lat, lon = float(row['LATITUDE']), float(row['LONGITUDE'])
                                    address = field('ADDRESS')
                                    if not address or not (math.isfinite(lat) and math.isfinite(lon)
                                            and 1.1 <= lat <= 1.6 and 103.5 <= lon <= 104.2):
                                        raise ValueError
                                    candidates.append(dict(address=address, postal_code=field('POSTAL'),
                                        block=field('BLK_NO'), road=field('ROAD_NAME'), building=field('BUILDING'),
                                        latitude=lat, longitude=lon, source_url=url, observed_at=observed_at))
                            except (KeyError, ValueError, TypeError, OverflowError):
                                raise self._error('PARSE_ERROR', 'OneMap 地址、坐标或分页信息不完整') from None
                            if total <= page:
                                complete = len(candidates) == found
                                break
                        return dict(candidates=candidates, complete=complete)
        except TimeoutError:
            raise self._error('TIMEOUT', '定位达到本次执行截止时间', True) from None


if __name__ == '__main__':
    import argparse
    import json
    from datetime import timedelta
    from uuid import uuid4

    parser = argparse.ArgumentParser(description='OneMap 真实地址查询；需要本地 .env 凭据，不使用预设响应')
    parser.add_argument('--queries', nargs='+', default=['200640', '307987', '049213'])
    args = parser.parse_args()

    async def main():
        provider = OneMapProvider.from_env()
        passed = 0
        for query in args.queries:
            identity = str(uuid4())
            ctx = dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                       trace_id=identity, call_id=identity, source_mode='live',
                       deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat())
            try:
                output = await provider.geocode(query, ctx=ctx)
                passed += bool(output['candidates'] and output['complete'])
                print(json.dumps(dict(input=dict(query=query, ctx=ctx), output=output), ensure_ascii=False), flush=True)
            except ProviderError as exc:
                print(json.dumps(dict(input=dict(query=query, ctx=ctx), error=exc.issue), ensure_ascii=False), flush=True)
        print(f'真实 OneMap 查询通过 {passed}/{len(args.queries)}；失败不算验收通过。')
        return 0 if passed == len(args.queries) else 1

    raise SystemExit(asyncio.run(main()))
