"""The orchestration state of the decision stage, and the rules for projecting it into DecisionState.

DState is an internal design and is not part of the public contract. Only the projected read-only view is passed to `decide_next`:
Pure functions cannot see the listing snapshot, the model client, or the full chat.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from property_agent.contracts import (
    AttemptSummary,
    Coverage,
    DecisionState,
    EvaluationResult,
    Issue,
    ListingSnapshot,
    PendingQuestion,
    Recommendation,
    RequirementCoverage,
    RelaxationProposal,
    RetrievalResult,
    ReviewResult,
    RouteDecision,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    ConversationProfile,
)
from property_agent.decision.boundaries import NextRunRequest

SCHEMA_VERSION = 2

RunStatus = Literal["running", "waiting_user", "completed", "failed", "cancelled", "superseded"]


class PendingAnswer(TypedDict, total=False):
    """Payload for the web recovery interface. accept_proposal passes only proposal_id; the new value is read from the server."""

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
    profile_snapshot: ConversationProfile
    profile_version: int
    current_profile_version: int

    # The product of one upstream search attempt.
    attempt_id: str | None
    search_attempts_used: int
    search_status: str
    failure_code: str | None
    listing_snapshot: ListingSnapshot | None
    screen_result: ScreenResult | None
    retrieval_result: RetrievalResult | None
    coverage: Coverage | None
    requirement_coverage: RequirementCoverage | None
    previous_attempts: list[AttemptSummary]

    # The product of module C and the repair budget.
    evaluation: EvaluationResult | None
    review: ReviewResult | None
    repair_context: ReviewResult | None
    repairs_used: int

    # The program counter and the cleaned next-step materials.
    eligible_count: int
    search_directive: SearchDirective | None
    evaluation_next_action: str | None
    evaluation_next_reason_code: str | None
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
    """The v0 field name continues to use eligible; in the main flow, what is counted is the deduplicated number of B handoff candidates."""
    if not screen_result:
        return 0
    return len({item["listing_key"] for item in screen_result.get("eligible", [])})


def eligible_keys(screen_result: ScreenResult | None) -> set[str]:
    if not screen_result:
        return set()
    return {item["listing_key"] for item in screen_result.get("eligible", [])}


def build_decision_state(state: DState) -> DecisionState:
    """Project the orchestration state into a read-only decision view. All fields are explicitly fetched to avoid leaking internal fields to pure functions."""
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
        "evaluation_next_action": state.get("evaluation_next_action"),
        "evaluation_next_reason_code": state.get("evaluation_next_reason_code"),
    }
    return projected  # type: ignore[return-value]
