"""正式模块 C：直接实现 EvaluationModule，签名与 property_agent.contracts 一致。"""
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
    """调用仓库根目录 ``part_c.evaluate`` / ``part_c.review``。"""

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
