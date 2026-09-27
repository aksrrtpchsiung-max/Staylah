"""3b: Address clues with provenance -> standardized address, coordinates, precision, and ambiguity.

Do not modify Listing.location_id, and do not guess the property location from the title or nearby amenities.
Run real geolocation input/output: .venv/bin/python -m property_agent.search.capabilities.location
"""
import asyncio
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from time import monotonic


from property_agent.contracts import ContractViolation, Listing, Result, RunContext
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.execution.tasks import GeocodeMatches, LocationRequest, LocationResult
from property_agent.domain.validation import fail, timestamp, validate_context, validate_listing, validate_type
from property_agent.search.providers.base import GeocodingProvider, ProviderError, issue


def normalized(value):
    text = re.sub(r'[^A-Z0-9 ]', ' ', (value or '').upper())
    aliases = {'RD': 'ROAD', 'AVE': 'AVENUE', 'ST': 'STREET', 'DR': 'DRIVE', 'CRES': 'CRESCENT'}
    return ' '.join(aliases.get(word, word) for word in text.split())


def address_identity(value):
    text = normalized(value)
    text = re.sub(r'\b(?:SINGAPORE|S)\s*\d{6}\b', '', text)
    text = re.sub(r'\b(?:BLOCK|BLK)\b', '', text)
    return ' '.join(text.split())


def request_from_listing(listing: Listing) -> LocationRequest:
    """Use only explicitly labeled location fields; do not scan nearby subway stations, descriptions, or numbers in the title."""
    validate_listing(listing)
    values = {'address': [], 'postal_code': [], 'building': []}
    excerpts = []

    def add(key, value, excerpt):
        if isinstance(value, str) and value.strip() and value.strip().upper() not in {'NIL', 'NULL'}:
            value = value.strip()
            if key == 'postal_code' and not re.fullmatch(r'\d{6}', value):
                return
            values[key].append(value)
            excerpts.append(excerpt)

    labels = {'address': 'address', 'fulladdress': 'address', 'formattedaddress': 'address',
              'postal': 'postal_code', 'postalcode': 'postal_code', 'postcode': 'postal_code',
              'building': 'building', 'buildingname': 'building', 'project': 'building', 'projectname': 'building'}
    for raw in listing['raw_details']:
        label, sep, value = raw.partition(':')
        key = re.sub(r'[^a-z]', '', label.lower())
        if not sep:
            continue
        if key in labels:
            add(labels[key], value, raw)
        elif key == 'locationinformation':
            try:
                info = json.loads(value)
            except ValueError:
                continue
            if isinstance(info, dict):
                # Access only explicit address keys; do not recursively fetch other locations such as nearby/place.
                for name, content in info.items():
                    mapped = labels.get(re.sub(r'[^a-z]', '', name.lower()))
                    if mapped:
                        add(mapped, content, raw)
                block = info.get('block') or info.get('blockNumber')
                road = info.get('roadName') or info.get('streetName')
                if isinstance(block, (str, int)) and isinstance(road, str) and not values['address']:
                    add('address', f'{block} {road}', raw)
    for address in values['address']:
        match = re.search(r'\b(?:Singapore|S)\s*(\d{6})\b', address, flags=re.I)
        if match:
            add('postal_code', match.group(1), address)
    # Conflicting raw addresses are left as gaps; do not quietly pick one.
    conflicting = any(len({normalized(v) for v in group}) > 1 for group in values.values())
    chosen = {key: group[0] if group else None for key, group in values.items()}
    query = chosen['postal_code'] or chosen['address'] or chosen['building'] or ''
    return dict(query='' if conflicting else query, **chosen,
                source_url=listing['source_url'], excerpt='\n'.join(dict.fromkeys(excerpts)))


def _blank(status, gaps, candidates=None):
    return dict(status=status, standard_address=None, latitude=None, longitude=None, precision='unknown',
                candidates=candidates or [], evidence=[], gaps=gaps)


