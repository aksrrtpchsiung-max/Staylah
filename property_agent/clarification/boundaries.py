"""The seam between the follow-up module and the model and onboarding."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from property_agent.clarification.models import AnswerInterpretation
from property_agent.contracts import PendingQuestion
if TYPE_CHECKING:
    from property_agent.decision.boundaries import NextRunRequest


class QuestionPolisher(Protocol):
    async def polish(self, question: PendingQuestion) -> str:
        """Only polish the question text; must not change the proposal, action, version, or identifier."""
        ...


class AnswerInterpreter(Protocol):
    async def interpret(
        self, text: str, question: PendingQuestion
    ) -> AnswerInterpretation:
        """Classify the natural language answer; the return value must still be re-verified by the server against the current question."""
        ...


class OnboardingHandoff(Protocol):
    def build_request(
        self,
        *,
        profile_id: str,
        profile_version: int,
        superseded_run_id: str,
        source_message_id: str | None,
    ) -> "NextRunRequest":
        """Construct the request handed to the external onboarding/runtime control layer."""
        ...
