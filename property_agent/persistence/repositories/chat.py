"""Chat responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from property_agent.contracts import ChatMessage
from property_agent.persistence.models import ConversationRow, MessageRow
from property_agent.persistence.repositories.common import SessionFactory


class SqlChatRepository:
    """Chat read/write reserved for onboarding; the follow-up node itself only writes messages through QuestionRepository."""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None:
        conversation = pg_insert(ConversationRow).values(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        with self.sessions.begin() as session:
            session.execute(conversation)
            stored = session.get(ConversationRow, conversation_id)
            if stored is None or stored.user_id != user_id:
                raise PermissionError("conversation does not belong to the current user")

    def set_conversation_title(
        self,
        conversation_id: str,
        *,
        user_id: str,
        title: str,
        overwrite: bool = False,
    ) -> None:
        clean = " ".join(title.split())[:500]
        if not clean:
            return
        conditions = [
            ConversationRow.conversation_id == conversation_id,
            ConversationRow.user_id == user_id,
        ]
        if not overwrite:
            conditions.append(ConversationRow.title.is_(None))
        with self.sessions.begin() as session:
            session.execute(
                update(ConversationRow)
                .where(*conditions)
                .values(title=clean)
            )

    def list_conversations(
        self, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        last_message = (
            select(
                MessageRow.conversation_id,
                func.max(MessageRow.created_at).label("last_message_at"),
            )
            .group_by(MessageRow.conversation_id)
            .subquery()
        )
        first_user_message = (
            select(
                MessageRow.conversation_id,
                MessageRow.text.label("first_text"),
                func.row_number()
                .over(
                    partition_by=MessageRow.conversation_id,
                    order_by=(MessageRow.created_at, MessageRow.message_id),
                )
                .label("position"),
            )
            .where(MessageRow.role == "user")
            .subquery()
        )
        with self.sessions() as session:
            rows = session.execute(
                select(
                    ConversationRow,
                    last_message.c.last_message_at,
                    first_user_message.c.first_text,
                )
                .outerjoin(
                    last_message,
                    last_message.c.conversation_id == ConversationRow.conversation_id,
                )
                .outerjoin(
                    first_user_message,
                    (first_user_message.c.conversation_id == ConversationRow.conversation_id)
                    & (first_user_message.c.position == 1),
                )
                .where(
                    ConversationRow.user_id == user_id,
                    or_(
                        ConversationRow.title.is_not(None),
                        first_user_message.c.first_text.is_not(None),
                    ),
                )
                .order_by(
                    func.coalesce(
                        last_message.c.last_message_at, ConversationRow.created_at
                    ).desc()
                )
                .limit(limit)
            ).all()
            return [
                {
                    "conversation_id": conversation.conversation_id,
                    "title": conversation.title
                    or " ".join(first_text.split())[:80],
                    "updated_at": (last_at or conversation.created_at).isoformat(),
                }
                for conversation, last_at, first_text in rows
            ]

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        with self.sessions.begin() as session:
            if session.get(ConversationRow, conversation_id) is None:
                raise KeyError(conversation_id)
            statement = pg_insert(MessageRow).values(
                message_id=message_id,
                conversation_id=conversation_id,
                role=role,
                text=content,
                client_message_id=client_message_id,
            )
            session.execute(
                statement.on_conflict_do_nothing(index_elements=["message_id"])
            )
            stored = session.get(MessageRow, message_id)
            if stored is None:
                raise KeyError(message_id)
            if (
                stored.conversation_id != conversation_id
                or stored.role != role
                or stored.text != content
                or stored.client_message_id != client_message_id
            ):
                raise ValueError("the same message_id corresponds to different content")
            return {
                "message_id": stored.message_id,
                "role": stored.role,  # type: ignore[typeddict-item]
                "text": stored.text,
            }

    def list_messages(
        self,
        conversation_id: str,
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        with self.sessions() as session:
            statement = (
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.created_at, MessageRow.message_id)
            )
            if after:
                current = session.get(MessageRow, after)
                if current is None:
                    return []
                statement = statement.where(
                    (MessageRow.created_at > current.created_at)
                    | (
                        (MessageRow.created_at == current.created_at)
                        & (MessageRow.message_id > current.message_id)
                    )
                )
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                {"message_id": row.message_id, "role": row.role, "text": row.text}  # type: ignore[typeddict-item]
                for row in rows
            ]
