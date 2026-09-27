"""4/5: Consolidate the facts already obtained and return Result[SearchResult]; do not dispatch work, filter, or recommend.

Prices retain their original currency and month/week/total basis; areas follow the sqft from 3a; do not guess exchange rates,
rental periods, or missing fields. Date-times are unified to UTC; original page text and evidence source text are preserved.
"""
from copy import deepcopy
from datetime import timezone
from pathlib import Path
import sys
from time import monotonic
from typing import get_args, get_type_hints


from property_agent.contracts import ContractViolation, Listing, ListingAttributes, Result, SearchResult
from property_agent.search.execution.history import fingerprint
from property_agent.domain.validation import timestamp, validate_listing, validate_search_result
from property_agent.search.providers.base import issue


def _unique(values):
    return list(dict.fromkeys(values))


def normalize_listing(listing: Listing) -> Listing:
    """Only normalize existing structured values; do not generate new facts from free text, and do not modify caller data."""
    validate_listing(listing)
    result = deepcopy(listing)
    result['price']['currency'] = result['price']['currency'].strip().upper()
    for key in ('fetched_at', 'source_updated_at', 'last_verified_at'):
        if result[key] is not None:
            result[key] = timestamp(result[key], key).astimezone(timezone.utc).isoformat()
    for fact in result['evidence']:
        fact['observed_at'] = timestamp(fact['observed_at'], 'evidence.observed_at').astimezone(timezone.utc).isoformat()
        if fact['field'] == 'price.currency' and isinstance(fact['value'], str):
            fact['value'] = fact['value'].strip().upper()
    for key in ('raw_details', 'field_issues'):
        result[key] = _unique(result[key])
    result['price']['evidence_ids'] = _unique(result['price']['evidence_ids'])
    return result


def merge_listing(existing: Listing, incoming: Listing) -> Listing:
    """Merge listings from the same source and same property; when renaming conflicting evidence, update all price references accordingly."""
    result, incoming = normalize_listing(existing), normalize_listing(incoming)
    if any(result[k] != incoming[k] for k in ('listing_key', 'source', 'source_mode')):
        raise ContractViolation('INVALID_OUTPUT', 'listing.listing_key', 'Cannot merge listings from different sources or identities')
    evidence = {e['evidence_id']: e for e in result['evidence']}
    remapped = {}
    for fact in incoming['evidence']:
        original_id = fact['evidence_id']
        eid = original_id
        if eid in evidence and evidence[eid] != fact:
            eid = original_id + ':' + fingerprint(fact)
            suffix = 1
            # When the same fact is merged again, reuse the same ID; do not append evidence indefinitely.
            while eid in evidence and dict(evidence[eid], evidence_id=original_id) != fact:
                eid = original_id + ':' + fingerprint(fact) + ':' + str(suffix)
                suffix += 1
        remapped[original_id] = eid
        evidence[eid] = dict(fact, evidence_id=eid)
    result['evidence'] = list(evidence.values())
    result['raw_details'] = _unique(result['raw_details'] + incoming['raw_details'])
    result['field_issues'] = _unique(result['field_issues'] + incoming['field_issues'])
    price_ids = _unique(result['price']['evidence_ids'] + [remapped[eid] for eid in incoming['price']['evidence_ids']])
    for field in ('amount', 'currency', 'period'):
        old, new = result['price'][field], incoming['price'][field]
        if old is not None and new is not None and old != new:
            result['field_issues'].append('price.' + field + ':conflict')
        elif old is None:
            result['price'][field] = new
    if (result['price']['status'] == 'conflict' or incoming['price']['status'] == 'conflict'
            or any(f.startswith('price.') and f.endswith(':conflict') for f in result['field_issues'])):
        result['price'].update(amount=None, status='conflict')
        if 'price.period:conflict' in result['field_issues']:
            result['price']['period'] = None
    else:
        result['price']['status'] = 'known' if result['price']['amount'] is not None else 'unknown'
    result['price']['evidence_ids'] = price_ids
    for target, source, prefix, schema in (
        (result, incoming, '', Listing), (result['attributes'], incoming['attributes'], 'attributes.', ListingAttributes)
    ):
        fields = ('bedrooms', 'location_id', 'listing_status', 'listed_date', 'source_listing_id') if not prefix else tuple(source)
        hints = get_type_hints(schema)
        for key in fields:
            old, new = target[key], source[key]
            conflict = prefix + key + ':conflict'
            if conflict in result['field_issues'] or (old not in (None, 'unknown') and new not in (None, 'unknown') and old != new):
                result['field_issues'].append(conflict)
                target[key] = 'unknown' if 'unknown' in get_args(hints[key]) else None
            elif old in (None, 'unknown'):
                target[key] = new
    if result['transaction_type'] != incoming['transaction_type']:
        # The contract does not allow an unknown transaction type; keep the first entry and explicitly flag the conflict; a match cannot be determined.
        result['field_issues'].append('transaction_type:conflict')
    if not result['source_url']:
        result['source_url'] = incoming['source_url']
    if timestamp(incoming['fetched_at'], 'incoming.fetched_at') >= timestamp(result['fetched_at'], 'existing.fetched_at'):
        if incoming['raw_description']:
            result['raw_description'] = incoming['raw_description']
        if incoming['title'].strip():
            result['title'] = incoming['title']
    elif not result['raw_description']:
        result['raw_description'] = incoming['raw_description']
    for key in ('fetched_at', 'source_updated_at', 'last_verified_at'):
        dates = [value for value in (result[key], incoming[key]) if value is not None]
        result[key] = max(dates, key=lambda value: timestamp(value, key)) if dates else None
    result['field_issues'] = _unique(result['field_issues'])
    validate_listing(result)
    return result


