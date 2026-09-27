"""Program-side read/write for ConversationProfile: confirm the profile, relax hard constraints, read current constraint values.

Module C and the decision graph both use the frozen ConversationProfile directly, no longer going through an adapter layer.
The legacy UserProfile fixture is converted into a confirmed profile only at the loading boundary.
"""
from __future__ import annotations

import copy
from typing import Any

from property_agent.contracts import (
    ConversationProfile,
    JsonValue,
    RelaxationProposal,
    SourceReference,
)

# Fields that can be proposed to the user for relaxation. The path corresponds to RelaxationProposal.field written by C.
RELAXABLE_FIELDS = frozenset({
    "listing_constraints.price.amount",
    "listing_constraints.bedrooms",
})

_LEGACY_CONSTRAINTS = (
    ("hard_constraints.currency", "price.currency", "eq", "currency"),
    ("hard_constraints.max_price", "price.amount", "lte", "max_price"),
    ("hard_constraints.price_period", "price.period", "eq", "price_period"),
    ("hard_constraints.rental_scope", "attributes.listing_scope", "eq", "rental_scope"),
    ("hard_constraints.min_bedrooms", "bedrooms", "gte", "min_bedrooms"),
)

_QUERYABLE_PREFERENCE_FIELDS = {
    "attributes.property_type",
    "attributes.unit_layout",
    "attributes.area_sqft",
    "attributes.bathrooms",
    "attributes.room_type",
    "attributes.ensuite_bathroom",
    "attributes.owner_stays",
    "attributes.cooking_policy",
    "attributes.utilities_included",
    "attributes.wifi_included",
    "attributes.visitors_allowed",
    "attributes.pets_allowed",
    "attributes.furnishing",
    "attributes.tenure_type",
    "attributes.lease_years",
    "listed_date",
}


def empty_source(message_id: str = "legacy-profile") -> SourceReference:
    """The legacy profile stores only message IDs; missing verbatim text remains empty, and user quotes are not fabricated."""
    return {"message_id": message_id, "text": "", "start": 0, "end": 0}


def listing_field_path(proposal_field: str) -> str:
    prefix = "listing_constraints."
    if not proposal_field.startswith(prefix):
        raise KeyError(proposal_field)
    return proposal_field[len(prefix):]


