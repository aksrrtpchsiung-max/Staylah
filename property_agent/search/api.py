"""Public entry point of B, fulfill_requirements; the remaining functions are for internal calls within B and independent integration testing."""

from property_agent.runtime.paths import PROJECT_ROOT
import os
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from property_agent.contracts import (
    AttemptSummary, ContractViolation, QueryFeatures, Result, RunContext,
    ConversationProfile, RequirementRequest, RequirementFulfillment,
    SearchDirective, SearchPlan, SearchResult,
)
from property_agent.search.graph import SearchService
from property_agent.domain.validation import validate_plan, validate_planner_input
from property_agent.search.execution.budget import remaining_seconds
from property_agent.search.aggregation.results import error_result
from property_agent.search.providers.base import ProviderError, issue


def create_live_planner_service(*, env_file=None, model=None, settings=None):
    """Construct the standalone service for 1; plan generation does not depend on a browser or OneMap credentials."""
    from property_agent.runtime.model_client import (create_chat_model, load_model_settings, load_search_plan_settings,
                        load_search_execution_settings)
    from dataclasses import replace
    from property_agent.search.planning.planner import SearchPlanner
    path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
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
    """Internal planning interface of B; only plans that pass validation may proceed to search."""
    from property_agent.runtime.model_client import ModelConfigurationError
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
        problem = issue('INTERNAL_ERROR', 'Failed to initialize or call the planning service', source=None)
    return error_result(problem, ctx, started)


def create_live_search_service(*, env_file=None, model=None) -> SearchService:
    """By default uses DeepSeek, guru_search, and OneMap; no network access at creation time."""
    from property_agent.runtime.model_client import create_chat_model, load_model_settings, load_search_execution_settings
    from dataclasses import replace
    from property_agent.search.providers.guru_search import GuruSearchProvider
    from property_agent.search.providers.onemap import OneMapProvider
    from property_agent.search.providers.osm import NeighborhoodProvider, OSMPlacesProvider
    path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
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
    """Construct the complete B service; dependencies are deferred until retrieval needs to be executed.

    The page count and candidate quota are internal configurations of B and are not added to A's business request.
    When OneMap is not configured or temporarily unavailable, the execution layer records the gap and retains the listings already obtained.
    """
    from dataclasses import replace
    from property_agent.runtime.model_client import load_search_plan_settings
    from property_agent.search.fulfillment import FulfillmentService

    def planner_factory():
        path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
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
    """The only public business entry point from A to B; ctx is passed through unchanged, and all stages observe the same deadline."""
    return await create_live_fulfillment_service().fulfill_requirements(request, ctx=ctx)


async def prepare_query(profile: ConversationProfile, *, ctx: RunContext) -> Result[QueryFeatures]:
    """Internal query preparation interface of B; A only needs to send a RequirementRequest."""
    from property_agent.search.planning.query import prepare_query as prepare
    return await prepare(profile, ctx=ctx)


async def search(plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
    """Convenient live entry point; batch calls should reuse the instance from create_live_search_service()."""
    from property_agent.runtime.model_client import ModelConfigurationError
    started = monotonic()
    try:
        validate_plan(plan, ctx)
        remaining_seconds(ctx)
        if ctx['source_mode'] == 'mock':
            problem = issue('SOURCE_UNAVAILABLE', 'In mock mode, construct a SearchService and inject simulated source responses', source=None)
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
        problem = issue('INTERNAL_ERROR', 'Failed to initialize or call the search service', source=None)
    return error_result(problem, ctx, started)


if __name__ == '__main__':
    import asyncio
    from scripts.live_requirements import run_all

    asyncio.run(run_all())
