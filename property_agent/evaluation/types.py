"""Types responsibilities extracted without changing behavior."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from property_agent.contracts import ConversationProfile, Coverage, EvaluationResult, Listing, RetrievalResult, ReviewIssue, RoutingPolicy, ScreenResult, QueryFeatures



DEFAULT_LLM_MODEL = "deepseek-v4-flash"


LLM_METHOD_VERSION = "deepseek-requirement-score-v3"


DETERMINISTIC_METHOD_VERSION = "deterministic-constraint-score-v1"


FRESHNESS_DAYS = 14


MAX_EVALUATION_CANDIDATES = 12


MAX_RECOMMENDATIONS = 10


class KeywordMatcherError(RuntimeError):
    """LLM 关键词匹配器不可用或返回了不符合约定的内容。"""

    def __init__(self, message: str, *, failure_kind: str = "INVALID_SCORE_SCHEMA") -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class EvaluationReviewModelError(RuntimeError):
    """LLM 评估或审查器不可用，或没有遵守固定 JSON 输出。"""

    def __init__(self, message: str, *, failure_kind: str = "MODEL_ERROR") -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


@dataclass(frozen=True)
class KeywordMatch:
    """单套房源的 LLM 语义评分结果。"""

    score: float
    matched_terms: list[str]


class KeywordMatcher(Protocol):
    """可注入的 LLM 匹配器；业务函数不携带 API client 或 API Key。"""

    async def match(self, query: QueryFeatures, listings: list[Listing]) -> dict[str, KeywordMatch]:
        """返回 listing_key -> 已验证的需求满足度分数。"""


@dataclass(frozen=True)
class EvaluationDecision:
    """LLM 给 evaluate 的选择与路线建议；会再经过本地约束验证。"""

    selected_listing_keys: list[str]
    enough_candidates: bool
    next_action: str
    next_reason_code: str
    summary: str
    limitations: list[str]


class EvaluationReviewModel(Protocol):
    """C 的评估与审查模型。可用 fake 实现注入测试，不把 API Key 传进业务函数。"""

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listings: list[Listing],
        coverage: Coverage,
        policy: RoutingPolicy,
    ) -> EvaluationDecision:
        """选择可展示的候选，并建议下一步。"""

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listings: list[Listing],
        policy: RoutingPolicy,
    ) -> list[ReviewIssue]:
        """独立核查 evaluation，返回固定 contract 的问题列表。"""
