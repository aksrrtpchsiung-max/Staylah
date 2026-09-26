"""Progress responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from typing import Any
from .models import Intent, RequirementGraphState
from .response_renderer import ResponseRenderer



def route_workflow(state: RequirementGraphState) -> dict[str, Any]:
    """根据已有 profile 状态把消息路由到确认处理或需求解析。"""

    profile = state.get("profile") or {}
    if profile.get("status") == "pending_confirmation" and state.get("confirmation"):
        return {"workflow_route": "handle_confirmation"}
    return {"workflow_route": "understand_requirement", "status": "parsing"}


def assess_completeness(state: RequirementGraphState) -> dict[str, Any]:
    """在合并后计算阻塞确认的缺失字段并选择后续分支。"""

    profile = copy.deepcopy(state["profile"])
    missing: set[str] = set()
    if profile.get("intent") is None:
        missing.add("intent")
    constraint_fields = {item["field_path"] for item in profile.get("listing_constraints", [])}
    if "price.amount" not in constraint_fields:
        missing.add("listing_constraints.price.amount")
    if profile.get("intent") == Intent.RENT.value and "attributes.listing_scope" not in constraint_fields:
        missing.add("listing_constraints.attributes.listing_scope")
    has_location = any(
        item.get("category") in {"accessibility", "commute"} and item.get("target")
        for item in profile.get("derived_data_requirements", [])
    )
    if not has_location:
        missing.add("derived_data_requirements.location")
    # 与 B 共用核心过滤检查；缺少周期/币种必须在确认前澄清。
    if profile.get("intent") is not None:
        from property_agent.domain.requirements import normalize_requirements
        missing.update(q["field"] for q in normalize_requirements({**profile, "unresolved": []})["clarification_questions"])
    profile["unresolved"] = sorted(missing)
    if missing:
        return {"profile": profile, "workflow_route": "select_clarification"}
    return {"profile": profile, "workflow_route": "generate_confirmation"}


def select_clarification(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """按业务优先级选择最多三个问题，避免一次向用户追问全部字段。"""

    response_renderer = renderer or ResponseRenderer()
    questions = {
        "intent": "Are you looking to rent or buy?",
        "listing_constraints.price.amount": "What is your budget in SGD?",
        "listing_constraints.price.period": "Is your rental budget per month or per week?",
        "listing_constraints.price.currency": "Which currency is your budget in?",
        "listing_constraints.attributes.listing_scope": (
            "Are you looking for a whole unit, a private room, or a bedspace?"
        ),
        "derived_data_requirements.location": (
            "Which area would you like to live in, or where do you need to commute to?"
        ),
    }
    priority = [
        "intent",
        "listing_constraints.price.amount",
        "listing_constraints.price.period",
        "listing_constraints.price.currency",
        "listing_constraints.attributes.listing_scope",
        "rental_scope",
        "derived_data_requirements.location",
    ]
    unresolved = state["profile"].get("unresolved", [])
    ordered = [field for field in priority if field in unresolved]
    ordered.extend(field for field in unresolved if field not in ordered)
    selected = [
        {"field": field, "text": questions.get(field, f"Please provide: {field}")}
        for field in ordered[:3]
    ]
    return {
        "clarification_questions": selected,
        "assistant_response": response_renderer.clarification(selected),
        "status": "awaiting_clarification",
        "workflow_route": "end",
    }


def recover_error(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """把节点失败收敛成不泄露密钥、堆栈和内部对象的安全响应。"""

    response_renderer = renderer or ResponseRenderer()
    return {
        "assistant_response": response_renderer.error(),
        "status": "failed",
        "workflow_route": "end",
    }
