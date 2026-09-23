"""B 的公开入口 fulfill_requirements；其余函数供 B 内部调用与独立联调。"""
import os
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from contracts_v0 import (
    AttemptSummary, ContractViolation, QueryFeatures, Result, RunContext,
    ConversationProfile, RequirementRequest, RequirementFulfillment,
    SearchDirective, SearchPlan, SearchResult,
)
from graph import SearchService
from part1.validation import validate_plan, validate_planner_input
from execution.budget import remaining_seconds
from part45.aggregation import error_result
from providers.base import ProviderError, issue


def create_live_planner_service(*, env_file=None, model=None, settings=None):
    """构造 1 的独立服务；计划生成不依赖浏览器或 OneMap 凭据。"""
    from config import (create_chat_model, load_model_settings, load_search_plan_settings,
                        load_search_execution_settings)
    from dataclasses import replace
    from part1.planner import SearchPlanner
    path = Path(env_file) if env_file is not None else Path(__file__).resolve().with_name('.env')
    model_settings = load_model_settings(path) if model is None else None
    execution = load_search_execution_settings(path)
    return SearchPlanner(
        model=model if model is not None else create_chat_model(replace(
            model_settings, max_tokens=min(model_settings.max_tokens, 512))),
        settings=settings if settings is not None else load_search_plan_settings(path),
        model_timeout_seconds=min(model_settings.timeout_seconds, execution.planner_timeout_seconds)
            if model_settings else execution.planner_timeout_seconds,
        finalize_reserve_seconds=execution.finalize_reserve_seconds)


async def build_search_plan(profile: ConversationProfile, query: QueryFeatures,
                            previous_attempts: list[AttemptSummary], directive: SearchDirective | None,
                            *, ctx: RunContext) -> Result[SearchPlan]:
    """B 内部计划接口；只有通过校验的计划才可进入 search。"""
    from config import ModelConfigurationError
    started = monotonic()
    try:
        validate_planner_input(profile, query, previous_attempts, directive, ctx)
        remaining_seconds(ctx)
        result = await create_live_planner_service().build_search_plan(
            profile, query, previous_attempts, directive, ctx=ctx)
        result['meta']['duration_ms'] = max(0, int((monotonic() - started) * 1000))
        return result
    except ContractViolation as exc:
        problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
    except ModelConfigurationError as exc:
        problem = issue('MODEL_UNAVAILABLE', str(exc), source='model')
    except ProviderError as exc:
        problem = exc.issue
    except Exception:
        problem = issue('INTERNAL_ERROR', '计划服务初始化或调用失败', source=None)
    return error_result(problem, ctx, started)


def create_live_search_service(*, env_file=None, model=None) -> SearchService:
    """默认使用 DeepSeek、guru_search 和 OneMap；创建时不联网。"""
    from config import create_chat_model, load_model_settings, load_search_execution_settings
    from dataclasses import replace
    from providers.guru_search import GuruSearchProvider
    from providers.onemap import OneMapProvider
    from providers.osm import NeighborhoodProvider, OSMPlacesProvider
    path = Path(env_file) if env_file is not None else Path(__file__).resolve().with_name('.env')
    values = dict(dotenv_values(path, interpolate=False))
    values.update(os.environ)
    location_provider = OneMapProvider.from_env(path)
    executable = values.get('OPENCLI_BIN')
    settings = load_model_settings(path) if model is None else None
    execution = load_search_execution_settings(path)
    return SearchService(listing_providers=[GuruSearchProvider((executable,) if executable else None,
        timeout_seconds=execution.provider_timeout_seconds)],
        location_provider=location_provider,
        routing_provider=location_provider,
        places_provider=NeighborhoodProvider(location_provider, OSMPlacesProvider(
            **({'endpoint': values['OVERPASS_URL']} if values.get('OVERPASS_URL') else {}))),
        model=model if model is not None else create_chat_model(replace(settings, max_tokens=min(settings.max_tokens, 256))),
        execution_settings=execution,
        model_timeout_seconds=min(settings.timeout_seconds, execution.supervisor_timeout_seconds)
            if settings else execution.supervisor_timeout_seconds)


def create_live_fulfillment_service(*, env_file=None, model=None, settings=None,
                                    page_limit=None, candidate_limit=None):
    """构造完整 B 服务，依赖延迟到需要执行检索时初始化。

    页数、候选额度属于 B 内部配置，不加入 A 的业务请求。
    OneMap 未配置或暂时不可用时由执行层记录缺口，保留已取得的房源。
    """
    from dataclasses import replace
    from config import load_search_plan_settings
    from fulfillment import FulfillmentService

    def planner_factory():
        path = Path(env_file) if env_file is not None else Path(__file__).resolve().with_name('.env')
        effective = settings if settings is not None else load_search_plan_settings(path)
        overrides = {key: value for key, value in (
            ('page_limit', page_limit), ('candidate_limit', candidate_limit)) if value is not None}
        if overrides:
            effective = replace(effective, **overrides)
        return create_live_planner_service(env_file=env_file, model=model, settings=effective)

    return FulfillmentService(planner_factory=planner_factory,
        search_factory=lambda: create_live_search_service(env_file=env_file, model=model))


async def fulfill_requirements(request: RequirementRequest, *,
                               ctx: RunContext) -> Result[RequirementFulfillment]:
    """A→B 唯一公开业务入口；ctx 原样传递，所有阶段遵守同一截止时间。"""
    return await create_live_fulfillment_service().fulfill_requirements(request, ctx=ctx)


async def prepare_query(profile: ConversationProfile, *, ctx: RunContext) -> Result[QueryFeatures]:
    """B 内部查询准备接口；A 只需发送 RequirementRequest。"""
    from part1.query import prepare_query as prepare
    return await prepare(profile, ctx=ctx)


async def search(plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
    """便捷 live 入口；批量调用应复用 create_live_search_service() 的实例。"""
    from config import ModelConfigurationError
    started = monotonic()
    try:
        validate_plan(plan, ctx)
        remaining_seconds(ctx)
        if ctx['source_mode'] == 'mock':
            problem = issue('SOURCE_UNAVAILABLE', 'mock 模式请构造 SearchService 并注入模拟来源响应', source=None)
        else:
            result = await create_live_search_service().search(plan, ctx=ctx)
            result['meta']['duration_ms'] = max(0, int((monotonic() - started) * 1000))
            return result
    except ContractViolation as exc:
        problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
    except ModelConfigurationError as exc:
        problem = issue('MODEL_UNAVAILABLE', str(exc), source='model')
    except ProviderError as exc:
        problem = exc.issue
    except Exception:
        problem = issue('INTERNAL_ERROR', '搜索服务初始化或调用失败', source=None)
    return error_result(problem, ctx, started)


if __name__ == '__main__':
    import asyncio
    from test_all import run_all

    asyncio.run(run_all())
