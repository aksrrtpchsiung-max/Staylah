"""LangGraph for A to B: parse requirements, create a plan, execute retrieval, and summarize requirement coverage.

The original RequirementRequest is always retained in the current graph state; do not falsify it into a complete profile,
and do not cache conditions across conversations. History, retries, and pagination are managed by B's internal services.
"""
from copy import deepcopy
from functools import wraps
import logging
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from property_agent.contracts import (
    ContractViolation, QueryFeatures, RequirementFulfillment, RequirementRequest,
    Result, RunContext, SearchPlan, SearchResult,
)
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.planning.query import prepare_request_query
from property_agent.domain.requirements import validate_requirement_request
from property_agent.search.aggregation.results import error_result
from property_agent.search.aggregation.requirements import build_fulfillment, validate_fulfillment_result
from property_agent.search.providers.base import ProviderError, issue


class FulfillmentState(TypedDict):
    request: RequirementRequest
    ctx: RunContext
    query: QueryFeatures | None
    plan: SearchPlan | None
    search_result: Result[SearchResult] | None
    result: Result[RequirementFulfillment] | None


class FulfillmentService:
    """Dependencies are injected via the constructor; each invocation has independent state, quota, and retrieval cache."""

    def __init__(self, *, planner=None, search_service=None,
                 planner_factory=None, search_factory=None):
        if planner is None and planner_factory is None:
            raise ValueError('A planning service or planning service factory is required')
        if search_service is None and search_factory is None:
            raise ValueError('A search service or search service factory is required')
        self.planner, self.search_service = planner, search_service
        self.planner_factory, self.search_factory = planner_factory, search_factory

    def _graph(self, started):
        def timed(name):
            def decorate(function):
                @wraps(function)
                async def measured(state):
                    before = monotonic()
                    try:
                        return await function(state)
                    finally:
                        logging.getLogger('search.audit').info('Fulfillment phase completed', extra={'audit': dict(
                            event='stage', stage=name, duration_ms=round((monotonic() - before) * 1000))})
                return measured
            return decorate

        @timed('prepare')
        async def prepare(state):
            request, ctx = state['request'], state['ctx']
            prepared = await prepare_request_query(request, ctx=ctx)
            if prepared['status'] == 'error':
                return {'result': prepared}
            query = prepared['data']
            if query['unresolved']:
                # Derived requirements not yet retrieved are unverified; open requirements still do not block.
                data = dict(request_id=request['request_id'], profile_version=request['profile_version'],
                    status='needs_clarification', search_result=None,
                    coverage=dict(fulfilled_requirement_ids=[], unsupported_requirement_ids=[],
                        unverified_requirement_ids=[r['requirement_id'] for r in request['derived_data_requirements']],
                        skipped_best_effort_requirement_ids=[r['requirement_id'] for r in request['open_data_requirements']]),
                    clarification_questions=deepcopy(query['unresolved']))
                return {'query': query, 'result': dict(status='success', data=data, issues=[],
                    meta=dict(trace_id=ctx['trace_id'], call_id=ctx['call_id'], duration_ms=0))}
            return {'query': query}

        @timed('plan')
        async def plan(state):
            # Complete input and clarification checks first, then read the model configuration; do not require A to provide history or policy.
            remaining_seconds(state['ctx'])
            planner = self.planner if self.planner is not None else self.planner_factory()
            planned = await planner.build_for_request(
                state['request'], state['query'], [], None, ctx=state['ctx'])
            if planned['status'] == 'error':
                return {'result': planned}
            return {'plan': planned['data']}

        @timed('search')
        async def retrieve(state):
            remaining_seconds(state['ctx'])
            service = self.search_service if self.search_service is not None else self.search_factory()
            return {'search_result': await service.search_for_request(state['plan'], state['request'], ctx=state['ctx'])}

        @timed('summarize')
        async def summarize(state):
            return {'result': build_fulfillment(state['request'], state['search_result'],
                ctx=state['ctx'], started=started, filters=state['plan']['required_filters'])}

        builder = StateGraph(FulfillmentState)
        builder.add_node('prepare_requirements', prepare)
        builder.add_node('plan_search', plan)
        builder.add_node('execute_search', retrieve)
        builder.add_node('summarize_requirements', summarize)
        builder.add_edge(START, 'prepare_requirements')
        builder.add_conditional_edges('prepare_requirements',
            lambda state: END if state['result'] is not None else 'plan_search', [END, 'plan_search'])
        builder.add_conditional_edges('plan_search',
            lambda state: END if state['result'] is not None else 'execute_search', [END, 'execute_search'])
        builder.add_edge('execute_search', 'summarize_requirements')
        builder.add_edge('summarize_requirements', END)
        return builder.compile()

    async def run(self, request: RequirementRequest, *, ctx: RunContext) -> FulfillmentState:
        """Internal integration testing returns the real inputs and outputs of each phase; external calls use fulfill_requirements."""
        from property_agent.runtime.model_client import ModelConfigurationError
        started = monotonic()
        state = dict(request=deepcopy(request), ctx=deepcopy(ctx), query=None,
                     plan=None, search_result=None, result=None)
        try:
            validate_requirement_request(request, ctx)
            remaining_seconds(ctx)
            state = await self._graph(started).ainvoke(state, config={
                'configurable': {'thread_id': ctx['conversation_id']}})
            state['result']['meta']['duration_ms'] = max(0, int((monotonic() - started) * 1000))
            try:
                validate_fulfillment_result(state['result'], request, ctx)
            except ContractViolation as exc:
                raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc
            return state
        except ContractViolation as exc:
            problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
        except ModelConfigurationError as exc:
            problem = issue('MODEL_UNAVAILABLE', str(exc), source='model')
        except ProviderError as exc:
            problem = exc.issue
        except Exception:
            problem = issue('INTERNAL_ERROR', 'An unhandled error occurred in the requirement fulfillment process', source=None)
        state['result'] = error_result(problem, ctx, started)
        return state

    async def fulfill_requirements(self, request: RequirementRequest, *,
                                   ctx: RunContext) -> Result[RequirementFulfillment]:
        return (await self.run(request, ctx=ctx))['result']
