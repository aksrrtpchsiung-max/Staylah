"""2: A search management Agent that searches first and enriches later; the model can only choose tasks within the current phase.

The model uses the ainvoke message interface from config.py and does not rely on the gateway's not-yet-verified bind_tools.
Running this file uses the real gateway and browser to check the search, filtering, and on-demand details of 2→3a.
"""
import asyncio
from copy import deepcopy
import json
import logging
from pathlib import Path
import sys
from time import monotonic


from property_agent.search.execution.history import fingerprint, task
from property_agent.search.capabilities.location import request_from_listing
from property_agent.search.capabilities.amenities import supports_requirement
from property_agent.search.execution.dispatcher import investigation_requirements
from property_agent.search.aggregation.results import merge_listing
from property_agent.search.aggregation.requirements import evaluate_listing_constraints
from property_agent.search.providers.base import ProviderError, issue


class SearchSupervisor:
    def __init__(self, dispatcher, *, model=None, max_retries=1, model_timeout_seconds=8,
                 max_model_calls=3):
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError('max_retries must be 0–3')
        if not 0 < model_timeout_seconds <= 60:
            raise ValueError('model decision timeout must be within 0–60 seconds')
        if type(max_model_calls) is not int or max_model_calls <= 0:
            raise ValueError('max_model_calls must be a positive integer')
        self.dispatcher, self.model = dispatcher, model
        self.max_retries = max_retries
        self.model_timeout_seconds = model_timeout_seconds
        self.max_model_calls, self.model_calls = max_model_calls, 0
        self._model_disabled = False

    def hard_rejections(self, state, listing):
        """Skip only hard failures that have evidence; missing, conflicting, and soft preferences may still be rechecked.

        Do not delete candidates; the final item-by-item check is still left to the summary/C; here we only control whether to continue spending retrieval budget.
        """
        request = state.get('requirement_request')
        if request is not None:
            failures = [c for c in evaluate_listing_constraints(listing,
                [c for c in request['listing_constraints'] if c['strength'] == 'hard'],
                filters=state['plan']['required_filters']) if c['status'] == 'fail']
        else:
            # The standalone search(plan) has no SourceReference, so do not fabricate user conditions/evidence.
            filters = state['plan']['required_filters']
            def known(field):
                value = listing
                for part in field.split('.'):
                    value = value[part]
                facts = [e for e in listing['evidence'] if e['field'] == field]
                if (value is None or value in ('unknown', 'conflict') or not facts
                        or field + ':conflict' in listing['field_issues']
                        or any(e['value'] != value for e in facts)
                        or field.startswith('price.') and listing['price']['status'] == 'conflict'):
                    return None
                return value
            limits = [('transaction_type', 'eq', 'sale' if state['plan']['intent'] == 'buy' else 'rent'),
                ('attributes.listing_scope', 'eq', filters['rental_scope']),
                ('bedrooms', 'gte', filters['min_bedrooms'])]
            if (known('price.currency') == filters['currency']
                    and known('price.period') == filters['price_period']):
                limits.append(('price.amount', 'lte', filters['max_price']))
            failures = []
            for field, operator, expected in limits:
                actual = known(field)
                if actual is not None and expected is not None and (
                        actual != expected if operator == 'eq' else
                        actual < expected if operator == 'gte' else actual > expected):
                    failures.append(dict(field=field, status='fail', reason='does not satisfy the search plan hard conditions',
                        evidence_ids=[e['evidence_id'] for e in listing['evidence'] if e['field'] == field]))
        # The contract explicitly prohibits recommending delisted listings; the original candidates remain for C to verify.
        if listing['listing_status'] == 'inactive' and any(
                e['field'] == 'listing_status' and e['value'] == 'inactive' for e in listing['evidence']):
            failures.append(dict(field='listing_status', status='fail', reason='listing has been delisted',
                evidence_ids=[e['evidence_id'] for e in listing['evidence'] if e['field'] == 'listing_status']))
        return failures

    def available_tasks(self, state):
        """Only one phase is open; the pre-execution secondary check also uses the same phase restriction."""
        tasks = []
        budget = self.dispatcher.budget
        has_budget = budget.pages_used < state['plan']['page_limit'] and budget.candidates_used < state['plan']['candidate_limit']
        for qid, progress in state['queries'].items():
            if progress['done'] or progress['blocked']:
                continue
            cached = self.dispatcher.history.get_page(state['plan'], qid, progress['cursor'])
            if has_budget or cached is not None:
                tasks.append(task('search_page', query_id=qid, cursor=progress['cursor']))
        # Includes real pages that can still be reused: when there is a search task, never open supplementary capabilities such as details or locating.
        if tasks:
            return tasks

        # After the search is complete, dispatch work according to the actual gaps; registering a Provider does not mean the user requested calling it.
        completed = set(state['completed_tasks'])
        requirements = [r for r in investigation_requirements(state.get('requirement_request') or {})
                        if supports_requirement(r)]
        for key, listing in state['listings'].items():
            # Filter the search cards once first; filter again after details fill in fields, to avoid continuing locating/amenity investigation.
            if self.hard_rejections(state, listing):
                continue
            detail = task('read_detail', listing_key=key)
            gaps = self.detail_gaps(state, listing)
            pending = [task('travel' if r['category'] == 'commute' else 'amenities',
                            listing_key=key, requirement_id=r['requirement_id'])
                       for r in sorted(requirements, key=lambda r: {'high': 0, 'medium': 1, 'low': 2}[r['priority']])]
            pending = [t for t in pending if t['task_id'] not in completed]
            # Only fetch the address from details when map investigation needs an address; do not make details a fixed prerequisite for all capabilities.
            address_needed = bool(pending) and not request_from_listing(listing)['query']
            if (gaps or address_needed) and detail['task_id'] not in completed:
                tasks.append(detail)
                continue
            if not pending:
                continue
            location = task('locate', listing_key=key)
            location['task_id'] += ':' + fingerprint(request_from_listing(listing))
            if location['task_id'] not in completed:
                tasks.append(location)
            else:
                tasks.extend(pending)
        return tasks

    def detail_gaps(self, state, listing):
        """Explicitly list the unknown requirement fields that guru details are capable of supplementing; known/conflicting ones are not read repeatedly."""
        capability = self.dispatcher.listings[listing['source']]
        fields = getattr(capability.provider, 'detail_fields', frozenset())
        request = state.get('requirement_request')
        if request is not None:
            checks = evaluate_listing_constraints(listing, request['listing_constraints'],
                                                   filters=state['plan']['required_filters'])
            return sorted({c['field'] for c in checks if c['status'] == 'unknown' and c['field'] in fields
                           and c['field'] + ':conflict' not in listing['field_issues']
                           and not (c['field'].startswith('price.') and listing['price']['status'] == 'conflict')})
        # The standalone search(plan) only checks the conditions it actually provides; do not fabricate a RequirementRequest.
        filters = state['plan']['required_filters']
        required = [('price.amount', filters['max_price']), ('bedrooms', filters['min_bedrooms']),
                    ('attributes.listing_scope', filters['rental_scope'])]
        gaps = []
        for field, expected in required:
            value = listing
            for part in field.split('.'):
                value = value[part]
            if (expected is not None and field in fields and field + ':conflict' not in listing['field_issues']
                    and (value in (None, 'unknown') or not any(e['field'] == field for e in listing['evidence']))):
                gaps.append(field)
        return gaps

    def task_context(self, state, selected):
        """Provide the Agent and timing log with the specific reason for each recheck, avoiding giving only an opaque task ID."""
        key = selected.get('listing_key')
        if key is None:
            return dict(query_id=selected['query_id'], cursor=selected['cursor'])
        listing = state['listings'][key]
        completed = set(state['completed_tasks'])
        needs_investigation = any(supports_requirement(r) and task(
            'travel' if r['category'] == 'commute' else 'amenities', listing_key=key,
            requirement_id=r['requirement_id'])['task_id'] not in completed
            for r in investigation_requirements(state.get('requirement_request') or {}))
        return dict(listing_key=key, title=listing['title'], price=listing['price']['amount'],
            bedrooms=listing['bedrooms'], property_type=listing['attributes']['property_type'],
            missing_fields=self.detail_gaps(state, listing), requirement_id=selected.get('requirement_id'),
            needs_address=selected['kind'] == 'read_detail' and needs_investigation
                          and not request_from_listing(listing)['query'])

    async def decide(self, state):
        screened = {key: failures for key, listing in state['listings'].items()
                    if (failures := self.hard_rejections(state, listing))}
        for key, failures in screened.items():
            if state.get('screened_out', {}).get(key) != failures:
                logging.getLogger('search.audit').info('skipping follow-up recheck of candidates that failed hard conditions',
                    extra={'audit': dict(event='screened_out', listing_key=key, failures=failures)})
        try:
            self.dispatcher.budget.work_seconds(state['ctx'])
        except ProviderError:
            return dict(selected_task=None, stop_reason='deadline', screened_out=screened, issues=state['issues'] + [
                issue('TIMEOUT', 'search has reached its execution time limit; reserve summary time and keep the listings already obtained', source=None)])
        tasks = self.available_tasks(state)
        phase = 'search' if tasks and tasks[0]['kind'] == 'search_page' else 'enrich'
        pending = any(not q['done'] and not q['blocked'] for q in state['queries'].values())
        problems = state['issues'][:]
        # Record the budget interruption when entering the enrichment phase, so that even if it times out later, the reason the search was incomplete is not lost.
        if phase == 'enrich' and pending and not any(p['code'] == 'BUDGET_EXHAUSTED' for p in problems):
            problems.append(issue('BUDGET_EXHAUSTED', 'the page or candidate budget for this run is used up; keep the subsequent cursor', source=None))
        if not tasks:
            return dict(selected_task=None, stop_reason='budget' if pending else 'complete', issues=problems, screened_out=screened)
        selected = tasks[0]
        reason = 'search phase: execute hard-condition search first' if phase == 'search' else 'enrichment phase: supplement candidate information by dependency'
        # When there is only one executable task, there is no need to spend another model round trip to choose the same task.
        if len(tasks) > 1 and self.model is not None and not self._model_disabled and self.model_calls < self.max_model_calls:
            # Limit the menu length for one decision; do not send the model the original web page text or any keys.
            menu = tasks[:64]
            payload = dict(phase=phase, available_tasks=menu,
                           task_context={t['task_id']: self.task_context(state, t) for t in menu},
                           pages_used=state['pages_used'], candidates_used=state['candidates_used'],
                           page_limit=state['plan']['page_limit'], candidate_limit=state['plan']['candidate_limit'],
                           queries_completed=all(q['done'] for q in state['queries'].values()),
                           candidate_count=len(state['listings']),
                           hard_constraints=state['plan']['required_filters'],
                           unresolved_locations=sum(x['status'] != 'resolved' for x in state['locations'].values()))
            model_started = monotonic()
            try:
                async with asyncio.timeout(min(self.model_timeout_seconds, self.dispatcher.budget.work_seconds(state['ctx']))):
                    self.model_calls += 1
                    reply = await self.model.ainvoke([
                        {'role': 'system', 'content': 'You are the search management Agent, responsible only for this one task selection; the program will then call the real tools. Scheduling has two phases: when phase=search, you may only choose the 3a search_page task, search first according to the established hard conditions and collect candidates, and must not supplement information in advance; when phase=enrich, the search phase has ended, and you may only choose supplementary tasks from the menu for the candidates already obtained: read_detail (3a details), locate (3b locating), amenities (3c amenities), or travel (3d travel); dependency relationships are guaranteed by the program. Entering the enrichment phase does not mean the search is fully complete; use queries_completed as the criterion; when the budget is exhausted or the query is blocked, existing candidates may still be enriched, and candidates do not mean all hard conditions have been satisfied. Phase switching and stopping are decided by the program, and the hard conditions must not be rewritten or relaxed. You may only copy one task_id from available_tasks; executing or simulating tasks is prohibited, and fabricating listings, coordinates, tool results, and subsequent dialogue is prohibited. Output only one line of JSON, containing exactly two non-empty strings, task_id and reason; reason must not exceed 30 characters. Do not use Markdown. After the JSON ends, output <END_DECISION> and end immediately.'},
                        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                        stream=False, stop=['<END_DECISION>'])
                content = reply.content
                if not isinstance(content, str):
                    raise ValueError
                content = content.strip().removesuffix('<END_DECISION>').strip()
                if content.startswith('```') and content.endswith('```'):
                    content = '\n'.join(content.splitlines()[1:-1]).strip()
                decision = json.loads(content)
                if (not isinstance(decision, dict) or set(decision) != {'task_id', 'reason'}
                        or not all(isinstance(v, str) and v.strip() for v in decision.values())):
                    raise ValueError
                selected = next(t for t in menu if t['task_id'] == decision['task_id'])
                reason = decision['reason'][:500]
            except (ValueError, StopIteration, AttributeError):
                problems.append(issue('INVALID_OUTPUT', 'the management model returned an invalid task; fixed scheduling has been used instead', source='model'))
                self._model_disabled = True
            except Exception as exc:
                code = 'TIMEOUT' if isinstance(exc, TimeoutError) else 'MODEL_UNAVAILABLE'
                problems.append(issue(code, f'management model call failed ({type(exc).__name__}); fixed scheduling has been used instead', source='model', retryable=True))
                self._model_disabled = True
            finally:
                logging.getLogger('search.audit').info('management model decision completed', extra={'audit': dict(
                    event='supervisor_model', duration_ms=round((monotonic() - model_started) * 1000),
                    phase=phase, fallback=self._model_disabled, available_count=len(tasks))})
        return dict(selected_task=selected, decision_reason=reason, issues=problems, screened_out=screened)

    async def execute(self, state):
        # Secondary check; the model output must not directly become external call parameters.
        selected = state['selected_task']
        if selected not in self.available_tasks(state):
            return dict(selected_task=None, stop_reason='invalid_state', issues=state['issues'] + [
                issue('INVALID_STATE', 'the selected task is no longer executable', source=None)])
        started = monotonic()
        try:
            async with asyncio.timeout(self.dispatcher.budget.work_seconds(state['ctx'])):
                result = await self.dispatcher.execute(selected, state)
        except (TimeoutError, ProviderError) as exc:
            problem = exc.issue if isinstance(exc, ProviderError) else issue(
                'TIMEOUT', 'the task has reached its execution time limit; reserve time to summarize existing results', source=None)
            result = dict(status='error', data=None, issues=[problem])
        updated = deepcopy(state)
        tid, kind = selected['task_id'], selected['kind']
        attempts = updated['attempts'].get(tid, 0) + 1
        updated['attempts'][tid] = attempts
        updated['task_issues'][tid] = result['issues']
        if kind == 'search_page':
            source = next(q['source'] for q in state['plan']['queries'] if q['query_id'] == selected['query_id'])
        else:
            source = ('routing' if kind == 'travel' else 'neighborhood' if kind == 'amenities' else
                self.dispatcher.location.provider.source if kind == 'locate' else state['listings'][selected['listing_key']]['source'])
        record = dict(task_id=tid, kind=kind, source=source, status=result['status'],
            attempt=attempts, reason=state['decision_reason'], duration_ms=round((monotonic() - started) * 1000),
            listing_key=selected.get('listing_key'), context=self.task_context(state, selected))
        updated['history'].append(record)
        logging.getLogger('search.audit').info('retrieval task completed', extra={'audit': dict(event='task', **record)})
        retryable = any(p['retryable'] for p in result['issues'])
        # The partial search page already has facts and a cursor, so do not refetch; detail/locate failures may be retried with bounds.
        retry = retryable and attempts <= self.max_retries and (kind != 'search_page' or result['data'] is None)
        if retry:
            delay = max((p['retry_after_seconds'] or 0 for p in result['issues']), default=0)
            try:
                if delay >= self.dispatcher.budget.work_seconds(state['ctx']):
                    retry = False
                elif delay:
                    await asyncio.sleep(delay)
            except ProviderError:
                retry = False
        data = result['data']
        if kind == 'search_page':
            progress = updated['queries'][selected['query_id']]
            if data is not None:
                updated['pages'].append(deepcopy(data))
                for item in data['items']:
                    key = item['listing_key']
                    updated['listings'][key] = merge_listing(updated['listings'][key], item) if key in updated['listings'] else deepcopy(item)
                progress['seen_cursors'].append(selected['cursor'])
                next_cursor = data['next_cursor']
                progress['cursor'] = next_cursor
                progress['done'] = data['pagination_known'] and next_cursor is None and not data['truncated']
                progress['blocked'] = not progress['done'] and next_cursor is None
                if next_cursor is not None and next_cursor in progress['seen_cursors']:
                    progress['blocked'] = True
                    progress['cursor'] = None  # A known looping cursor must not continue to be handed to A as a usable continuation page.
                    updated['task_issues'][tid].append(issue('RETRIEVAL_DEGRADED', 'the source repeatedly returned a cursor that has already been executed; this query has been stopped', source=source))
                if progress['blocked'] and not updated['task_issues'][tid]:
                    updated['task_issues'][tid].append(issue('RETRIEVAL_DEGRADED', 'the source did not provide a reliable cursor that can continue', source=source))
            elif not retry:
                progress['blocked'] = True
        elif kind == 'read_detail':
            if data is not None:
                if result['status'] == 'success':
                    data['field_issues'] = [f for f in data['field_issues'] if not f.startswith('detail:')]
                updated['listings'][selected['listing_key']] = data
        elif kind == 'locate' and data is not None:
            key = selected['listing_key']
            updated['locations'][key] = data
            listing = updated['listings'][key]
            listing['evidence'] = [e for e in listing['evidence'] if e['field'] != 'location'] + deepcopy(data['evidence'])
            listing['field_issues'] = [f for f in listing['field_issues'] if not f.startswith('location:')] + data['gaps']
        elif kind in ('amenities', 'travel'):
            updated['investigations'][tid] = deepcopy(result)
            listing = updated['listings'][selected['listing_key']]
            prefix = 'investigation:' + tid + ':'
            listing['field_issues'] = [f for f in listing['field_issues'] if not f.startswith(prefix)]
            if data is not None:
                evidence = {e['evidence_id']: e for e in listing['evidence']}
                evidence.update({e['evidence_id']: deepcopy(e) for e in data['evidence']})
                listing['evidence'] = list(evidence.values())
                listing['field_issues'].extend(prefix + gap for gap in data['gaps'])
            else:
                listing['field_issues'].append(prefix + 'unverified')
        if not retry:
            updated['completed_tasks'].append(tid)
        updated['pages_used'] = self.dispatcher.budget.pages_used
        updated['candidates_used'] = self.dispatcher.budget.candidates_used
        updated['selected_task'] = None
        return updated


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from property_agent.search.api import create_live_search_service
    from property_agent.runtime.model_client import ModelConfigurationError
    from property_agent.search.aggregation.results import aggregate
    from time import monotonic

    class AuditedModel:
        """Check the task menu of each real model call; do not replace model replies or tool results."""
        def __init__(self, model):
            self.model = model
            self.decisions = []

        async def ainvoke(self, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            phase = payload['phase']
            kinds = {t['kind'] for t in payload['available_tasks']}
            assert (phase == 'search' and kinds == {'search_page'}) or (
                phase == 'enrich' and kinds and kinds <= {'read_detail', 'locate', 'amenities', 'travel'}), 'task menu crosses phases'
            assert not (phase == 'search' and any(d['phase'] == 'enrich' for d in self.decisions)), 'returned to search after enrichment'
            self.decisions.append(dict(phase=phase, kinds=sorted(kinds),
                                       candidate_count=payload['candidate_count']))
            print(f'real decision: {phase}, candidates {payload["candidate_count"]}, tasks {sorted(kinds)}', file=sys.stderr, flush=True)
            return await self.model.ainvoke(messages, **kwargs)

    parser = argparse.ArgumentParser(description='real 2→3a integration; fails when real dependencies are missing, and does not use virtual responses')
    parser.add_argument('--live', action='store_true', help='compatible with the old command; now the default is live')
    parser.add_argument('--input', type=Path, help='at least three real search inputs of {plan, ctx}; when omitted, three sets of actual area queries are used')
    parser.add_argument('--output', type=Path, help='optional: save the input, execution history, and output JSON of this real call')
    args = parser.parse_args()

    async def main():
        if args.input:
            loaded = json.loads(args.input.read_text())
            cases = loaded if isinstance(loaded, list) else [loaded]
            if len(cases) < 3 or any(c['plan']['source_mode'] != 'live' or c['ctx']['source_mode'] != 'live' for c in cases):
                parser.error('at least three sets of live input are required; virtual responses are not used')
        else:
            cases = []
            for name, maximum in [('Tampines', 4000), ('Clementi', 4500), ('Punggol', 4000)]:
                identity = str(uuid4())
                plan = dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[name.upper()], min_bedrooms=2),
                    # The same query enters the plan with different IDs; the second item reuses the real page.
                    # Even if candidates already exist and the budget is used up, this search task must be completed before reading details.
                    queries=[dict(query_id='q-' + name.lower() + suffix, source='propertyguru', text=name, cursor=None)
                             for suffix in ('', '-reuse')],
                    page_limit=1, candidate_limit=1, source_mode='live', reason='real integration: search and reuse pages first, then enrich candidate information')
                ctx = dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                    trace_id=identity, call_id=identity, source_mode='live',
                    deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
                cases.append(dict(plan=plan, ctx=ctx))
        records, passed = [], 0
        for case in cases:
            if not args.input:
                case['ctx']['deadline_at'] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            started = monotonic()
            try:
                service = create_live_search_service()
                print(f'starting real phase test: {case["plan"]["queries"][0]["text"]}', file=sys.stderr, flush=True)
                audit = AuditedModel(service.model)
                service.model = audit
                state = await service.run(case['plan'], ctx=case['ctx'])
                result = aggregate(state, started)
                # All returns come from the real service; check both the model menu and the actual call order constraints.
                order = [h['kind'] for h in state['history']]
                first_enrich = next((i for i, kind in enumerate(order) if kind != 'search_page'), len(order))
                phase_order_verified = bool(order) and order[0] == 'search_page' and 'search_page' not in order[first_enrich:]
                queries_complete = all(q['done'] for q in state['queries'].values())
                incomplete_reported = queries_complete or (result['status'] != 'success'
                    and result['data'] is not None and not result['data']['coverage']['queries_completed'])
                # The default three sets must actually pass through the boundary of "listings already exist, but the search task must still continue to be processed".
                search_with_candidates = sum(h['kind'] == 'search_page' and h['status'] in ('success', 'partial')
                                             for h in state['history']) >= 2
                kinds = {h['kind'] for h in state['history'] if h['status'] == 'success'}
                # These original plans have no amenity/commute requirements, so the map should not be queried merely because a Provider is registered.
                accepted = ({'search_page', 'read_detail'} <= kinds
                    and not ({'locate', 'amenities', 'travel'} & set(order))
                    and not any(p['source'] == 'model' for p in result['issues'])
                    and phase_order_verified and incomplete_reported
                    and (bool(args.input) or search_with_candidates))
                record = dict(input=case, history=state['history'], decisions=audit.decisions,
                    phase_order_verified=phase_order_verified, incomplete_reported=incomplete_reported,
                    search_with_candidates=search_with_candidates, output=result, live_chain_verified=accepted)
            except (ProviderError, ModelConfigurationError) as exc:
                problem = exc.issue if isinstance(exc, ProviderError) else issue('MODEL_UNAVAILABLE', str(exc), source='model')
                record = dict(input=case, output=dict(status='error', data=None, issues=[problem]), live_chain_verified=False)
            passed += record['live_chain_verified']
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'real 2→3a chain passed {passed}/{len(cases)}; it passes only if search and details actually succeed and there are no map calls without requirements.')
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
