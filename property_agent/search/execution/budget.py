"""同一次 search 共享此对象；分页额度不会在每个查询中重置。"""
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
        raise ProviderError(issue("TIMEOUT", "已到本次执行的截止时间", retryable=True))
    return remaining


class SearchBudget:
    def __init__(self, plan: SearchPlan, ctx: RunContext, *, page_result_limit=6,
                 finalize_reserve_seconds=10):
        validate_plan(plan, ctx)
        if type(page_result_limit) is not int or page_result_limit <= 0:
            raise ValueError('page_result_limit 必须是正整数')
        if not math.isfinite(finalize_reserve_seconds) or finalize_reserve_seconds <= 0:
            raise ValueError('finalize_reserve_seconds 必须是有限正数')
        self.page_result_limit = page_result_limit
        self.finalize_reserve_seconds = finalize_reserve_seconds
        self._plan = deepcopy(plan)
        self._scope = tuple(ctx[k] for k in
                            ("user_id", "run_id", "conversation_id", "attempt_id", "source_mode"))
        self._deadline = ctx["deadline_at"]
        self.pages_used = 0
        self.candidates_used = 0
        # 同一个 browser-backed provider 串行执行，也使额度检查和扣减原子化。
        self.lock = asyncio.Lock()

    def check(self, ctx: RunContext, plan: SearchPlan | None = None) -> None:
        scope = tuple(ctx[k] for k in
                      ("user_id", "run_id", "conversation_id", "attempt_id", "source_mode"))
        if (scope != self._scope or ctx["deadline_at"] != self._deadline or
                (plan is not None and plan != self._plan)):
            raise ProviderError(issue("STATE_CONFLICT", "额度对象属于另一次搜索或不同的计划"))
        remaining_seconds(ctx)

    def work_seconds(self, ctx: RunContext) -> float:
        """预留汇总时间，但原样传递调用方的 ctx/deadline_at。"""
        remaining = remaining_seconds(ctx) - self.finalize_reserve_seconds
        if remaining <= 0:
            raise ProviderError(issue('TIMEOUT', '已进入汇总预留时间，停止外部任务', retryable=False))
        return remaining

    def begin_page(self, plan: SearchPlan, ctx: RunContext) -> int:
        self.check(ctx, plan)
        self.work_seconds(ctx)
        remaining = plan["candidate_limit"] - self.candidates_used
        if self.pages_used >= plan["page_limit"] or remaining <= 0:
            raise ProviderError(issue("BUDGET_EXHAUSTED", "页数或候选额度已用完"))
        # 外部调用失败也消耗一次尝试，不在能力层偷偷重试。
        self.pages_used += 1
        return min(remaining, self.page_result_limit)
