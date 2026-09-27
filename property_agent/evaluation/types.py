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
    """The LLM keyword matcher is unavailable or returned content that does not conform to the contract."""

    def __init__(self, message: str, *, failure_kind: str = "INVALID_SCORE_SCHEMA") -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class EvaluationReviewModelError(RuntimeError):
    """The LLM evaluator or reviewer is unavailable, or did not adhere to the fixed JSON output."""

    def __init__(self, message: str, *, failure_kind: str = "MODEL_ERROR") -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


@dataclass(frozen=True)
class KeywordMatch:
    """The LLM semantic scoring result for a single listing."""

    score: float
    matched_terms: list[str]


class KeywordMatcher(Protocol):
    """An injectable LLM matcher; business functions do not carry an API client or API Key."""

    async def match(self, query: QueryFeatures, listings: list[Listing]) -> dict[str, KeywordMatch]:
        """Returns listing_key -> verified requirement satisfaction scores."""


@dataclass(frozen=True)
class EvaluationDecision:
    """The LLM's choice and route suggestions for evaluate; they will then undergo local constraint validation."""

    selected_listing_keys: list[str]
    enough_candidates: bool
    next_action: str
    next_reason_code: str
    summary: str
    limitations: list[str]


class EvaluationReviewModel(Protocol):
    """C's evaluation and review model. A fake implementation can be injected for testing, without passing the API Key into business functions."""

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listings: list[Listing],
        coverage: Coverage,
        policy: RoutingPolicy,
    ) -> EvaluationDecision:
        """Selects displayable candidates and suggests the next step."""

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listings: list[Listing],
        policy: RoutingPolicy,
    ) -> list[ReviewIssue]:
        """Independently verifies the evaluation and returns a list of issues under a fixed contract."""
