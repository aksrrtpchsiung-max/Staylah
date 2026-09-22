"""追问 Agent 的内部运行时模型。

公共模块间类型仍以 ``property_agent.contracts`` 为准；这里的 Pydantic 模型
用于约束不可信的模型输出，不能替代服务端的业务校验。
"""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class QuestionPolish(BaseModel):
    """模型只能返回润色后的文案。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=3000)


class AnswerInterpretation(BaseModel):
    """模型对用户回答的建议分类，不含服务端身份或版本字段。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["answer", "accept_proposal", "decline", "cancel"]
    proposal_id: str | None = None


class RawUserAnswer(TypedDict, total=False):
    """恢复 interrupt 时允许客户端提交的最小自然语言载荷。"""

    client_message_id: str
    text: str
