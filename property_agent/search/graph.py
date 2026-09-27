"""LangGraph search subgraph: manage -> execute -> manage -> aggregate.

Each run creates fresh state, budget, and cache, so the service can be called repeatedly; persistence-based checkpoint recovery is currently not enabled.
Plan generation is handled by property_agent.search.planning; here we receive an already-generated SearchPlan.
"""
from time import monotonic

from langgraph.graph import END, START, StateGraph

from property_agent.contracts import ContractViolation, Result, RunContext, SearchPlan, SearchResult
from property_agent.search.execution.budget import SearchBudget
from property_agent.search.execution.dispatcher import Dispatcher
from property_agent.domain.validation import validate_plan, validate_search_result
from property_agent.search.execution.supervisor import SearchSupervisor
from property_agent.search.aggregation.results import aggregate, error_result
from property_agent.search.providers.base import ProviderError, issue
from property_agent.search.state import SearchState, initial_state


def build_search_graph(supervisor, *, started=None):
    started = monotonic() if started is None else started

    def finalize(state):
        try:
            return {'result': aggregate(state, started)}
        except ContractViolation as exc:
            raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc

    builder = StateGraph(SearchState)
    builder.add_node('supervisor', supervisor.decide)
    builder.add_node('execute', supervisor.execute)
    builder.add_node('aggregate', finalize)
    builder.add_edge(START, 'supervisor')
    builder.add_conditional_edges('supervisor',
        lambda state: 'execute' if state['selected_task'] is not None else 'aggregate', ['execute', 'aggregate'])
    builder.add_conditional_edges('execute',
        lambda state: 'aggregate' if state['stop_reason'] else 'supervisor', ['supervisor', 'aggregate'])
    builder.add_edge('aggregate', END)
    return builder.compile()


