"""Internal runtime model for the follow-up Agent.

Types shared across public modules are still governed by ``property_agent.contracts``; the Pydantic models here
are used to constrain untrusted model output and cannot replace server-side business validation.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class QuestionPolish(BaseModel):
    """The model may only return the polished copy."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=3000)


class AnswerInterpretation(BaseModel):
    """The model's suggested classification for the user's answer, excluding server-side identity or version fields."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["answer", "accept_proposal", "decline", "cancel"]
    proposal_id: str | None = None


class RawUserAnswer(TypedDict, total=False):
    """The minimal natural-language payload the client is allowed to submit when resuming an interrupt."""

    client_message_id: str
    text: str
