"""3c: Investigate five amenity categories from a home within an explicit radius; reuse 3d for walking constraints."""
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
    'mrt': r'\b(?:rail|railway|mrt|lrt|metro|subway)\b',
    'bus': r'\b(?:public\s+bus|bus)\b',
    'supermarket': r'\b(?:supermarket|grocery|fairprice|sheng\s+siong|cold\s+storage)\b',
    'school': r'\b(?:primary\s+school|secondary\s+school|school|college|university|kindergarten)\b',
    'park': r'\b(?:park|garden)\b',
}
GENERIC_TARGETS = {
    'mrt': {'mrt', 'lrt', 'mrt station', 'mrt stations', 'metro', 'subway', 'rail station'},
    'bus': {'bus', 'bus stop', 'bus stops', 'public bus'},
    'supermarket': {'supermarket', 'supermarkets', 'grocery', 'grocery store'},
    'school': {'school', 'schools', 'primary school', 'secondary school'},
    'park': {'park', 'parks', 'garden'},
}


def matches_target(place, requirement):
    target = (requirement['target'] or '').strip().lower()
    target = re.sub(r'^(?:nearby\s+|nearest\s+|closest\s+|surrounding\s+)', '', target)
    if not target or target in GENERIC_TARGETS[place['category']]:
        return True
    # A specific brand or school name cannot be replaced by any amenity of the same category; leave unmatched aliases unresolved.
    return target in place['name'].lower()


def categories_for(requirement):
    text = (requirement['target'] or '') + ' ' + requirement['metric']
    return [category for category, pattern in CATEGORY_PATTERNS.items() if re.search(pattern, text, re.I)]


def supports_requirement(requirement):
    """Support only interpretable metrics; continue reporting other derived categories as unsupported."""
    if requirement['category'] == 'commute':
        return metric_kind(requirement) is not None
    if requirement['category'] == 'nearby_amenity':
        return bool(categories_for(requirement)) and (metric_kind(requirement) is not None or
            requirement['metric'].lower() in ('nearby', 'exists', 'existence', 'count', 'amenities'))
    return False


