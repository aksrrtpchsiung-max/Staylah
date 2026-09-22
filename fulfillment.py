"""A→B 的 LangGraph：解析需求、制定计划、执行检索、汇总需求覆盖。

原始 RequirementRequest 始终保留在当前图状态中；不把它伪造成完整画像，
不跨 conversation 缓存条件。历史、重试与分页由 B 的内部服务管理。
"""
from copy import deepcopy
from functools import wraps
import logging
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from contracts_v0 import (
    ContractViolation, QueryFeatures, RequirementFulfillment, RequirementRequest,
    Result, RunContext, SearchPlan, SearchResult,
)
from execution.budget import remaining_seconds
from part1.query import prepare_request_query
from part1.requirements import validate_requirement_request
from part45.aggregation import error_result
from part45.requirements import build_fulfillment, validate_fulfillment_result
from providers.base import ProviderError, issue


class FulfillmentState(TypedDict):
    request: RequirementRequest
    ctx: RunContext
    query: QueryFeatures | None
    plan: SearchPlan | None
    search_result: Result[SearchResult] | None
    result: Result[RequirementFulfillment] | None


class FulfillmentService:
    """依赖通过构造注入；每次调用拥有独立状态、额度及检索缓存。"""

    def __init__(self, *, planner=None, search_service=None,
                 planner_factory=None, search_factory=None):
        if planner is None and planner_factory is None:
            raise ValueError('需要计划服务或计划服务工厂')
        if search_service is None and search_factory is None:
            raise ValueError('需要搜索服务或搜索服务工厂')
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
                        logging.getLogger('search.audit').info('履约阶段完成', extra={'audit': dict(
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
                # 尚未检索的派生需求是 unverified；开放需求仍不阻塞。
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
            # 先完成输入及澄清检查，再读取模型配置；不要求 A 提供历史或策略。
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
        """内部联调返回各阶段真实输入输出；外部调用 fulfill_requirements。"""
        from config import ModelConfigurationError
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
            problem = issue('INTERNAL_ERROR', '需求履行流程发生未处理错误', source=None)
        state['result'] = error_result(problem, ctx, started)
        return state

    async def fulfill_requirements(self, request: RequirementRequest, *,
                                   ctx: RunContext) -> Result[RequirementFulfillment]:
        return (await self.run(request, ctx=ctx))['result']
