"""跨模块共享的数据契约（2026-09-14 版）。

本文件是 `mock_property_data 2/contracts/contracts_v0.py` 类型部分的同步副本，
只保留类型，不含九个函数的未实现签名。相对 `docs/contracts_v0.py` 的破坏性差异：
金额为整数、`Price.scope` 已移除（改用 `attributes.listing_scope`）、
`Listing.attributes` 必填、`RunContext` 新增 `user_id`。

TypedDict 只提供静态类型，不做运行时校验；运行时校验在各模块的边界完成。
`tests/test_contracts_sync.py` 负责检测本副本与权威文件的漂移。
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


class HardConstraints(TypedDict):
    currency: str
    max_price: int | None
    price_period: Literal['month', 'week', 'total'] | None
    rental_scope: Literal['whole_unit', 'room'] | None
    locations: list[str]  # 规范化地点 ID；空数组表示用户明确不限地区
    min_bedrooms: int | None


class Preference(TypedDict):
    field: str
    value: JsonValue
    priority: Literal['high', 'medium', 'low']
    source_message_id: str


class UserProfile(TypedDict):
    profile_id: str
    version: int
    intent: Literal['rent', 'buy'] | None
    hard_constraints: HardConstraints
    preferences: list[Preference]
    unresolved: list[str]
    field_sources: dict[str, str]  # 字段路径 -> 用户消息 ID


class ProfileChange(TypedDict):
    field: str
    value: JsonValue
    source_message_id: str


class Clarification(TypedDict):
    field: str
    text: str


class OnboardResult(TypedDict):
    base_profile_version: int
    profile_patch: list[ProfileChange]
    missing_required_fields: list[str]
    questions: list[Clarification]
    ready_for_search: bool


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


class RouteDecision(TypedDict):
    action: Literal['publish', 'research', 'repair', 'ask_user', 'finish', 'stop']
    reason_code: str
    search_directive: SearchDirective | None
    pending_question: PendingQuestion | None
