"""Conversation dependency interfaces."""
from __future__ import annotations
from typing import Any, Protocol
from property_agent.contracts import ConversationProfile, RequirementRequest, Result, RunContext

class SearchCoordinator(Protocol):
    async def run_initial(
        self,
        request: RequirementRequest,
        profile: ConversationProfile,
        *,
        ctx: RunContext,
    ) -> Result: ...

    async def run_attempt(
        self,
        directive: Any,
        profile: ConversationProfile,
        *,
        previous_attempts: list[Any],
        ctx: RunContext,
    ) -> Result: ...

class ChatStore(Protocol):
    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None: ...

    def set_conversation_title(
        self,
        conversation_id: str,
        *,
        user_id: str,
        title: str,
        overwrite: bool = False,
    ) -> None: ...

    def list_conversations(
        self, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def list_messages(
        self, conversation_id: str, *, after: str | None = None, limit: int = 50
    ) -> list[Any]: ...

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> Any: ...

class RunStore(Protocol):
    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None: ...

    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None: ...

    def find_waiting_run(
        self, conversation_id: str, *, user_id: str
    ) -> dict[str, Any] | None: ...

class ProfileStore(Protocol):
    def get(self, profile_id: str) -> ConversationProfile | None: ...
