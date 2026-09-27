"""decide_next: the orchestrator's single routing decision point.

The contract requires this to be a deterministic pure function: it only reads DecisionState, does not call models, does not write to storage, and does not generate random IDs.
Illegal fields or impossible states raise ContractViolation, which the calling boundary converts into a system error and records in the trace.

Business routing is delegated to C's `part_c.decide_next`: after review passes, it tries to execute evaluate's
`next_action`. Structural validation and the preparation stage before any search are still handled by this module, because C assumes
a round has already been evaluated.
"""
from __future__ import annotations

from typing import Any

from property_agent.evaluation import service as part_c

from property_agent.contracts import (
    ContractViolation,
    DecisionState,
    RouteDecision,
    RoutingPolicy,
    SearchDirective,
)
from property_agent.decision.policy import validate_policy

SEARCH_STATUSES = frozenset({"not_started", "success", "partial", "error"})
STRATEGY_KINDS = frozenset({"next_page", "alias_query", "alternate_source"})
EVALUATION_ACTIONS = frozenset({"publish", "research", "ask_user", "finish"})

# Reason codes for terminal states and actions. The strings enter persistence and frontend display, so they are defined centrally.
CANCELLED = "cancelled"
PROFILE_SUPERSEDED = "profile_superseded"
USER_DECLINED = "user_declined"
SOURCE_FAILURE = "source_failure"
SERVICE_FAILURE = "service_failure"
REVIEW_BLOCKED = "review_blocked"
REPAIR_EXHAUSTED = "repair_exhausted"
DEADLINE_EXHAUSTED = "deadline_exhausted"
ENOUGH_MATCHES = "enough_matches"
INSUFFICIENT_MATCHES = "insufficient_matches"


def _invalid_state(field_path: str, message: str) -> ContractViolation:
    return ContractViolation("INVALID_STATE", field_path, message)


def _require_int(container: Any, key: str, path: str, minimum: int) -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} must be an integer not less than {minimum}")
    return value


def _require_bool(container: Any, key: str, path: str) -> bool:
    value = container.get(key)
    if not isinstance(value, bool):
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} must be a boolean")
    return value


def _require_text(container: Any, key: str, path: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} must be a non-empty string")
    return value


def _validate_directive(directive: SearchDirective) -> None:
    path = "state.search_directive"
    if directive.get("reason_code") not in ("insufficient_candidates", "incomplete_coverage"):
        raise _invalid_state(f"{path}.reason_code", "the reason_code of the supplementary search directive is not among the allowed values")
    _require_int(directive, "base_profile_version", path, 0)
    changes = directive.get("strategy_changes")
    if not isinstance(changes, list) or not changes:
        raise _invalid_state(f"{path}.strategy_changes", "the supplementary search directive must provide at least one strategy adjustment")
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or change.get("kind") not in STRATEGY_KINDS:
            raise _invalid_state(
                f"{path}.strategy_changes[{index}].kind", "the kind of the strategy adjustment is not among the allowed values"
            )
    if not isinstance(directive.get("evidence_listing_keys"), list):
        raise _invalid_state(f"{path}.evidence_listing_keys", "evidence_listing_keys must be an array")


def _validate_question(question: Any, state: DecisionState) -> None:
    path = "state.pending_question"
    _require_text(question, "question_id", path)
    _require_text(question, "text", path)
    _require_text(question, "reason_code", path)
    actions = question.get("allowed_actions")
    if not isinstance(actions, list) or not actions:
        raise _invalid_state(f"{path}.allowed_actions", "the pending question must provide at least one allowed action")
    proposals = question.get("proposals")
    if not isinstance(proposals, list):
        raise _invalid_state(f"{path}.proposals", "proposals must be an array")
    for index, proposal in enumerate(proposals):
        if proposal.get("requires_user_confirmation") is not True:
            raise _invalid_state(
                f"{path}.proposals[{index}].requires_user_confirmation",
                "a concession proposal must be marked as requiring user confirmation",
            )
    # The question must belong to the current decision view, otherwise an old question could override a new requirement.
    if question.get("base_profile_version") != state["profile_version"]:
        raise _invalid_state(
            f"{path}.base_profile_version", "the profile version of the pending question is inconsistent with this decision view"
        )
    if question.get("state_version") != state["state_version"]:
        raise _invalid_state(f"{path}.state_version", "the state version of the pending question is inconsistent with this decision view")


