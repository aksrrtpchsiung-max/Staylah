"""Invoke the guru_search adapter in the repository via OpenCLI (JSON page/structured mode)."""
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from collections.abc import Sequence

# Supports running the real invocation checks in this file directly from an editor; does not modify the search path on package import.

from property_agent.contracts import ContractViolation, HardConstraints, Listing, ListingConstraint, RunContext, SearchQuery
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.execution.tasks import ListingDetail, ListingPage
from property_agent.domain.validation import timestamp, validate_listing, validate_type
from property_agent.domain.requirements import _bounds, _single_value
from property_agent.search.providers.base import ProviderError, issue


def _matches_number(number, condition):
    value, operator = condition['value'], condition['operator']
    if operator == 'between':
        return value[0] <= number <= value[1]
    if operator == 'in':
        return number in value
    return {'eq': lambda: number == value, 'neq': lambda: number != value,
        'gte': lambda: number >= value, 'gt': lambda: number > value,
        'lte': lambda: number <= value, 'lt': lambda: number < value}[operator]()


def bedroom_buckets(conditions):
    """Map raw numeric conditions to 0, 1, 2, 3, 4, 5+; 5+ must not be disguised as exactly five rooms."""
    lower, upper = _bounds(conditions, 'bedrooms', [])
    matches = lambda n: all(_matches_number(n, c) for c in conditions)
    buckets = [n for n in range(5) if matches(n)]
    finite = next((c['value'] for c in conditions if c['operator'] == 'in'), None)
    # When there is no in, after the range boundary skip at most a number of neq points equal to the condition to find a possible 5+ listing.
    probes = finite if finite is not None else range(max(5, lower), max(5, lower) + len(conditions) + 1)
    if any(n >= 5 and int(n) == n and (upper is None or n <= upper) and matches(n) for n in probes):
        buckets.append(5)
    exact = 5 not in buckets or (upper is None and lower <= 5 and all(
        c['operator'] in ('gt', 'gte') or c['operator'] == 'neq' and (
            c['value'] < 5 or int(c['value']) != c['value']) for c in conditions))
    return buckets, exact


