"""Canonical shared business types. Runtime validation lives at module boundaries.

All active modules import these exact types; retired root aliases are removed.
"""
from typing import Generic, Literal, TypeVar, TypedDict, Union

JsonValue = Union[None, bool, int, float, str, list['JsonValue'], dict[str, 'JsonValue']]
T = TypeVar('T')


class ContractViolation(ValueError):
    """Contract error for a pure function; code is INVALID_INPUT or INVALID_STATE."""
    def __init__(self, code: str, field_path: str, message: str) -> None:
        self.code = code
        self.field_path = field_path
        super().__init__(message)


class Issue(TypedDict):
    code: Literal['INVALID_INPUT', 'INVALID_OUTPUT', 'INVALID_STATE',
                  'TIMEOUT', 'RATE_LIMITED', 'AUTH_REQUIRED', 'SOURCE_UNAVAILABLE',
                  'TEMPORARY_UNAVAILABLE', 'PARSE_ERROR', 'MODEL_UNAVAILABLE',
                  'CONSTRAINT_CHANGE_NOT_ALLOWED', 'NO_NEW_QUERY',
                  'BUDGET_EXHAUSTED', 'STATE_CONFLICT', 'INTERNAL_ERROR',
                  'PERSISTENCE_ERROR', 'RETRIEVAL_DEGRADED']
    message: str
    field_path: str | None
    source: str | None
    retryable: bool
    retry_after_seconds: int | None


class ResultMeta(TypedDict):
    trace_id: str
    call_id: str
    duration_ms: int


class Result(TypedDict, Generic[T]):
    status: Literal['success', 'partial', 'error']
    data: T | None
    issues: list[Issue]
    meta: ResultMeta


class RunContext(TypedDict):
    user_id: str
    run_id: str
    conversation_id: str  # In the MVP, uses the same value as the LangGraph thread_id
    attempt_id: str | None
    trace_id: str
    call_id: str
    deadline_at: str
    source_mode: Literal['mock', 'live']


class UserMessage(TypedDict):
    message_id: str
    text: str


class ChatMessage(TypedDict):
    message_id: str
    role: Literal['user', 'assistant']
    text: str


class SourceReference(TypedDict):
    """Records the verbatim user original text on which the structured fields are based."""
    message_id: str
    text: str
    start: int
    end: int


QueryableListingField = Literal[
    'transaction_type',
    'price.amount', 'price.currency', 'price.period',
    'attributes.property_type', 'attributes.unit_layout',
    'attributes.listing_scope', 'attributes.area_sqft',
    'attributes.bathrooms', 'attributes.room_type',
    'attributes.ensuite_bathroom', 'attributes.owner_stays',
    'attributes.cooking_policy', 'attributes.utilities_included',
    'attributes.wifi_included', 'attributes.visitors_allowed',
    'attributes.pets_allowed', 'attributes.furnishing',
    'attributes.tenure_type', 'attributes.lease_years',
    'bedrooms', 'listed_date',
]


class ProfileFact(TypedDict):
    """Stores user background related to house hunting in this conversation but not constituting listing facts."""
    field: Literal['household.occupant_count', 'household.has_children',
                   'household.planning_children', 'occupant.workplace',
                   'occupant.school']
    value: JsonValue
    source: SourceReference


class ListingConstraint(TypedDict):
    """Expresses the user's expectations for queryable Listing fields, without fabricating real listings."""
    constraint_id: str
    field_path: QueryableListingField
    operator: Literal['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'between',
                      'in', 'contains']
    value: JsonValue
    strength: Literal['hard', 'soft']
    priority: Literal['high', 'medium', 'low']
    source: SourceReference


class DerivedDataRequirement(TypedDict):
    """Expresses derived data that B needs to obtain through databases, maps, or tools."""
    requirement_id: str
    category: Literal['commute', 'nearby_amenity', 'environment', 'accessibility']
    target: str | None
    metric: str
    operator: Literal['eq', 'lte', 'gte', 'between', 'minimize', 'maximize',
                      'preferred']
    value: JsonValue
    unit: str | None
    strength: Literal['hard', 'soft']
    priority: Literal['high', 'medium', 'low']
    source: SourceReference