def validate_decision_state(state: DecisionState) -> None:
    """Performs only structural and value validation; business semantics are left to the routing itself."""
    if not isinstance(state, dict):
        raise _invalid_state("state", "state must be an object")
    _require_text(state, "run_id", "state")
    _require_int(state, "state_version", "state", 0)
    _require_int(state, "profile_version", "state", 0)
    _require_int(state, "current_profile_version", "state", 0)
    _require_bool(state, "cancelled", "state")
    _require_bool(state, "user_declined", "state")
    _require_bool(state, "deadline_exhausted", "state")
    _require_int(state, "search_attempts_used", "state", 0)
    _require_int(state, "repairs_used", "state", 0)
    _require_int(state, "eligible_count", "state", 0)

    if state.get("search_status") not in SEARCH_STATUSES:
        raise _invalid_state("state.search_status", "search_status is not among the allowed values")

    failure_code = state.get("failure_code")
    if failure_code is not None and (not isinstance(failure_code, str) or not failure_code.strip()):
        raise _invalid_state("state.failure_code", "failure_code must be a non-empty string or null")

    review = state.get("review")
    if review is not None:
        if not isinstance(review, dict) or not isinstance(review.get("passed"), bool):
            raise _invalid_state("state.review.passed", "review.passed must be a boolean")
        issues = review.get("issues")
        if not isinstance(issues, list):
            raise _invalid_state("state.review.issues", "review.issues must be an array")
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict) or issue.get("severity") not in ("blocking", "warning"):
                raise _invalid_state(
                    f"state.review.issues[{index}].severity", "the severity of the review issue is not among the allowed values"
                )
        # passed is defined as "no blocking issues"; a contradiction between the two indicates an upstream assembly error.
        if review["passed"] and _blocking_issues(review):
            raise _invalid_state("state.review.passed", "passed=true cannot be accompanied by blocking review issues")

    directive = state.get("search_directive")
    if directive is not None:
        _validate_directive(directive)

    question = state.get("pending_question")
    if question is not None:
        _validate_question(question, state)

    action = state.get("evaluation_next_action")
    if action is not None:
        if action not in EVALUATION_ACTIONS:
            raise _invalid_state(
                "state.evaluation_next_action",
                "evaluation_next_action must be publish/research/ask_user/finish or null",
            )
        reason = state.get("evaluation_next_reason_code")
        if not isinstance(reason, str) or not reason.strip():
            raise _invalid_state(
                "state.evaluation_next_reason_code",
                "when evaluation_next_action is provided, a non-empty reason code must also be given",
            )


def _blocking_issues(review: Any) -> list[Any]:
    return [issue for issue in review.get("issues", []) if issue.get("severity") == "blocking"]


def _decision(
    action: str,
    reason_code: str,
    *,
    search_directive: SearchDirective | None = None,
    pending_question: Any = None,
) -> RouteDecision:
    """research carries only a directive, ask_user carries only a question, and both fields are null for all other actions."""
    if action == "research" and search_directive is None:
        raise _invalid_state("state.search_directive", "research must carry a supplementary search directive")
    if action == "ask_user" and pending_question is None:
        raise _invalid_state("state.pending_question", "ask_user must carry a pending question")
    return {
        "action": action,  # type: ignore[typeddict-item]
        "reason_code": reason_code,
        "search_directive": search_directive if action == "research" else None,
        "pending_question": pending_question if action == "ask_user" else None,
    }


def _research_available(state: DecisionState, policy: RoutingPolicy) -> bool:
    """A supplementary search must maintain a hard condition: the directive version must match the requirement version fixed for this run."""
    directive = state.get("search_directive")
    if directive is None or state["deadline_exhausted"]:
        return False
    if directive["base_profile_version"] != state["profile_version"]:
        return False
    return state["search_attempts_used"] < policy["max_search_attempts"]


def decide_next(state: DecisionState, policy: RoutingPolicy) -> RouteDecision:
    validate_policy(policy)
    validate_decision_state(state)

    question = state.get("pending_question")
    # C assumes a round has already been evaluated. When no search has been performed yet, the orchestration layer still decides whether to ask first or search first.
    if state["search_status"] == "not_started":
        if question is not None:
            return _decision("ask_user", question["reason_code"], pending_question=question)
        if _research_available(state, policy):
            directive = state["search_directive"]
            return _decision("research", directive["reason_code"], search_directive=directive)
        raise _invalid_state(
            "state.search_status", "no search has been performed yet and there is no pending question or executable supplementary search directive, so routing is impossible"
        )

    try:
        return part_c.decide_next(state, policy)
    except Exception as exc:
        if getattr(exc, "code", None) in {"INVALID_INPUT", "INVALID_STATE"} and hasattr(
            exc, "field_path"
        ):
            raise ContractViolation(exc.code, exc.field_path, str(exc)) from exc
        raise