class GuruSearchProvider:
    source = "propertyguru"
    source_mode = "live"
    # Corresponds to the detail parsing capability of contract-listing.js; does not turn any missing field into a read-detail task.
    detail_fields = frozenset({'price.amount', 'price.currency', 'price.period', 'bedrooms',
        'listing_status', 'listed_date', 'attributes.property_type', 'attributes.listing_scope',
        'attributes.area_sqft', 'attributes.bathrooms', 'attributes.room_type',
        'attributes.furnishing', 'attributes.tenure_type', 'attributes.lease_years',
        'attributes.ensuite_bathroom', 'attributes.owner_stays', 'attributes.utilities_included',
        'attributes.wifi_included', 'attributes.visitors_allowed', 'attributes.pets_allowed',
        'attributes.cooking_policy'})

    def __init__(self, command: Sequence[str] | None = None, *, timeout_seconds: float = 30):
        if command is None:
            configured = os.getenv('OPENCLI_BIN')
            if configured:
                configured_path = Path(configured).expanduser()
                command = ((shutil.which('node') or 'node'), str(configured_path)) \
                    if configured_path.suffix == '.js' else (str(configured_path),)
            else:
                executable = shutil.which('opencli')
                user_install = Path.home() / '.npm-global/bin/opencli'
                command = (executable or (str(user_install) if user_install.is_file() else 'opencli'),)
        if not command or isinstance(command, str) or timeout_seconds <= 0:
            raise ValueError("command must be an argument array, timeout_seconds must be greater than zero")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self._native_pages = {}

    async def _call(self, args: list[str], ctx: RunContext):
        timeout = min(self.timeout_seconds, remaining_seconds(ctx))
        logging.getLogger('search.audit').info('Invoking real OpenCLI',
            extra={'audit': dict(event='cli_call', args=args)})
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command, "propertyguru", *args, "-f", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise ProviderError(issue("SOURCE_UNAVAILABLE", "Unable to start OpenCLI; please install and register the guru_search adapter")) from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ProviderError(issue("TIMEOUT", "PropertyGuru invocation timed out", retryable=True)) from exc
        if process.returncode:
            # Compatible with OpenCLI text/JSON errors; does not expose the entire browser log as public output.
            message = (stderr + stdout).decode("utf-8", errors="replace").upper()
            codes = (("AUTH_REQUIRED", ("AUTH_REQUIRED", "LOGIN_WALL", "CAPTCHA")),
                     ("RATE_LIMITED", ("RATE_LIMIT", "429")),
                     ("TIMEOUT", ("TIMEOUT", "TIMED OUT")),
                     ("TEMPORARY_UNAVAILABLE", ("SESSION_BUSY", "TEMPORARY_UNAVAILABLE")),
                     ("PARSE_ERROR", ("PARSE_ERROR", "COULD NOT FIND LISTING", "PAGE DID NOT LOAD")),
                     ("INVALID_INPUT", ("CODE: ARGUMENT", '"CODE": "ARGUMENT"', "INVALID ARGUMENT")),
                     ("SOURCE_UNAVAILABLE", ("EXTENSION NOT CONNECTED", "BROWSER BRIDGE", "EXTENSION_NOT_CONNECTED")))
            code = next((code for code, words in codes if any(word in message for word in words)),
                        "SOURCE_UNAVAILABLE")
            description = ('OpenCLI Browser Bridge not connected, please install or enable the extension in Chrome'
                           if 'EXTENSION' in message and ('NOT CONNECTED' in message or 'CONNECT' in message)
                           else f"PropertyGuru command failed: {code}")
            if code == "PARSE_ERROR":
                # Only maps known error text in the adapter, preserving the reason without leaking the entire browser log.
                parse_reasons = (
                    ("NO __NEXT_DATA__", "PropertyGuru search page did not provide __NEXT_DATA__, unable to read page data"),
                    ("COULD NOT FIND LISTING DATA ON PAGE", "PropertyGuru page data is missing the listingsData listing field"),
                    ("EMPTY LISTING PAYLOAD WITHOUT AN EXPLICIT NO-RESULTS STATE",
                     "PropertyGuru did not parse any listings, and the page did not explicitly indicate no matches, so it cannot be treated as an empty result"),
                    ("PAGE CHANGED; CONTINUATION OFFSET IS NO LONGER VALID",
                     "PropertyGuru page content has changed, the in-page offset of the continuation cursor is no longer valid"),
                    ("DETAIL PAGE DOES NOT IDENTIFY A PROPERTYGURU LISTING",
                     "PropertyGuru detail page did not provide a valid listing link and ID"),
                    ("DETAIL REDIRECTED TO A DIFFERENT LISTING",
                     "PropertyGuru detail page redirected to a different listing"),
                    ("PAGE DID NOT LOAD", "PropertyGuru page did not provide expected data, unable to parse"),
                )
                description = next((reason for marker, reason in parse_reasons if marker in message), description)
            elif code == "TEMPORARY_UNAVAILABLE":
                if "SEARCH PAGE DATA NOT READY" in message:
                    description = "PropertyGuru search page did not provide listing data within the wait time limit, can retry within the remaining quota"
                elif "DETAIL PAGE DATA NOT READY" in message:
                    description = "PropertyGuru detail page did not provide listing data within the wait time limit, can retry within the remaining quota"
            raise ProviderError(issue(code, description,
                                      retryable=code in {"TIMEOUT", "RATE_LIMITED", "SOURCE_UNAVAILABLE", "TEMPORARY_UNAVAILABLE"}))
        try:
            payload = json.loads(stdout)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError(issue("PARSE_ERROR", "OpenCLI did not return valid JSON")) from exc
        # OpenCLI's table command wraps structured output in a single-row array.
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        raise ProviderError(issue("PARSE_ERROR", "Requires the structured output of the new guru_search version"))

    async def search_page(self, query: SearchQuery, *, intent: str,
                          filters: HardConstraints, limit: int,
                          ctx: RunContext, constraints: list[ListingConstraint] | None = None) -> ListingPage:
        page, offset = 1, 0
        if query["cursor"] is not None:
            match = re.fullmatch(r"pg:v1:([1-9][0-9]*):([0-9]+)", query["cursor"])
            if not match:
                raise ProviderError(issue("INVALID_INPUT", "Invalid PropertyGuru pagination cursor", field_path="query.cursor"))
            page, offset = map(int, match.groups())
            if max(page, offset) > 2**53 - 1:
                raise ProviderError(issue("INVALID_INPUT", "Pagination cursor exceeds the range supported by the source", field_path="query.cursor"))
        args = ["search", query["text"], "--listing", "sale" if intent == "buy" else "rent",
                "--page", str(page), "--offset", "0", "--output-mode", "full-page"]
        applied = ["transaction_type"]
        unsupported = ["price.currency"]
        hard = [c for c in constraints or [] if c['strength'] == 'hard']
        if filters["price_period"] is not None:
            unsupported.append("price.period")
        expected_period = "total" if intent == "buy" else "month"
        if filters["max_price"] is not None:
            if (filters["currency"] == "SGD" and filters["price_period"] == expected_period
                    and filters["max_price"] > 0):
                args += ["--max", str(filters["max_price"])]
                applied.append("price.amount")
            else:
                unsupported.append("price.amount")
        amounts = [c for c in hard if c['field_path'] == 'price.amount']
        if amounts and filters['currency'] == 'SGD' and filters['price_period'] == expected_period:
            lower_price, _ = _bounds(amounts, 'price.amount', [])
            if lower_price > 0:
                args += ['--min', str(lower_price)]
                if 'price.amount' not in applied:
                    applied.append('price.amount')
            # Ranges cannot precisely express discrete whitelist or exclusion values, so retain the local verification flag.
            if any(c['operator'] in ('in', 'neq') for c in amounts):
                unsupported.append('price.amount')
        minimum = filters['min_bedrooms']
        bedroom_conditions = [c for c in hard if c['field_path'] == 'bedrooms']
        if bedroom_conditions:
            buckets, exact = bedroom_buckets(bedroom_conditions)
            if not buckets:
                raise ProviderError(issue('INVALID_INPUT', 'Bedroom hard condition has no searchable integer value'))
            args += ['--bedroom-buckets', ','.join(map(str, buckets))]
            (applied if exact else unsupported).append('bedrooms')
        elif minimum is not None and minimum > 0:
            args += ['--min-bedrooms', str(minimum)]
            # The website's largest bucket is 5+; conditions such as at least six rooms still require local verification.
            (applied if minimum <= 5 else unsupported).append('bedrooms')
        scope = filters['rental_scope']
        if scope is not None:
            if intent == 'rent':
                args += ['--rental-scope', scope]
                # Room only may also include shared beds, so it cannot claim to precisely exclude bedspace.
                (applied if scope == 'whole_unit' else unsupported).append('attributes.listing_scope')
            else:
                unsupported.append('attributes.listing_scope')
        def exact_value(field):
            return _single_value(hard, field, [])
        property_type = exact_value('attributes.property_type')
        group = {'hdb': 'H', 'condo': 'N', 'apartment': 'N', 'landed': 'L'}.get(
            property_type if isinstance(property_type, str) else None)
        if group:
            args += ['--property-group', group]
            if property_type == 'condo':
                # The contract condo also includes Executive Condominium; use the website's real subcategory code.
                args += ['--property-codes', 'CONDO,EXCON']
                applied.append('attributes.property_type')
            elif group != 'N':
                applied.append('attributes.property_type')
        room_type = exact_value('attributes.room_type')
        if intent == 'rent' and scope == 'room' and room_type in ('common', 'master', 'shared'):
            args += ['--room-type', room_type]
            applied.append('attributes.room_type')
        unsupported.extend(c['field_path'] for c in hard if c['field_path'] not in applied)
        applied = [field for field in applied if field not in unsupported]
        if filters["locations"]:
            unsupported.append("location_id")  # Free-text search is not equivalent to canonical location ID filtering.
        # In-page cursors must not cause the same web page to be opened repeatedly. Cache the raw full page, then slice by this run's candidate quota;
        # Identity, request, conditions, and physical page all participate in the key; reuse across users/rounds is prohibited.
        cache_key = json.dumps([ctx['user_id'], ctx['run_id'], ctx['conversation_id'],
            ctx['attempt_id'], ctx['source_mode'], args, filters, constraints], sort_keys=True)
        cached = self._native_pages.get(cache_key)
        payload = deepcopy(cached) if cached is not None else await self._call(args, ctx)
        try:
            if set(payload) != {"items", "next_cursor", "pagination_known", "truncated"}:
                raise ValueError("Search output fields do not match")
            if type(payload["items"]) is not list:
                raise ValueError("items is not an array")
            if offset > len(payload['items']):
                raise ValueError('In-page offset is no longer valid, cannot skip unread listings')
            items, problems = [], []
            for index, raw in enumerate(payload["items"]):
                try:
                    validate_listing(raw)
                    if raw["source"] != self.source or raw["source_mode"] != self.source_mode:
                        raise ValueError("Listing source is inconsistent with Provider")
                    if offset <= index < offset + limit:
                        items.append(raw)
                except (ContractViolation, ValueError) as exc:
                    problems.append(issue("INVALID_OUTPUT", f"Skipped a listing that could not be validated: {exc}"))
            # Validate native pagination metadata first, to avoid caching a wrong source endpoint as success.
            validate_type(ListingPage, dict(query_id=query['query_id'], items=[],
                next_cursor=payload['next_cursor'], pagination_known=payload['pagination_known'],
                truncated=payload['truncated'], applied_filters=[], unsupported_filters=[], issues=[]))
            if payload['next_cursor'] is not None and not re.fullmatch(r'pg:v1:[1-9][0-9]*:[0-9]+', payload['next_cursor']):
                raise ValueError('Returned an invalid pagination cursor')
            remainder = offset + limit < len(payload['items'])
            result = dict(query_id=query["query_id"], items=items,
                          next_cursor=f'pg:v1:{page}:{offset + limit}' if remainder else payload['next_cursor'],
                          pagination_known=remainder or payload["pagination_known"],
                          truncated=remainder or payload["truncated"],
                          applied_filters=list(dict.fromkeys(applied)),
                          unsupported_filters=list(dict.fromkeys(unsupported)), issues=problems)
            validate_type(ListingPage, result)
            if result["next_cursor"] is not None and not re.fullmatch(r"pg:v1:[1-9][0-9]*:[0-9]+", result["next_cursor"]):
                raise ValueError("Returned an invalid pagination cursor")
            if len(items) > limit:
                raise ValueError("Source did not comply with the candidate quota")
            if not result["pagination_known"]:
                problems.append(issue("RETRIEVAL_DEGRADED", "Page did not provide a reliable pagination endpoint; the search cannot be considered complete"))
            if payload["items"][offset:offset + limit] and not items:
                raise ValueError("All listings failed contract validation")
            if not problems and cached is None:
                # Bounded, short-term cache; does not change the listing's real fetched_at.
                if len(self._native_pages) >= 16:
                    self._native_pages.pop(next(iter(self._native_pages)))
                self._native_pages[cache_key] = deepcopy(payload)
            logging.getLogger('search.audit').info('PropertyGuru search page', extra={'audit': dict(
                event='native_page', cache_hit=cached is not None, page=page, offset=offset,
                args=args, returned=len(items), native_count=len(payload['items']))})
            return result
        except (ContractViolation, ValueError, TypeError, KeyError) as exc:
            raise ProviderError(issue("PARSE_ERROR", f"Search results could not be parsed: {exc}")) from exc

    async def read_detail(self, listing: Listing, *, ctx: RunContext) -> ListingDetail:
        # When the search has already provided a real detail link, use it directly, avoiding another reliance on a slug-less numeric ID redirect.
        url = listing['source_url'] or ''
        identifier = (url if url.startswith('https://www.propertyguru.com.sg/listing/')
                      else listing['source_listing_id'])
        if not identifier:
            raise ProviderError(issue("INVALID_INPUT", "Listing is missing source ID and link"))
        payload = await self._call(["detail", identifier, "--output-mode", "structured"], ctx)
        try:
            validate_type(ListingDetail, payload)
            timestamp(payload["fetched_at"], "detail.fetched_at")
        except ContractViolation as exc:
            raise ProviderError(issue("PARSE_ERROR", f"Detail does not conform to the internal interface: {exc}")) from exc
        return payload


if __name__ == '__main__':
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    async def main():
        provider=GuruSearchProvider()
        passed=0
        for area in ('Tampines', 'Clementi', 'Punggol'):
            identity=str(uuid4())
            ctx=dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                trace_id=identity, call_id=identity, source_mode='live',
                deadline_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
            query=dict(query_id='q-'+area.lower(), source='propertyguru', text=area, cursor=None)
            filters=dict(currency='SGD', max_price=4500, price_period='month', rental_scope='whole_unit',
                         locations=[area.upper()], min_bedrooms=2)
            try:
                output=await provider.search_page(query, intent='rent', filters=filters, limit=1, ctx=ctx)
                details=[await provider.read_detail(item, ctx=ctx) for item in output['items']]
                passed+=bool(output['items'] and details)
                print(json.dumps(dict(input=dict(query=query, filters=filters, ctx=ctx), output=output, details=details), ensure_ascii=False), flush=True)
            except ProviderError as exc:
                print(json.dumps(dict(input=dict(query=query, filters=filters, ctx=ctx), error=exc.issue), ensure_ascii=False), flush=True)
        print(f'Real guru_search invocation passed {passed}/3')
        return 0 if passed==3 else 1

    raise SystemExit(asyncio.run(main()))