def read_relaxable_value(profile: ConversationProfile, field: str) -> JsonValue:
    """Read the current values of relaxable hard constraints; aligned with the proposal field paths in C."""
    field_path = listing_field_path(field)
    values = [
        constraint["value"]
        for constraint in profile.get("listing_constraints") or []
        if constraint.get("strength") == "hard" and constraint.get("field_path") == field_path
    ]
    if not values:
        raise KeyError(field)
    if field_path == "price.amount":
        amounts = [
            value
            for value in values
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if not amounts:
            raise KeyError(field)
        return min(amounts)
    return values[0]


def is_relaxation(field: str, old: JsonValue, proposed: JsonValue) -> bool:
    """Only definite relaxation directions are recognized; all others are rejected."""
    if field == "listing_constraints.price.amount":
        return (
            isinstance(old, int)
            and isinstance(proposed, int)
            and not isinstance(proposed, bool)
            and proposed > old
        )
    if field == "listing_constraints.bedrooms":
        if old is None:
            return False
        if proposed is None:
            return True
        return isinstance(proposed, int) and not isinstance(proposed, bool) and proposed < old
    return False


def apply_relaxation(
    profile: ConversationProfile,
    *,
    proposal: RelaxationProposal,
    source_message_id: str,
) -> ConversationProfile:
    """Update the corresponding hard listing_constraint according to the proposal, and keep the profile still confirmed."""
    field = proposal["field"]
    if field not in RELAXABLE_FIELDS:
        raise ValueError(f"Field is not in the relaxable whitelist: {field}")
    field_path = listing_field_path(field)
    updated = copy.deepcopy(profile)
    found = False
    for constraint in updated["listing_constraints"]:
        if constraint.get("strength") != "hard" or constraint.get("field_path") != field_path:
            continue
        constraint["value"] = proposal["proposed_value"]
        source = dict(constraint.get("source") or empty_source(source_message_id))
        source["message_id"] = source_message_id
        constraint["source"] = source  # type: ignore[typeddict-item]
        found = True
        break
    if not found:
        raise ValueError(f"No relaxable hard constraint in the profile: {field}")
    updated["version"] = int(updated["version"]) + 1
    updated["confirmed_version"] = updated["version"]
    updated["status"] = "confirmed"
    sources = dict(updated.get("field_sources") or {})
    sources[field] = source_message_id
    updated["field_sources"] = sources
    return updated


def from_legacy_user_profile(
    profile: dict[str, Any],
    *,
    user_id: str = "mock-user-001",
    conversation_id: str | None = None,
    timestamp: str = "2026-09-15T12:00:00+08:00",
) -> ConversationProfile:
    """Project the legacy frozen profile into a confirmed ConversationProfile.

    Used only for fixtures and migration-period input. If it is already a new profile, copy it directly and fill in the confirmation fields.
    """
    if "listing_constraints" in profile:
        confirmed = copy.deepcopy(profile)
        confirmed.setdefault("user_id", user_id)
        confirmed.setdefault("conversation_id", conversation_id or f"conversation-{profile['profile_id']}")
        confirmed.setdefault("status", "confirmed")
        confirmed.setdefault("confirmed_version", confirmed["version"])
        confirmed.setdefault("user_context", [])
        confirmed.setdefault("derived_data_requirements", [])
        confirmed.setdefault("open_data_requirements", [])
        confirmed.setdefault("created_at", timestamp)
        confirmed.setdefault("updated_at", timestamp)
        confirmed.setdefault("last_user_message_at", timestamp)
        confirmed.setdefault("confirmed_at", timestamp)
        return confirmed  # type: ignore[return-value]

    hard = profile["hard_constraints"]
    field_sources = dict(profile.get("field_sources") or {})
    constraints: list[dict[str, Any]] = []
    for source_field, listing_field, operator, key in _LEGACY_CONSTRAINTS:
        value = hard.get(key)
        if value is None:
            continue
        constraints.append(
            {
                "constraint_id": f"legacy:{profile['profile_id']}:{listing_field}",
                "field_path": listing_field,
                "operator": operator,
                "value": value,
                "strength": "hard",
                "priority": "high",
                "source": empty_source(field_sources.get(source_field, "legacy-profile")),
            }
        )

    open_requirements: list[dict[str, Any]] = []
    for index, preference in enumerate(profile.get("preferences") or []):
        field = preference.get("field")
        source = empty_source(field_sources.get(field, "legacy-profile"))
        if field in _QUERYABLE_PREFERENCE_FIELDS:
            constraints.append(
                {
                    "constraint_id": f"legacy:{profile['profile_id']}:{field}",
                    "field_path": field,
                    "operator": "eq",
                    "value": preference.get("value"),
                    "strength": "soft",
                    "priority": preference.get("priority", "medium"),
                    "source": source,
                }
            )
        else:
            open_requirements.append(
                {
                    "requirement_id": f"legacy-preference-{index}",
                    "description": f"{field}: {preference.get('value')!r}",
                    "handling": "best_effort",
                    "strength": "soft",
                    "priority": preference.get("priority", "medium"),
                    "source": source,
                }
            )

    derived = [
        {
            "requirement_id": f"legacy-location-{index}",
            "category": "accessibility",
            "target": location,
            "metric": "residential_area",
            "operator": "eq",
            "value": True,
            "unit": None,
            "strength": "hard",
            "priority": "high",
            "source": empty_source(field_sources.get("hard_constraints.locations", "legacy-profile")),
        }
        for index, location in enumerate(hard.get("locations") or [])
    ]
    return {
        "profile_id": profile["profile_id"],
        "user_id": user_id,
        "conversation_id": conversation_id or f"conversation-{profile['profile_id']}",
        "version": profile["version"],
        "confirmed_version": profile["version"],
        "status": "confirmed",
        "intent": profile.get("intent"),
        "user_context": [],
        "listing_constraints": constraints,  # type: ignore[typeddict-item]
        "derived_data_requirements": derived,  # type: ignore[typeddict-item]
        "open_data_requirements": open_requirements,  # type: ignore[typeddict-item]
        "unresolved": list(profile.get("unresolved") or []),
        "field_sources": field_sources,
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_user_message_at": timestamp,
        "confirmed_at": timestamp,
    }
