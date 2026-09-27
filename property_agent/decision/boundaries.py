"""The seam between this module and the outside: module C, module B, profile writing, and persistence.

The Protocol only describes the call shapes we depend on; implementations are provided by their respective owners. `evaluate` and `review`
signatures are exactly consistent with property_agent.contracts, and once C is complete the stub should be directly replaceable.

AttemptOutcome and NextRunRequest are **newly added internal delivery objects** and are not part of the frozen public contract;
they are passed only within module A and between it and the run control layer, and whether to make them public will be decided after interface review.
"""
from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict

from property_agent.contracts import (
    Coverage,
    AttemptSummary,
    EvaluationResult,
    ListingSnapshot,
    Recommendation,
    RelaxationProposal,
    RequirementCoverage,
    Result,
    RetrievalResult,
    ReviewResult,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    ConversationProfile,
)


class EvaluationModule(Protocol):
    """Module C: evaluation and review. Neither method writes to the business database, nor decides the next step on its own."""

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result: ...

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result: ...


class AttemptOutcome(TypedDict):
    """The result set of one search attempt (internal object).

    attempt_id is assigned by the shared run control layer; the decision section of module A only consumes it and does not generate it or reset the count on its own.
    """

    attempt_id: str
    search_status: str  # success | partial | error
    failure_code: str | None
    listing_snapshot: ListingSnapshot | None
    screen_result: ScreenResult | None
    retrieval_result: RetrievalResult | None
    coverage: Coverage | None
    requirement_coverage: NotRequired[RequirementCoverage | None]
    attempt_summary: NotRequired[AttemptSummary]


class SearchRunner(Protocol):
    """The module B side: execute the next attempt according to the supplementary search instruction that preserves the hard conditions."""

    async def run_attempt(
        self,
        directive: SearchDirective,
        profile: ConversationProfile,
        *,
        previous_attempts: list[AttemptSummary],
        ctx: RunContext,
    ) -> Result: ...


class ProfileVersionConflict(Exception):
    """The profile has been modified by another operation; the old answer cannot overwrite the new requirement."""


class ProfileWriter(Protocol):
    """Profile writing is handled by module A's profile service; this section is called only after the user explicitly accepts the proposal."""

    def current_version(self, profile_id: str) -> int: ...

    def apply_relaxation(
        self,
        profile_id: str,
        *,
        base_version: int,
        proposal: RelaxationProposal,
        source_message_id: str,
        op_key: str,
    ) -> ConversationProfile:
        """Idempotent by op_key; raises ProfileVersionConflict when base_version does not match the current version."""
        ...


class NextRunRequest(TypedDict):
    """The new task request handed to the run control layer when the old run is superseded (internal object)."""

    reason_code: str
    profile_id: str
    profile_version: int
    superseded_run_id: str
    accepted_proposal_id: str | None
    source_message_id: str | None


class RecommendationRepository(Protocol):
    def save(
        self, run_id: str, recommendation: Recommendation, *, op_key: str
    ) -> tuple[str, bool]:
        """Returns (final_result_id, whether it is a replay). A replay must not insert the recommendation again."""
        ...


class RunRepository(Protocol):
    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None: ...


class QuestionRepository(Protocol):
    def save_question(self, run_id: str, question: dict) -> dict:
        """Idempotently saved by question_id; rerunning a node does not ask the question again."""
        ...

    def mark_answered(
        self,
        run_id: str,
        question_id: str,
        *,
        client_message_id: str | None = None,
        answer_text: str | None = None,
    ) -> bool:
        """Returns True on the first consumption and save of the answer; returns False for a duplicate answer or an old question."""
        ...
