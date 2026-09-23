"""LLM 直接输出标准化需求时使用的严格共享模型。"""

from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """为需求理解模型统一启用未知字段拒绝策略。"""

    model_config = ConfigDict(extra="forbid")


class Intent(str, Enum):
    """表示用户希望租赁或购买住宅。"""

    RENT = "rent"
    BUY = "buy"


class ConstraintStrength(str, Enum):
    """区分不可自动放宽的硬条件和允许权衡的软条件。"""

    HARD = "hard"
    SOFT = "soft"


class PricePeriod(str, Enum):
    """表示标准金额的计价周期。"""

    MONTH = "month"
    WEEK = "week"
    TOTAL = "total"


class RentalScope(str, Enum):
    """表示整套、单间或床位范围。"""

    WHOLE_UNIT = "whole_unit"
    ROOM = "room"
    BEDSPACE = "bedspace"


class PropertyType(str, Enum):
    """表示标准化后的新加坡住宅类型。"""

    HDB = "hdb"
    CONDO = "condo"
    LANDED = "landed"
    APARTMENT = "apartment"
    OTHER = "other"


class NumericOperator(str, Enum):
    """表示数值约束的精确、下限或上限语义。"""

    EQ = "eq"
    GTE = "gte"
    LTE = "lte"


class LocationRelation(str, Enum):
    """区分位于某地点内和位于某地点附近。"""

    IN = "in"
    NEAR = "near"


class LocationResolutionStatus(str, Enum):
    """表示地点实体是否已经由外部地点服务解析。"""

    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class TravelMode(str, Enum):
    """表示标准化后的交通方式。"""

    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"
    CYCLING = "cycling"
    UNKNOWN = "unknown"


class DestinationType(str, Enum):
    """表示通勤目的地的业务类别。"""

    MRT = "mrt"
    STATION = "station"
    LANDMARK = "landmark"
    WORKPLACE = "workplace"
    SCHOOL = "school"
    UNKNOWN = "unknown"


