"""决策段的图装配。

```text
START ─┬─ evaluate_candidates ─┬─ review_recommendation ─┐
       │                       └───────────────────────┐ │
       └───────────────────────────────────────────────┼─┴─ prepare_decision ─ route
                                                       │                         │
  repair ◀── research ◀── ask_user ── wait_for_user ── apply_answer ◀────────────┤
     └─────────▶ evaluate_candidates                publish / finish_run / stop_run
```

一个找房 run 对应一个 thread（thread_id=run_id）；等待用户使用 interrupt，
恢复使用同一 thread 与 Command(resume=...)。业务写入与框架 checkpoint 不是同一事务，
因此推荐交付、提问和档案修改都设了稳定幂等键。
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
    """返回未编译的图，便于调用方自己决定 checkpointer。"""
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

    # repair 与 research 都回到评价入口；两者各自扣自己的额度，不互相清零。
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
    """把上游一次搜索尝试的产物装成本段的初始状态。

    `profile` 是本 run 固定的需求版本快照；等待用户期间档案可能变化，
    路由前会重新读取当前版本来判断是否已被取代。
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