class SearchService:
    def __init__(self, *, listing_providers, location_provider, model=None, max_retries=None, model_timeout_seconds=None,
                 routing_provider=None, places_provider=None, execution_settings=None):
        from property_agent.runtime.model_client import SearchExecutionSettings
        self.execution_settings = execution_settings if execution_settings is not None else SearchExecutionSettings()
        self.listing_providers = tuple(listing_providers)
        if len({p.source for p in self.listing_providers}) != len(self.listing_providers):
            raise ValueError('Only one Provider can be registered per source')
        self.location_provider, self.model = location_provider, model
        self.routing_provider, self.places_provider = routing_provider, places_provider
        self.max_retries = self.execution_settings.max_retries if max_retries is None else max_retries
        self.model_timeout_seconds = (self.execution_settings.supervisor_timeout_seconds
            if model_timeout_seconds is None else model_timeout_seconds)

    async def run(self, plan: SearchPlan, *, ctx: RunContext, requirement_request=None) -> SearchState:
        """Internal integration entry point, returns the graph state including execution history and localization results."""
        started = monotonic()
        validate_plan(plan, ctx)
        if requirement_request is not None:
            from property_agent.domain.requirements import validate_requirement_request
            validate_requirement_request(requirement_request, ctx)
            if requirement_request['profile_version'] != plan['profile_version']:
                raise ContractViolation('STATE_CONFLICT', 'request.profile_version', 'Requirement and plan versions are inconsistent')
        if self.location_provider.source_mode != ctx['source_mode'] or any(
                p.source_mode != ctx['source_mode'] for p in self.listing_providers):
            raise ProviderError(issue('INVALID_INPUT', 'Provider and ctx.source_mode are inconsistent', source=None))
        budget = SearchBudget(plan, ctx, page_result_limit=self.execution_settings.page_result_limit,
                              finalize_reserve_seconds=self.execution_settings.finalize_reserve_seconds)
        dispatcher = Dispatcher(self.listing_providers, self.location_provider, budget,
                                routing_provider=self.routing_provider, places_provider=self.places_provider)
        supervisor = SearchSupervisor(dispatcher, model=self.model, max_retries=self.max_retries,
                                      model_timeout_seconds=self.model_timeout_seconds,
                                      max_model_calls=self.execution_settings.supervisor_max_calls)
        graph = build_search_graph(supervisor, started=started)
        # Two nodes form one round; detail/localization and bounded retries all count toward an explicit limit.
        # The same page can be reused by multiple query_ids, and nodes with cache hits also count as graph steps.
        page_tasks = (plan['page_limit'] + 1) * max(1, len(plan['queries']))
        from property_agent.search.execution.dispatcher import investigation_requirements
        extras = len(investigation_requirements(requirement_request or {})) + int(self.places_provider is not None)
        limit = 10 + 8 * (self.max_retries + 1) * (page_tasks + (2 + extras) * plan['candidate_limit'])
        return await graph.ainvoke(initial_state(plan, ctx, requirement_request), config={
            'configurable': {'thread_id': ctx['conversation_id']}, 'recursion_limit': limit})

    async def search(self, plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
        """Contract entry point; a complete run with no matches is success, while incomplete data or mid-run termination is partial."""
        return await self._search(plan, ctx=ctx)

    async def search_for_request(self, plan, request, *, ctx):
        """B's internal graph boundary explicitly passes the original confirmed requirements; does not change search's shared signature."""
        return await self._search(plan, ctx=ctx, requirement_request=request)

    async def _search(self, plan, *, ctx, requirement_request=None):
        started = monotonic()
        try:
            state = await self.run(plan, ctx=ctx, requirement_request=requirement_request)
            result = state['result']
            validate_search_result(result, plan, ctx)
            return result
        except ContractViolation as exc:
            problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
        except ProviderError as exc:
            problem = exc.issue
        except Exception:
            problem = issue('INTERNAL_ERROR', 'Unhandled error occurred in search orchestration', source=None)
        return error_result(problem, ctx, started)


if __name__ == '__main__':
    import argparse
    import asyncio
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone
    import json
    from pathlib import Path
    from uuid import uuid4

    from property_agent.search.api import create_live_search_service
    from property_agent.runtime.model_client import load_search_plan_settings
    from property_agent.search.execution.budget import remaining_seconds
    from scripts.live_search import search_plan_1, search_plan_2, search_plan_3

    parser = argparse.ArgumentParser(description='Execution parameters for three sets of real search validation; no virtual responses are injected')
    parser.add_argument('--timeout-seconds', type=int, default=120, help='Total time limit for each set of real inputs')
    parser.add_argument('--output', type=Path, help='Save real inputs, execution records, and return values')
    args = parser.parse_args()
    if args.timeout_seconds <= 10:
        parser.error('Total time limit must be greater than the default 10-second aggregation reserve time')

    class AuditedModel:
        def __init__(self, model):
            self.model, self.durations = model, []

        async def ainvoke(self, *arguments, **kwargs):
            started = monotonic()
            try:
                return await self.model.ainvoke(*arguments, **kwargs)
            finally:
                self.durations.append(monotonic() - started)

    class AuditedListings:
        def __init__(self, provider):
            self.provider, self.calls = provider, []

        def __getattr__(self, name):
            return getattr(self.provider, name)

        async def search_page(self, *arguments, **kwargs):
            self.calls.append(dict(limit=kwargs['limit'], remaining=remaining_seconds(kwargs['ctx'])))
            return await self.provider.search_page(*arguments, **kwargs)

        async def read_detail(self, *arguments, **kwargs):
            return await self.provider.read_detail(*arguments, **kwargs)

    async def main():
        records = []
        settings = load_search_plan_settings()
        for template in (search_plan_1, search_plan_2, search_plan_3):
            plan = deepcopy(template)
            plan.update(page_limit=settings.page_limit, candidate_limit=settings.candidate_limit)
            identity = uuid4().hex
            ctx = dict(user_id='live-config-check', run_id=identity, conversation_id=identity,
                attempt_id=plan['attempt_id'], trace_id=identity, call_id=identity, source_mode='live',
                deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=args.timeout_seconds)).isoformat())
            before = deepcopy(dict(plan=plan, ctx=ctx))
            service = create_live_search_service()
            model = AuditedModel(service.model)
            provider = AuditedListings(service.listing_providers[0])
            service.model, service.listing_providers = model, (provider,)
            print('Starting real configuration validation: ' + plan['queries'][0]['text'], flush=True)
            started = monotonic()
            state = await service.run(plan, ctx=ctx)
            elapsed = monotonic() - started
            result = state['result']
            validate_search_result(result, plan, ctx)
            execution = service.execution_settings
            kinds = [h['kind'] for h in state['history']]
            enrich = next((i for i, kind in enumerate(kinds) if kind != 'search_page'), len(kinds))
            checks = dict(
                real_candidates=bool(result['data'] and result['data']['items']),
                inputs_unchanged=before == dict(plan=plan, ctx=ctx),
                page_cap=state['pages_used'] <= plan['page_limit'],
                candidate_cap=state['candidates_used'] <= plan['candidate_limit'],
                batch_cap=bool(provider.calls) and all(0 < c['limit'] <= execution.page_result_limit for c in provider.calls),
                model_cap=0 < len(model.durations) <= execution.supervisor_max_calls,
                model_timeout=all(d <= service.model_timeout_seconds + 2 for d in model.durations),
                retry_cap=all(n <= service.max_retries + 1 for n in state['attempts'].values()),
                phase_order='search_page' not in kinds[enrich:],
                reserve=elapsed <= args.timeout_seconds - execution.finalize_reserve_seconds + 2,
                no_search_in_reserve=all(c['remaining'] > execution.finalize_reserve_seconds for c in provider.calls),
            )
            record = dict(input=before, output=result, history=state['history'], checks=checks,
                model_calls=len(model.durations), model_durations=model.durations,
                search_calls=provider.calls, pages_used=state['pages_used'],
                candidates_used=state['candidates_used'], elapsed_seconds=elapsed,
                verified=all(checks.values()))
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        passed = sum(r['verified'] for r in records)
        print(f'Real search configuration check passed {passed}/3', flush=True)
        return 0 if passed == 3 else 1

    raise SystemExit(asyncio.run(main()))
