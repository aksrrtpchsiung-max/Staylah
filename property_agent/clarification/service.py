"""Deterministic safety shell for the follow-up questioning flow."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from property_agent.clarification.boundaries import AnswerInterpreter, QuestionPolisher
from property_agent.clarification.models import AnswerInterpretation
from property_agent.contracts import PendingQuestion

_ACCEPT_RE = re.compile(
    r"\b(?:accept|agree|sure|okay|alright|fine|no problem|increase|relax|raise|yes|ok)\b",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"\b(?:do not accept|do not agree|do not adjust|do not relax|do not raise|keep the original conditions|keep as is|forget it|no thanks)\b|\bno\b(?!\s+problem)",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"\b(?:cancel(?: house hunting| task| search)?|stop(?: house hunting| task| search)?|end task|stop looking)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClarificationAgent:
    """Combine model adapters and apply non-bypassable programmatic validation to the output."""

    polisher: QuestionPolisher
    interpreter: AnswerInterpreter

    async def prepare_question(self, question: PendingQuestion) -> PendingQuestion:
        try:
            text = (await self.polisher.polish(question)).strip()
        except Exception:
            return question
        if not _preserves_proposal_values(text, question):
            return question
        return {**question, "text": text}

    async def parse_answer(
        self,
        *,
        text: str,
        question: PendingQuestion,
        client_message_id: str | None,
    ) -> dict[str, Any]:
        clean = text.strip()
        if not clean:
            interpretation = AnswerInterpretation(action="answer")
        else:
            try:
                interpretation = await self.interpreter.interpret(clean, question)
            except Exception:
                interpretation = deterministic_interpret(clean, question)
        interpretation = _sanitize_interpretation(clean, question, interpretation)

        answer: dict[str, Any] = {
            "question_id": question["question_id"],
            "expected_state_version": question["state_version"],
            "action": interpretation.action,
        }
        if client_message_id:
            answer["client_message_id"] = client_message_id
        answer["answer"] = clean
        if interpretation.action == "accept_proposal":
            answer["proposal_id"] = interpretation.proposal_id
        return answer


def deterministic_interpret(
    text: str, question: PendingQuestion
) -> AnswerInterpretation:
    """Conservative classification when network or structured output fails."""
    if _CANCEL_RE.search(text):
        return AnswerInterpretation(action="cancel")
    if _DECLINE_RE.search(text):
        return AnswerInterpretation(action="decline")
    proposals = question.get("proposals") or []
    if len(proposals) == 1 and _ACCEPT_RE.search(text):
        return AnswerInterpretation(
            action="accept_proposal", proposal_id=proposals[0]["proposal_id"]
        )
    return AnswerInterpretation(action="answer")


def _sanitize_interpretation(
    text: str,
    question: PendingQuestion,
    interpretation: AnswerInterpretation,
) -> AnswerInterpretation:
    """The model must not turn vague wording or unknown proposals into authorized actions."""
    if interpretation.action == "cancel":
        return (
            interpretation
            if _CANCEL_RE.search(text)
            else AnswerInterpretation(action="answer")
        )
    if interpretation.action == "decline":
        return (
            interpretation
            if _DECLINE_RE.search(text)
            else AnswerInterpretation(action="answer")
        )
    if interpretation.action != "accept_proposal":
        return AnswerInterpretation(action="answer")

    proposals = question.get("proposals") or []
    known_ids = {proposal["proposal_id"] for proposal in proposals}
    if (
        len(proposals) != 1
        or interpretation.proposal_id not in known_ids
        or not _ACCEPT_RE.search(text)
        or _DECLINE_RE.search(text)
    ):
        return AnswerInterpretation(action="answer")
    return interpretation


def _preserves_proposal_values(text: str, question: PendingQuestion) -> bool:
    """The polished result must retain at least the old and new values of each adjustment to prevent semantic drift."""
    if not text:
        return False
    for proposal in question.get("proposals") or []:
        if str(proposal["old_value"]) not in text:
            return False
        if str(proposal["proposed_value"]) not in text:
            return False
    return True
