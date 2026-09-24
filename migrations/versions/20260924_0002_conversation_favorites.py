"""create conversation favorites

Revision ID: 20260924_0002
Revises: 20260919_0001
Create Date: 2026-09-24
"""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0002"
down_revision: str | None = "20260919_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_favorites",
        sa.Column(
            "conversation_id",
            sa.String(200),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("listing_key", sa.String(500), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("conversation_favorites")
