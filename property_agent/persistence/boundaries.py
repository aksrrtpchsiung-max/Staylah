"""Persistence interfaces that the upstream run control layer can implement.

These Protocols do not change the frozen public function contracts. The onboarding / search teams can follow the same shape
to read and write session messages, profiles, and runs without depending on the internal state of follow-up questions.
"""
from __future__ import annotations

from typing import Protocol

from property_agent.contracts import ChatMessage, RunContext, ConversationProfile
from property_agent.favorites import ConversationFavorite


class ChatRepository(Protocol):
    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None:
        """Idempotently create a conversation, and reject user ownership conflicts."""
        ...

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        """Idempotently write by (conversation_id, client_message_id) or message_id."""
        ...

    def list_messages(
        self,
        conversation_id: str,
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        """Return a bounded history in chronological order, for use by onboard(messages=...)."""
        ...


class RunBootstrap(Protocol):
    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        """Idempotently create a conversation, profile, and agent_run; graph_thread_id is fixed to run_id."""
        ...


class ConversationFavoriteRepository(Protocol):
    def counts_by_conversation(
        self, conversation_ids: list[str], *, user_id: str
    ) -> dict[str, int]:
        """Return the favorite counts for each session of the current user in a batch."""
        ...

    def add(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> ConversationFavorite:
        """Idempotently favorite a listing in the current user's session."""
        ...

    def remove(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> bool:
        """Remove a favorite; return False when it does not exist."""
        ...

    def list(
        self, conversation_id: str, *, user_id: str
    ) -> list[ConversationFavorite]:
        """Return the favorites of the current session ordered by favorite time."""
        ...
