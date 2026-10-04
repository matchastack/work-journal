"""Telegram links, raw updates and journal messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04 14:50:22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("update_id", name=op.f("pk_telegram_updates")),
    )
    op.create_index(
        op.f("ix_telegram_updates_received_at"), "telegram_updates", ["received_at"], unique=False
    )
    op.create_table(
        "journal_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender", sa.String(length=8), nullable=False),
        sa.Column("text", sa.LargeBinary(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sender IN ('owner', 'bot')", name=op.f("ck_journal_messages_sender")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_journal_messages_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_messages")),
        sa.UniqueConstraint("chat_id", "message_id", name=op.f("uq_journal_messages_chat_id")),
    )
    op.create_index(
        op.f("ix_journal_messages_user_id"), "journal_messages", ["user_id"], unique=False
    )
    op.create_table(
        "telegram_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_telegram_links")),
        sa.UniqueConstraint("chat_id", name=op.f("uq_telegram_links_chat_id")),
    )
    op.create_table(
        "journal_message_edits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.LargeBinary(), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["journal_messages.id"],
            name=op.f("fk_journal_message_edits_message_id_journal_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_journal_message_edits_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_message_edits")),
    )
    op.create_index(
        op.f("ix_journal_message_edits_message_id"),
        "journal_message_edits",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_journal_message_edits_message_id"), table_name="journal_message_edits")
    op.drop_table("journal_message_edits")
    op.drop_table("telegram_links")
    op.drop_index(op.f("ix_journal_messages_user_id"), table_name="journal_messages")
    op.drop_table("journal_messages")
    op.drop_index(op.f("ix_telegram_updates_received_at"), table_name="telegram_updates")
    op.drop_table("telegram_updates")
