"""LangGraph 搜索子图：管理 → 执行 → 管理 → 汇总。

每次 run 新建状态、额度和缓存，服务可重复调用；当前不启用持久化断点恢复。
计划生成仍由 part1 负责，这里接收已经生成的 SearchPlan。
"""
from time import monotonic

from langgraph.graph import END, START, StateGraph

from contracts_v0 import ContractViolation, Result, RunContext, SearchPlan, SearchResult
from execution.budget import SearchBudget
from execution.dispatcher import Dispatcher
from part1.validation import validate_plan, validate_search_result
from part2.supervisor import SearchSupervisor
from part45.aggregation import aggregate, error_result
from providers.base import ProviderError, issue
from state import SearchState, initial_state


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
    def __init__(self, *, listing_providers, location_provider, model=None, max_retries=1, model_timeout_seconds=15):
        self.listing_providers = tuple(listing_providers)
        if len({p.source for p in self.listing_providers}) != len(self.listing_providers):
            raise ValueError('同一来源只能注册一个 Provider')
        self.location_provider, self.model = location_provider, model
        self.max_retries, self.model_timeout_seconds = max_retries, model_timeout_seconds

    async def run(self, plan: SearchPlan, *, ctx: RunContext) -> SearchState:
        """内部联调入口，返回含执行历史和定位结果的图状态。"""
        started = monotonic()
        validate_plan(plan, ctx)
        if self.location_provider.source_mode != ctx['source_mode'] or any(
                p.source_mode != ctx['source_mode'] for p in self.listing_providers):
            raise ProviderError(issue('INVALID_INPUT', 'Provider 和 ctx.source_mode 不一致', source=None))
        budget = SearchBudget(plan, ctx)
        dispatcher = Dispatcher(self.listing_providers, self.location_provider, budget)
        supervisor = SearchSupervisor(dispatcher, model=self.model, max_retries=self.max_retries,
                                      model_timeout_seconds=self.model_timeout_seconds)
        graph = build_search_graph(supervisor, started=started)
        # 两个节点组成一轮；详情/定位以及有界重试都计入明确上限。
        # 同一页可由多个 query_id 复用，缓存命中的节点也算图步数。
        page_tasks = (plan['page_limit'] + 1) * max(1, len(plan['queries']))
        limit = 10 + 8 * (self.max_retries + 1) * (page_tasks + 2 * plan['candidate_limit'])
        return await graph.ainvoke(initial_state(plan, ctx), config={
            'configurable': {'thread_id': ctx['conversation_id']}, 'recursion_limit': limit})

    async def search(self, plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
        """契约入口；完整无匹配是 success，数据不全或中途终止是 partial。"""
        started = monotonic()
        try:
            state = await self.run(plan, ctx=ctx)
            result = state['result']
            validate_search_result(result, plan, ctx)
            return result
        except ContractViolation as exc:
            problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
        except ProviderError as exc:
            problem = exc.issue
        except Exception:
            problem = issue('INTERNAL_ERROR', '搜索编排发生未处理错误', source=None)
        return error_result(problem, ctx, started)
