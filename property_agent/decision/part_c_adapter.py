"""把团队 Part C 接到现有 decision graph 的 EvaluationModule 边界。"""
from __future__ import annotations

import copy
from typing import Any

import part_c

from property_agent.contracts import (
    Coverage,
    EvaluationResult,
    ListingSnapshot,
    Result,
    RetrievalResult,
    ReviewResult,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    UserProfile,
)


_LEGACY_PROPOSAL_FIELDS = {
    "listing_constraints.price.amount": "hard_constraints.max_price",
}


def _source(profile: UserProfile, field: str) -> dict[str, Any]:
    """旧档案只保存消息 ID；缺失的逐字原文保持为空，不伪造用户引用。"""
    return {
        "message_id": profile.get("field_sources", {}).get(field, "legacy-profile"),
        "text": "",
        "start": 0,
        "end": 0,
    }


def _constraint(
    profile: UserProfile,
    *,
    field: str,
    field_path: str,
    operator: str,
    value: Any,
    strength: str = "hard",
    priority: str = "high",
) -> dict[str, Any]:
    return {
        "constraint_id": f"legacy:{profile['profile_id']}:{field_path}",
        "field_path": field_path,
        "operator": operator,
        "value": value,
        "strength": strength,
        "priority": priority,
        "source": _source(profile, field),
    }


def confirmed_profile(profile: UserProfile, ctx: RunContext) -> dict[str, Any]:
    """把旧版冻结档案投影成 Part C 的 confirmed ConversationProfile。

    这是迁移期只读投影，不会写回数据库。若调用方已经传入新版确认档案，直接复制。
    """
    if "listing_constraints" in profile:
        return copy.deepcopy(profile)

    hard = profile["hard_constraints"]
    constraints: list[dict[str, Any]] = []
    mapping = (
        ("hard_constraints.currency", "price.currency", "eq", hard.get("currency")),
        ("hard_constraints.max_price", "price.amount", "lte", hard.get("max_price")),
        ("hard_constraints.price_period", "price.period", "eq", hard.get("price_period")),
        (
            "hard_constraints.rental_scope",
            "attributes.listing_scope",
            "eq",
            hard.get("rental_scope"),
        ),
        ("hard_constraints.min_bedrooms", "bedrooms", "gte", hard.get("min_bedrooms")),
    )
    for source_field, listing_field, operator, value in mapping:
        if value is not None:
            constraints.append(
                _constraint(
                    profile,
                    field=source_field,
                    field_path=listing_field,
                    operator=operator,
                    value=value,
                )
            )

    queryable = {
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
    open_requirements: list[dict[str, Any]] = []
    for index, preference in enumerate(profile.get("preferences") or []):
        field = preference.get("field")
        if field in queryable:
            constraints.append(
                _constraint(
                    profile,
                    field=field,
                    field_path=field,
                    operator="eq",
                    value=preference.get("value"),
                    strength="soft",
                    priority=preference.get("priority", "medium"),
                )
            )
        else:
            open_requirements.append(
                {
                    "requirement_id": f"legacy-preference-{index}",
                    "description": f"{field}: {preference.get('value')!r}",
                    "handling": "best_effort",
                    "strength": "soft",
                    "priority": preference.get("priority", "medium"),
                    "source": _source(profile, field or f"preferences[{index}]"),
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
            "source": _source(profile, "hard_constraints.locations"),
        }
        for index, location in enumerate(hard.get("locations") or [])
    ]
    timestamp = ctx["deadline_at"]
    return {
        "profile_id": profile["profile_id"],
        "user_id": ctx["user_id"],
        "conversation_id": ctx["conversation_id"],
        "version": profile["version"],
        "confirmed_version": profile["version"],
        "status": "confirmed",
        "intent": profile.get("intent"),
        "user_context": [],
        "listing_constraints": constraints,
        "derived_data_requirements": derived,
        "open_data_requirements": open_requirements,
        "unresolved": list(profile.get("unresolved") or []),
        "field_sources": dict(profile.get("field_sources") or {}),
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_user_message_at": timestamp,
        "confirmed_at": timestamp,
    }


def _legacy_result(result: Result) -> Result:
    """把 Part C 的提案字段映射回当前 ProfileWriter 支持的路径。"""
    if result.get("data") is None:
        return result
    converted = copy.deepcopy(result)
    assessment = converted["data"].get("assessment") or {}
    for proposal in assessment.get("relaxation_proposals") or []:
        proposal["field"] = _LEGACY_PROPOSAL_FIELDS.get(
            proposal.get("field"), proposal.get("field")
        )
    return converted


class PartCEvaluationModule:
    """使用团队根目录 ``part_c.py`` 的正式 evaluate/review 实现。"""

    async def evaluate(
        self,
        profile: UserProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        result = await part_c.evaluate(
            confirmed_profile(profile, ctx),
            retrieval,
            screen_result,
            listing_snapshot,
            coverage,
            repair_context,
            policy=policy,
            ctx=ctx,
        )
        return _legacy_result(result)

    async def review(
        self,
        profile: UserProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        return await part_c.review(
            confirmed_profile(profile, ctx),
            evaluation,
            listing_snapshot,
            policy=policy,
            ctx=ctx,
        )
