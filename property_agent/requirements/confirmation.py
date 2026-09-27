"""Confirmation responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from collections.abc import Callable
from typing import Any
from .models import ConversationProfileModel, IssueCode, RequirementConfirmationModel, RequirementGraphState, RequirementRequestModel
from .response_renderer import ResponseRenderer
from property_agent.requirements.presentation import _profile_summary
from property_agent.requirements.profile_repository import ProfileRepository
from property_agent.requirements.support import _error_update, utc_now


def handle_confirmation(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """Deterministically recognize confirmation and cancellation; all other text is re-parsed as a requirement revision."""

    response_renderer = renderer or ResponseRenderer()
    normalized = state.get("current_input", "").strip().lower().rstrip(".!")
    confirmations = {"yes", "y", "confirm", "confirmed", "yes indeed", "correct", "no problem"}
    cancellations = {"cancel", "not looking anymore", "end", "stop"}
    if normalized in confirmations:
        return {"workflow_route": "persist_confirmed_profile"}
    if normalized in cancellations:
        profile = copy.deepcopy(state.get("profile") or {})
        if profile:
            profile["status"] = "idle"
        return {
            "profile": profile,
            "workflow_route": "end",
            "status": "cancelled",
            "assistant_response": response_renderer.cancelled(),
        }
    return {"workflow_route": "understand_requirement", "status": "parsing"}


def generate_confirmation(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """Deterministically generate a confirmation summary from the profile to avoid inconsistency between the summary and the JSON conditions."""

    response_renderer = renderer or ResponseRenderer()
    profile = copy.deepcopy(state["profile"])
    summary = _profile_summary(profile)
    confirmation = RequirementConfirmationModel(
        confirmation_id=f"confirm-{profile['profile_id']}-v{profile['version']}",
        profile_id=profile["profile_id"],
        profile_version=profile["version"],
        summary=summary,
    )
    profile["status"] = "pending_confirmation"
    return {
        "profile": profile,
        "confirmation": confirmation.model_dump(mode="json"),
        "clarification_questions": [],
        "assistant_response": response_renderer.confirmation(summary),
        "status": "awaiting_confirmation",
        "workflow_route": "end",
    }


def make_persist_confirmed_profile_node(
    repository: ProfileRepository,
    clock: Callable[[], str] = utc_now,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """Create a node that atomically persists the profile after user confirmation."""

    def persist_confirmed_profile(state: RequirementGraphState) -> dict[str, Any]:
        """Validate the confirmation version, save the confirmed profile, and prohibit handing off to B on failure."""

        profile = copy.deepcopy(state.get("profile") or {})
        confirmation = state.get("confirmation") or {}
        if confirmation.get("profile_version") != profile.get("version"):
            return _error_update(
                IssueCode.STATE_CONFLICT,
                "confirmation.profile_version",
                "The confirmation version does not match the current profile version.",
            )
        now = clock()
        profile.update(
            confirmed_version=profile["version"],
            confirmed_at=now,
            updated_at=now,
            last_user_message_at=now,
            status="confirmed",
        )
        try:
            validated = ConversationProfileModel.model_validate(profile)
            repository.save(
                validated.model_dump(mode="json"),
                user_id=state["user_id"],
            )
        except Exception as exc:
            return _error_update(
                IssueCode.PERSISTENCE_ERROR,
                "profile",
                f"Failed to save the profile: {type(exc).__name__}",
            )
        confirmed = copy.deepcopy(confirmation)
        confirmed["status"] = "confirmed"
        return {
            "profile": validated.model_dump(mode="json"),
            "confirmation": confirmed,
            "status": "confirmed",
            "workflow_route": "build_requirement_request",
        }

    return persist_confirmed_profile


def build_requirement_request(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """Construct the minimal fixed request from A to B from the confirmed profile, but do not call B."""

    response_renderer = renderer or ResponseRenderer()
    profile = ConversationProfileModel.model_validate(state["profile"])
    if profile.confirmed_version != profile.version or profile.confirmed_at is None:
        return _error_update(IssueCode.STATE_CONFLICT, "profile", "The profile has not been confirmed.")
    if profile.intent is None:
        return _error_update(IssueCode.INVALID_PATCH, "profile.intent", "The profile is missing an intent.")
    request = RequirementRequestModel(
        request_id=f"requirement-{profile.conversation_id}-v{profile.version}",
        conversation_id=profile.conversation_id,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        intent=profile.intent,
        user_context=profile.user_context,
        listing_constraints=profile.listing_constraints,
        derived_data_requirements=profile.derived_data_requirements,
        open_data_requirements=profile.open_data_requirements,
        unresolved_fields=profile.unresolved,
        confirmed_at=profile.confirmed_at,
    )
    return {
        "requirement_request": request.model_dump(mode="json"),
        "assistant_response": response_renderer.ready_for_b(),
        "status": "ready_for_b",
        "workflow_route": "end",
    }
