"""Conversation-scoped favorite contract, independent from the A/B/C search contract."""
from __future__ import annotations

from typing import TypedDict


class ConversationFavorite(TypedDict):
    """A listing saved inside one conversation."""

    conversation_id: str
    listing_key: str
    created_at: str
