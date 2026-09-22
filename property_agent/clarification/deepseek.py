"""DeepSeek 的结构化追问 adapter。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from property_agent.clarification.models import AnswerInterpretation, QuestionPolish
from property_agent.contracts import PendingQuestion

_POLISH_SYSTEM = """你是房产助手的中文文案编辑。
只润色给用户的追问，使其简洁、友好、无压迫感。
必须保留每个调整项的新旧值，不得增加承诺、事实、房源或调整项。
只返回规定的结构化结果。"""

_INTERPRET_SYSTEM = """你只负责分类用户对当前追问的回答。
action 只能是 answer、accept_proposal、decline、cancel。
只有用户明确同意某一调整时才用 accept_proposal，并且 proposal_id 只能从输入中选择。
拒绝当前调整用 decline；明确取消整个任务用 cancel；补充/修改需求、歧义表达用 answer。
不得创造 proposal_id、question_id、用户身份或状态版本。只返回规定的结构化结果。"""


@dataclass
class DeepSeekClarificationAdapter:
    """同时实现 QuestionPolisher 与 AnswerInterpreter。"""

    model: Any
    max_calls: int = 6
    calls_used: int = 0

    @classmethod
    def from_env(cls) -> "DeepSeekClarificationAdapter":
        from runtime_settings import load_runtime_settings

        settings = load_runtime_settings().deepseek
        api_key = os.getenv(settings.api_key_env)
        if not api_key:
            raise RuntimeError(f"{settings.api_key_env} 未配置")
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
            raise RuntimeError("追问模型调用已达上限")
        self.calls_used += 1
        structured = self.model.with_structured_output(schema)
        return await structured.ainvoke(messages)


def _as_model(value: Any, model_type: type[Any]) -> Any:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)
