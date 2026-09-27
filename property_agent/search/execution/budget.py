"""This object is shared across the same search; the pagination quota is not reset on each query."""
import asyncio
import math
from copy import deepcopy
from datetime import datetime, timezone

from property_agent.contracts import RunContext, SearchPlan
from property_agent.domain.validation import timestamp, validate_plan
from property_agent.search.providers.base import ProviderError, issue


def remaining_seconds(ctx: RunContext) -> float:
    remaining = (timestamp(ctx["deadline_at"], "ctx.deadline_at") -
                 datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise ProviderError(issue("TIMEOUT", "The deadline for this execution has been reached", retryable=True))
    return remaining


class SearchBudget:
    def __init__(self, plan: SearchPlan, ctx: RunContext, *, page_result_limit=6,
                 finalize_reserve_seconds=10):
        validate_plan(plan, ctx)
        if type(page_result_limit) is not int or page_result_limit <= 0:
            raise ValueError('page_result_limit must be a positive integer')
        if not math.isfinite(finalize_reserve_seconds) or finalize_reserve_seconds <= 0:
            raise ValueError('finalize_reserve_seconds must be a finite positive number')
        self.page_result_limit = page_result_limit
        self.finalize_reserve_seconds = finalize_reserve_seconds
        self._plan = deepcopy(plan)
        self._scope = tuple(ctx[k] for k in
                            ("user_id", "run_id", "conversation_id", "attempt_id", "source_mode"))
        self._deadline = ctx["deadline_at"]
        self.pages_used = 0
        self.candidates_used = 0
        # The same browser-backed provider executes serially, which also makes quota checks and deductions atomic.
        self.lock = asyncio.Lock()

    def check(self, ctx: RunContext, plan: SearchPlan | None = None) -> None:
        scope = tuple(ctx[k] for k in
                      ("user_id", "run_id", "conversation_id", "attempt_id", "source_mode"))
        if (scope != self._scope or ctx["deadline_at"] != self._deadline or
                (plan is not None and plan != self._plan)):
            raise ProviderError(issue("STATE_CONFLICT", "The quota object belongs to another search or a different plan"))
        remaining_seconds(ctx)

    def work_seconds(self, ctx: RunContext) -> float:
        """Reserve time for finalization, but pass the caller's ctx/deadline_at through unchanged."""
        remaining = remaining_seconds(ctx) - self.finalize_reserve_seconds
        if remaining <= 0:
            raise ProviderError(issue('TIMEOUT', 'The finalization reserve time has been entered; stopping external tasks', retryable=False))
        return remaining

    def begin_page(self, plan: SearchPlan, ctx: RunContext) -> int:
        self.check(ctx, plan)
        self.work_seconds(ctx)
        remaining = plan["candidate_limit"] - self.candidates_used
        if self.pages_used >= plan["page_limit"] or remaining <= 0:
            raise ProviderError(issue("BUDGET_EXHAUSTED", "The page or candidate quota has been exhausted"))
        # A failed external call also consumes one attempt; do not retry secretly at the capability layer.
        self.pages_used += 1
        return min(remaining, self.page_result_limit)
