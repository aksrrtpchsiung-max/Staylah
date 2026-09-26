"""decide_next：编排器唯一的路由决策点。

契约要求这是确定性纯函数：只读 DecisionState，不调模型、不写库、不生成随机 ID。
非法字段或不可能状态抛 ContractViolation，由调用边界转成系统错误并记录 trace。

业务路线交给 C 的 `part_c.decide_next`：review 通过后尽量执行 evaluate 的
`next_action`。结构校验、尚未搜索的准备阶段仍由本模块处理，因为 C 假定
已经评过一轮。
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

# 终态与动作的原因码。字符串进入持久化与前端展示，因此集中定义。
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
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} 必须是不小于 {minimum} 的整数")
    return value


def _require_bool(container: Any, key: str, path: str) -> bool:
    value = container.get(key)
    if not isinstance(value, bool):
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} 必须是布尔值")
    return value


def _require_text(container: Any, key: str, path: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_state(f"{path}.{key}", f"{path}.{key} 必须是非空字符串")
    return value


def _validate_directive(directive: SearchDirective) -> None:
    path = "state.search_directive"
    if directive.get("reason_code") not in ("insufficient_candidates", "incomplete_coverage"):
        raise _invalid_state(f"{path}.reason_code", "补搜指令的 reason_code 不在允许取值内")
    _require_int(directive, "base_profile_version", path, 0)
    changes = directive.get("strategy_changes")
    if not isinstance(changes, list) or not changes:
        raise _invalid_state(f"{path}.strategy_changes", "补搜指令必须给出至少一项策略调整")
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or change.get("kind") not in STRATEGY_KINDS:
            raise _invalid_state(
                f"{path}.strategy_changes[{index}].kind", "策略调整的 kind 不在允许取值内"
            )
    if not isinstance(directive.get("evidence_listing_keys"), list):
        raise _invalid_state(f"{path}.evidence_listing_keys", "evidence_listing_keys 必须是数组")


def _validate_question(question: Any, state: DecisionState) -> None:
    path = "state.pending_question"
    _require_text(question, "question_id", path)
    _require_text(question, "text", path)
    _require_text(question, "reason_code", path)
    actions = question.get("allowed_actions")
    if not isinstance(actions, list) or not actions:
        raise _invalid_state(f"{path}.allowed_actions", "待问问题必须给出至少一个允许动作")
    proposals = question.get("proposals")
    if not isinstance(proposals, list):
        raise _invalid_state(f"{path}.proposals", "proposals 必须是数组")
    for index, proposal in enumerate(proposals):
        if proposal.get("requires_user_confirmation") is not True:
            raise _invalid_state(
                f"{path}.proposals[{index}].requires_user_confirmation",
                "让步提案必须标记为需要用户确认",
            )
    # 问题必须属于当前决策视图，否则旧问题可能覆盖新需求。
    if question.get("base_profile_version") != state["profile_version"]:
        raise _invalid_state(
            f"{path}.base_profile_version", "待问问题的档案版本与本次决策视图不一致"
        )
    if question.get("state_version") != state["state_version"]:
        raise _invalid_state(f"{path}.state_version", "待问问题的状态版本与本次决策视图不一致")


def validate_decision_state(state: DecisionState) -> None:
    """只做结构与取值校验；business 语义留给路由本身。"""
    if not isinstance(state, dict):
        raise _invalid_state("state", "state 必须是对象")
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
        raise _invalid_state("state.search_status", "search_status 不在允许取值内")

    failure_code = state.get("failure_code")
    if failure_code is not None and (not isinstance(failure_code, str) or not failure_code.strip()):
        raise _invalid_state("state.failure_code", "failure_code 必须是非空字符串或 null")

    review = state.get("review")
    if review is not None:
        if not isinstance(review, dict) or not isinstance(review.get("passed"), bool):
            raise _invalid_state("state.review.passed", "review.passed 必须是布尔值")
        issues = review.get("issues")
        if not isinstance(issues, list):
            raise _invalid_state("state.review.issues", "review.issues 必须是数组")
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict) or issue.get("severity") not in ("blocking", "warning"):
                raise _invalid_state(
                    f"state.review.issues[{index}].severity", "审查问题的 severity 不在允许取值内"
                )
        # passed 的定义是"没有阻断问题"，两者矛盾说明上游拼装有误。
        if review["passed"] and _blocking_issues(review):
            raise _invalid_state("state.review.passed", "passed=true 不能同时带阻断性审查问题")

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
                "evaluation_next_action 必须是 publish/research/ask_user/finish 或 null",
            )
        reason = state.get("evaluation_next_reason_code")
        if not isinstance(reason, str) or not reason.strip():
            raise _invalid_state(
                "state.evaluation_next_reason_code",
                "提供 evaluation_next_action 时必须同时给出非空原因码",
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
    """research 只带 directive，ask_user 只带 question，其余动作两项均为 null。"""
    if action == "research" and search_directive is None:
        raise _invalid_state("state.search_directive", "research 必须带补搜指令")
    if action == "ask_user" and pending_question is None:
        raise _invalid_state("state.pending_question", "ask_user 必须带待问问题")
    return {
        "action": action,  # type: ignore[typeddict-item]
        "reason_code": reason_code,
        "search_directive": search_directive if action == "research" else None,
        "pending_question": pending_question if action == "ask_user" else None,
    }


def _research_available(state: DecisionState, policy: RoutingPolicy) -> bool:
    """补搜必须保持硬条件：指令版本要对上本 run 固定的需求版本。"""
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
    # C 假定已经评过一轮。尚未搜索时仍由编排层决定先问还是先搜。
    if state["search_status"] == "not_started":
        if question is not None:
            return _decision("ask_user", question["reason_code"], pending_question=question)
        if _research_available(state, policy):
            directive = state["search_directive"]
            return _decision("research", directive["reason_code"], search_directive=directive)
        raise _invalid_state(
            "state.search_status", "尚未搜索且没有待问问题或可执行补搜指令，无法路由"
        )

    try:
        return part_c.decide_next(state, policy)
    except Exception as exc:
        if getattr(exc, "code", None) in {"INVALID_INPUT", "INVALID_STATE"} and hasattr(
            exc, "field_path"
        ):
            raise ContractViolation(exc.code, exc.field_path, str(exc)) from exc
        raise
