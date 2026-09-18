"""执行已经通过管理层检查的任务；3a 和 3b 共用同一调用上下文。"""
from copy import deepcopy

from contracts_v0 import ContractViolation
from execution.history import ExecutionHistory
from part3.capabilities.listings import ListingsCapability
from part3.capabilities.location import LocationCapability, request_from_listing
from providers.base import ProviderError, issue


class Dispatcher:
    def __init__(self, listing_providers, location_provider, budget):
        self.budget = budget
        self.listings = {p.source: ListingsCapability(p, budget) for p in listing_providers}
        self.location = LocationCapability(location_provider)
        self.history = ExecutionHistory()

    async def execute(self, task, state):
        plan, ctx = state['plan'], state['ctx']
        try:
            self.budget.check(ctx, plan)
            if task['kind'] == 'search_page':
                cached = self.history.get_page(plan, task['query_id'], task['cursor'])
                if cached is not None:
                    return cached
                query = next(q for q in plan['queries'] if q['query_id'] == task['query_id'])
                capability = self.listings.get(query['source'])
                if capability is None:
                    raise ProviderError(issue('SOURCE_UNAVAILABLE', '未注册该房源来源', source=query['source']))
                result = await capability.search_page(plan, task['query_id'], ctx=ctx, cursor=task['cursor'])
                self.history.save_page(plan, task['query_id'], task['cursor'], result)
                return result
            listing = state['listings'][task['listing_key']]
            if task['kind'] == 'read_detail':
                return await self.listings[listing['source']].read_detail(deepcopy(listing), ctx=ctx)
            if task['kind'] == 'locate':
                return await self.location.locate(request_from_listing(listing), ctx=ctx)
            raise ProviderError(issue('INVALID_STATE', '未知内部任务类型', source=None))
        except ProviderError as exc:
            problem = exc.issue
        except ContractViolation as exc:
            problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
        except Exception:
            # 未预料的插件异常也不能让本轮已经取得的房源全部丢失。
            problem = issue('INTERNAL_ERROR', '执行能力发生未处理错误', source=None)
        return dict(status='error', data=None, issues=[problem],
                    meta=dict(trace_id=ctx['trace_id'], call_id=ctx['call_id'], duration_ms=0))
