"""决策段的编排状态，以及投影成 DecisionState 的规则。

DState 是内部设计，不属于公共契约。传给 `decide_next` 的只有投影后的只读视图：
纯函数看不到房源快照、模型客户端和完整聊天。
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from property_agent.contracts import (
    Coverage,
    DecisionState,
    EvaluationResult,
    Issue,
    ListingSnapshot,
    PendingQuestion,
    Recommendation,
    RelaxationProposal,
    RetrievalResult,
    ReviewResult,
    RouteDecision,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    UserProfile,
)
from property_agent.decision.boundaries import NextRunRequest

SCHEMA_VERSION = 1

RunStatus = Literal["running", "waiting_user", "completed", "failed", "cancelled", "superseded"]


class PendingAnswer(TypedDict, total=False):
    """网页恢复接口的载荷。accept_proposal 只传 proposal_id，新值从服务端读取。"""

    client_message_id: str
    question_id: str
    expected_state_version: int
    action: Literal["answer", "accept_proposal", "decline", "cancel"]
    answer: str | None
    proposal_id: str | None


class DState(TypedDict, total=False):
    schema_version: int
    ctx: RunContext
    policy: RoutingPolicy

    run_id: str
    conversation_id: str
    profile_id: str
    profile_snapshot: UserProfile
    profile_version: int
    current_profile_version: int

    # 上游一次搜索尝试的产物。
    attempt_id: str | None
    search_attempts_used: int
    search_status: str
    failure_code: str | None
    listing_snapshot: ListingSnapshot | None
    screen_result: ScreenResult | None
    retrieval_result: RetrievalResult | None
    coverage: Coverage | None

    # 模块 C 的产物与修复预算。
    evaluation: EvaluationResult | None
    review: ReviewResult | None
    repair_context: ReviewResult | None
    repairs_used: int

    # 程序计数与清洗后的下一步材料。
    eligible_count: int
    search_directive: SearchDirective | None
    relaxation_proposals: list[RelaxationProposal]
    pending_question: PendingQuestion | None
    pending_answer: PendingAnswer | dict[str, Any] | str | None
    answer_rejected: bool
    decision: RouteDecision | None

    cancelled: bool
    user_declined: bool
    deadline_exhausted: bool

    state_version: int
    status: RunStatus
    completion_reason: str | None
    final_result_id: str | None
    published_recommendation: Recommendation | None
    delivery_is_partial: bool
    next_run_request: NextRunRequest | None
    last_issues: list[Issue]


def count_eligible(screen_result: ScreenResult | None) -> int:
    """数量规则依据 ScreenResult.eligible 的去重数量，不依据网页条数或检索 Top-K。"""
    if not screen_result:
        return 0
    return len({item["listing_key"] for item in screen_result.get("eligible", [])})


def eligible_keys(screen_result: ScreenResult | None) -> set[str]:
    if not screen_result:
        return set()
    return {item["listing_key"] for item in screen_result.get("eligible", [])}


def build_decision_state(state: DState) -> DecisionState:
    """把编排状态投影成只读决策视图。字段一律显式取，避免把内部字段漏给纯函数。"""
    projected: dict[str, Any] = {
        "run_id": state["run_id"],
        "state_version": state["state_version"],
        "profile_version": state["profile_version"],
        "current_profile_version": state["current_profile_version"],
        "cancelled": state.get("cancelled", False),
        "user_declined": state.get("user_declined", False),
        "deadline_exhausted": state.get("deadline_exhausted", False),
        "search_status": state.get("search_status", "not_started"),
        "search_attempts_used": state.get("search_attempts_used", 0),
        "repairs_used": state.get("repairs_used", 0),
        "eligible_count": state.get("eligible_count", 0),
        "review": state.get("review"),
        "failure_code": state.get("failure_code"),
        "search_directive": state.get("search_directive"),
        "pending_question": state.get("pending_question"),
    }
    return projected  # type: ignore[return-value]
