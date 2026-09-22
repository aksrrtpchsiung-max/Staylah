"""业务表模型；LangGraph checkpoint 表由官方 saver 单独管理。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationProfileRow(Base):
    __tablename__ = "conversation_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_confirmation', 'confirmed', 'idle')",
            name="ck_conversation_profiles_status",
        ),
        CheckConstraint(
            "intent IS NULL OR intent IN ('rent', 'buy')",
            name="ck_conversation_profiles_intent",
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(20))
    user_context: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    listing_constraints: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    derived_data_requirements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    open_data_requirements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    unresolved: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    field_sources: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, onupdate=func.now()
    )
    last_user_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileMutationRow(Base):
    __tablename__ = "profile_mutations"

    op_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_profiles.profile_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_client_message",
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'waiting_user', 'completed', 'failed', "
            "'cancelled', 'superseded')",
            name="ck_agent_runs_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="RESTRICT"), nullable=False
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_profiles.profile_id", ondelete="RESTRICT"), nullable=False
    )
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    graph_thread_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_reason: Mapped[str | None] = mapped_column(String(200))
    final_result_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RunQuestionRow(Base):
    __tablename__ = "run_questions"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    question: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_message_id: Mapped[str | None] = mapped_column(String(500))
    answer_text: Mapped[str | None] = mapped_column(Text)


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_recommendations_run"),
        UniqueConstraint("op_key", name="uq_recommendations_op_key"),
    )

    final_result_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    op_key: Mapped[str] = mapped_column(String(500), nullable=False)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