class PreferencePriority(str, Enum):
    """表示软偏好或附加条件的相对优先级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PreferenceTopic(str, Enum):
    """限定推荐系统当前可稳定消费的偏好主题。"""

    ENSUITE_BATHROOM = "ensuite_bathroom"
    NEAR_BUS_STOP = "near_bus_stop"
    OWNER_NOT_STAYING = "owner_not_staying"
    COOKING_ALLOWED = "cooking_allowed"
    UTILITIES_INCLUDED = "utilities_included"
    WIFI_INCLUDED = "wifi_included"
    VISITORS_ALLOWED = "visitors_allowed"
    PETS_ALLOWED = "pets_allowed"
    FURNISHING = "furnishing"
    QUIETNESS = "quietness"
    FAMILY_FRIENDLY = "family_friendly"
    MODERN_INTERIOR = "modern_interior"
    AVOID_FIRST_FLOOR = "avoid_first_floor"
    OTHER = "other"


class ProfileFactField(str, Enum):
    """限定本 conversation 可保存的搜索相关用户背景字段。"""

    OCCUPANT_COUNT = "household.occupant_count"
    HAS_CHILDREN = "household.has_children"
    PLANNING_CHILDREN = "household.planning_children"
    WORKPLACE = "occupant.workplace"
    SCHOOL = "occupant.school"


class IssueCode(str, Enum):
    """枚举直接需求理解阶段可能产生的问题。"""

    INVALID_INPUT = "invalid_input"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    UNSUPPORTED_SOURCE = "unsupported_source"
    INVALID_PATCH = "invalid_patch"
    STATE_CONFLICT = "state_conflict"
    PERSISTENCE_ERROR = "persistence_error"


class ProcessingStatus(str, Enum):
    """表示需求理解节点的最终执行状态。"""

    NORMALIZED = "normalized"
    FAILED = "failed"


class SourceSpan(StrictModel):
    """记录标准化字段在原始用户消息中的逐字依据。"""

    message_id: str = Field(description="ID of the user message that produced the field.", min_length=1)
    text: str = Field(description="Verbatim input span supporting the field.", min_length=1)
    start: int = Field(description="Inclusive start offset of the input span.", ge=0)
    end: int = Field(description="Exclusive end offset of the input span.", ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "SourceSpan":
        """确保原文下标为正向且与保存的文本长度一致。"""

        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        if self.end - self.start != len(self.text):
            raise ValueError("source span length must match source text")
        return self


class IntentConstraint(StrictModel):
    """表示带硬软属性和原文依据的标准交易意图。"""

    value: Intent = Field(description="Rent or buy intent.")
    strength: ConstraintStrength = Field(description="Whether the intent is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the intent.")


class RentalScopeConstraint(StrictModel):
    """表示带硬软属性和原文依据的标准租赁范围。"""

    value: RentalScope = Field(description="Whole unit, private room, or bedspace.")
    strength: ConstraintStrength = Field(description="Whether the rental scope is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the rental scope.")


class PropertyTypeConstraint(StrictModel):
    """表示带硬软属性和原文依据的标准住宅类型。"""

    value: PropertyType = Field(description="Normalized residential property type.")
    strength: ConstraintStrength = Field(description="Whether the property type is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the property type.")


class MoneyConstraint(StrictModel):
    """表示由 LLM 已转换为整数和标准周期的预算上限。"""

    currency: Literal["SGD"] = Field(description="Currency, fixed to SGD for this project.")
    max_price: int = Field(description="Normalized integer budget ceiling.", gt=0)
    period: PricePeriod | None = Field(default=None, description="Monthly, weekly, or total period.")
    approximate: bool = Field(description="Whether the user expressed the amount approximately.")
    strength: ConstraintStrength = Field(description="Whether the budget is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the budget.")


class NumericConstraint(StrictModel):
    """表示由 LLM 已转换为整数和比较符的数值约束。"""

    operator: NumericOperator = Field(description="Equality, minimum, or maximum operator.")
    value: int = Field(description="Normalized non-negative integer.", ge=0)
    strength: ConstraintStrength = Field(description="Whether the numeric condition is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the numeric condition.")


class LocationRequirement(StrictModel):
    """保存 LLM 提取的地点实体和关系，等待外部地点服务解析。"""

    raw_name: str = Field(description="Location entity from the user input, without an invented ID.", min_length=1)
    relation: LocationRelation = Field(description="Whether the home should be in or near the location.")
    resolution_status: Literal[LocationResolutionStatus.UNRESOLVED] = Field(
        default=LocationResolutionStatus.UNRESOLVED,
        description="Fixed to unresolved during the LLM stage.",
    )
    strength: ConstraintStrength = Field(description="Whether the location condition is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence including the location relation.")


class CommuteRequirement(StrictModel):
    """表示由 LLM 已完成枚举和分钟数转换的通勤需求。"""

    destination: str = Field(description="Commute destination from the user input.", min_length=1)
    destination_type: DestinationType = Field(description="Business type of the destination.")
    travel_mode: TravelMode = Field(description="Normalized travel mode.")
    max_minutes: int | None = Field(
        default=None,
        description="Maximum acceptable commute time, or null when the user gives no limit.",
        gt=0,
    )
    strength: ConstraintStrength = Field(description="Whether the commute condition is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the full commute condition.")


class PreferenceRequirement(StrictModel):
    """表示由 LLM 映射到稳定主题词表的附加需求。"""

    topic: PreferenceTopic = Field(description="Normalized preference topic.")
    value: Any = Field(
        description=(
            "Value for the topic, using that topic's controlled vocabulary. "
            "furnishing must be fully, partially, or unfurnished; boolean topics use true."
        )
    )
    priority: PreferencePriority = Field(description="Relative preference priority.")
    strength: ConstraintStrength = Field(description="Whether the preference is negotiable.")
    source: SourceSpan = Field(description="User-input evidence for the preference.")


class ProfileFactRequirement(StrictModel):
    """表示从本轮输入提取、与找房有关的 conversation 用户背景。"""

    field: ProfileFactField = Field(description="Controlled user-context field.")
    value: Any = Field(description="JSON value for the selected field.")
    source: SourceSpan = Field(description="Verbatim user-input evidence for the profile fact.")


class NormalizedRequirement(StrictModel):
    """表示 LLM 直接产生并通过 Pydantic 校验的标准化用户需求。"""

    message_id: str = Field(description="Current user-message ID.", min_length=1)
    original_text: str = Field(description="Complete unmodified user input.", min_length=1)
    intent: IntentConstraint | None = Field(default=None, description="Rent or buy intent.")
    user_context: list[ProfileFactRequirement] = Field(
        default_factory=list,
        description="Housing-relevant context belonging only to this conversation.",
    )
    budget: MoneyConstraint | None = Field(default=None, description="Normalized budget ceiling.")
    rental_scope: RentalScopeConstraint | None = Field(default=None, description="Whole unit, room, or bedspace.")
    locations: list[LocationRequirement] = Field(default_factory=list, description="Locations without resolved IDs.")
    bedrooms: NumericConstraint | None = Field(default=None, description="Normalized bedroom-count constraint.")
    property_types: list[PropertyTypeConstraint] = Field(default_factory=list, description="Normalized property types.")
    commute: list[CommuteRequirement] = Field(default_factory=list, description="Normalized commute requirements.")
    preferences: list[PreferenceRequirement] = Field(default_factory=list, description="Normalized preferences.")
    unresolved_fields: list[str] = Field(default_factory=list, description="Current-message clarification hints.")


class RequirementIssue(StrictModel):
    """描述输入、模型服务、输出契约或原文核验问题。"""

    code: IssueCode = Field(description="Machine-readable issue code.")
    field: str = Field(description="Field path associated with the issue.", min_length=1)
    message: str = Field(description="Safe English error message without secrets.", min_length=1)


class ParserMetadata(StrictModel):
    """记录本次结构化生成实际使用的模型和安全调用元数据。"""

    provider: str = Field(description="Model provider.", min_length=1)
    model: str = Field(description="Client-requested model ID.", min_length=1)
    reported_model: str | None = Field(default=None, description="Model name reported by the service.")
    request_id: str | None = Field(default=None, description="Service request ID.")
    usage: dict[str, int] = Field(default_factory=dict, description="Non-sensitive token usage.")


class RequirementResult(StrictModel):
    """封装标准化需求、问题和本次模型调用信息。"""

    requirement: NormalizedRequirement = Field(description="Strictly validated normalized requirement.")
    issues: list[RequirementIssue] = Field(default_factory=list, description="Non-fatal source-validation issues.")
    metadata: ParserMetadata = Field(description="Model provider, version, and usage.")


class InputGuardDecision(StrictModel):
    """表示输入结构是否有效以及是否属于 Falcon 找房范围。"""

    valid: bool = Field(description="Whether message structure and length are valid.")
    housing_related: bool = Field(description="Whether the message relates to Singapore housing.")


class ProfileChangeModel(StrictModel):
    """表示 LLM 解析结果转换得到、尚未合并的 profile patch。"""

    operation: Literal["set", "append", "remove"] = Field(description="Patch operation.")
    field: Literal[
        "intent",
        "user_context",
        "listing_constraints",
        "derived_data_requirements",
        "open_data_requirements",
        "unresolved",
    ] = Field(description="Allowed top-level ConversationProfile field.")
    value: Any = Field(description="JSON value matching the target-field contract.")
    source_message_id: str = Field(description="User-message ID that produced this change.", min_length=1)


class ConversationProfileModel(StrictModel):
    """表示 LangGraph 中可版本化、确认和持久化的 conversation 需求画像。"""

    profile_id: str = Field(description="Stable conversation-profile ID.", min_length=1)
    user_id: str = Field(description="User ID used only for ownership and authorization.", min_length=1)
    conversation_id: str = Field(description="Conversation ID aligned with the LangGraph thread_id.", min_length=1)
    version: int = Field(description="Version incremented after every successful patch merge.", ge=0)
    confirmed_version: int | None = Field(default=None, description="Last user-confirmed version.", ge=0)
    status: Literal["draft", "pending_confirmation", "confirmed", "idle"] = "draft"
    intent: Intent | None = Field(default=None, description="Rent or buy intent for this conversation.")
    user_context: list[dict[str, Any]] = Field(default_factory=list, description="Search-relevant user context.")
    listing_constraints: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Constraints on queryable Listing fields.",
    )
    derived_data_requirements: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Derived data that module B must retrieve or calculate.",
    )
    open_data_requirements: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Non-blocking best-effort requirements that do not map to known fields.",
    )
    unresolved: list[str] = Field(default_factory=list, description="Field paths that still require clarification.")
    field_sources: dict[str, str] = Field(default_factory=dict, description="Field-to-message-ID mapping.")
    created_at: str = Field(description="Profile creation time.", min_length=1)
    updated_at: str = Field(description="Most recent profile update time.", min_length=1)
    last_user_message_at: str = Field(description="Most recent inbound user-message time.", min_length=1)
    confirmed_at: str | None = Field(default=None, description="Most recent confirmation time.")


class RequirementConfirmationModel(StrictModel):
    """表示绑定特定 profile version 的用户确认请求。"""

    confirmation_id: str = Field(description="Confirmation-request ID.", min_length=1)
    profile_id: str = Field(description="Profile ID awaiting confirmation.", min_length=1)
    profile_version: int = Field(description="Profile version awaiting confirmation.", ge=1)
    summary: str = Field(description="Structured requirement summary shown to the user.", min_length=1)
    status: Literal["pending", "confirmed", "rejected", "cancelled"] = "pending"


class RequirementRequestModel(StrictModel):
    """表示 A 在用户确认后准备交给 B 的固定请求。"""

    request_id: str = Field(description="A-to-B request ID.", min_length=1)
    schema_version: Literal["0.3-draft"] = "0.3-draft"
    conversation_id: str = Field(description="Source conversation ID.", min_length=1)
    profile_id: str = Field(description="Source profile ID.", min_length=1)
    profile_version: int = Field(description="Confirmed profile version.", ge=1)
    intent: Intent = Field(description="Rent or buy intent.")
    user_context: list[dict[str, Any]] = Field(default_factory=list)
    listing_constraints: list[dict[str, Any]] = Field(default_factory=list)
    derived_data_requirements: list[dict[str, Any]] = Field(default_factory=list)
    open_data_requirements: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    confirmed_at: str = Field(description="User-confirmation time.", min_length=1)


class RequirementGraphState(TypedDict, total=False):
    """定义 A-side 需求理解、确认、持久化和 B 请求构造状态。"""

    current_input: str
    message_id: str
    user_id: str
    conversation_id: str
    profile: dict[str, Any]
    workflow_route: str
    input_guard: dict[str, Any]
    turn_intent: dict[str, Any]
    housing_question_answer: dict[str, Any]
    normalized_requirement: dict[str, Any]
    proposed_patch: list[dict[str, Any]]
    validated_patch: list[dict[str, Any]]
    confirmation: dict[str, Any] | None
    clarification_questions: list[dict[str, str]]
    requirement_request: dict[str, Any]
    assistant_response: str
    parser_metadata: dict[str, Any]
    requirement_issues: list[dict[str, Any]]
    search_request_id: str | None
    processed_turns: dict[str, dict[str, Any]]
    status: Literal[
        "new",
        "parsing",
        "awaiting_clarification",
        "awaiting_confirmation",
        "confirmed",
        "ready_for_b",
        "out_of_scope",
        "cancelled",
        "failed",
    ]