class OpenDataRequirement(TypedDict):
    """Stores non-blocking requirements; even when strength=hard, handling forbids B from using it to block core results."""
    requirement_id: str
    description: str
    handling: Literal['best_effort']
    strength: Literal['hard', 'soft']
    priority: Literal['high', 'medium', 'low']
    source: SourceReference


class HardConstraints(TypedDict):
    """Stores the parsed filter conditions used by B's internal SearchPlan."""
    currency: str
    max_price: int | None
    price_period: Literal['month', 'week', 'total'] | None
    rental_scope: Literal['whole_unit', 'room'] | None
    locations: list[str]  # Normalized location IDs; an empty array means the user explicitly does not restrict the area
    min_bedrooms: int | None


class ConversationProfile(TypedDict):
    """Stores a house-hunting requirement profile independently owned by one conversation, which can be confirmed and restored."""
    profile_id: str
    user_id: str  # Used only for ownership and permissions; does not represent a long-term user profile across conversations
    conversation_id: str
    version: int
    confirmed_version: int | None
    status: Literal['draft', 'pending_confirmation', 'confirmed', 'idle']
    intent: Literal['rent', 'buy'] | None
    user_context: list[ProfileFact]
    listing_constraints: list[ListingConstraint]
    derived_data_requirements: list[DerivedDataRequirement]
    open_data_requirements: list[OpenDataRequirement]
    unresolved: list[str]
    field_sources: dict[str, str]  # Field path -> user message ID
    created_at: str
    updated_at: str
    last_user_message_at: str
    confirmed_at: str | None


ProfilePatchField = Literal[
    'intent', 'user_context', 'listing_constraints',
    'derived_data_requirements', 'open_data_requirements', 'unresolved',
]


class ProfileChange(TypedDict):
    """Describes a single modification proposed by the LLM but not yet committed to the ConversationProfile."""
    operation: Literal['set', 'append', 'remove']
    field: ProfilePatchField
    value: JsonValue
    source_message_id: str


class Clarification(TypedDict):
    """Describes a structured clarification question that A needs to ask the user."""
    field: str
    text: str


class RequirementConfirmation(TypedDict):
    """Stores the requirement summary awaiting user confirmation and its bound version."""
    confirmation_id: str
    profile_id: str
    profile_version: int
    summary: str
    status: Literal['pending', 'confirmed', 'rejected', 'cancelled']


class OnboardResult(TypedDict):
    """Returns candidate patches, the draft profile, and the next clarification or confirmation action."""
    base_profile_version: int
    profile_patch: list[ProfileChange]
    draft_profile: ConversationProfile
    missing_required_fields: list[str]
    questions: list[Clarification]
    confirmation: RequirementConfirmation | None
    next_action: Literal['ask_clarification', 'ask_confirmation']


class ConfirmationReply(TypedDict):
    """Represents the user's confirmation, modification, or cancellation of the specified requirement version."""
    confirmation_id: str
    message_id: str
    action: Literal['confirm', 'correct', 'cancel']
    text: str | None


class ConfirmationResult(TypedDict):
    """Returns the confirmed profile, or a revised draft that needs confirmation again."""
    profile: ConversationProfile
    ready_for_handoff: bool
    confirmation: RequirementConfirmation | None
    questions: list[Clarification]


class RequirementRequest(TypedDict):
    """Defines the only public request that A sends to B after user confirmation."""
    request_id: str
    schema_version: Literal['0.3-draft']
    conversation_id: str
    profile_id: str
    profile_version: int
    intent: Literal['rent', 'buy']
    user_context: list[ProfileFact]
    listing_constraints: list[ListingConstraint]
    derived_data_requirements: list[DerivedDataRequirement]
    open_data_requirements: list[OpenDataRequirement]
    unresolved_fields: list[str]
    confirmed_at: str


class Entity(TypedDict):
    type: Literal['location', 'project', 'station', 'landmark']
    raw_text: str
    canonical_id: str | None
    aliases: list[str]


