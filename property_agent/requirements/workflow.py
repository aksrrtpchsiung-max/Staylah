"""A-side 需求工作流的确定性节点、输入守卫和 profile 持久化边界。"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from .deepseek_parser import DeepSeekAPIError, DeepSeekConfigurationError, DeepSeekParserConfig
from .models import (
    ConversationProfileModel,
    InputGuardDecision,
    Intent,
    IssueCode,
    NormalizedRequirement,
    PreferenceRequirement,
    PreferenceTopic,
    ProfileChangeModel,
    RequirementConfirmationModel,
    RequirementGraphState,
    RequirementRequestModel,
)
from .response_renderer import ResponseRenderer
from .workflow_constants import DEFAULT_USER_ID, FALCON_SCOPE_MESSAGE, SAFE_ERROR_MESSAGE


MAX_INPUT_CHARS = 8000

QUERYABLE_LISTING_FIELDS = {
    "transaction_type",
    "price.amount",
    "price.currency",
    "price.period",
    "attributes.property_type",
    "attributes.unit_layout",
    "attributes.listing_scope",
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
    "bedrooms",
    "listed_date",
}
LISTING_OPERATORS = {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "contains"}
# Listing.furnishing 在共享契约中是枚举字符串；布尔 True 会被 B 判为 INVALID_INPUT。
FURNISHING_VALUES = frozenset({"fully", "partially", "unfurnished"})
# 顺序即优先级：先判否定词，因为“不带家具”包含“带家具”。
FURNISHING_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("eq", "unfurnished", (
        "unfurnished", "not furnished", "no furniture", "without furniture",
        "无家具", "不带家具", "不配家具", "不要家具",
    )),
    ("eq", "fully", (
        "fully furnished", "full furnished", "fully-furnished",
        "家具齐全", "全装修", "精装修", "拎包入住",
    )),
    ("eq", "partially", (
        "partially furnished", "partly furnished", "semi furnished", "semi-furnished",
        "部分家具", "部分家私", "半装修",
    )),
    # 只说“有家具/带家具”时只是“不能没有家具”，用 neq 才不会误杀家具齐全的房源。
    ("neq", "unfurnished", (
        "带家具", "有家具", "配家具", "家具", "家私", "furnished", "furniture",
    )),
)
DERIVED_CATEGORIES = {"commute", "nearby_amenity", "environment", "accessibility"}
DERIVED_OPERATORS = {"eq", "lte", "gte", "between", "minimize", "maximize", "preferred"}
OPEN_REQUIREMENT_HANDLING = {"best_effort"}
PROFILE_FACT_FIELDS = {
    "household.occupant_count",
    "household.has_children",
    "household.planning_children",
    "occupant.workplace",
    "occupant.school",
}


class InputGuard(Protocol):
    """定义输入有效性和新加坡住房相关性的判定接口。"""

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """返回输入结构与业务范围判定，不生成任何 profile 字段。"""


class ProfileRepository(Protocol):
    """定义 confirmed ConversationProfile 的持久化接口。"""

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """以当前调用用户身份原子保存一版 confirmed profile。"""


class InMemoryProfileRepository:
    """提供仅供本地开发和测试使用的非持久化 profile repository。"""

    def __init__(self) -> None:
        """初始化以 profile_id 为键的隔离内存存储。"""

        self._profiles: dict[str, dict[str, Any]] = {}

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """校验用户归属后深拷贝保存，避免后续 state 修改污染已确认版本。"""

        if profile.get("user_id") != user_id:
            raise PermissionError("profile.user_id does not match the current user")
        self._profiles[profile["profile_id"]] = copy.deepcopy(profile)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        """读取指定 profile 的副本，缺失时返回 None。"""

        value = self._profiles.get(profile_id)
        return copy.deepcopy(value) if value is not None else None


class DeepSeekInputGuard:
    """使用 DeepSeek JSON Output 判断输入是否属于 Falcon 住房服务范围。"""

    def __init__(
        self,
        config: DeepSeekParserConfig | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """注入无密钥配置、可选进程内密钥和测试 HTTP 客户端。"""

        self._config = config or DeepSeekParserConfig.from_runtime()
        self._api_key = api_key
        self._client = client

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """校验长度，并让模型只返回 valid 与 housing_related 两个布尔值。"""

        if not text.strip() or len(text) > MAX_INPUT_CHARS:
            return InputGuardDecision(valid=False, housing_related=False)
        api_key = self._api_key or os.getenv(self._config.api_key_env)
        if not api_key:
            raise DeepSeekConfigurationError(
                f"Environment variable {self._config.api_key_env} is missing; "
                "the API key must not be stored in source code or Git-tracked files"
            )
        client = self._client or httpx.Client(timeout=self._config.timeout_seconds)
        should_close = self._client is None
        try:
            response = client.post(
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self._config.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Determine whether the input is related to Singapore residential renting, "
                                "buying, listing requirements, locations, commuting, neighbourhoods, schools, "
                                f"or the current housing conversation. The workflow status is {workflow_status}. "
                                "Answers to confirmation or clarification questions are housing-related. "
                                "Return JSON only: "
                                '{"valid": boolean, "housing_related": boolean}.'
                            ),
                        },
                        {"role": "user", "content": text},
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                    "max_tokens": 80,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return InputGuardDecision.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekAPIError(f"Input scope classification failed: {type(exc).__name__}") from None
        finally:
            if should_close:
                client.close()


def utc_now() -> str:
    """返回适合 contract 和数据库保存的 UTC ISO-8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


