"""Strict shared model used when the LLM directly outputs standardized requirements."""

from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Uniformly enable the unknown-field rejection policy for requirement understanding models."""

    model_config = ConfigDict(extra="forbid")


class Intent(str, Enum):
    """Indicates that the user wishes to rent or purchase a residence."""

    RENT = "rent"
    BUY = "buy"


class ConstraintStrength(str, Enum):
    """Distinguishes hard constraints that cannot be automatically relaxed from soft constraints that allow trade-offs."""

    HARD = "hard"
    SOFT = "soft"


class PricePeriod(str, Enum):
    """Indicates the pricing period for the standard amount."""

    MONTH = "month"
    WEEK = "week"
    TOTAL = "total"


class RentalScope(str, Enum):
    """Indicates the whole-unit, single-room, or bed-space scope."""

    WHOLE_UNIT = "whole_unit"
    ROOM = "room"
    BEDSPACE = "bedspace"


class PropertyType(str, Enum):
    """Indicates the standardized Singapore residential property type."""

    HDB = "hdb"
    CONDO = "condo"
    LANDED = "landed"
    APARTMENT = "apartment"
    OTHER = "other"


class NumericOperator(str, Enum):
    """Indicates the exact, lower-bound, or upper-bound semantics of a numeric constraint."""

    EQ = "eq"
    GTE = "gte"
    LTE = "lte"


class LocationRelation(str, Enum):
    """Distinguishes being located within a place from being located near a place."""

    IN = "in"
    NEAR = "near"


class LocationResolutionStatus(str, Enum):
    """Indicates whether the place entity has already been resolved by an external place service."""

    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


class TravelMode(str, Enum):
    """Indicates the standardized transportation mode."""

    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"
    CYCLING = "cycling"
    UNKNOWN = "unknown"


class DestinationType(str, Enum):
    """Indicates the business category of the commute destination."""

    MRT = "mrt"
    STATION = "station"
    LANDMARK = "landmark"
    WORKPLACE = "workplace"
    SCHOOL = "school"
    UNKNOWN = "unknown"


class PreferencePriority(str, Enum):
    """Indicates the relative priority of a soft preference or additional condition."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PreferenceTopic(str, Enum):
    """Restricts the preference topics that the recommendation system can currently consume reliably."""

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
    """Restricts the search-related user background fields that this conversation can store."""

    OCCUPANT_COUNT = "household.occupant_count"
    HAS_CHILDREN = "household.has_children"
    PLANNING_CHILDREN = "household.planning_children"
    WORKPLACE = "occupant.workplace"
    SCHOOL = "occupant.school"


class IssueCode(str, Enum):
    """Enumerates the issues that may arise during the direct requirement understanding stage."""

    INVALID_INPUT = "invalid_input"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    UNSUPPORTED_SOURCE = "unsupported_source"
    INVALID_PATCH = "invalid_patch"
    STATE_CONFLICT = "state_conflict"
    PERSISTENCE_ERROR = "persistence_error"


class ProcessingStatus(str, Enum):
    """Indicates the final execution status of the requirement understanding node."""

    NORMALIZED = "normalized"
    FAILED = "failed"