def _wrap(data, problems, ctx, started):
    context = ctx if isinstance(ctx, dict) else {}
    return dict(status='error' if data is None else 'partial' if problems else 'success',
                data=data, issues=problems, meta=dict(
                    trace_id=context.get('trace_id', '') if isinstance(context.get('trace_id', ''), str) else '',
                    call_id=context.get('call_id', '') if isinstance(context.get('call_id', ''), str) else '',
                    duration_ms=max(0, int((monotonic() - started) * 1000))))


class LocationCapability:
    def __init__(self, provider: GeocodingProvider):
        self.provider = provider
        self._cache = {}
        self._lock = asyncio.Lock()

    async def locate(self, request: LocationRequest, *, ctx: RunContext) -> Result[LocationResult]:
        started = monotonic()
        try:
            validate_context(ctx)
            validate_type(LocationRequest, request, 'location_request')
            if self.provider.source_mode != ctx['source_mode']:
                fail('ctx.source_mode', 'Geolocation Provider differs from context mode')
            if request['postal_code'] is not None and not re.fullmatch(r'\d{6}', request['postal_code']):
                fail('location_request.postal_code', 'A six-digit Singapore postal code is required')
            if not request['query'].strip() or not request['excerpt'].strip():
                problem = issue('RETRIEVAL_DEGRADED', 'Missing an explicit property address, or the raw addresses conflict', source=self.provider.source)
                return _wrap(_blank('insufficient', ['address:insufficient_or_conflicting']), [problem], ctx, started)
            async with asyncio.timeout(remaining_seconds(ctx)):
                scope = tuple(ctx[k] for k in ('user_id', 'run_id', 'conversation_id', 'attempt_id', 'source_mode'))
                key = (scope, normalized(request['query']))
                async with self._lock:
                    if key in self._cache:
                        matches = deepcopy(self._cache[key])
                    else:
                        matches = await self.provider.geocode(request['query'], ctx=ctx)
                        try:
                            validate_type(GeocodeMatches, matches, 'geocoding')
                            for candidate in matches['candidates']:
                                lat, lon = candidate['latitude'], candidate['longitude']
                                if not (math.isfinite(lat) and math.isfinite(lon) and 1.1 <= lat <= 1.6 and 103.5 <= lon <= 104.2):
                                    fail('geocoding.coordinates', 'Not valid coordinates within Singapore')
                                if not candidate['address'].strip() or not candidate['source_url'].strip():
                                    fail('geocoding', 'Geolocation results must have an address and a source')
                                timestamp(candidate['observed_at'], 'geocoding.observed_at')
                        except ContractViolation as exc:
                            raise ProviderError(issue('INVALID_OUTPUT', str(exc), source=self.provider.source, field_path=exc.field_path)) from exc
                        if matches['complete']:
                            self._cache[key] = deepcopy(matches)
            data = self._match(request, matches)
            problems = [] if data['status'] == 'resolved' else [issue('RETRIEVAL_DEGRADED',
                'Address did not get a unique reliable match: ' + ', '.join(data['gaps']), source=self.provider.source)]
            validate_type(LocationResult, data, 'location_result')
            return _wrap(data, problems, ctx, started)
        except ContractViolation as exc:
            return _wrap(None, [issue(exc.code, str(exc), source=self.provider.source, field_path=exc.field_path)], ctx, started)
        except ProviderError as exc:
            problem = dict(exc.issue, source=self.provider.source)
            return _wrap(_blank('unavailable', ['location:' + problem['code']]), [problem], ctx, started)
        except TimeoutError:
            return _wrap(_blank('unavailable', ['location:TIMEOUT']),
                         [issue('TIMEOUT', 'Geolocation reached the deadline', source=self.provider.source, retryable=True)], ctx, started)

    def _match(self, request, matches):
        candidates = list({json.dumps(c, sort_keys=True): c for c in matches['candidates']}.values())
        if not matches['complete']:
            return _blank('ambiguous', ['location:incomplete_candidates'], candidates)
        if not candidates:
            return _blank('not_found', ['location:no_match'])
        eligible = []
        for candidate in candidates:
            matched, precision = False, 'unknown'
            if request['postal_code']:
                if candidate['postal_code'] != request['postal_code']:
                    continue
                matched, precision = True, 'building'
            if request['address']:
                address = address_identity(request['address'])
                full = address_identity(candidate['address'])
                street = address_identity(' '.join(filter(None, [candidate['block'], candidate['road']])))
                if address in {full, street}:
                    matched, precision = True, 'building' if candidate['block'] else 'road'
                elif address == normalized(candidate['road']):
                    if not matched:
                        matched, precision = True, 'road'
                elif (matched and request['postal_code']
                      and re.fullmatch(r'(?:STREET|AVENUE|DRIVE|ROAD) \d+[A-Z]?', address)
                      and normalized(candidate['road']).endswith(' ' + address)):
                    # The source may only label St 96; it can be completed only if the exact postal code matches and the road suffix agrees.
                    # Complete house number or road conflicts are still rejected by the branch below.
                    pass
                else:
                    continue  # Even if the postal code matches, an explicit house number/road contradiction cannot be confirmed.
            if request['building']:
                if normalized(request['building']) != normalized(candidate['building']):
                    continue
                matched, precision = True, 'building'
            if matched:
                eligible.append((candidate, precision))
        # Duplicate records with the same address and coordinates do not create ambiguity; multi-building projects still retain ambiguity.
        unique = {(normalized(c['address']), c['latitude'], c['longitude']): (c, p) for c, p in eligible}
        if len(unique) != 1:
            return _blank('ambiguous', ['location:multiple_matches' if unique else 'location:unverified_match'], candidates)
        chosen, precision = next(iter(unique.values()))
        payload = dict(standard_address=chosen['address'], postal_code=chosen['postal_code'],
                       latitude=chosen['latitude'], longitude=chosen['longitude'], precision=precision,
                       source=self.provider.source, source_mode=self.provider.source_mode,
                       input_query=request['query'], input_source_url=request['source_url'])
        identity = json.dumps([payload, chosen['observed_at']], ensure_ascii=False, sort_keys=True)
        evidence = dict(evidence_id='location:' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        field='location', value=payload, source_url=chosen['source_url'],
                        observed_at=chosen['observed_at'], excerpt=f"Address basis: {request['excerpt']}\nMap returned: {chosen['address']}")
        return dict(status='resolved', standard_address=chosen['address'], latitude=chosen['latitude'],
                    longitude=chosen['longitude'], precision=precision, candidates=candidates, evidence=[evidence], gaps=[])


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from property_agent.search.providers.onemap import OneMapProvider

    parser = argparse.ArgumentParser(description='3b real geolocation input/output; requires OneMap credentials')
    parser.add_argument('--input', type=Path, help='Listing array or SearchResult JSON file returned by real 3a')
    args = parser.parse_args()

    async def main():
        if args.input:
            payload = json.loads(args.input.read_text())
            if isinstance(payload, dict):
                payload = payload.get('data') or payload
                payload = payload.get('items', [])
            requests = [request_from_listing(item) for item in payload]
        else:
            # Only the real postal code to query; no expected address or coordinates are hardcoded.
            requests = [dict(query=postal, address=None, postal_code=postal, building=None,
                             source_url=None, excerpt='Geolocation query postal code: ' + postal)
                        for postal in ('200640', '307987', '049213')]
        if len(requests) < 3:
            parser.error('At least three real geolocation inputs are required')
        capability = LocationCapability(OneMapProvider.from_env())
        passed = 0
        for request in requests:
            identity = str(uuid4())
            ctx = dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                       trace_id=identity, call_id=identity, source_mode='live',
                       deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat())
            result = await capability.locate(request, ctx=ctx)
            passed += result['status'] == 'success' and result['data']['status'] == 'resolved'
            print(json.dumps(dict(input=request, output=result), ensure_ascii=False), flush=True)
        print(f'Real 3b geolocation passed {passed}/{len(requests)}; gaps and failures will not be replaced with virtual coordinates.')
        return 0 if passed == len(requests) else 1

    raise SystemExit(asyncio.run(main()))