def error_result(problem, ctx, started) -> Result[SearchResult]:
    """When boundary input is invalid, still return the contract shell, and do not echo secrets or exception stack traces."""
    context = ctx if isinstance(ctx, dict) else {}
    return dict(status='error', data=None, issues=[deepcopy(problem)], meta=dict(
        trace_id=context.get('trace_id', '') if isinstance(context.get('trace_id', ''), str) else '',
        call_id=context.get('call_id', '') if isinstance(context.get('call_id', ''), str) else '',
        duration_ms=max(0, int((monotonic() - started) * 1000))))


def aggregate(state, started) -> Result[SearchResult]:
    """Build the snapshot for C from the management layer's final state; a complete run with no matches still returns a success empty list."""
    plan, ctx = state['plan'], state['ctx']
    queries, pages = state['queries'], state['pages']
    problems = deepcopy(state['issues'] + [p for group in state['task_issues'].values() for p in group])
    queried_sources = _unique(entry['source'] for entry in state['history'] if entry['kind'] == 'search_page')
    # Count only attempts that ultimately still failed; historical errors from successful retries do not pollute the final coverage.
    latest = {entry['task_id']: entry for entry in state['history']}
    failed_sources = _unique(entry['source'] for tid, entry in latest.items()
        if entry['source'] in queried_sources and entry['status'] != 'success'
        and any(p['code'] not in {'RETRIEVAL_DEGRADED', 'BUDGET_EXHAUSTED'}
                for p in state['task_issues'].get(tid, [])))
    applied = set(pages[0]['applied_filters']) if pages else set()
    reported = set()
    unsupported = set()
    for page in pages:
        applied.intersection_update(page['applied_filters'])
        reported.update(page['applied_filters'])
        unsupported.update(page['unsupported_filters'])
    unsupported.update(reported - applied)
    applied -= unsupported
    next_pages = [dict(kind='next_page', query_id=qid, cursor=q['cursor'])
        for qid, q in queries.items() if not q['done'] and not q['blocked'] and q['cursor'] is not None]
    completed = all(q['done'] for q in queries.values())
    coverage = dict(queried_sources=queried_sources, failed_sources=failed_sources,
        queries_completed=completed, has_more=bool(next_pages), next_pages=next_pages,
        truncated=any(p['truncated'] for p in pages) or state['stop_reason'] in {'budget', 'deadline'},
        applied_filters=sorted(applied), unsupported_filters=sorted(unsupported))
    items_by_key = {}
    for listing in state['listings'].values():
        key = listing['listing_key']
        items_by_key[key] = merge_listing(items_by_key[key], listing) if key in items_by_key else normalize_listing(listing)
    items = list(items_by_key.values())
    for index, listing in enumerate(items):
        if listing['field_issues']:
            problems.append(issue('RETRIEVAL_DEGRADED', 'Listing still has unresolved fields: ' + ', '.join(listing['field_issues']),
                field_path=f'result.data.items[{index}].field_issues', source=listing['source']))
    if not completed and not problems:
        problems.append(issue('RETRIEVAL_DEGRADED', 'Some queries are still incomplete; the current coverage cannot be considered a complete search', source=None))
    if coverage['truncated'] and not problems:
        problems.append(issue('RETRIEVAL_DEGRADED', 'Source results were truncated; keep the listings obtained so far', source=None))
    if not pages and not items and plan['queries'] and not problems:
        problems.append(issue('INVALID_STATE', 'No valid search page was obtained; cannot declare the search successful', source=None))
    problems = list({fingerprint(p): p for p in problems}.values())
    data = dict(plan_id=plan['plan_id'], profile_version=plan['profile_version'], items=items, coverage=coverage)
    status = 'partial' if problems or not completed else 'success'
    if not pages and problems and not items:
        data, status = None, 'error'
    result = dict(status=status, data=data, issues=problems, meta=dict(trace_id=ctx['trace_id'], call_id=ctx['call_id'],
        duration_ms=max(0, int((monotonic() - started) * 1000))))
    validate_search_result(result, plan, ctx)
    return result


if __name__ == '__main__':
    import argparse
    import asyncio
    import json
    from property_agent.search.api import create_live_search_service

    parser = argparse.ArgumentParser(description='Check 4/5 using the real final states of 2/3a/3b; do not generate synthetic listings')
    parser.add_argument('--input', type=Path, required=True, help='At least three sets of actual {plan, ctx} JSON inputs')
    parser.add_argument('--output', type=Path, help='Save the actual internal state and the return value for C')
    args = parser.parse_args()

    async def main():
        cases = json.loads(args.input.read_text())
        if not isinstance(cases, list) or len(cases) < 3:
            parser.error('At least three sets of real business inputs are required')
        service, records, passed = create_live_search_service(), [], 0
        for case in cases:
            if case['ctx']['source_mode'] != 'live':
                parser.error('Only real live input is accepted here')
            started = monotonic()
            state = await service.run(case['plan'], ctx=case['ctx'])
            before = deepcopy(state)
            result = aggregate(state, started)
            validate_search_result(result, case['plan'], case['ctx'])
            accepted = state == before and result['status'] != 'error'
            for item in result['data']['items'] if result['data'] else []:
                # Aggregating actual duplicate listings must be idempotent; do not construct preset responses or tamper with page facts.
                normalized = normalize_listing(item)
                accepted = accepted and merge_listing(normalized, normalized) == normalized
            passed += accepted
            record = dict(input=case, aggregation_input=before, output=result, verified=accepted)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'Real 4/5 aggregation passed {passed}/{len(cases)}')
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
