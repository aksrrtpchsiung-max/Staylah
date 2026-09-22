"""追问模块与模型、onboarding 的接缝。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from property_agent.clarification.models import AnswerInterpretation
from property_agent.contracts import PendingQuestion
if TYPE_CHECKING:
    from property_agent.decision.boundaries import NextRunRequest


class QuestionPolisher(Protocol):
    async def polish(self, question: PendingQuestion) -> str:
        """仅润色问题文本；不得改变提案、动作、版本或标识。"""
        ...


class AnswerInterpreter(Protocol):
    async def interpret(
        self, text: str, question: PendingQuestion
    ) -> AnswerInterpretation:
        """把自然语言回答分类；返回值仍须由服务端按当前问题复核。"""
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
        """构造交给外部 onboarding/运行控制层的请求。"""
        ...
