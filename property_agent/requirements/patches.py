"""Patches responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any
from .models import NormalizedRequirement, PreferenceRequirement, PreferenceTopic, ProfileChangeModel
from property_agent.requirements.constants import FURNISHING_RULES, FURNISHING_VALUES


def normalized_requirement_to_patch(requirement: NormalizedRequirement) -> list[dict[str, Any]]:
    """把已验证的 LLM 标准需求转换为共享 contract 的 profile patch。"""

    changes: list[ProfileChangeModel] = []
    message_id = requirement.message_id
    if requirement.intent is not None:
        changes.append(ProfileChangeModel(
            operation="set",
            field="intent",
            value=requirement.intent.value.value,
            source_message_id=message_id,
        ))
    for fact in requirement.user_context:
        changes.append(ProfileChangeModel(
            operation="append",
            field="user_context",
            value={
                "field": fact.field.value,
                "value": fact.value,
                "source": fact.source.model_dump(mode="json"),
            },
            source_message_id=message_id,
        ))

    listing_constraints: list[dict[str, Any]] = []
    if requirement.budget is not None:
        budget = requirement.budget
        listing_constraints.extend([
            _listing_constraint("price.amount", "lte", budget.max_price, budget.strength.value,
                                "high", budget.source),
            _listing_constraint("price.currency", "eq", budget.currency, budget.strength.value,
                                "high", budget.source),
        ])
        if budget.period is not None:
            listing_constraints.append(_listing_constraint(
                "price.period", "eq", budget.period.value, budget.strength.value, "high", budget.source
            ))
    if requirement.rental_scope is not None:
        scope = requirement.rental_scope
        listing_constraints.append(_listing_constraint(
            "attributes.listing_scope", "eq", scope.value.value, scope.strength.value, "high", scope.source
        ))
    if requirement.bedrooms is not None:
        bedrooms = requirement.bedrooms
        listing_constraints.append(_listing_constraint(
            "bedrooms", bedrooms.operator.value, bedrooms.value, bedrooms.strength.value,
            "high", bedrooms.source
        ))
    if requirement.property_types:
        first = requirement.property_types[0]
        listing_constraints.append(_listing_constraint(
            "attributes.property_type", "in",
            [item.value.value for item in requirement.property_types],
            first.strength.value, "medium", first.source,
        ))
    preference_constraints, unparsed_preferences = _preference_listing_constraints(requirement)
    listing_constraints.extend(preference_constraints)
    for value in listing_constraints:
        changes.append(ProfileChangeModel(
            operation="append", field="listing_constraints", value=value,
            source_message_id=message_id,
        ))

    derived = _derived_requirements(requirement)
    for value in derived:
        changes.append(ProfileChangeModel(
            operation="append", field="derived_data_requirements", value=value,
            source_message_id=message_id,
        ))
    for value in _open_data_requirements(requirement, unparsed_preferences):
        changes.append(ProfileChangeModel(
            operation="append", field="open_data_requirements", value=value,
            source_message_id=message_id,
        ))
    return [change.model_dump(mode="json") for change in changes]


def _listing_constraint(
    field_path: str,
    operator: str,
    value: Any,
    strength: str,
    priority: str,
    source: Any,
) -> dict[str, Any]:
    """构造具有稳定字段级 ID 的 ListingConstraint 字典。"""

    return {
        "constraint_id": f"constraint:{field_path}",
        "field_path": field_path,
        "operator": operator,
        "value": value,
        "strength": strength,
        "priority": priority,
        "source": source.model_dump(mode="json"),
    }


def _derived_requirements(requirement: NormalizedRequirement) -> list[dict[str, Any]]:
    """把地点、通勤和非 Listing 偏好转换为 B 可消费的派生数据需求。"""

    values: list[dict[str, Any]] = []
    for location in requirement.locations:
        values.append(_derived(
            "accessibility", location.raw_name, "residential_area",
            "eq" if location.strength.value == "hard" else "preferred",
            location.relation.value, None, location.strength.value, "high", location.source,
        ))
    for commute in requirement.commute:
        values.append(_derived(
            "commute", commute.destination, "travel_time",
            "lte" if commute.max_minutes is not None else "minimize", commute.max_minutes,
            "minute", commute.strength.value, "high", commute.source,
        ))
    for preference in requirement.preferences:
        if preference.topic == PreferenceTopic.NEAR_BUS_STOP:
            values.append(_derived(
                "nearby_amenity", "bus_stop", "proximity", "preferred", preference.value,
                None, preference.strength.value, preference.priority.value, preference.source,
            ))
        elif preference.topic == PreferenceTopic.QUIETNESS:
            values.append(_derived(
                "environment", None, "noise_level", "preferred", "low",
                None, preference.strength.value, preference.priority.value, preference.source,
            ))
        elif preference.topic == PreferenceTopic.FAMILY_FRIENDLY:
            values.append(_derived(
                "nearby_amenity", "school", "proximity", "preferred", preference.value,
                None, preference.strength.value, preference.priority.value, preference.source,
            ))
    return values


def _open_data_requirement(preference: PreferenceRequirement) -> dict[str, Any]:
    """按逐字原文构造一条非阻塞的 best-effort 开放需求。"""

    source = preference.source
    return {
        "requirement_id": f"requirement:open:{source.message_id}:{source.start}:{source.end}",
        "description": source.text,
        "handling": "best_effort",
        "strength": preference.strength.value,
        "priority": preference.priority.value,
        "source": source.model_dump(mode="json"),
    }


def _open_data_requirements(
    requirement: NormalizedRequirement,
    extra_preferences: list[PreferenceRequirement] | None = None,
) -> list[dict[str, Any]]:
    """把无法映射到稳定字段的偏好保存为非阻塞 best-effort 需求。"""

    values: list[dict[str, Any]] = []
    for preference in requirement.preferences:
        if preference.topic != PreferenceTopic.OTHER:
            continue
        source = preference.source
        source_text = source.text.casefold()
        if requirement.commute and ("commute" in source_text or "通勤" in source_text):
            continue
        values.append(_open_data_requirement(preference))
    for preference in extra_preferences or []:
        values.append(_open_data_requirement(preference))
    return values


def _derived(
    category: str,
    target: str | None,
    metric: str,
    operator: str,
    value: Any,
    unit: str | None,
    strength: str,
    priority: str,
    source: Any,
) -> dict[str, Any]:
    """构造具有稳定语义身份的 DerivedDataRequirement 字典。"""

    identity = f"{category}:{target or 'none'}:{metric}"
    return {
        "requirement_id": f"requirement:{identity}",
        "category": category,
        "target": target,
        "metric": metric,
        "operator": operator,
        "value": value,
        "unit": unit,
        "strength": strength,
        "priority": priority,
        "source": source.model_dump(mode="json"),
    }


def _furnishing_value(preference: PreferenceRequirement) -> tuple[str, str] | None:
    """把家具偏好收敛成 Listing.furnishing 的枚举取值；无法判断时返回 None。

    逐字原文优先，模型给出的值只作为兜底，避免布尔 True 直接进入共享契约。
    """

    value = preference.value
    haystack = preference.source.text.casefold()
    if isinstance(value, str):
        # 模型偶尔写成 "fully-furnished" 这类变体，一并参与关键词判断。
        haystack = f"{value.casefold()}\n{haystack}"
    for operator, normalized, keywords in FURNISHING_RULES:
        if any(keyword in haystack for keyword in keywords):
            return operator, normalized
    if isinstance(value, str):
        candidate = value.strip().casefold()
        if candidate in FURNISHING_VALUES:
            return "eq", candidate
    elif value is True:
        return "neq", "unfurnished"
    elif value is False:
        return "eq", "unfurnished"
    return None


def _preference_listing_constraints(
    requirement: NormalizedRequirement,
) -> tuple[list[dict[str, Any]], list[PreferenceRequirement]]:
    """把可直接匹配 Listing 字段的稳定偏好主题转换为约束。

    取值固定的主题在这里写死取值；需要透传模型取值的主题必须显式规一化，否则会把
    共享契约不允许的值交给 B。无法规一化的家具偏好返回给调用方降级处理。
    """

    mapping: dict[PreferenceTopic, tuple[str, Any]] = {
        PreferenceTopic.ENSUITE_BATHROOM: ("attributes.ensuite_bathroom", True),
        PreferenceTopic.OWNER_NOT_STAYING: ("attributes.owner_stays", False),
        PreferenceTopic.COOKING_ALLOWED: ("attributes.cooking_policy", "full"),
        PreferenceTopic.UTILITIES_INCLUDED: ("attributes.utilities_included", True),
        PreferenceTopic.WIFI_INCLUDED: ("attributes.wifi_included", True),
        PreferenceTopic.VISITORS_ALLOWED: ("attributes.visitors_allowed", True),
        PreferenceTopic.PETS_ALLOWED: ("attributes.pets_allowed", True),
    }
    values: list[dict[str, Any]] = []
    unparsed: list[PreferenceRequirement] = []
    for preference in requirement.preferences:
        if preference.topic == PreferenceTopic.FURNISHING:
            normalized = _furnishing_value(preference)
            if normalized is None:
                unparsed.append(preference)
                continue
            operator, value = normalized
            values.append(_listing_constraint(
                "attributes.furnishing", operator, value, preference.strength.value,
                preference.priority.value, preference.source,
            ))
            continue
        mapped = mapping.get(preference.topic)
        if mapped is None:
            continue
        field_path, fixed_value = mapped
        values.append(_listing_constraint(
            field_path, "eq", fixed_value, preference.strength.value,
            preference.priority.value, preference.source,
        ))
    return values, unparsed
