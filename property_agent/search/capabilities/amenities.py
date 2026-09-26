"""3c：从住宅出发调查五类设施，半径明确；步行条件复用 3d。"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from time import monotonic


from property_agent.contracts import ContractViolation, Result, RunContext
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.execution.history import fingerprint
from property_agent.search.execution.tasks import AmenityRequest, AmenityResult, PlaceMatches
from property_agent.domain.validation import fail, validate_context, validate_type
from property_agent.search.capabilities.location import _wrap, request_from_listing
from property_agent.search.capabilities.travel import (SG, TIME_UNITS, DISTANCE_UNITS, compare_measurement,
    commute_options, make_evidence, metric_kind, resolved_location)
from property_agent.search.providers.base import ProviderError, issue

CATEGORIES = ('mrt', 'bus', 'supermarket', 'school', 'park')
DEFAULT_RADIUS_M = 1500
CATEGORY_PATTERNS = {
    'mrt': r'地铁|轨道|\bmrt\b|\blrt\b|metro|subway',
    'bus': r'公交|巴士|bus',
    'supermarket': r'超市|supermarket|grocery|fairprice|sheng.siong|cold.storage',
    'school': r'学校|小学|中学|大学|幼儿园|school|college|university|kindergarten',
    'park': r'公园|park|garden',
}
GENERIC_TARGETS = {
    'mrt': {'mrt', 'lrt', 'mrt station', 'mrt stations', 'metro', 'subway', '地铁', '地铁站'},
    'bus': {'bus', 'bus stop', 'bus stops', '公交', '公交站', '巴士站'},
    'supermarket': {'supermarket', 'supermarkets', 'grocery', '超市'},
    'school': {'school', 'schools', '学校'},
    'park': {'park', 'parks', '公园'},
}


def matches_target(place, requirement):
    target = (requirement['target'] or '').strip().lower()
    target = re.sub(r'^(?:附近的?|周围的?|最近的?|nearby\s+|nearest\s+|closest\s+)', '', target)
    if not target or target in GENERIC_TARGETS[place['category']]:
        return True
    # 具体品牌或校名不能被任意同类设施替代；未匹配别名时留下待核实。
    return target in place['name'].lower()


def categories_for(requirement):
    text = (requirement['target'] or '') + ' ' + requirement['metric']
    return [category for category, pattern in CATEGORY_PATTERNS.items() if re.search(pattern, text, re.I)]


def supports_requirement(requirement):
    """只承诺可解释的指标；其他派生类别继续报告 unsupported。"""
    if requirement['category'] == 'commute':
        return metric_kind(requirement) is not None
    if requirement['category'] == 'nearby_amenity':
        return bool(categories_for(requirement)) and (metric_kind(requirement) is not None or
            requirement['metric'].lower() in ('nearby', 'exists', 'existence', 'count', 'amenities', '附近', '数量', '存在'))
    return False


def point_location(place):
    """设施查询已经给出点坐标；保留中心点口径，不把它称作校门或公园入口。"""
    value = dict(latitude=place['latitude'], longitude=place['longitude'], coordinate_kind=place['coordinate_kind'])
    evidence = make_evidence('amenity_location', value, url=place['source_url'], observed=place['observed_at'],
                             excerpt=place['name'] + '：设施数据源点位；不代表入口')
    return dict(status='resolved', standard_address=place['name'], latitude=place['latitude'], longitude=place['longitude'],
                precision='point', candidates=[], evidence=[evidence], gaps=[])


class AmenitiesCapability:
    def __init__(self, provider, location, travel, *, max_calls=60, route_limit=5):
        self.provider, self.location, self.travel = provider, location, travel
        self.max_calls, self.calls, self.route_limit = max_calls, 0, route_limit
        self._cache = {}

    async def investigate(self, request: AmenityRequest, *, ctx: RunContext) -> Result[AmenityResult]:
        started = monotonic()
        places, completed, evidence, gaps, problems = [], [], [], [], []
        try:
            validate_context(ctx)
            validate_type(AmenityRequest, request, 'amenity_request')
            if not 1 <= request['radius_m'] < 5000 or not request['categories'] or len(set(request['categories'])) != len(request['categories']):
                fail('amenity_request', '需要不重复类别及 1–4999 米查询半径')
            if self.provider is None:
                raise ProviderError(issue('SOURCE_UNAVAILABLE', '未配置设施来源', source='neighborhood'))
            if self.provider.source_mode != ctx['source_mode']:
                fail('ctx.source_mode', '设施来源模式不一致')
            origin = request['origin_location']
            if origin is None:
                response = await self.location.locate(request['origin'], ctx=ctx)
                origin = response['data']; problems.extend(response['issues'])
            if not resolved_location(origin):
                raise ProviderError(issue('RETRIEVAL_DEGRADED', '住宅尚未精确定位，无法核实周边设施', source='neighborhood'))
            for category in request['categories']:
                try:
                    remaining_seconds(ctx)
                    scope = [ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode')]
                    key = fingerprint([scope, origin['latitude'], origin['longitude'], category, request['radius_m']])
                    if key in self._cache:
                        matches = deepcopy(self._cache[key])
                    else:
                        if self.calls >= self.max_calls:
                            raise ProviderError(issue('BUDGET_EXHAUSTED', '设施查询额度已用完', source='neighborhood'))
                        self.calls += 1
                        async with asyncio.timeout(remaining_seconds(ctx)):
                            matches = await self.provider.nearby(origin, category, request['radius_m'], ctx=ctx)
                        try:
                            validate_type(PlaceMatches, matches, 'places')
                            if any(p['category'] != category or not p['source_url'] or
                                   not 0 <= p['straight_line_distance_m'] <= request['radius_m'] for p in matches['items']):
                                fail('places', '设施类别、距离或来源不合法')
                        except ContractViolation as exc:
                            raise ProviderError(issue('INVALID_OUTPUT', str(exc), source='neighborhood')) from exc
                        if matches['complete']:
                            self._cache[key] = deepcopy(matches)
                    # OSM/OneMap 自身的稳定 ID 去重；不按名称合并不同分店或站点。
                    items = list({p['place_id']: p for p in matches['items']}.values())
                    places.extend(items)
                    if matches['complete']:
                        completed.append(category)
                    else:
                        gaps.append('amenities:' + category + ':incomplete_source')
                        problems.append(issue('RETRIEVAL_DEGRADED', '设施响应不完整：' + category, source='neighborhood'))
                    payload = dict(category=category, origin={k: origin[k] for k in ('standard_address', 'latitude', 'longitude')},
                        radius_m=request['radius_m'], distance_basis='straight_line_to_source_point',
                        source_response_complete=matches['complete'], coverage_scope='mapped_facilities_only',
                        source_mode=ctx['source_mode'], places=items,
                        limitations=['数据源可能遗漏设施；空结果不证明现实中不存在', '点位或区域中心不保证是实际入口'])
                    evidence.append(make_evidence('nearby_amenity.' + category, payload, url=matches['source_url'],
                        observed=matches['observed_at'], excerpt=f"{category}：住宅周围 {request['radius_m']} 米直线半径，"
                        f"取得 {len(items)} 个来源点位；未将直线距离解释为步行距离"))
                except (ProviderError, TimeoutError) as exc:
                    problem = exc.issue if isinstance(exc, ProviderError) else issue('TIMEOUT', '设施查询达到截止时间', source='neighborhood', retryable=True)
                    problems.append(problem); gaps.append('amenities:' + category + ':' + problem['code'])
            data = dict(places=places, completed_categories=completed, radius_m=request['radius_m'], evidence=evidence, gaps=gaps)
            validate_type(AmenityResult, data, 'amenity_result')
            return _wrap(data, problems, ctx, started)
        except ContractViolation as exc:
            return _wrap(None, [issue(exc.code, str(exc), source='neighborhood', field_path=exc.field_path)], ctx, started)
        except ProviderError as exc:
            return _wrap(dict(places=[], completed_categories=[], radius_m=request['radius_m'], evidence=[], gaps=['amenities:' + exc.issue['code']]),
                         [exc.issue], ctx, started)

    async def investigate_requirement(self, listing, location, requirement, *, ctx):
        started = monotonic()
        categories = categories_for(requirement)
        metric, kind = requirement['metric'].lower(), metric_kind(requirement)
        value = requirement['value']
        settings = value if isinstance(value, dict) else {}
        threshold = settings.get('threshold', value)
        unit = (requirement['unit'] or '').lower()
        radius = settings.get('radius_m', DEFAULT_RADIUS_M)
        # 明确直线/步行距离的上界决定候选检索范围；不扩大用户约束本身。
        if kind == 'distance' and requirement['operator'] in ('lte', 'between') and unit in DISTANCE_UNITS:
            maximum = threshold[-1] if isinstance(threshold, list) else threshold
            if type(maximum) in (int, float) and maximum > 0:
                radius = math.ceil(maximum * DISTANCE_UNITS[unit])
        radius_exceeded = type(radius) not in (int, float) or not 0 < radius < 5000
        radius = DEFAULT_RADIUS_M if radius_exceeded else int(radius)
        request = dict(origin=request_from_listing(listing), origin_location=location, categories=categories, radius_m=radius)
        response = await self.investigate(request, ctx=ctx)
        data = response['data']
        if data is None:
            return response
        evidence, gaps, problems = data['evidence'][:], data['gaps'][:], response['issues'][:]
        if radius_exceeded:
            gaps.append('amenities:requested_radius_outside_supported_range')
        observations = []
        route_needed = kind == 'time' or kind == 'distance' and not re.search(r'straight|直线', metric + ' ' + requirement['source']['text'], re.I)
        for category in categories:
            candidates = [p for p in data['places'] if p['category'] == category and matches_target(p, requirement)]
            actual = None
            if route_needed:
                # 配套出行从家出发，缺省步行；3d 的通勤默认方式不套用到配套步行。
                modes, transit, departure, time_kind, assumptions, option_gaps = commute_options(requirement)
                if any(a.startswith('未指定方式') for a in assumptions):
                    modes = ['walk']
                    assumptions = [a for a in assumptions if not a.startswith('未指定方式')] + ['配套距离/时间未指定方式：步行']
                if option_gaps or len(modes) != 1 or time_kind != 'departure':
                    gaps.extend(option_gaps or ['amenities:travel_options_need_clarification'])
                    observations.append(dict(category=category, actual=None, unit=requirement['unit'], check='unknown'))
                    continue
                routes = []
                for place in candidates[:self.route_limit]:
                    loc = point_location(place)
                    target_request = dict(query=place['name'], address=None, postal_code=None, building=place['name'],
                                          source_url=place['source_url'], excerpt=place['name'])
                    result = await self.travel.travel(dict(origin=request['origin'], origin_location=location,
                        destination=target_request, destination_location=loc, mode=modes[0], transit_mode=transit,
                        departure_at=departure, assumptions=assumptions), ctx=ctx)
                    if result['data']:
                        evidence.extend(result['data']['evidence'])
                        routes.extend(result['data']['routes'])
                divisor = (TIME_UNITS if kind == 'time' else DISTANCE_UNITS).get(unit)
                if routes and divisor:
                    field = 'duration_seconds' if kind == 'time' else 'distance_m'
                    actual = min(r[field] for r in routes) / divisor
            elif kind == 'distance' and candidates and unit in DISTANCE_UNITS:
                actual = min(p['straight_line_distance_m'] for p in candidates) / DISTANCE_UNITS[unit]
            elif metric in ('exists', 'existence', 'nearby', 'amenities', '附近', '存在'):
                actual = True if candidates else None
            elif metric in ('count', '数量'):
                actual = len(candidates)
            check = compare_measurement(requirement, actual)
            # 可用点位/路线可以证明存在一个满足上界的候选；有限地图覆盖不能证明不存在。
            witnessed = check == 'pass' and (requirement['operator'] == 'lte' and kind in ('time', 'distance')
                or requirement['operator'] in ('eq', 'preferred') and actual is True
                or requirement['operator'] == 'gte' and metric in ('count', '数量'))
            if not witnessed:
                check = 'unknown'
                gaps.append('amenities:' + category + ':requirement_not_proven')
            if category not in data['completed_categories'] and not witnessed:
                gaps.append('amenities:' + category + ':source_incomplete')
            observations.append(dict(category=category, actual=actual, unit=requirement['unit'], check=check,
                                     candidate_count=len(candidates), scope_radius_m=radius,
                                     routes_limited_to=self.route_limit if route_needed else None))
        status = 'fulfilled' if observations and not gaps else 'unverified'
        check = 'pass' if status == 'fulfilled' else 'unknown'
        if status == 'fulfilled':
            first = evidence[0]
            evidence.append(make_evidence('derived_requirement.' + requirement['requirement_id'],
                dict(requirement_id=requirement['requirement_id'], investigation_status=status, check=check,
                     observations=observations), url=first['source_url'], observed=first['observed_at'],
                excerpt='周边设施与所需路线已核实；' + check))
        elif not problems:
            problems.append(issue('RETRIEVAL_DEGRADED', '配套需求尚未取得充分证据', source='neighborhood'))
        return _wrap(dict(evidence=evidence, gaps=gaps, investigation_status=status, check=check), problems, ctx, started)


if __name__ == '__main__':
    import argparse
    from property_agent.search.providers.onemap import OneMapProvider
    from property_agent.search.providers.osm import NeighborhoodProvider
    from property_agent.search.capabilities.location import LocationCapability
    from property_agent.search.capabilities.travel import TravelCapability
    from property_agent.domain.validation import validate_result_envelope

    parser = argparse.ArgumentParser(description='至少三组真实 2→3c 输入输出；五类设施均调用真实来源')
    parser.add_argument('--input', type=Path, required=True, help='数组：{request: AmenityRequest, ctx}')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    async def main():
        records = json.loads(args.input.read_text())
        if not isinstance(records, list) or len(records) < 3:
            parser.error('需要至少三组真实业务输入')
        p = OneMapProvider.from_env(); location = LocationCapability(p)
        capability = AmenitiesCapability(NeighborhoodProvider(p), location, TravelCapability(p, location))
        outputs = []
        for record in records:
            assert record['ctx']['source_mode'] == 'live'
            before = deepcopy(record)
            result = await capability.investigate(record['request'], ctx=record['ctx'])
            attempts = [deepcopy(result)]
            # 与模块 2 相同的有界重试；保留第一次真实失败，不能把故障隐藏为一次成功。
            if any(p['retryable'] for p in result['issues']):
                delay = max((p['retry_after_seconds'] or 0 for p in result['issues']), default=0)
                if delay < remaining_seconds(record['ctx']):
                    if delay:
                        await asyncio.sleep(delay)
                    result = await capability.investigate(record['request'], ctx=record['ctx'])
                    attempts.append(deepcopy(result))
            validate_result_envelope(result, AmenityResult, record['ctx'])
            assert before == record, '不得修改模块 2 的输入'
            verified = result['status'] == 'success' and set(result['data']['completed_categories']) == set(CATEGORIES)
            if verified:
                assert len(result['data']['evidence']) == 5
                assert all(p['source_url'] and 0 <= p['straight_line_distance_m'] <= record['request']['radius_m'] for p in result['data']['places'])
            outputs.append(dict(input=record, attempts=attempts, output=result, passed=verified))
            print(json.dumps(dict(status=result['status'], passed=verified, counts={c: sum(p['category'] == c for p in (result['data'] or {}).get('places', [])) for c in CATEGORIES}), ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2))
        return 0 if all(o['passed'] for o in outputs) else 1

    raise SystemExit(asyncio.run(main()))
