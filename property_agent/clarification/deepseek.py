"""Structured follow-up question adapter for DeepSeek."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from property_agent.clarification.models import AnswerInterpretation, QuestionPolish
from property_agent.contracts import PendingQuestion

_POLISH_SYSTEM = """You are the English copy editor for the real estate assistant.
Only polish the follow-up question shown to the user so that it is concise, friendly, and non-pressuring.
You must preserve the old and new values of every adjustment item, and must not add promises, facts, listings, or adjustment items.
Return only the specified structured result."""

_INTERPRET_SYSTEM = """You are only responsible for classifying the user's answer to the current follow-up question.
action can only be answer, accept_proposal, decline, or cancel.
Use accept_proposal only when the user explicitly agrees to a certain adjustment, and proposal_id can only be selected from the input.
Use decline to reject the current adjustment; use cancel to explicitly cancel the entire task; use answer for supplementing/modifying requirements or ambiguous expressions.
You must not create proposal_id, question_id, user identity, or state version. Return only the specified structured result."""


@dataclass
class DeepSeekClarificationAdapter:
    """Implement both QuestionPolisher and AnswerInterpreter."""

    model: Any
    max_calls: int = 6
    calls_used: int = 0

    @classmethod
    def from_env(cls) -> "DeepSeekClarificationAdapter":
        from property_agent.runtime.settings import load_runtime_settings

        settings = load_runtime_settings().deepseek
        api_key = os.getenv(settings.api_key_env)
        if not api_key:
            raise RuntimeError(f"{settings.api_key_env} is not configured")
        model = ChatDeepSeek(
            model=settings.clarification_model,
            api_base=settings.base_url,
            api_key=api_key,
            max_tokens=settings.clarification_max_tokens,
            timeout=settings.clarification_timeout_seconds,
            max_retries=1,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return cls(
            model=model,
            max_calls=settings.clarification_max_calls,
        )

    async def polish(self, question: PendingQuestion) -> str:
        payload = {
            "original_text": question["text"],
            "reason_code": question["reason_code"],
            "proposals": [
                {
                    "field": item["field"],
                    "old_value": item["old_value"],
                    "proposed_value": item["proposed_value"],
                    "reason": item["reason"],
                }
                for item in question.get("proposals") or []
            ],
        }
        result = await self._invoke(
            QuestionPolish,
            [
                SystemMessage(content=_POLISH_SYSTEM),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
        )
        return _as_model(result, QuestionPolish).text

    async def interpret(
        self, text: str, question: PendingQuestion
    ) -> AnswerInterpretation:
        payload = {
            "question_text": question["text"],
            "allowed_actions": question["allowed_actions"],
            "proposals": [
                {
                    "proposal_id": item["proposal_id"],
                    "field": item["field"],
                    "old_value": item["old_value"],
                    "proposed_value": item["proposed_value"],
                }
                for item in question.get("proposals") or []
            ],
            "user_answer": text,
        }
        result = await self._invoke(
            AnswerInterpretation,
            [
                SystemMessage(content=_INTERPRET_SYSTEM),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
        )
        return _as_model(result, AnswerInterpretation)

    async def _invoke(self, schema: type[Any], messages: list[Any]) -> Any:
        if self.calls_used >= self.max_calls:
            raise RuntimeError("The follow-up model call has reached its limit")
        self.calls_used += 1
        structured = self.model.with_structured_output(schema)
        return await structured.ainvoke(messages)


def _as_model(value: Any, model_type: type[Any]) -> Any:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)
