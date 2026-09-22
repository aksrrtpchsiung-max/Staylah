"""离线测试和本地无模型模式使用的追问替身。"""
from __future__ import annotations

from dataclasses import dataclass, field

from property_agent.clarification.models import AnswerInterpretation
from property_agent.clarification.service import deterministic_interpret
from property_agent.contracts import PendingQuestion


@dataclass
class PassthroughClarificationAdapter:
    async def polish(self, question: PendingQuestion) -> str:
        return question["text"]

    async def interpret(
        self, text: str, question: PendingQuestion
    ) -> AnswerInterpretation:
        return deterministic_interpret(text, question)


@dataclass
class ScriptedClarificationAdapter(PassthroughClarificationAdapter):
    polished_texts: list[str] = field(default_factory=list)
    interpretations: list[AnswerInterpretation] = field(default_factory=list)

    async def polish(self, question: PendingQuestion) -> str:
        if self.polished_texts:
            return self.polished_texts.pop(0)
        return await super().polish(question)

    async def interpret(
        self, text: str, question: PendingQuestion
    ) -> AnswerInterpretation:
        if self.interpretations:
            return self.interpretations.pop(0)
        return await super().interpret(text, question)
