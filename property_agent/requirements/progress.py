"""Progress responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from typing import Any
from .models import Intent, RequirementGraphState
from .response_renderer import ResponseRenderer



def route_workflow(state: RequirementGraphState) -> dict[str, Any]:
    """Route the message to confirmation handling or requirement parsing based on the existing profile state."""

    profile = state.get("profile") or {}
    if profile.get("status") == "pending_confirmation" and state.get("confirmation"):
        return {"workflow_route": "handle_confirmation"}
    return {"workflow_route": "understand_requirement", "status": "parsing"}


def assess_completeness(state: RequirementGraphState) -> dict[str, Any]:
    """After merging, compute the missing fields for blocking confirmation and select the subsequent branch."""

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
    # Shares the core filter check with B; missing period/currency must be clarified before confirmation.
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
    """Select at most three questions by business priority to avoid asking the user for all fields at once."""

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
    """Converge node failures into a safe response that does not leak keys, stack traces, or internal objects."""

    response_renderer = renderer or ResponseRenderer()
    return {
        "assistant_response": response_renderer.error(),
        "status": "failed",
        "workflow_route": "end",
    }
