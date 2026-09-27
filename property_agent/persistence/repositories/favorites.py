"""Favorites responsibilities extracted without changing behavior."""
from __future__ import annotations
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from property_agent.favorites import ConversationFavorite
from property_agent.persistence.models import ConversationFavoriteRow, ConversationRow
from property_agent.persistence.repositories.common import SessionFactory


class SqlConversationFavoriteRepository:
    """Conversation-scoped favorites; ownership is inherited from conversations."""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    @staticmethod
    def _favorite_from_row(row: ConversationFavoriteRow) -> ConversationFavorite:
        return {
            "conversation_id": row.conversation_id,
            "listing_key": row.listing_key,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _owned_conversation(
        session: Session, conversation_id: str, user_id: str
    ) -> ConversationRow | None:
        conversation = session.get(ConversationRow, conversation_id)
        if conversation is not None and conversation.user_id != user_id:
            raise PermissionError("conversation does not belong to the current user")
        return conversation

    def counts_by_conversation(
        self, conversation_ids: list[str], *, user_id: str
    ) -> dict[str, int]:
        unique_ids = list(dict.fromkeys(conversation_ids))
        if not unique_ids:
            return {}
        with self.sessions() as session:
            rows = session.execute(
                select(
                    ConversationFavoriteRow.conversation_id,
                    func.count(ConversationFavoriteRow.listing_key),
                )
                .join(
                    ConversationRow,
                    ConversationRow.conversation_id
                    == ConversationFavoriteRow.conversation_id,
                )
                .where(
                    ConversationRow.user_id == user_id,
                    ConversationFavoriteRow.conversation_id.in_(unique_ids),
                )
                .group_by(ConversationFavoriteRow.conversation_id)
            ).all()
            return {
                conversation_id: int(favorite_count)
                for conversation_id, favorite_count in rows
            }

    def add(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> ConversationFavorite:
        with self.sessions.begin() as session:
            if self._owned_conversation(session, conversation_id, user_id) is None:
                raise KeyError(conversation_id)
            statement = pg_insert(ConversationFavoriteRow).values(
                conversation_id=conversation_id,
                listing_key=listing_key,
            )
            session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["conversation_id", "listing_key"]
                )
            )
            stored = session.get(
                ConversationFavoriteRow, (conversation_id, listing_key)
            )
            if stored is None:
                raise RuntimeError("favorite insert did not persist")
            return self._favorite_from_row(stored)

    def remove(
        self, conversation_id: str, *, user_id: str, listing_key: str
    ) -> bool:
        with self.sessions.begin() as session:
            if self._owned_conversation(session, conversation_id, user_id) is None:
                return False
            removed = session.execute(
                delete(ConversationFavoriteRow).where(
                    ConversationFavoriteRow.conversation_id == conversation_id,
                    ConversationFavoriteRow.listing_key == listing_key,
                ).returning(ConversationFavoriteRow.listing_key)
            ).scalar_one_or_none()
            return removed is not None

    def list(
        self, conversation_id: str, *, user_id: str
    ) -> list[ConversationFavorite]:
        with self.sessions() as session:
            if self._owned_conversation(session, conversation_id, user_id) is None:
                return []
            rows = session.execute(
                select(ConversationFavoriteRow)
                .where(ConversationFavoriteRow.conversation_id == conversation_id)
                .order_by(
                    ConversationFavoriteRow.created_at,
                    ConversationFavoriteRow.listing_key,
                )
            ).scalars().all()
            return [self._favorite_from_row(row) for row in rows]