class QueryFeatures(TypedDict):
    profile_version: int
    entities: list[Entity]
    semantic_query: str
    unresolved: list[Clarification]


class NextPage(TypedDict):
    kind: Literal['next_page']
    query_id: str
    cursor: str


class AliasQuery(TypedDict):
    kind: Literal['alias_query']
    entity_id: str
    alias: str


class AlternateSource(TypedDict):
    kind: Literal['alternate_source']
    source: str


class SearchDirective(TypedDict):
    reason_code: Literal['insufficient_candidates', 'incomplete_coverage']
    strategy_changes: list[NextPage | AliasQuery | AlternateSource]
    base_profile_version: int
    evidence_listing_keys: list[str]


class SearchQuery(TypedDict):
    query_id: str
    source: str
    text: str
    cursor: str | None


class SearchPlan(TypedDict):
    plan_id: str
    profile_version: int
    attempt_id: str
    intent: Literal['rent', 'buy']
    required_filters: HardConstraints
    queries: list[SearchQuery]
    page_limit: int  # Upper limit on the total number of pages for the entire search call
    candidate_limit: int
    source_mode: Literal['mock', 'live']
    reason: str


class AttemptSummary(TypedDict):
    attempt_id: str
    query_fingerprints: list[str]
    status: Literal['success', 'partial', 'error']
    eligible_count: int


class Evidence(TypedDict):
    evidence_id: str
    field: str
    value: JsonValue
    source_url: str | None
    observed_at: str
    excerpt: str


class Price(TypedDict):
    amount: int | None
    currency: str
    period: Literal['month', 'week', 'total'] | None
    status: Literal['known', 'unknown', 'conflict']
    evidence_ids: list[str]


class ListingAttributes(TypedDict):
    property_type: Literal['hdb', 'condo', 'landed', 'apartment', 'other', 'unknown']
    unit_layout: str | None
    listing_scope: Literal['whole_unit', 'room', 'bedspace'] | None
    area_sqft: int | None
    bathrooms: int | None
    room_type: Literal['master', 'common', 'shared', 'unknown']
    ensuite_bathroom: bool | None
    owner_stays: bool | None
    cooking_policy: Literal['none', 'light', 'full', 'unknown']
    utilities_included: bool | None
    wifi_included: bool | None
    visitors_allowed: bool | None
    pets_allowed: bool | None
    furnishing: Literal['fully', 'partially', 'unfurnished', 'unknown']
    tenure_type: Literal['freehold', 'leasehold', 'unknown']
    lease_years: int | None


class Listing(TypedDict):
    listing_key: str
    source: str
    source_listing_id: str | None
    source_url: str | None
    source_mode: Literal['mock', 'live']
    title: str
    transaction_type: Literal['rent', 'sale']
    price: Price
    attributes: ListingAttributes
    bedrooms: int | None
    location_id: str | None
    listing_status: Literal['active', 'inactive', 'unknown']
    listed_date: str | None
    fetched_at: str
    source_updated_at: str | None
    last_verified_at: str | None
    raw_description: str | None
    raw_details: list[str]
    evidence: list[Evidence]
    field_issues: list[str]


class Coverage(TypedDict):
    queried_sources: list[str]
    failed_sources: list[str]
    queries_completed: bool
    has_more: bool
    next_pages: list[NextPage]
    truncated: bool
    applied_filters: list[str]
    unsupported_filters: list[str]


class SearchResult(TypedDict):
    plan_id: str
    profile_version: int
    items: list[Listing]
    coverage: Coverage


class RequirementCoverage(TypedDict):
    """Describes B's coverage of the data requirements submitted by A."""
    fulfilled_requirement_ids: list[str]
    unsupported_requirement_ids: list[str]
    unverified_requirement_ids: list[str]
    skipped_best_effort_requirement_ids: list[str]


class RequirementFulfillment(TypedDict):
    """Defines B's unified response; skipping open requirements must not prevent returning already matched listings."""
    request_id: str
    profile_version: int
    status: Literal['completed', 'partial', 'needs_clarification']
    search_result: SearchResult | None
    coverage: RequirementCoverage
    clarification_questions: list[Clarification]


