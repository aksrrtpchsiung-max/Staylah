"""Profile responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from collections.abc import Callable
from typing import Any
from .models import ConversationProfileModel, RequirementGraphState
from property_agent.requirements.support import utc_now


def make_merge_profile_node(
    clock: Callable[[], str] = utc_now,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """创建确定性合并 patch 并递增 draft version 的节点。"""

    def merge_profile(state: RequirementGraphState) -> dict[str, Any]:
        """创建或更新 ConversationProfile，但不改写 confirmed_version。"""

        now = clock()
        changes = state.get("validated_patch", [])
        profile = copy.deepcopy(state.get("profile")) if state.get("profile") else {
            "profile_id": f"profile-{state['conversation_id']}",
            "user_id": state["user_id"],
            "conversation_id": state["conversation_id"],
            "version": 0,
            "confirmed_version": None,
            "status": "draft",
            "intent": None,
            "user_context": [],
            "listing_constraints": [],
            "derived_data_requirements": [],
            "open_data_requirements": [],
            "unresolved": [],
            "field_sources": {},
            "created_at": now,
            "updated_at": now,
            "last_user_message_at": now,
            "confirmed_at": None,
        }
        for change in changes:
            field = change["field"]
            if change["operation"] == "set":
                profile[field] = copy.deepcopy(change["value"])
            elif change["operation"] == "append":
                profile[field].append(copy.deepcopy(change["value"]))
            elif change["operation"] == "remove":
                profile[field] = [item for item in profile[field] if item != change["value"]]
            profile["field_sources"][field] = change["source_message_id"]
        if changes:
            profile.update(
                version=profile["version"] + 1,
                status="draft",
                updated_at=now,
                last_user_message_at=now,
            )
        else:
            profile["last_user_message_at"] = now
        validated = ConversationProfileModel.model_validate(profile)
        return {
            "profile": validated.model_dump(mode="json"),
            "workflow_route": "assess_completeness",
        }

    return merge_profile


def _upsert(items: list[dict[str, Any]], new_item: dict[str, Any], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """按稳定语义键直接替换旧项，不要求用户再次确认冲突策略。"""

    identity = key(new_item)
    return [item for item in items if key(item) != identity] + [copy.deepcopy(new_item)]


def _profile_fact_identity(item: dict[str, Any]) -> Any:
    """允许一个 conversation 同时保存多个工作地或学校，其余事实仍按字段覆盖。"""

    field = item["field"]
    if field in {"occupant.workplace", "occupant.school"}:
        return field, item.get("value")
    return field
