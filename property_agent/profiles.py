"""ConversationProfile 的程序侧读写：确认档案、放宽硬条件、读取当前约束值。

模块 C 与 decision 图都直接使用冻结的 ConversationProfile，不再经过适配层。
旧版 UserProfile 夹具只在加载边界转换成确认档案。
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

# 可以向用户提议放宽的字段。路径对应 C 写入的 RelaxationProposal.field。
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
    """旧档案只保存消息 ID；缺失的逐字原文保持为空，不伪造用户引用。"""
    return {"message_id": message_id, "text": "", "start": 0, "end": 0}


def listing_field_path(proposal_field: str) -> str:
    prefix = "listing_constraints."
    if not proposal_field.startswith(prefix):
        raise KeyError(proposal_field)
    return proposal_field[len(prefix):]


def read_relaxable_value(profile: ConversationProfile, field: str) -> JsonValue:
    """读取可放宽硬条件的当前值；与 C 的提案字段路径对齐。"""
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
    """只认确定的放宽方向，其余一律不认。"""
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
    """按提案更新对应 hard listing_constraint，并保持档案仍为 confirmed。"""
    field = proposal["field"]
    if field not in RELAXABLE_FIELDS:
        raise ValueError(f"字段不在可放宽白名单: {field}")
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
        raise ValueError(f"档案中没有可放宽的硬条件: {field}")
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
    """把旧版冻结档案投影成 confirmed ConversationProfile。

    只用于夹具和迁移期输入。若已经是新档案，直接复制并补齐确认字段。
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
