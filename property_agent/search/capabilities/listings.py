"""3a: Listing search/detail capabilities for LangGraph execution nodes to await.

Run real search/detail tests: python3 -m property_agent.search.capabilities.listings
Directly running this file is also supported; path resolution does not depend on the current working directory.
This module does not generate search plans, does not decide pagination, and does not execute C's hard-condition filtering.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from time import monotonic
from typing import get_args, get_type_hints
from urllib.parse import urlparse

# When executing the file directly, Python only adds the script directory, so the root directory must be added before importing within the project.
# Normal package imports and python -m do not modify sys.path.

from property_agent.contracts import ContractViolation, Listing, ListingAttributes, Price, Result, RunContext, SearchPlan
from property_agent.search.execution.budget import SearchBudget
from property_agent.search.execution.tasks import ListingDetail, ListingPage
from property_agent.domain.validation import fail, timestamp, validate_context, validate_listing, validate_plan, validate_type
from property_agent.search.providers.base import ListingProvider, ProviderError, issue


def _fact_type(field):
    if field.startswith("attributes."):
        return get_type_hints(ListingAttributes).get(field.removeprefix("attributes."))
    if field in {"price.amount", "price.currency", "price.period"}:
        return get_type_hints(Price)[field.removeprefix("price.")]
    if field in {"title", "bedrooms", "location_id", "listing_status", "listed_date", "source_updated_at"}:
        return get_type_hints(Listing)[field]
    return None


def merge_detail(listing: Listing, detail: ListingDetail) -> Listing:
    """Preserve the input snapshot and evidence from both sides; conflicting prices will not be overwritten by a later source."""
    validate_listing(listing)
    validate_type(ListingDetail, detail, "detail")
    timestamp(detail["fetched_at"], "detail.fetched_at")
    expected_id = listing["source_listing_id"]
    if expected_id is None and listing["source_url"]:
        match = re.search(r"(?:/|-)([0-9]+)/?$", urlparse(listing["source_url"]).path)
        expected_id = match.group(1) if match else None
    if expected_id is None or detail["source_listing_id"] != expected_id:
        fail("detail.source_listing_id", "The detail does not belong to the requested listing")
    result = deepcopy(listing)
    if detail["raw_description"]:
        result["raw_description"] = detail["raw_description"]
    result["raw_details"] = list(dict.fromkeys(result["raw_details"] + detail["raw_details"]))
    if timestamp(detail["fetched_at"], "detail.fetched_at") > timestamp(result["fetched_at"], "listing.fetched_at"):
        result["fetched_at"] = detail["fetched_at"]
    price_values = {key: set() for key in ("amount", "currency", "period")}
    for key in price_values:
        old = result["price"][key]
        if old is not None:
            price_values[key].add(old)
    evidence_ids = {e["evidence_id"] for e in result["evidence"]}
    for fact in detail["facts"]:
        field, value = fact["field"], fact["value"]
        schema = _fact_type(field)
        if schema is None:
            fail(f"detail.facts.{field}", "The detail field is not among the fields that Listing can supplement")
        validate_type(schema, value, f"detail.facts.{field}")
        if value is None or value == "unknown":
            continue
        if not fact["excerpt"].strip():
            fail(f"detail.facts.{field}", "Facts must be accompanied by the original page text")
        if type(value) is int and value < 0:
            fail(f"detail.facts.{field}", "Numeric values cannot be negative")
        identity = json.dumps([field, value, detail["source_url"], detail["fetched_at"], fact["excerpt"]],
                              ensure_ascii=False, sort_keys=True)
        eid = f'{listing["listing_key"]}:detail:{hashlib.sha256(identity.encode()).hexdigest()[:20]}'
        if eid not in evidence_ids:
            result["evidence"].append(dict(evidence_id=eid, field=field, value=value,
                source_url=detail["source_url"], observed_at=detail["fetched_at"], excerpt=fact["excerpt"]))
            evidence_ids.add(eid)
        if field.startswith("price."):
            price_values[field.split(".")[1]].add(value)
            if eid not in result["price"]["evidence_ids"]:
                result["price"]["evidence_ids"].append(eid)
            continue
        target, key = (result["attributes"], field.split(".")[1]) if field.startswith("attributes.") else (result, field)
        old = target[key]
        conflict_key = f"{field}:conflict"
        if conflict_key in result["field_issues"]:
            continue  # Subsequent details must not silently eliminate existing conflicts.
        if old not in (None, "unknown", "") and old != value and field != "title":
            result["field_issues"].append(conflict_key)
            target[key] = "unknown" if "unknown" in get_args(schema) else None
        else:
            target[key] = value
    conflict = result["price"]["status"] == "conflict"
    for key, values in price_values.items():
        if len(values) > 1:
            conflict = True
            result["field_issues"].append(f"price.{key}:conflict")
        elif values:
            result["price"][key] = next(iter(values))
    if conflict:
        result["price"].update(amount=None, status="conflict")
        if len(price_values["period"]) > 1:
            result["price"]["period"] = None
    else:
        result["price"]["status"] = "known" if result["price"]["amount"] is not None else "unknown"
    # Reaching this point means the provider returned a valid detail payload
    # for this exact listing. Preserve when that verification happened.
    result["last_verified_at"] = detail["fetched_at"]
    result["field_issues"] = list(dict.fromkeys(result["field_issues"]))
    validate_listing(result)
    return result


def _result(data, problems, ctx, started):
    context = ctx if isinstance(ctx, dict) else {}
    trace_id = context.get("trace_id", "")
    call_id = context.get("call_id", "")
    return dict(status="error" if data is None else "partial" if problems else "success",
                data=data, issues=problems, meta=dict(trace_id=trace_id if isinstance(trace_id, str) else "",
                call_id=call_id if isinstance(call_id, str) else "", duration_ms=max(0, int((monotonic() - started) * 1000))))


class ListingsCapability:
    def __init__(self, provider: ListingProvider, budget: SearchBudget):
        self.provider = provider
        self.budget = budget

    def _context(self, ctx):
        validate_context(ctx)
        if self.provider.source_mode != ctx["source_mode"]:
            fail("ctx.source_mode", "The context is inconsistent with the injected Provider mode")
        self.budget.check(ctx)

    async def search_page(self, plan: SearchPlan, query_id: str, *,
                          ctx: RunContext, cursor: str | None = None, constraints=None) -> Result[ListingPage]:
        """cursor is the continuation-page instruction obtained by the management layer from the previous next_cursor; it does not change the plan."""
        started = monotonic()
        try:
            validate_plan(plan, ctx)
            self._context(ctx)
            query = next((deepcopy(q) for q in plan["queries"] if q["query_id"] == query_id), None)
            if query is None:
                fail("query_id", "The query is not in the plan")
            if query["source"] != self.provider.source:
                fail("query.source", "The query source is inconsistent with the Provider")
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor.strip():
                    fail("cursor", "The continuation-page cursor must be a non-empty string")
                query["cursor"] = cursor
            async with asyncio.timeout(self.budget.work_seconds(ctx)):
                async with self.budget.lock:
                    limit = self.budget.begin_page(plan, ctx)
                    page = await self.provider.search_page(query, intent=plan["intent"],
                        filters=deepcopy(plan["required_filters"]), limit=limit, ctx=ctx,
                        **({'constraints': deepcopy(constraints)} if constraints else {}))
                    try:
                        validate_type(ListingPage, page, "page")
                        if page["query_id"] != query_id or len(page["items"]) > limit:
                            fail("page", "The source query ID or candidate quota does not match")
                        for item in page["items"]:
                            validate_listing(item)
                            if item["source_mode"] != ctx["source_mode"] or item["source"] != query["source"]:
                                fail("page.items", "The returned listing source or mode is inconsistent")
                    except ContractViolation as exc:
                        raise ProviderError(issue("INVALID_OUTPUT", str(exc), field_path=exc.field_path)) from exc
                    self.budget.candidates_used += len(page["items"])
            problems = list(page["issues"])
            if not page["pagination_known"] and not any(p["code"] == "RETRIEVAL_DEGRADED" for p in problems):
                problems.append(issue("RETRIEVAL_DEGRADED", "Unable to confirm whether the search has a next page"))
            page["issues"] = problems
            return _result(page, problems, ctx, started)
        except ContractViolation as exc:
            return _result(None, [issue(exc.code, str(exc), field_path=exc.field_path)], ctx, started)
        except ProviderError as exc:
            return _result(None, [exc.issue], ctx, started)
        except TimeoutError:
            return _result(None, [issue("TIMEOUT", "Search reached the deadline", retryable=True)], ctx, started)

    async def read_detail(self, listing: Listing, *, ctx: RunContext) -> Result[Listing]:
        started = monotonic()
        try:
            validate_context(ctx)
            validate_listing(listing)
            if (listing["source"] != self.provider.source or listing["source_mode"] != ctx["source_mode"]
                    or self.provider.source_mode != ctx["source_mode"]):
                fail("listing.source", "The listing is inconsistent with the Provider / context")
        except ContractViolation as exc:
            return _result(None, [issue(exc.code, str(exc), field_path=exc.field_path)], ctx, started)
        try:
            self._context(ctx)
            async with asyncio.timeout(self.budget.work_seconds(ctx)):
                async with self.budget.lock:
                    self.budget.check(ctx)
                    detail = await self.provider.read_detail(deepcopy(listing), ctx=ctx)
                    merged = merge_detail(listing, detail)
            return _result(merged, [], ctx, started)
        except (ProviderError, ContractViolation, TimeoutError) as exc:
            if isinstance(exc, ProviderError):
                problem = exc.issue
            elif isinstance(exc, ContractViolation):
                problem = issue("INVALID_OUTPUT", str(exc), field_path=exc.field_path)
            else:
                problem = issue("TIMEOUT", "Detail reached the deadline", retryable=True)
            original = deepcopy(listing)
            original["field_issues"] = list(dict.fromkeys(original["field_issues"] + [f'detail:{problem["code"]}']))
            return _result(original, [problem], ctx, started)


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from property_agent.search.providers.guru_search import GuruSearchProvider

    def verify_observed_details(listing):
        """Verify entry acceptance fields and evidence against the real page, without presupposing listings or service responses."""
        observed = {
            ('furnished-o', 'Fully furnished'): ('attributes.furnishing', 'fully'),
            ('furnished-o', 'Unfurnished'): ('attributes.furnishing', 'unfurnished'),
            ('people-o', 'Staying with owner'): ('attributes.owner_stays', True),
            ('people-o', 'No Owner Stays'): ('attributes.owner_stays', False),
            ('document-with-lines-o', 'Utilities included'): ('attributes.utilities_included', True),
            ('wifi-2-f', 'Wi-Fi included'): ('attributes.wifi_included', True),
            ('cooker-o', 'No cooking'): ('attributes.cooking_policy', 'none'),
            ('people-behind-o', 'Visitors not allowed'): ('attributes.visitors_allowed', False),
            ('pet-o', 'Pets not allowed'): ('attributes.pets_allowed', False),
            ('room-o', 'Common room (Shared bath)'): ('attributes.ensuite_bathroom', False),
            ('calendar-days-o', '99-year lease'): ('attributes.lease_years', 99),
        }
        checked, failures = 0, []
        for raw in listing['raw_details']:
            label, separator, value = raw.partition(':')
            if not separator:
                continue
            label, value = label.strip(), value.strip()
            expected = observed.get((label, value))
            if label == 'calendar-time-o' and value.startswith('Listed on '):
                try:
                    date = datetime.strptime(value.removeprefix('Listed on '), '%d %b %Y').date().isoformat()
                except ValueError:
                    failures.append('The original format of the listing date has changed, needs checking: ' + raw)
                    continue
                expected = ('listed_date', date)
            if expected is None:
                continue
            field, value = expected
            actual = listing
            for key in field.split('.'):
                actual = actual[key]
            checked += 1
            if type(actual) is not type(value) or actual != value:
                failures.append(f'{field} did not retain a clear detail fact: {raw}')
            if not any(e['field'] == field and type(e['value']) is type(value) and e['value'] == value
                       and ':detail:' in e['evidence_id'] for e in listing['evidence']):
                failures.append(f'{field} is missing corresponding detail evidence: {raw}')
        if not checked:
            failures.append('No verifiable clear detail entries were obtained')
        return checked, failures

    parser = argparse.ArgumentParser(description='Real search/detail check for 3a; input SearchPlan, output actual Result')
    parser.add_argument('--input', type=Path, help='JSON file containing at least three real {plan, ctx} groups')
    parser.add_argument('--output', type=Path, help='Save real input/output records')
    parser.add_argument('--photos-only', action='store_true', help='Real search card images and summary acceptance, without reading details for each listing')
    args = parser.parse_args()

    class PhotoVerificationProvider(GuruSearchProvider):
        """Real OpenCLI call, enabling only raw media diagnostics, without faking service responses."""
        async def _call(self, arguments, ctx):
            if arguments[0] == 'search':
                payload = await super()._call([*arguments, '--include-media-source'], ctx)
                self.media_source = payload.pop('media_source')
                return payload
            return await super()._call(arguments, ctx)

    async def verify_photos(cap, plan, ctx, page):
        """Independently read the same real search page and check that all source images pass through 3a and the final summary."""
        from property_agent.search.aggregation.results import aggregate, merge_listing
        from property_agent.search.state import initial_state
        source = {r['id']: r for r in cap.provider.media_source}
        photo_field = 'media.search_card_photos'
        for item in page['items']:
            records = [e for e in item['evidence'] if e['field'] == photo_field]
            assert len(records) == 1, 'Each listing must have one search image record (even with no images, it must be recorded)'
            evidence = records[0]
            media = evidence['value']
            raw = source[item['source_listing_id']]
            expected = [entry['src'] for group in ('images', 'floorPlans', 'sitePlans')
                        for entry in raw['preview'][group]['items']]
            if raw.get('thumbnail'):
                expected.append(raw['thumbnail'])
            actual = [url for image in media['images'] for url in image['urls']]
            assert set(actual) == set(expected), 'Missing or extraneous image links from outside the source'
            assert len(actual) == len(set(actual)), 'Duplicate URLs were not deduplicated'
            assert evidence['source_url'] == item['source_url'], 'Image source crossed listings'
            photo_urls = [image['urls'][0] for image in media['images'] if image['kind'] == 'photo']
            source_photos = list(dict.fromkeys(e['src'] for e in raw['preview']['images']['items']))
            assert photo_urls == source_photos, 'Photo order or photo count changed'
            reported = next((int(m['text']) for m in raw['mediaItems'] if m['mediaType'] == 'images'), None)
            assert media['reported_count'] == reported
            assert media['extracted_count'] == len(photo_urls)
            if reported == len(photo_urls):
                assert media['status'] == 'complete', 'A real complete photo should be marked as complete'
            assert [e['value'] for e in merge_listing(item, item)['evidence'] if e['field'] == photo_field] == [
                media], 'Duplicate merging lost image evidence'
        # Verify that links remain complete after caching the same real physical page, without making additional web requests.
        query = next(q for q in plan['queries'] if q['query_id'] == page['query_id'])
        cached = await cap.provider.search_page(query, intent=plan['intent'],
            filters=plan['required_filters'], limit=len(page['items']), ctx=ctx)
        assert cached['items'] == page['items'], 'Page caching changed the image records'
        state = initial_state(plan, ctx)
        state['pages'] = [page]
        state['listings'] = {item['listing_key']: item for item in page['items']}
        state['queries'][page['query_id']].update(cursor=page['next_cursor'],
            done=page['pagination_known'] and page['next_cursor'] is None)
        result = aggregate(state, monotonic())
        assert result['data'] is not None
        for item in result['data']['items']:
            original = state['listings'][item['listing_key']]
            # The summary normalizes the timezone format of observed_at; image content must be preserved as-is.
            assert [e['value'] for e in item['evidence'] if e['field'] == photo_field] == [
                e['value'] for e in original['evidence'] if e['field'] == photo_field]
        return dict(source_cards=source, aggregation_output=result)

    async def main():
        if args.input:
            payload = json.loads(args.input.read_text())
            cases = payload if isinstance(payload, list) else [payload]
            if len(cases) < 3:
                parser.error('At least three real business input groups are required')
        else:
            cases=[]
            for area, maximum in [('Tampines', 4000), ('Clementi', 5000 if args.photos_only else 4500), ('Punggol', 4000)]:
                identity=str(uuid4())
                plan=dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[area.upper()], min_bedrooms=2),
                    queries=[dict(query_id='q-'+area.lower(), source='propertyguru', text=area, cursor=None)],
                    page_limit=1, candidate_limit=20 if args.photos_only else 1,
                    source_mode='live', reason='Real 3a input/output check')
                ctx=dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                    trace_id=identity, call_id=identity, source_mode='live',
                    deadline_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
                cases.append(dict(plan=plan, ctx=ctx))
        records=[]
        passed=0
        for case in cases:
            plan, ctx=case['plan'], case['ctx']
            if ctx['source_mode'] != 'live':
                parser.error('Acceptance only accepts live input, not virtual responses')
            cap=ListingsCapability(PhotoVerificationProvider() if args.photos_only else GuruSearchProvider(), SearchBudget(plan, ctx,
                page_result_limit=20 if args.photos_only else 6))
            output=await cap.search_page(plan, plan['queries'][0]['query_id'], ctx=ctx)
            details=[]
            if output['data'] and not args.photos_only:
                for item in output['data']['items']:
                    details.append(await cap.read_detail(item, ctx=ctx))
            failures, checked_facts = [], 0
            photo_verification = None
            if args.photos_only and output['data'] and output['data']['items']:
                try:
                    photo_verification = await verify_photos(cap, plan, ctx, output['data'])
                except (AssertionError, KeyError, TypeError, ValueError, TimeoutError) as exc:
                    failures.append('Image acceptance failed: ' + str(exc))
            if output['data']:
                page = output['data']
                filters = plan['required_filters']
                for item in page['items']:
                    expected_type = 'sale' if plan['intent'] == 'buy' else 'rent'
                    if item['transaction_type'] != expected_type:
                        failures.append(item['listing_key'] + ': Transaction type does not match the query')
                    price = item['price']
                    if ('price.amount' in page['applied_filters'] and price['status'] == 'known'
                            and price['currency'] == filters['currency']
                            and price['period'] == filters['price_period']
                            and price['amount'] > filters['max_price']):
                        failures.append(item['listing_key'] + ': Search results include over-budget listings')
                if page['truncated'] and not page['next_cursor']:
                    failures.append('After candidate truncation, the real continuation-page cursor must be retained')
            for detail in details:
                if detail['data'] is not None:
                    checked, problems = verify_observed_details(detail['data'])
                    checked_facts += checked
                    failures.extend(detail['data']['listing_key'] + ': ' + problem for problem in problems)
            accepted=bool(output['data'] and output['data']['items'] and not failures and (
                photo_verification if args.photos_only else details and all(x['status']=='success' for x in details)))
            passed+=accepted
            record=dict(input=case, search_output=output, detail_outputs=details,
                        checked_detail_facts=checked_facts, photo_verification=photo_verification,
                        failures=failures, live_verified=accepted)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        label = 'search images and summary' if args.photos_only else 'search and details'
        print(f'Real 3a {label} passed {passed}/{len(cases)}; it will not pass without real returns.')
        return 0 if passed==len(cases) else 1

    raise SystemExit(asyncio.run(main()))
