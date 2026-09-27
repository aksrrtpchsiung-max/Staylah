"""Routing responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any
from property_agent.contracts import DecisionState, RouteDecision, RoutingPolicy, SearchDirective
from property_agent.evaluation.validation import _validate_policy, _violation


def _route(
    action: str,
    reason_code: str,
    directive: SearchDirective | None = None,
    question: dict[str, Any] | None = None,
) -> RouteDecision:
    return {
        "action": action,  # type: ignore[typeddict-item]
        "reason_code": reason_code,
        "search_directive": directive,
        "pending_question": question,  # type: ignore[typeddict-item]
    }


def decide_next(state: DecisionState, policy: RoutingPolicy) -> RouteDecision:
    """Execute the route suggested by evaluate, but do not bypass review, count limits, deadlines, or user intent."""
    _validate_policy(policy)
    for field in ("state_version", "profile_version", "current_profile_version", "search_attempts_used", "repairs_used", "eligible_count"):
        value = state.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _violation(f"state.{field}", "must be a non-negative integer", "INVALID_STATE")
    evaluation_action = state.get("evaluation_next_action")
    evaluation_reason = state.get("evaluation_next_reason_code")
    if evaluation_action is not None and evaluation_action not in {"publish", "research", "ask_user", "finish"}:
        raise _violation("state.evaluation_next_action", "must be a valid evaluation action or null", "INVALID_STATE")
    if evaluation_action is not None and (not isinstance(evaluation_reason, str) or not evaluation_reason.strip()):
        raise _violation("state.evaluation_next_reason_code", "must be a non-empty string with an evaluation action", "INVALID_STATE")

    if state["current_profile_version"] != state["profile_version"]:
        return _route("stop", "profile_superseded")
    if state["cancelled"]:
        return _route("stop", "cancelled")
    if state["user_declined"]:
        return _route("finish", "user_declined")
    if state["deadline_exhausted"]:
        return _route("stop", "deadline_exhausted")
    if state["search_status"] == "error":
        return _route("stop", "source_failure")

    review_result = state["review"]
    if review_result is None:
        return _route("stop", "review_missing")
    if not review_result["passed"]:
        if state["repairs_used"] < policy["max_repairs"]:
            return _route("repair", "review_blocked")
        return _route("stop", "repair_exhausted")

    # evaluate is the primary decision-maker for the route. Below, only check whether the route is still executable; for example, review
    # has passed but the search count is exhausted, research cannot continue.
    if evaluation_action == "publish" and state["eligible_count"] >= policy["min_matches"]:
        return _route("publish", evaluation_reason or "enough_matches")
    if (
        evaluation_action == "research"
        and state["search_directive"] is not None
        and state["search_attempts_used"] < policy["max_search_attempts"]
    ):
        return _route("research", evaluation_reason or state["search_directive"]["reason_code"], directive=state["search_directive"])
    if evaluation_action == "ask_user" and state["pending_question"] is not None:
        return _route("ask_user", evaluation_reason or "insufficient_candidates", question=state["pending_question"])
    if evaluation_action == "finish":
        return _route("finish", evaluation_reason or "insufficient_candidates")

    if state["eligible_count"] >= policy["min_matches"]:
        return _route("publish", "enough_matches")

    directive = state["search_directive"]
    if directive is not None and state["search_attempts_used"] < policy["max_search_attempts"]:
        return _route("research", directive["reason_code"], directive=directive)
    if state["pending_question"] is not None:
        return _route("ask_user", "insufficient_candidates", question=state["pending_question"])
    if state["search_attempts_used"] >= policy["max_search_attempts"]:
        return _route("finish", "budget_exhausted")
    return _route("finish", "insufficient_candidates")
