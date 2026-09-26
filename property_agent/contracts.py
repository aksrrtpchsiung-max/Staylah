"""Canonical shared business types. Runtime validation lives at module boundaries.

contracts_v0 re-exports these exact objects for legacy callers.
"""
from typing import Generic, Literal, TypeVar, TypedDict, Union

JsonValue = Union[None, bool, int, float, str, list['JsonValue'], dict[str, 'JsonValue']]
T = TypeVar('T')


class ContractViolation(ValueError):
    """纯函数的契约错误；code 为 INVALID_INPUT 或 INVALID_STATE。"""
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
    conversation_id: str  # MVP 中与 LangGraph thread_id 使用同一值
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
    """记录结构化字段所依据的逐字用户原文。"""
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
    """保存本 conversation 中与找房有关、但不属于房源事实的用户背景。"""
    field: Literal['household.occupant_count', 'household.has_children',
                   'household.planning_children', 'occupant.workplace',
                   'occupant.school']
    value: JsonValue
    source: SourceReference


class ListingConstraint(TypedDict):
    """表达用户对可查询 Listing 字段的期望，不伪造真实房源。"""
    constraint_id: str
    field_path: QueryableListingField
    operator: Literal['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'between',
                      'in', 'contains']
    value: JsonValue
    strength: Literal['hard', 'soft']
    priority: Literal['high', 'medium', 'low']
    source: SourceReference


class DerivedDataRequirement(TypedDict):
    """表达需要 B 通过数据库、地图或工具获取的派生数据。"""
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
    """保存非阻塞需求；即使 strength=hard，handling 也禁止 B 用它阻断核心结果。"""
    requirement_id: str
    description: str
    handling: Literal['best_effort']
    strength: Literal['hard', 'soft']
    priority: Literal['high', 'medium', 'low']
    source: SourceReference


class HardConstraints(TypedDict):
    """保存 B 内部 SearchPlan 使用的已解析过滤条件。"""
    currency: str
    max_price: int | None
    price_period: Literal['month', 'week', 'total'] | None
    rental_scope: Literal['whole_unit', 'room'] | None
    locations: list[str]  # 规范化地点 ID；空数组表示用户明确不限地区
    min_bedrooms: int | None


class ConversationProfile(TypedDict):
    """保存一个 conversation 独立拥有、可确认和恢复的找房需求画像。"""
    profile_id: str
    user_id: str  # 仅用于归属和权限，不表示跨 conversation 的长期用户画像
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
    field_sources: dict[str, str]  # 字段路径 -> 用户消息 ID
    created_at: str
    updated_at: str
    last_user_message_at: str
    confirmed_at: str | None


ProfilePatchField = Literal[
    'intent', 'user_context', 'listing_constraints',
    'derived_data_requirements', 'open_data_requirements', 'unresolved',
]


class ProfileChange(TypedDict):
    """描述 LLM 提议、尚未提交到 ConversationProfile 的单项修改。"""
    operation: Literal['set', 'append', 'remove']
    field: ProfilePatchField
    value: JsonValue
    source_message_id: str


class Clarification(TypedDict):
    """描述需要由 A 向用户提出的结构化澄清问题。"""
    field: str
    text: str


class RequirementConfirmation(TypedDict):
    """保存等待用户确认的需求摘要及其绑定版本。"""
    confirmation_id: str
    profile_id: str
    profile_version: int
    summary: str
    status: Literal['pending', 'confirmed', 'rejected', 'cancelled']


class OnboardResult(TypedDict):
    """返回候选 patch、草稿画像以及下一步澄清或确认动作。"""
    base_profile_version: int
    profile_patch: list[ProfileChange]
    draft_profile: ConversationProfile
    missing_required_fields: list[str]
    questions: list[Clarification]
    confirmation: RequirementConfirmation | None
    next_action: Literal['ask_clarification', 'ask_confirmation']


class ConfirmationReply(TypedDict):
    """表示用户对指定需求版本的确认、修改或取消。"""
    confirmation_id: str
    message_id: str
    action: Literal['confirm', 'correct', 'cancel']
    text: str | None


class ConfirmationResult(TypedDict):
    """返回确认后的画像，或需要再次确认的修订草稿。"""
    profile: ConversationProfile
    ready_for_handoff: bool
    confirmation: RequirementConfirmation | None
    questions: list[Clarification]


class RequirementRequest(TypedDict):
    """定义 A 在用户确认后发送给 B 的唯一公开请求。"""
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
    page_limit: int  # 整个 search 调用的总页数上限
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
    """说明 B 对 A 所提交数据需求的覆盖情况。"""
    fulfilled_requirement_ids: list[str]
    unsupported_requirement_ids: list[str]
    unverified_requirement_ids: list[str]
    skipped_best_effort_requirement_ids: list[str]


class RequirementFulfillment(TypedDict):
    """定义 B 的统一响应；跳过开放需求不得阻止返回已匹配房源。"""
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
    # C 的 evaluate 由模型基于 screen/retrieve 决定的建议路线。实际执行前仍会由
    # review 和 decide_next 做状态、次数和用户意愿的边界检查。
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
    # 编排层从 EvaluationResult.assessment 复制而来；None 表示旧调用方尚未提供。
    evaluation_next_action: Literal['publish', 'research', 'ask_user', 'finish'] | None
    evaluation_next_reason_code: str | None


class RouteDecision(TypedDict):
    action: Literal['publish', 'research', 'repair', 'ask_user', 'finish', 'stop']
    reason_code: str
    search_directive: SearchDirective | None
    pending_question: PendingQuestion | None
