"""Conversation turn results; public shape is unchanged."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

TurnPhase = Literal[
    "a_dialogue",
    "waiting_user",
    "published",
    "b_clarification",
    "finished",
    "failed",
]

@dataclass(frozen=True)
class TurnResult:
    phase: TurnPhase
    assistant_response: str
    conversation_id: str
    user_id: str
    run_id: str | None = None
    status: str | None = None
    pending_question: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    clarification_questions: list[dict[str, str]] = field(default_factory=list)
    next_run_request: dict[str, Any] | None = None
    requirement_request: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