class ConstraintCheck(TypedDict):
    field: str
    status: Literal['pass', 'fail', 'unknown']
    reason: str
    evidence_ids: list[str]


class ScreenedListing(TypedDict):
    listing_key: str
    checks: list[ConstraintCheck]


class ScreenResult(TypedDict):
    profile_version: int
    eligible: list[ScreenedListing]
    rejected: list[ScreenedListing]
    needs_verification: list[ScreenedListing]


class RetrievalCandidate(TypedDict):
    listing_key: str
    exact_matches: list[str]
    vector_score: float | None
    keyword_score: float | None
    retrieval_rank: int
    retrieval_score: float | None


class RetrievalResult(TypedDict):
    profile_version: int
    candidates: list[RetrievalCandidate]
    input_count: int
    returned_count: int
    truncated: bool
    method_version: str


class ListingSnapshot(TypedDict):
    snapshot_id: str
    profile_version: int
    items: list[Listing]


class Claim(TypedDict):
    kind: Literal['fact', 'judgment']
    text: str
    evidence_ids: list[str]


class RecommendationItem(TypedDict):
    listing_key: str
    rank: int
    reasons: list[Claim]
    tradeoffs: list[Claim]
    unknowns: list[str]


class Recommendation(TypedDict):
    ordered_items: list[RecommendationItem]
    summary: str
    limitations: list[str]


class RelaxationProposal(TypedDict):
    proposal_id: str
    field: str
    old_value: JsonValue
    proposed_value: JsonValue
    reason: str
    evidence_listing_keys: list[str]
    requires_user_confirmation: Literal[True]


class Assessment(TypedDict):
    constraint_findings: list[str]
    search_directive: SearchDirective | None
    relaxation_proposals: list[RelaxationProposal]
    # C's evaluate is the suggested route decided by the model based on screen/retrieve. Before actual execution, it will still be
    # checked by review and decide_next for boundaries on state, counts, and user intent.
    next_action: Literal['publish', 'research', 'ask_user', 'finish']
    next_reason_code: str


class EvaluationResult(TypedDict):
    profile_version: int
    snapshot_id: str
    recommendation: Recommendation
    assessment: Assessment


class ReviewIssue(TypedDict):
    code: Literal['UNKNOWN_LISTING', 'UNSUPPORTED_CLAIM', 'HARD_CONSTRAINT_VIOLATION',
                  'INVALID_RANK', 'TOO_MANY_ITEMS', 'MISSING_LIMITATION', 'STALE_EVIDENCE']
    listing_key: str | None
    field_path: str
    message: str
    severity: Literal['blocking', 'warning']
    suggested_fix: str


class ReviewResult(TypedDict):
    passed: bool
    issues: list[ReviewIssue]


class RoutingPolicy(TypedDict):
    min_matches: int
    display_limit: int
    max_search_attempts: int
    max_repairs: int


class PendingQuestion(TypedDict):
    question_id: str
    text: str
    reason_code: str
    proposals: list[RelaxationProposal]
    allowed_actions: list[Literal['answer', 'accept_proposal', 'decline', 'cancel']]
    base_profile_version: int
    state_version: int


class DecisionState(TypedDict):
    run_id: str
    state_version: int
    profile_version: int
    current_profile_version: int
    cancelled: bool
    user_declined: bool
    deadline_exhausted: bool
    search_status: Literal['not_started', 'success', 'partial', 'error']
    search_attempts_used: int
    repairs_used: int
    eligible_count: int
    review: ReviewResult | None
    failure_code: str | None
    search_directive: SearchDirective | None
    pending_question: PendingQuestion | None
    # Copied by the orchestration layer from EvaluationResult.assessment; None means the old caller has not yet provided it.
    evaluation_next_action: Literal['publish', 'research', 'ask_user', 'finish'] | None
    evaluation_next_reason_code: str | None


class RouteDecision(TypedDict):
    action: Literal['publish', 'research', 'repair', 'ask_user', 'finish', 'stop']
    reason_code: str
    search_directive: SearchDirective | None
    pending_question: PendingQuestion | None
