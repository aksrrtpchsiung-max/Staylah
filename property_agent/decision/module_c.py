"""Formal module C: directly implements EvaluationModule, with signatures consistent with property_agent.contracts."""
from __future__ import annotations

from property_agent.evaluation import service as part_c

from property_agent.contracts import (
    ConversationProfile,
    Coverage,
    EvaluationResult,
    ListingSnapshot,
    Result,
    RetrievalResult,
    ReviewResult,
    RoutingPolicy,
    RunContext,
    ScreenResult,
)


class PartCEvaluationModule:
    """Calls ``part_c.evaluate`` / ``part_c.review`` in the repository root directory."""

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        return await part_c.evaluate(
            profile,
            retrieval,
            screen_result,
            listing_snapshot,
            coverage,
            repair_context,
            policy=policy,
            ctx=ctx,
        )

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        return await part_c.review(
            profile,
            evaluation,
            listing_snapshot,
            policy=policy,
            ctx=ctx,
        )