def point_location(place):
    """Amenity queries provide point coordinates; retain the centroid convention without calling it a school gate or park entrance."""
    value = dict(latitude=place['latitude'], longitude=place['longitude'], coordinate_kind=place['coordinate_kind'])
    evidence = make_evidence('amenity_location', value, url=place['source_url'], observed=place['observed_at'],
                             excerpt=place['name'] + ': amenity source point; it does not represent an entrance')
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
                fail('amenity_request', 'Distinct categories and a search radius of 1–4999 metres are required')
            if self.provider is None:
                raise ProviderError(issue('SOURCE_UNAVAILABLE', 'No amenity source is configured', source='neighborhood'))
            if self.provider.source_mode != ctx['source_mode']:
                fail('ctx.source_mode', 'Amenity source mode does not match')
            origin = request['origin_location']
            if origin is None:
                response = await self.location.locate(request['origin'], ctx=ctx)
                origin = response['data']; problems.extend(response['issues'])
            if not resolved_location(origin):
                raise ProviderError(issue('RETRIEVAL_DEGRADED', 'The home has not been geolocated precisely enough to verify nearby amenities', source='neighborhood'))
            for category in request['categories']:
                try:
                    remaining_seconds(ctx)
                    scope = [ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode')]
                    key = fingerprint([scope, origin['latitude'], origin['longitude'], category, request['radius_m']])
                    if key in self._cache:
                        matches = deepcopy(self._cache[key])
                    else:
                        if self.calls >= self.max_calls:
                            raise ProviderError(issue('BUDGET_EXHAUSTED', 'The amenity query budget is exhausted', source='neighborhood'))
                        self.calls += 1
                        async with asyncio.timeout(remaining_seconds(ctx)):
                            matches = await self.provider.nearby(origin, category, request['radius_m'], ctx=ctx)
                        try:
                            validate_type(PlaceMatches, matches, 'places')
                            if any(p['category'] != category or not p['source_url'] or
                                   not 0 <= p['straight_line_distance_m'] <= request['radius_m'] for p in matches['items']):
                                fail('places', 'Amenity category, distance, or source is invalid')
                        except ContractViolation as exc:
                            raise ProviderError(issue('INVALID_OUTPUT', str(exc), source='neighborhood')) from exc
                        if matches['complete']:
                            self._cache[key] = deepcopy(matches)
                    # Deduplicate by stable OSM/OneMap IDs; do not merge separate branches or stops by name.
                    items = list({p['place_id']: p for p in matches['items']}.values())
                    places.extend(items)
                    if matches['complete']:
                        completed.append(category)
                    else:
                        gaps.append('amenities:' + category + ':incomplete_source')
                        problems.append(issue('RETRIEVAL_DEGRADED', 'Incomplete amenity response: ' + category, source='neighborhood'))
                    payload = dict(category=category, origin={k: origin[k] for k in ('standard_address', 'latitude', 'longitude')},
                        radius_m=request['radius_m'], distance_basis='straight_line_to_source_point',
                        source_response_complete=matches['complete'], coverage_scope='mapped_facilities_only',
                        source_mode=ctx['source_mode'], places=items,
                        limitations=['The data source may omit amenities; an empty result does not prove none exist', 'A point or area centroid is not guaranteed to be the actual entrance'])
                    evidence.append(make_evidence('nearby_amenity.' + category, payload, url=matches['source_url'],
                        observed=matches['observed_at'], excerpt=f"{category}: within a {request['radius_m']}-metre straight-line radius of the home, "
                        f"the source returned {len(items)} points; straight-line distance was not treated as walking distance"))
                except (ProviderError, TimeoutError) as exc:
                    problem = exc.issue if isinstance(exc, ProviderError) else issue('TIMEOUT', 'The amenity query reached its deadline', source='neighborhood', retryable=True)
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
        # An explicit straight-line or walking upper bound determines candidate retrieval range without relaxing the user constraint itself.
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
        route_needed = kind == 'time' or kind == 'distance' and not re.search(r'straight|straight-line', metric + ' ' + requirement['source']['text'], re.I)
        for category in categories:
            candidates = [p for p in data['places'] if p['category'] == category and matches_target(p, requirement)]
            actual = None
            if route_needed:
                # Amenity trips start from home and default to walking; the 3d commute default does not apply to amenity walking.
                modes, transit, departure, time_kind, assumptions, option_gaps = commute_options(requirement)
                if any(a.startswith('Travel mode not specified') for a in assumptions):
                    modes = ['walk']
                    assumptions = [a for a in assumptions if not a.startswith('Travel mode not specified')] + ['Amenity distance/time travel mode not specified: walking']
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
            elif metric in ('exists', 'existence', 'nearby', 'amenities'):
                actual = True if candidates else None
            elif metric in ('count',):
                actual = len(candidates)
            check = compare_measurement(requirement, actual)
            # An available point or route can prove that a candidate meets the upper bound; limited map coverage cannot prove absence.
            witnessed = check == 'pass' and (requirement['operator'] == 'lte' and kind in ('time', 'distance')
                or requirement['operator'] in ('eq', 'preferred') and actual is True
                or requirement['operator'] == 'gte' and metric in ('count',))
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
                excerpt='Nearby amenities and required routes were verified; ' + check))
        elif not problems:
            problems.append(issue('RETRIEVAL_DEGRADED', 'The amenity requirement does not yet have sufficient evidence', source='neighborhood'))
        return _wrap(dict(evidence=evidence, gaps=gaps, investigation_status=status, check=check), problems, ctx, started)


if __name__ == '__main__':
    import argparse
    from property_agent.search.providers.onemap import OneMapProvider
    from property_agent.search.providers.osm import NeighborhoodProvider
    from property_agent.search.capabilities.location import LocationCapability
    from property_agent.search.capabilities.travel import TravelCapability
    from property_agent.domain.validation import validate_result_envelope

    parser = argparse.ArgumentParser(description='At least three real 2→3c input/output cases; all five amenity categories use real sources')
    parser.add_argument('--input', type=Path, required=True, help='Array: {request: AmenityRequest, ctx}')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    async def main():
        records = json.loads(args.input.read_text())
        if not isinstance(records, list) or len(records) < 3:
            parser.error('At least three real business inputs are required')
        p = OneMapProvider.from_env(); location = LocationCapability(p)
        capability = AmenitiesCapability(NeighborhoodProvider(p), location, TravelCapability(p, location))
        outputs = []
        for record in records:
            assert record['ctx']['source_mode'] == 'live'
            before = deepcopy(record)
            result = await capability.investigate(record['request'], ctx=record['ctx'])
            attempts = [deepcopy(result)]
            # Use the same bounded retry as module 2; retain the first real failure rather than hiding it behind a successful retry.
            if any(p['retryable'] for p in result['issues']):
                delay = max((p['retry_after_seconds'] or 0 for p in result['issues']), default=0)
                if delay < remaining_seconds(record['ctx']):
                    if delay:
                        await asyncio.sleep(delay)
                    result = await capability.investigate(record['request'], ctx=record['ctx'])
                    attempts.append(deepcopy(result))
            validate_result_envelope(result, AmenityResult, record['ctx'])
            assert before == record, 'Module 2 input must not be modified'
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