class SourceSpan(StrictModel):
    """Records the verbatim evidence of standardized fields in the original user message."""

    message_id: str = Field(description="ID of the user message that produced the field.", min_length=1)
    text: str = Field(description="Verbatim input span supporting the field.", min_length=1)
    start: int = Field(description="Inclusive start offset of the input span.", ge=0)
    end: int = Field(description="Exclusive end offset of the input span.", ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "SourceSpan":
        """Ensures that the source-text index is positive and consistent with the stored text length."""

        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        if self.end - self.start != len(self.text):
            raise ValueError("source span length must match source text")
        return self


class IntentConstraint(StrictModel):
    """Indicates a standard transaction intent with hard/soft attributes and source-text evidence."""

    value: Intent = Field(description="Rent or buy intent.")
    strength: ConstraintStrength = Field(description="Whether the intent is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the intent.")


class RentalScopeConstraint(StrictModel):
    """Indicates a standard rental scope with hard/soft attributes and source-text evidence."""

    value: RentalScope = Field(description="Whole unit, private room, or bedspace.")
    strength: ConstraintStrength = Field(description="Whether the rental scope is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the rental scope.")


class PropertyTypeConstraint(StrictModel):
    """Indicates a standard residential property type with hard/soft attributes and source-text evidence."""

    value: PropertyType = Field(description="Normalized residential property type.")
    strength: ConstraintStrength = Field(description="Whether the property type is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the property type.")


class MoneyConstraint(StrictModel):
    """Indicates a budget cap already converted by the LLM into an integer and standard period."""

    currency: Literal["SGD"] = Field(description="Currency, fixed to SGD for this project.")
    max_price: int = Field(description="Normalized integer budget ceiling.", gt=0)
    period: PricePeriod | None = Field(default=None, description="Monthly, weekly, or total period.")
    approximate: bool = Field(description="Whether the user expressed the amount approximately.")
    strength: ConstraintStrength = Field(description="Whether the budget is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the budget.")


class NumericConstraint(StrictModel):
    """Indicates a numeric constraint already converted by the LLM into an integer and comparison operator."""

    operator: NumericOperator = Field(description="Equality, minimum, or maximum operator.")
    value: int = Field(description="Normalized non-negative integer.", ge=0)
    strength: ConstraintStrength = Field(description="Whether the numeric condition is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence for the numeric condition.")


class LocationRequirement(StrictModel):
    """Stores the place entities and relationships extracted by the LLM, pending resolution by an external place service."""

    raw_name: str = Field(description="Location entity from the user input, without an invented ID.", min_length=1)
    relation: LocationRelation = Field(description="Whether the home should be in or near the location.")
    resolution_status: Literal[LocationResolutionStatus.UNRESOLVED] = Field(
        default=LocationResolutionStatus.UNRESOLVED,
        description="Fixed to unresolved during the LLM stage.",
    )
    strength: ConstraintStrength = Field(description="Whether the location condition is hard or soft.")
    source: SourceSpan = Field(description="User-input evidence including the location relation.")


class CommuteRequirement(StrictModel):
    """Indicates a commute requirement already converted by the LLM into an enumeration and minutes."""

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
    """Indicates an additional requirement already mapped by the LLM to a stable topic vocabulary."""

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
    """Indicates the conversation user background related to home searching extracted from the current round of input."""

    field: ProfileFactField = Field(description="Controlled user-context field.")
    value: Any = Field(description="JSON value for the selected field.")
    source: SourceSpan = Field(description="Verbatim user-input evidence for the profile fact.")


class NormalizedRequirement(StrictModel):
    """Indicates standardized user requirements directly produced by the LLM and validated by Pydantic."""

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
    """Describes input, model service, output contract, or source-text verification issues."""

    code: IssueCode = Field(description="Machine-readable issue code.")
    field: str = Field(description="Field path associated with the issue.", min_length=1)
    message: str = Field(description="Safe English error message without secrets.", min_length=1)


class ParserMetadata(StrictModel):
    """Records the model and safe invocation metadata actually used for this structured generation."""

    provider: str = Field(description="Model provider.", min_length=1)
    model: str = Field(description="Client-requested model ID.", min_length=1)
    reported_model: str | None = Field(default=None, description="Model name reported by the service.")
    request_id: str | None = Field(default=None, description="Service request ID.")
    usage: dict[str, int] = Field(default_factory=dict, description="Non-sensitive token usage.")


class RequirementResult(StrictModel):
    """Encapsulates the standardized requirements, issues, and model invocation information for this round."""

    requirement: NormalizedRequirement = Field(description="Strictly validated normalized requirement.")
    issues: list[RequirementIssue] = Field(default_factory=list, description="Non-fatal source-validation issues.")
    metadata: ParserMetadata = Field(description="Model provider, version, and usage.")


class InputGuardDecision(StrictModel):
    """Indicates whether the input structure is valid and whether it falls within the Falcon home-searching scope."""

    valid: bool = Field(description="Whether message structure and length are valid.")
    housing_related: bool = Field(description="Whether the message relates to Singapore housing.")


class ProfileChangeModel(StrictModel):
    """Indicates a profile patch obtained from converting the LLM parsing result and not yet merged."""

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
    """Indicates a conversation requirement profile in LangGraph that can be versioned, confirmed, and persisted."""

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
    """Indicates a user confirmation request bound to a specific profile version."""

    confirmation_id: str = Field(description="Confirmation-request ID.", min_length=1)
    profile_id: str = Field(description="Profile ID awaiting confirmation.", min_length=1)
    profile_version: int = Field(description="Profile version awaiting confirmation.", ge=1)
    summary: str = Field(description="Structured requirement summary shown to the user.", min_length=1)
    status: Literal["pending", "confirmed", "rejected", "cancelled"] = "pending"


class RequirementRequestModel(StrictModel):
    """Indicates the fixed request that A prepares to hand to B after user confirmation."""

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
    """Defines the A-side requirement understanding, confirmation, persistence, and B request construction states."""

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
