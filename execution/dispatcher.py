"""执行已经通过管理层检查的任务；3a 和 3b 共用同一调用上下文。"""
from copy import deepcopy

from contracts_v0 import ContractViolation
from execution.history import ExecutionHistory
from part3.capabilities.listings import ListingsCapability
from part3.capabilities.location import LocationCapability, request_from_listing
from part3.capabilities.travel import TravelCapability
from part3.capabilities.amenities import AmenitiesCapability, CATEGORIES, DEFAULT_RADIUS_M
from providers.base import ProviderError, issue


class Dispatcher:
    def __init__(self, listing_providers, location_provider, budget, *, routing_provider=None, places_provider=None):
        self.budget = budget
        self.listings = {p.source: ListingsCapability(p, budget) for p in listing_providers}
        self.location = LocationCapability(location_provider)
        self.travel = TravelCapability(routing_provider, self.location)
        self.amenities = AmenitiesCapability(places_provider, self.location, self.travel)
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
                result = await capability.search_page(plan, task['query_id'], ctx=ctx, cursor=task['cursor'],
                    constraints=(state.get('requirement_request') or {}).get('listing_constraints'))
                self.history.save_page(plan, task['query_id'], task['cursor'], result)
                return result
            listing = state['listings'][task['listing_key']]
            if task['kind'] == 'read_detail':
                return await self.listings[listing['source']].read_detail(deepcopy(listing), ctx=ctx)
            if task['kind'] == 'locate':
                return await self.location.locate(request_from_listing(listing), ctx=ctx)
            if task['kind'] in ('amenities', 'travel'):
                request = state.get('requirement_request') or {}
                rid = task.get('requirement_id')
                location = state['locations'].get(task['listing_key'])
                if task['kind'] == 'amenities' and rid is None:
                    return await self.amenities.investigate(dict(origin=request_from_listing(listing),
                        origin_location=location, categories=list(CATEGORIES), radius_m=DEFAULT_RADIUS_M), ctx=ctx)
                requirements = investigation_requirements(request)
                requirement = next(r for r in requirements if r['requirement_id'] == rid)
                if task['kind'] == 'travel':
                    return await self.travel.investigate(listing, location, requirement,
                        request.get('user_context', []), ctx=ctx)
                return await self.amenities.investigate_requirement(listing, location, requirement, ctx=ctx)
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


def investigation_requirements(request):
    """派生需求原样传递；只有工作/学校背景时补做默认通勤概览（无阈值）。"""
    requirements = deepcopy(request.get('derived_data_requirements', []))
    has_commute = any(r['category'] == 'commute' for r in requirements)
    # 已有通勤需求时由其 target 和 user_context 配对，避免背景条目重复出行任务。
    if not has_commute:
        for index, fact in enumerate(request.get('user_context', [])):
            if fact['field'] in ('occupant.workplace', 'occupant.school') and isinstance(fact['value'], str) and fact['value'].strip():
                rid = 'context-commute-' + str(index)
                while any(r['requirement_id'] == rid for r in requirements):
                    rid = '_' + rid
                requirements.append(dict(requirement_id=rid, category='commute',
                    target=fact['value'], metric='travel_time', operator='preferred', value=None, unit='minutes',
                    strength='soft', priority='medium', source=deepcopy(fact['source'])))
    return requirements
