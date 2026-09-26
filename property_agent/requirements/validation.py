"""Validation responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from typing import Any
from .models import IssueCode, ProfileChangeModel, RequirementGraphState
from property_agent.requirements.constants import DERIVED_CATEGORIES, DERIVED_OPERATORS, LISTING_OPERATORS, OPEN_REQUIREMENT_HANDLING, PROFILE_FACT_FIELDS, QUERYABLE_LISTING_FIELDS
from property_agent.requirements.profile import _profile_fact_identity, _upsert
from property_agent.requirements.support import _error_update


def validate_patch(state: RequirementGraphState) -> dict[str, Any]:
    """验证 patch 字段、操作符、来源和 JSON 结构，拒绝模型发明的字段。"""

    try:
        changes = [ProfileChangeModel.model_validate(item) for item in state.get("proposed_patch", [])]
        for change in changes:
            if change.field == "listing_constraints":
                _validate_listing_constraint(change.value, state)
            elif change.field == "derived_data_requirements":
                _validate_derived_requirement(change.value, state)
            elif change.field == "open_data_requirements":
                _validate_open_data_requirement(change.value, state)
            elif change.field == "user_context":
                _validate_profile_fact(change.value, state)
        return {
            "validated_patch": [change.model_dump(mode="json") for change in changes],
            "workflow_route": "detect_conflicts",
        }
    except (TypeError, ValueError, KeyError) as exc:
        return _error_update(IssueCode.INVALID_PATCH, "proposed_patch", str(exc))


def detect_conflicts(state: RequirementGraphState) -> dict[str, Any]:
    """把同一字段的新值直接转换为覆盖旧值的 set patch。"""

    profile = state.get("profile") or {}
    constraints = copy.deepcopy(profile.get("listing_constraints", []))
    derived = copy.deepcopy(profile.get("derived_data_requirements", []))
    open_requirements = copy.deepcopy(profile.get("open_data_requirements", []))
    context = copy.deepcopy(profile.get("user_context", []))
    scalar_changes: list[dict[str, Any]] = []
    touched: set[str] = set()
    sources: dict[str, str] = {}

    for change in state.get("validated_patch", []):
        field = change["field"]
        sources[field] = change["source_message_id"]
        if field == "listing_constraints":
            constraints = _upsert(constraints, change["value"], lambda item: item["field_path"])
            touched.add(field)
        elif field == "derived_data_requirements":
            derived = _upsert(
                derived,
                change["value"],
                lambda item: (item["category"], item.get("target"), item["metric"]),
            )
            touched.add(field)
        elif field == "open_data_requirements":
            open_requirements = _upsert(
                open_requirements,
                change["value"],
                lambda item: item["requirement_id"],
            )
            touched.add(field)
        elif field == "user_context":
            context = _upsert(context, change["value"], _profile_fact_identity)
            touched.add(field)
        else:
            scalar_changes.append({**change, "operation": "set"})

    list_values = {
        "listing_constraints": constraints,
        "derived_data_requirements": derived,
        "open_data_requirements": open_requirements,
        "user_context": context,
    }
    for field in (
        "user_context",
        "listing_constraints",
        "derived_data_requirements",
        "open_data_requirements",
    ):
        if field in touched:
            scalar_changes.append({
                "operation": "set",
                "field": field,
                "value": list_values[field],
                "source_message_id": sources[field],
            })
    return {"validated_patch": scalar_changes, "workflow_route": "merge_profile"}


def _validate_listing_constraint(value: Any, state: RequirementGraphState) -> None:
    """验证单条 ListingConstraint 的字段、操作符和逐字来源。"""

    if not isinstance(value, dict):
        raise TypeError("A listing constraint must be an object.")
    if value.get("field_path") not in QUERYABLE_LISTING_FIELDS:
        raise ValueError("The listing constraint contains a field that is not allowed.")
    if value.get("operator") not in LISTING_OPERATORS:
        raise ValueError("The listing constraint operator is invalid.")
    _validate_source(value.get("source"), state)


def _validate_derived_requirement(value: Any, state: RequirementGraphState) -> None:
    """验证单条派生数据需求的类别、操作符和逐字来源。"""

    if not isinstance(value, dict):
        raise TypeError("A derived requirement must be an object.")
    if value.get("category") not in DERIVED_CATEGORIES:
        raise ValueError("The derived requirement category is invalid.")
    if value.get("operator") not in DERIVED_OPERATORS:
        raise ValueError("The derived requirement operator is invalid.")
    _validate_source(value.get("source"), state)


def _validate_open_data_requirement(value: Any, state: RequirementGraphState) -> None:
    """验证开放需求只能以非阻塞 best-effort 方式进入 A/B 请求。"""

    if not isinstance(value, dict):
        raise TypeError("An open data requirement must be an object.")
    if value.get("handling") not in OPEN_REQUIREMENT_HANDLING:
        raise ValueError("An open data requirement must use best_effort handling.")
    if not isinstance(value.get("description"), str) or not value["description"].strip():
        raise ValueError("An open data requirement must contain a description.")
    _validate_source(value.get("source"), state)


def _validate_profile_fact(value: Any, state: RequirementGraphState) -> None:
    """验证用户背景字段来自受控词表且具有逐字来源。"""

    if not isinstance(value, dict):
        raise TypeError("A profile fact must be an object.")
    if value.get("field") not in PROFILE_FACT_FIELDS:
        raise ValueError("The profile fact contains a field that is not allowed.")
    _validate_source(value.get("source"), state)


def _validate_source(source: Any, state: RequirementGraphState) -> None:
    """再次确认 patch 来源属于当前消息且字符位置与原文一致。"""

    if not isinstance(source, dict) or source.get("message_id") != state.get("message_id"):
        raise ValueError("The patch source_message_id does not match the current message.")
    text = state.get("current_input", "")
    start, end = source.get("start"), source.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("The patch source does not contain valid character offsets.")
    if text[start:end] != source.get("text"):
        raise ValueError("The patch source is not a verbatim span of the current input.")
