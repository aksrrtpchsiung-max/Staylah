"""create conversation-profile business tables

Revision ID: 20260919_0001
Revises:
Create Date: 2026-09-19
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(200), primary_key=True),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "conversation_profiles",
        sa.Column("profile_id", sa.String(200), primary_key=True),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(200),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("confirmed_version", sa.Integer()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("intent", sa.String(20)),
        sa.Column("user_context", postgresql.JSONB(), nullable=False),
        sa.Column("listing_constraints", postgresql.JSONB(), nullable=False),
        sa.Column("derived_data_requirements", postgresql.JSONB(), nullable=False),
        sa.Column("open_data_requirements", postgresql.JSONB(), nullable=False),
        sa.Column("unresolved", postgresql.JSONB(), nullable=False),
        sa.Column("field_sources", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("last_user_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_confirmation', 'confirmed', 'idle')",
            name="ck_conversation_profiles_status",
        ),
        sa.CheckConstraint(
            "intent IS NULL OR intent IN ('rent', 'buy')",
            name="ck_conversation_profiles_intent",
        ),
    )
    op.create_index(
        "ix_conversation_profiles_user_id", "conversation_profiles", ["user_id"]
    )

    op.create_table(
        "profile_mutations",
        sa.Column("op_key", sa.String(500), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(200),
            sa.ForeignKey("conversation_profiles.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("result_profile", postgresql.JSONB(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_profile_mutations_profile_id", "profile_mutations", ["profile_id"]
    )

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(500), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(200),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.String(500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_client_message",
        ),
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(200), primary_key=True),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(200),
            sa.ForeignKey("conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.String(200),
            sa.ForeignKey("conversation_profiles.profile_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("profile_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("graph_thread_id", sa.String(200), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_reason", sa.String(200)),
        sa.Column("final_result_id", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'waiting_user', 'completed', 'failed', "
            "'cancelled', 'superseded')",
            name="ck_agent_runs_status",
        ),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])

    op.create_table(
        "run_questions",
        sa.Column(
            "run_id",
            sa.String(200),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("question_id", sa.String(500), primary_key=True),
        sa.Column("question", postgresql.JSONB(), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("answer_message_id", sa.String(500)),
        sa.Column("answer_text", sa.Text()),
    )

    op.create_table(
        "recommendations",
        sa.Column("final_result_id", sa.String(200), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(200),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("op_key", sa.String(500), nullable=False),
        sa.Column("recommendation", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_id", name="uq_recommendations_run"),
        sa.UniqueConstraint("op_key", name="uq_recommendations_op_key"),
    )


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("run_questions")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_profile_mutations_profile_id", table_name="profile_mutations")
    op.drop_table("profile_mutations")
    op.drop_index(
        "ix_conversation_profiles_user_id", table_name="conversation_profiles"
    )
    op.drop_table("conversation_profiles")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
