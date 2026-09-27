"""3d: Real route from home to destination. Defaults to next Monday-Friday 08:00 (Singapore) public transport."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
from time import monotonic
from zoneinfo import ZoneInfo


from property_agent.contracts import ContractViolation, DerivedDataRequirement, ProfileFact, Result, RunContext
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.execution.history import fingerprint
from property_agent.search.execution.tasks import InvestigationResult, LocationResult, RouteFact, TravelRequest, TravelResult
from property_agent.domain.validation import fail, timestamp, validate_context, validate_listing, validate_type
from property_agent.search.capabilities.location import _wrap, request_from_listing
from property_agent.search.providers.base import ProviderError, issue

SG = ZoneInfo('Asia/Singapore')
TIME_UNITS = {
    's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
    'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
    'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
}
DISTANCE_UNITS = {
    'm': 1, 'meter': 1, 'meters': 1, 'metre': 1, 'metres': 1,
    'km': 1000, 'kilometer': 1000, 'kilometers': 1000,
    'kilometre': 1000, 'kilometres': 1000,
}


def make_evidence(field, value, *, url, observed, excerpt):
    return dict(evidence_id=field + ':' + fingerprint([value, url, observed]), field=field,
                value=deepcopy(value), source_url=url, observed_at=observed, excerpt=excerpt)


def location_request(target, *, excerpt=None):
    """Explicit postal code/street number/building name is handed to 3b; ambiguous targets will not arbitrarily take the first result."""
    text = target.strip()
    postal = re.fullmatch(r'(?:Singapore\s*)?(\d{6})', text, re.I)
    address = text if not postal and re.match(r'^\d+[A-Za-z]?\s', text) else None
    return dict(query=text, address=address, postal_code=postal.group(1) if postal else None,
                building=text if not postal and not address else None, source_url=None, excerpt=excerpt or text)


def resolved_location(value):
    """Only building-level precision with corresponding location evidence can serve as a residential/destination route endpoint."""
    if value is None or value['status'] != 'resolved' or value['precision'] not in ('building', 'point'):
        return False
    return any(e['field'] in ('location', 'amenity_location') and isinstance(e['value'], dict)
        and e['value'].get('latitude') == value['latitude']
        and e['value'].get('longitude') == value['longitude'] for e in value['evidence'])


def commute_options(requirement, *, now=None):
    """Only parse clearly identifiable user mode/date/time; explicit conditions that cannot be interpreted retain gaps."""
    now = (now or datetime.now(SG)).astimezone(SG)
    value = requirement.get('value')
    options = value if isinstance(value, dict) else {}
    text = requirement['source']['text']
    metric = requirement.get('metric', '').lower()
    gaps, assumptions = [], []
    modes = []
    if options.get('mode'):
        aliases = {'public_transport': 'pt', 'transit': 'pt', 'walking': 'walk', 'driving': 'drive', 'cycling': 'cycle'}
        mode = aliases.get(options['mode'], options['mode'])
        if mode in ('pt', 'walk', 'drive', 'cycle'):
            modes = [mode]
        else:
            gaps.append('travel:unsupported_mode')
    else:
        for mode, pattern in [
            ('drive', r'\b(?:drive|driving|car)\b'),
            ('cycle', r'\b(?:cycle|cycling|bicycle|bike)\b'),
            ('walk', r'\b(?:walk|walking)\b'),
            ('pt', r'\b(?:public[\s_-]*transport|transit|bus|rail|mrt|subway)\b'),
        ]:
            if re.search(pattern, text + ' ' + metric, re.I):
                modes.append(mode)
        if re.search(r'\b(?:taxi|grab|motorcycle)\b', text, re.I):
            gaps.append('travel:unsupported_explicit_mode')
        if re.search(r"\b(?:do\s+not|don't|cannot|can't|avoid|not|no)\b", text, re.I) and modes:
            gaps.append('travel:mode_negation_needs_clarification')
    if not modes and not gaps:
        modes = ['pt']
        assumptions.append('Mode not specified: public transport (bus and rail combination)')
    transit_mode = options.get('transit_mode', 'TRANSIT').upper()
    if transit_mode not in ('TRANSIT', 'BUS', 'RAIL'):
        gaps.append('travel:unsupported_transit_mode')
    if re.search(r'\b(?:bus[\s_-]*only|only\s+(?:take|ride|use)?\s*bus)\b', text, re.I):
        transit_mode = 'BUS'
    if re.search(r'\b(?:(?:rail|mrt|subway)[\s_-]*only|only\s+(?:take|ride|use)?\s*(?:rail|mrt|subway))\b', text, re.I):
        transit_mode = 'RAIL'
    arrival_language = re.search(r'\b(?:arrive|arrival|reach|start\s+work|attend\s+class)\b', text, re.I)
    departure_language = re.search(r'\b(?:depart|departure|leave|leaving)\b', text, re.I)
    clock_language = re.search(
        r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
        text,
        re.I,
    )
    if options.get('arrival_at') or (not departure_language and arrival_language and clock_language):
        # OneMap documentation only guarantees departure time; arrival constraints are handled by a bounded reverse search in the travel call.
        time_kind = 'arrival'
    else:
        time_kind = 'departure'
    explicit = options.get('departure_at') or options.get('arrival_at')
    departure = None
    if explicit:
        try:
            departure = timestamp(explicit, 'requirement.value.departure_at').astimezone(SG)
        except ContractViolation:
            gaps.append('travel:invalid_datetime')
    else:
        hour, minute = 8, 0
        translated = text
        for word, digit in [('twelve', '12'), ('eleven', '11'), ('ten', '10'), ('nine', '9'), ('eight', '8'), ('seven', '7'), ('six', '6'), ('five', '5'), ('four', '4'), ('three', '3'), ('two', '2'), ('one', '1')]:
            translated = re.sub(rf"\b{word}\s+o['’]?clock\b", f"{digit} o'clock", translated, flags=re.I)
        matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?::(\d{2})|\s*o['’]?clock(?:\s+(half)|\s+(\d{1,2})\s+minutes?)?|\s*(am|pm)\b)", translated, re.I))
        if len(matches) > 1:
            gaps.append('travel:multiple_times_need_clarification')
        if matches:
            match = matches[0]
            hour, minute = int(match[1]), int(match[2] or match[4] or (30 if match[3] else 0))
            suffix = re.match(r'\s*(am|pm)\b', translated[match.end():], re.I)
            meridiem = (match[5] or (suffix[1] if suffix else '')).lower()
            if meridiem == 'pm' or re.search(r'afternoon|evening|dusk', translated):
                if hour < 12:
                    hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                gaps.append('travel:invalid_clock_time')
                hour, minute = 8, 0
        elif re.search(r'\b(?:evening rush|evening|afternoon|noon|early morning|dusk|night)\b', text, re.I):
            gaps.append('travel:explicit_time_needs_clarification')
        elif re.search(r'\bat\s+\d|\b\d{1,2}\s*o.clock', text, re.I):
            gaps.append('travel:explicit_time_needs_clarification')
        else:
            assumptions.append('Time not specified: morning rush 08:00 (Asia/Singapore)')
        date_match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', text)
        day = now.date()
        explicit_day = False
        if date_match:
            try:
                day = datetime.strptime(date_match[1], '%Y-%m-%d').date()
                explicit_day = True
            except ValueError:
                gaps.append('travel:invalid_date')
        elif 'day after tomorrow' in text:
            day += timedelta(days=2); explicit_day = True
        elif re.search(r'\btomorrow\b', text, re.I):
            day += timedelta(days=1); explicit_day = True
        elif re.search(r'\btoday\b', text, re.I):
            explicit_day = True
        else:
            weekdays = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
            found = [i for i, weekday in enumerate(weekdays) if re.search(rf'\b{weekday}\b', text, re.I)]
            if len(found) > 1:
                gaps.append('travel:multiple_dates_need_clarification')
            if found:
                offset = (found[0] - day.weekday()) % 7
                if 'next week' in text or re.search(r'next\s', text, re.I):
                    offset = 7 - day.weekday() + found[0]
                elif offset == 0 and now.hour * 60 + now.minute >= hour * 60 + minute:
                    offset = 7
                day += timedelta(days=offset); explicit_day = True
            elif re.search(r'\b(?:weekend|next\s+month|next\s+week)\b|\d{4}/\d|\d{1,2}/\d{1,2}', text, re.I):
                gaps.append('travel:explicit_date_needs_clarification')
        departure = datetime.combine(day, datetime.min.time(), SG).replace(hour=hour, minute=minute)
        if not explicit_day:
            if departure <= now:
                departure += timedelta(days=1)
            while departure.weekday() >= 5:
                departure += timedelta(days=1)
            assumptions.append('Date not specified: next queryable Monday-Friday; public holidays not verified')
    if departure is not None and departure <= now:
        gaps.append('travel:requested_time_in_past')
    return modes, transit_mode, departure.isoformat() if departure else None, time_kind, assumptions, gaps


def metric_kind(requirement):
    metric = requirement['metric'].lower()
    if any(word in metric for word in ('time', 'duration', 'time spent', 'minute', 'hour')):
        return 'time'
    if 'distance' in metric:
        return 'distance'
    if metric in ('commute', 'route', 'travel'):
        return 'time'
    return None


def compare_measurement(requirement, actual):
    expected, op = requirement['value'], requirement['operator']
    if isinstance(expected, dict):
        expected = expected.get('threshold')
    if op in ('minimize', 'maximize', 'preferred'):
        return 'observed'
    if actual is None or type(expected) not in (int, float, bool) and not (op == 'between' and isinstance(expected, list)):
        return 'unknown'
    if op == 'between':
        if len(expected) != 2 or not all(type(x) in (int, float) for x in expected):
            return 'unknown'
        passed = expected[0] <= actual <= expected[1]
    elif op in ('eq', 'lte', 'gte'):
        passed = {'eq': lambda: actual == expected, 'lte': lambda: actual <= expected, 'gte': lambda: actual >= expected}[op]()
    else:
        return 'unknown'
    return 'pass' if passed else 'fail'


def measurement(requirement, route):
    kind = metric_kind(requirement)
    units = TIME_UNITS if kind == 'time' else DISTANCE_UNITS
    unit = (requirement['unit'] or '').lower()
    divisor = units.get(unit)
    if divisor is None:
        return None
    return route['duration_seconds' if kind == 'time' else 'distance_m'] / divisor


class TravelCapability:
    def __init__(self, provider, location, *, max_calls=60):
        self.provider, self.location = provider, location
        self.max_calls, self.calls = max_calls, 0
        self._cache = {}

    async def travel(self, request: TravelRequest, *, ctx: RunContext) -> Result[TravelResult]:
        started = monotonic()
        evidence, gaps, problems = [], [], []
        try:
            validate_context(ctx)
            validate_type(TravelRequest, request, 'travel_request')
            departure = timestamp(request['departure_at'], 'travel_request.departure_at')
            if departure <= datetime.now(SG):
                fail('travel_request.departure_at', 'Travel time must be in the future')
            if self.provider is None:
                raise ProviderError(issue('SOURCE_UNAVAILABLE', 'Real route source not configured', source='routing'))
            if self.provider.source_mode != ctx['source_mode']:
                fail('ctx.source_mode', 'Route source mode is inconsistent')
            locations = []
            for side in ('origin', 'destination'):
                result = request[side + '_location']
                if result is None:
                    located = await self.location.locate(request[side], ctx=ctx)
                    problems.extend(located['issues'])
                    result = located['data']
                if not resolved_location(result):
                    gaps.append('travel:' + side + '_not_precise')
                locations.append(result)
            if gaps:
                if not problems:
                    problems.append(issue('RETRIEVAL_DEGRADED', 'Travel endpoints not yet precisely located', source='routing'))
                return _wrap(dict(status='unverified', routes=[], evidence=[], gaps=gaps), problems, ctx, started)
            origin, destination = locations
            scope = [ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode')]
            key = fingerprint([scope, origin['latitude'], origin['longitude'], destination['latitude'],
                destination['longitude'], request['mode'], request['transit_mode'], request['departure_at']])
            remaining_seconds(ctx)
            if key in self._cache:
                routes = deepcopy(self._cache[key])
            else:
                if self.calls >= self.max_calls:
                    raise ProviderError(issue('BUDGET_EXHAUSTED', 'Route call quota exhausted', source='routing'))
                self.calls += 1
                async with asyncio.timeout(remaining_seconds(ctx)):
                    routes = await self.provider.route(origin, destination, mode=request['mode'],
                        transit_mode=request['transit_mode'], departure_at=request['departure_at'], ctx=ctx)
                try:
                    validate_type(list[RouteFact], routes, 'routes')
                    for route in routes:
                        if route['duration_seconds'] < 0 or route['distance_m'] < 0 or not route['source_url']:
                            fail('routes', 'Route time/distance/source invalid')
                        timestamp(route['observed_at'], 'routes.observed_at')
                except ContractViolation as exc:
                    raise ProviderError(issue('INVALID_OUTPUT', str(exc), source=self.provider.source)) from exc
                if routes:
                    self._cache[key] = deepcopy(routes)
            for route in routes:
                value = dict(origin={k: origin[k] for k in ('standard_address', 'latitude', 'longitude', 'precision')},
                    destination={k: destination[k] for k in ('standard_address', 'latitude', 'longitude', 'precision')},
                    mode=request['mode'], transit_mode=request['transit_mode'], requested_departure_at=request['departure_at'],
                    destination_coordinate_kind=next((e['value'].get('coordinate_kind') for e in destination['evidence']
                        if e['field'] == 'amenity_location'), None),
                    assumptions=request['assumptions'], source_mode=ctx['source_mode'], **route)
                evidence.append(make_evidence('travel', value, url=route['source_url'], observed=route['observed_at'],
                    excerpt=f"{origin['standard_address']} → {destination['standard_address']}; "
                            f"{request['mode']}, {route['duration_seconds']} seconds, {route['distance_m']} meters; "
                            + ('Query by specified departure time period' if route['time_dependent'] else 'Static route time estimate, does not represent real-time morning rush traffic')))
            if not routes:
                gaps.append('travel:no_route_returned')
                problems.append(issue('RETRIEVAL_DEGRADED', 'No verifiable route obtained', source=self.provider.source))
            return _wrap(dict(status='resolved' if routes else 'unverified', routes=routes, evidence=evidence, gaps=gaps),
                         problems, ctx, started)
        except ContractViolation as exc:
            return _wrap(None, [issue(exc.code, str(exc), source='routing', field_path=exc.field_path)], ctx, started)
        except (ProviderError, TimeoutError) as exc:
            problem = exc.issue if isinstance(exc, ProviderError) else issue('TIMEOUT', 'Route query reached deadline', source='routing', retryable=True)
            return _wrap(dict(status='unverified', routes=[], evidence=evidence, gaps=['travel:' + problem['code']]),
                         [problem], ctx, started)

    async def investigate(self, listing, origin_location, requirement, user_context, *, ctx, now=None):
        """Module 2 commute investigation task; returns two independent conclusions: data completion status and condition match status."""
        started = monotonic()
        try:
            validate_context(ctx)
            validate_listing(listing)
            validate_type(DerivedDataRequirement, requirement, 'requirement')
            validate_type(list[ProfileFact], user_context, 'user_context')
            validate_type(LocationResult | None, origin_location, 'origin_location')
        except ContractViolation as exc:
            return _wrap(None, [issue(exc.code, str(exc), field_path=exc.field_path, source='routing')], ctx, started)
        modes, transit, departure, time_kind, assumptions, gaps = commute_options(requirement, now=now)
        target = requirement['target']
        if target is None or target.lower() in ('company', 'school', 'work', 'workplace', 'office'):
            desired = 'occupant.school' if target and target.lower() == 'school' else 'occupant.workplace' if target else None
            targets = [fact['value'] for fact in user_context if fact['field'] in ('occupant.workplace', 'occupant.school')
                       and (desired is None or fact['field'] == desired) and isinstance(fact['value'], str)]
            targets = list(dict.fromkeys(targets))
            target = targets[0] if len(targets) == 1 else None
        if not target:
            gaps.append('travel:destination_missing_or_ambiguous')
        evidence, problems, observations = [], [], []
        if not gaps:
            for mode in modes:
                query_time = datetime.fromisoformat(departure)
                if time_kind == 'arrival':
                    query_time -= timedelta(hours=2)
                    query_time = max(query_time, datetime.now(SG) + timedelta(minutes=1))
                request = dict(origin=request_from_listing(listing), destination=location_request(target),
                    origin_location=origin_location, destination_location=None, mode=mode, transit_mode=transit,
                    departure_at=query_time.isoformat(), assumptions=assumptions)
                response = await self.travel(request, ctx=ctx)
                problems.extend(response['issues'])
                data = response['data']
                if data is None:
                    gaps.append('travel:invalid_request'); continue
                evidence.extend(data['evidence']); gaps.extend(data['gaps'])
                routes = data['routes']
                if time_kind == 'arrival':
                    # Use real departure query for reverse inference, supplement at most once; do not falsely claim to obtain the latest departure option.
                    if mode != 'pt':
                        gaps.append('travel:arrival_time_requires_time_dependent_route'); continue
                    deadline = datetime.fromisoformat(departure)
                    valid = [r for r in routes if r['arrival_at'] and datetime.fromisoformat(r['arrival_at']) <= deadline]
                    if valid:
                        earliest = min(valid, key=lambda r: r['duration_seconds'])
                        revised = deadline - timedelta(seconds=earliest['duration_seconds'] + 600)
                        if revised > query_time:
                            request['departure_at'] = revised.isoformat()
                            second = await self.travel(request, ctx=ctx)
                            if second['data']:
                                evidence.extend(second['data']['evidence'])
                                valid.extend(r for r in second['data']['routes'] if r['arrival_at'] and datetime.fromisoformat(r['arrival_at']) <= deadline)
                    routes = valid
                    if not routes:
                        gaps.append('travel:arrival_deadline_unverified')
                if routes:
                    best = min(routes, key=lambda r: r['distance_m' if metric_kind(requirement) == 'distance' else 'duration_seconds'])
                    actual = measurement(requirement, best)
                    check = compare_measurement(requirement, actual)
                    if actual is None:
                        gaps.append('travel:metric_unit_not_understood')
                    observations.append(dict(mode=mode, actual=actual, unit=requirement['unit'], check=check,
                                             route=best, requested_time=departure, time_kind=time_kind))
        if gaps and not problems:
            problems.append(issue('RETRIEVAL_DEGRADED', 'Commute requirement pending verification: ' + ', '.join(gaps), source='routing'))
        complete = bool(observations) and len(observations) == len(modes) and not gaps
        status = 'fulfilled' if complete else 'unverified'
        check = ('fail' if observations and all(o['check'] == 'fail' for o in observations) else 'pass'
                 if observations and all(o['check'] == 'pass' for o in observations) else 'unknown')
        payload = dict(requirement_id=requirement['requirement_id'], investigation_status=status,
                       check=check, observations=observations, assumptions=assumptions, gaps=gaps)
        if complete:
            route = observations[0]['route']
            evidence.append(make_evidence('derived_requirement.' + requirement['requirement_id'], payload,
                url=route['source_url'], observed=route['observed_at'], excerpt='Real route investigation complete; condition comparison result: ' + check))
        return _wrap(dict(evidence=evidence, gaps=gaps, investigation_status=status, check=check), problems, ctx, started)


if __name__ == '__main__':
    import argparse
    from property_agent.search.providers.onemap import OneMapProvider
    from property_agent.search.capabilities.location import LocationCapability
    from property_agent.domain.validation import validate_result_envelope

    parser = argparse.ArgumentParser(description='At least three sets of real 2→3d input and output; do not use preset routes')
    parser.add_argument('--input', type=Path, required=True, help='Array: {listing, location, requirement, user_context, ctx}')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    async def main():
        records = json.loads(args.input.read_text())
        if not isinstance(records, list) or len(records) < 3:
            parser.error('At least three sets of real business input required')
        provider = OneMapProvider.from_env()
        capability = TravelCapability(provider, LocationCapability(provider))
        outputs = []
        for record in records:
            assert record['ctx']['source_mode'] == record['listing']['source_mode'] == 'live'
            before = deepcopy(record)
            result = await capability.investigate(record['listing'], record['location'], record['requirement'],
                                                 record['user_context'], ctx=record['ctx'])
            validate_result_envelope(result, InvestigationResult, record['ctx'])
            assert before == record, 'Do not modify module 2 input'
            expected = record.get('expected', {})
            expected_status = expected.get('investigation_status', 'fulfilled')
            verified = result['data'] is not None and result['data']['investigation_status'] == expected_status
            if verified and expected_status == 'fulfilled':
                assert result['status'] == 'success'
                facts = [e for e in result['data']['evidence'] if e['field'] == 'travel']
                assert facts and all(e['source_url'] and e['value']['duration_seconds'] >= 0 for e in facts)
                assert all(e['value']['origin']['latitude'] == record['location']['latitude'] for e in facts)
                if 'mode' in expected:
                    assert all(e['value']['mode'] == expected['mode'] for e in facts)
                if 'hour' in expected:
                    departures = [datetime.fromisoformat(e['value']['requested_departure_at']).astimezone(SG) for e in facts]
                    assert all(t.hour == expected['hour'] and t.minute == expected.get('minute', 0) for t in departures)
                    assert all(t.weekday() < 5 for t in departures)
            outputs.append(dict(input=record, output=result, passed=verified))
            print(json.dumps(dict(requirement=record['requirement']['requirement_id'], status=result['status'], passed=verified), ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2))
        return 0 if all(o['passed'] for o in outputs) else 1

    raise SystemExit(asyncio.run(main()))