def make_validate_input_node(
    guard: InputGuard,
    renderer: ResponseRenderer | None = None,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """创建输入合法性与住房相关性检查节点。"""

    response_renderer = renderer or ResponseRenderer()

    def validate_input(state: RequirementGraphState) -> dict[str, Any]:
        """拒绝非法或无关输入，并返回用户指定的固定 Falcon 提示。"""

        text = state.get("current_input", "")
        if not state.get("message_id", "").strip():
            return _error_update(IssueCode.INVALID_INPUT, "message_id", "message_id must not be empty")
        supplied_user_id = state.get("user_id")
        user_id = DEFAULT_USER_ID if supplied_user_id is None else supplied_user_id.strip()
        if not user_id:
            return _error_update(IssueCode.INVALID_INPUT, "user_id", "user_id must not be empty")
        if not state.get("conversation_id", "").strip():
            return _error_update(
                IssueCode.INVALID_INPUT,
                "conversation_id",
                "conversation_id must not be empty",
            )
        try:
            decision = guard.check(text, workflow_status=state.get("status", "new"))
        except (DeepSeekConfigurationError, DeepSeekAPIError) as exc:
            return _error_update(IssueCode.MODEL_UNAVAILABLE, "input_guard", str(exc))
        if not decision.valid or not decision.housing_related:
            return {
                "user_id": user_id,
                "input_guard": decision.model_dump(mode="json"),
                "assistant_response": response_renderer.out_of_scope(),
                "workflow_route": "end",
                "status": "out_of_scope",
            }
        return {
            "user_id": user_id,
            "input_guard": decision.model_dump(mode="json"),
            "workflow_route": "classify_turn_intent",
        }

    return validate_input


def route_workflow(state: RequirementGraphState) -> dict[str, Any]:
    """根据已有 profile 状态把消息路由到确认处理或需求解析。"""

    profile = state.get("profile") or {}
    if profile.get("status") == "pending_confirmation" and state.get("confirmation"):
        return {"workflow_route": "handle_confirmation"}
    return {"workflow_route": "understand_requirement", "status": "parsing"}


def handle_confirmation(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """确定性识别确认和取消；其余文本作为需求修订重新解析。"""

    response_renderer = renderer or ResponseRenderer()
    normalized = state.get("current_input", "").strip().lower().rstrip("。.!！")
    confirmations = {"yes", "y", "confirm", "confirmed", "是", "是的", "确认", "正确", "没问题"}
    cancellations = {"cancel", "取消", "不找了", "结束", "停止"}
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


def generate_confirmation(
    state: RequirementGraphState,
    *,
    renderer: ResponseRenderer | None = None,
) -> dict[str, Any]:
    """从 profile 确定性生成确认摘要，避免摘要与 JSON 条件不一致。"""

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
    """创建用户确认后原子持久化 profile 的节点。"""

    def persist_confirmed_profile(state: RequirementGraphState) -> dict[str, Any]:
        """校验 confirmation version，保存 confirmed profile，失败时禁止交给 B。"""

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
    """从 confirmed profile 构造 A 到 B 的最小化固定请求，但不调用 B。"""

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


def _profile_summary(profile: dict[str, Any]) -> str:
    """把 confirmed 候选字段转换为简洁且可核对的英文摘要。"""

    parts = ["Rent" if profile.get("intent") == "rent" else "Buy"]
    constraints = profile.get("listing_constraints", [])
    by_field = {item["field_path"]: item for item in constraints}
    budget = by_field.get("price.amount")
    if budget is not None:
        currency = by_field.get("price.currency", {}).get("value", "SGD")
        period = by_field.get("price.period", {}).get("value")
        period_text = {
            "month": "Monthly budget",
            "week": "Weekly budget",
            "total": "Total budget",
        }.get(period, "Budget")
        operator_text = {
            "lte": "up to",
            "lt": "below",
            "gte": "at least",
            "gt": "above",
            "eq": "of",
            "between": "around",
        }.get(budget["operator"], "of")
        parts.append(f"{period_text} {operator_text} {currency} {budget['value']}")
    scope = by_field.get("attributes.listing_scope")
    if scope is not None:
        parts.append({"whole_unit": "Whole unit", "room": "Private room", "bedspace": "Bedspace"}.get(
            scope["value"], f"Rental scope: {scope['value']}"
        ))
    bedrooms = by_field.get("bedrooms")
    if bedrooms is not None:
        operator_text = {"gte": "At least ", "lte": "At most ", "eq": ""}.get(
            bedrooms["operator"], ""
        )
        parts.append(f"{operator_text}{bedrooms['value']} bedroom(s)")
    property_type = by_field.get("attributes.property_type")
    if property_type is not None:
        values = property_type["value"] if isinstance(property_type["value"], list) else [property_type["value"]]
        labels = {"hdb": "HDB", "condo": "condo", "landed": "landed property", "apartment": "apartment"}
        parts.append("Property type: " + ", ".join(labels.get(value, str(value)) for value in values))
    ensuite = by_field.get("attributes.ensuite_bathroom")
    if ensuite is not None and ensuite.get("value") is True:
        parts.append("Ensuite bathroom required")
    for item in constraints:
        field = item["field_path"]
        if field in {
            "price.amount", "price.currency", "price.period", "attributes.listing_scope",
            "bedrooms", "attributes.property_type", "attributes.ensuite_bathroom",
        }:
            continue
        parts.append(f"{field}: {json.dumps(item['value'], ensure_ascii=False)}")
    for item in profile.get("derived_data_requirements", []):
        category, target, metric = item["category"], item.get("target"), item["metric"]
        if category == "accessibility" and metric == "residential_area":
            relation = "near " if item.get("value") == "near" else "in "
            parts.append(f"Preferred location: {relation}{target}")
        elif category == "commute" and metric == "travel_time":
            if item.get("value") is not None:
                parts.append(f"Commute to {target} within {item['value']} minutes")
            else:
                parts.append(f"Convenient commute to {target}")
        elif category == "nearby_amenity" and target == "school":
            parts.append("Schools nearby")
        elif category == "nearby_amenity" and target == "bus_stop":
            parts.append("Near a bus stop")
        elif category == "environment" and metric == "noise_level":
            parts.append("Quiet surroundings preferred")
        else:
            parts.append(f"{metric}: {item.get('value')}")
    for item in profile.get("open_data_requirements", []):
        parts.append(f"Best effort: {item['description']}")
    return "; ".join(parts)


def _error_update(code: IssueCode, field: str, message: str) -> dict[str, Any]:
    """生成统一错误状态并把后续路由指向 recover_error。"""

    return {
        "requirement_issues": [{"code": code.value, "field": field, "message": message}],
        "workflow_route": "recover_error",
        "status": "failed",
    }
