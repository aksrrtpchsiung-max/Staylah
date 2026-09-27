"""Boundaries responsibilities extracted without changing behavior."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypedDict
from property_agent.contracts import AttemptSummary, Clarification, ConversationProfile, QueryFeatures, RequirementCoverage, RequirementRequest, Result, RunContext, SearchDirective, SearchPlan
from property_agent.decision.boundaries import AttemptOutcome



class Planner(Protocol):
    async def build_search_plan(
        self,
        profile: ConversationProfile,
        query: QueryFeatures,
        previous_attempts: list[AttemptSummary],
        directive: SearchDirective | None,
        *,
        ctx: RunContext,
    ) -> Result: ...


class SearchService(Protocol):
    async def search_for_request(
        self, plan: SearchPlan, request: RequirementRequest, *, ctx: RunContext
    ) -> Result: ...


class FulfillmentService(Protocol):
    async def run(
        self, request: RequirementRequest, *, ctx: RunContext
    ) -> dict[str, Any]: ...


class BCTransition(TypedDict):
    """The explicit route handed to the outer orchestration after the first B fulfillment."""

    route: Literal["decision", "clarification"]
    outcome: AttemptOutcome | None
    clarification_questions: list[Clarification]
    requirement_coverage: RequirementCoverage


QueryPreparer = Callable[
    [ConversationProfile], Awaitable[Result]
]


RetrieveFunction = Callable[..., Awaitable[Result]]
