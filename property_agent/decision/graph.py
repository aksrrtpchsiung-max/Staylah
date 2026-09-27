"""Graph assembly for the decision stage.

```text
START ─┬─ evaluate_candidates ─┬─ review_recommendation ─┐
       │                       └───────────────────────┐ │
       └───────────────────────────────────────────────┼─┴─ prepare_decision ─ route
                                                       │                         │
  repair ◀── research ◀── ask_user ── wait_for_user ── apply_answer ◀────────────┤
     └─────────▶ evaluate_candidates                publish / finish_run / stop_run
```

One house-hunting run corresponds to one thread (thread_id=run_id); wait for the user using interrupt,
and resume using the same thread with Command(resume=...). Business writes and framework checkpoints are not in the same transaction,
so stable idempotency keys are set for delivery, questions, and profile modifications.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from property_agent.contracts import ConversationProfile, RoutingPolicy, RunContext
from property_agent.decision.boundaries import AttemptOutcome
from property_agent.decision.deps import DecisionDeps
from property_agent.decision.nodes import (
    DecisionNodes,
    route_after_answer,
    route_after_evaluate,
    route_after_research,
    route_decision,
    route_entry,
)
from property_agent.decision.policy import DEFAULT_POLICY, validate_policy
from property_agent.decision.state import SCHEMA_VERSION, DState


def build_decision_graph(deps: DecisionDeps) -> StateGraph:
    """Return the uncompiled graph so the caller can decide the checkpointer themselves."""
    nodes = DecisionNodes(deps)
    builder = StateGraph(DState)

    builder.add_node("evaluate_candidates", nodes.evaluate_candidates)
    builder.add_node("review_recommendation", nodes.review_recommendation)
    builder.add_node("prepare_decision", nodes.prepare_decision)
    builder.add_node("route", nodes.route)
    builder.add_node("publish", nodes.publish)
    builder.add_node("repair", nodes.repair)
    builder.add_node("research", nodes.research)
    builder.add_node("ask_user", nodes.ask_user)
    builder.add_node("wait_for_user", nodes.wait_for_user)
    builder.add_node("apply_answer", nodes.apply_answer)
    builder.add_node("finish_run", nodes.finish_run)
    builder.add_node("stop_run", nodes.stop_run)

    builder.add_conditional_edges(
        START, route_entry, ["evaluate_candidates", "prepare_decision"]
    )
    builder.add_conditional_edges(
        "evaluate_candidates", route_after_evaluate, ["review_recommendation", "prepare_decision"]
    )
    builder.add_edge("review_recommendation", "prepare_decision")
    builder.add_edge("prepare_decision", "route")
    builder.add_conditional_edges(
        "route",
        route_decision,
        ["publish", "repair", "research", "ask_user", "finish_run", "stop_run", END],
    )

    # Both repair and research return to the evaluation entry point; each deducts from its own quota and does not reset the other.
    builder.add_edge("repair", "evaluate_candidates")
    builder.add_conditional_edges(
        "research", route_after_research, ["evaluate_candidates", "prepare_decision"]
    )

    builder.add_edge("ask_user", "wait_for_user")
    builder.add_edge("wait_for_user", "apply_answer")
    builder.add_conditional_edges(
        "apply_answer", route_after_answer, ["wait_for_user", "prepare_decision", END]
    )

    for terminal in ("publish", "finish_run", "stop_run"):
        builder.add_edge(terminal, END)
    return builder


def initial_state(
    *,
    ctx: RunContext,
    profile: ConversationProfile,
    outcome: AttemptOutcome,
    policy: RoutingPolicy | None = None,
    search_attempts_used: int = 1,
    deadline_exhausted: bool = False,
) -> DState:
    """Load the output of one upstream search attempt as the initial state for this stage.

    `profile` is the requirement version snapshot fixed for this run; the profile may change while waiting for the user,
    and before routing, the current version is re-read to determine whether it has been superseded.
    """
    resolved_policy = policy or DEFAULT_POLICY
    validate_policy(resolved_policy)
    first_summary = outcome.get("attempt_summary")
    return {
        "schema_version": SCHEMA_VERSION,
        "ctx": ctx,
        "policy": resolved_policy,
        "run_id": ctx["run_id"],
        "conversation_id": ctx["conversation_id"],
        "profile_id": profile["profile_id"],
        "profile_snapshot": profile,
        "profile_version": profile["version"],
        "current_profile_version": profile["version"],
        "attempt_id": outcome["attempt_id"],
        "search_attempts_used": search_attempts_used,
        "search_status": outcome["search_status"],
        "failure_code": outcome.get("failure_code"),
        "listing_snapshot": outcome.get("listing_snapshot"),
        "screen_result": outcome.get("screen_result"),
        "retrieval_result": outcome.get("retrieval_result"),
        "coverage": outcome.get("coverage"),
        "requirement_coverage": outcome.get("requirement_coverage"),
        "previous_attempts": [first_summary] if first_summary is not None else [],
        "evaluation": None,
        "review": None,
        "repair_context": None,
        "repairs_used": 0,
        "eligible_count": 0,
        "search_directive": None,
        "evaluation_next_action": None,
        "evaluation_next_reason_code": None,
        "relaxation_proposals": [],
        "pending_question": None,
        "pending_answer": None,
        "answer_rejected": False,
        "decision": None,
        "cancelled": False,
        "user_declined": False,
        "deadline_exhausted": deadline_exhausted,
        "state_version": 0,
        "status": "running",
        "completion_reason": None,
        "final_result_id": None,
        "published_recommendation": None,
        "delivery_is_partial": False,
        "next_run_request": None,
        "last_issues": [],
    }
