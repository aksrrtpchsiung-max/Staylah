"""追问流程的确定性安全壳。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from property_agent.clarification.boundaries import AnswerInterpreter, QuestionPolisher
from property_agent.clarification.models import AnswerInterpretation
from property_agent.contracts import PendingQuestion

_ACCEPT_RE = re.compile(
    r"(接受|同意|可以|好的|好吧|行|没问题|调高|放宽|提高|yes|ok|okay)",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"(不接受|不同意|不要调整|不放宽|不提高|维持原条件|保持原样|算了吧|no\b)",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"(取消(?:找房|任务|搜索)?|停止(?:找房|任务|搜索)?|结束任务|不找了|cancel|stop)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClarificationAgent:
    """组合模型 adapter，并对输出施加不可绕过的程序校验。"""

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
    """网络或结构化输出失败时的保守分类。"""
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
    """模型不能把模糊话术或未知 proposal 变成授权动作。"""
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
    """润色结果至少要保留每项调整的新旧值，防止语义漂移。"""
    if not text:
        return False
    for proposal in question.get("proposals") or []:
        if str(proposal["old_value"]) not in text:
            return False
        if str(proposal["proposed_value"]) not in text:
            return False
    return True
