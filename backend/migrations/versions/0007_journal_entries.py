"""Journal entries

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04 15:22:13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUP_EARLIER_MESSAGES = """
    WITH gaps AS (
        SELECT id, user_id, sent_at,
               CASE WHEN sent_at - lag(sent_at) OVER (PARTITION BY user_id ORDER BY sent_at, id)
                         < interval '30 minutes' THEN 0 ELSE 1 END AS starts_entry
        FROM journal_messages
        WHERE sender = 'owner' AND entry_id IS NULL
    ),
    numbered AS (
        SELECT id, user_id, sent_at,
               sum(starts_entry) OVER (PARTITION BY user_id ORDER BY sent_at, id) AS entry_number
        FROM gaps
    ),
    entries AS MATERIALIZED (
        SELECT gen_random_uuid() AS id, user_id, entry_number,
               min(sent_at) AS opened_at, max(sent_at) AS last_message_at,
               max(sent_at) <= now() - interval '30 minutes' AS quiet
        FROM numbered
        GROUP BY user_id, entry_number
    ),
    inserted AS (
        INSERT INTO journal_entries (id, user_id, opened_at, last_message_at, closed_at, closed_by)
        SELECT id, user_id, opened_at, last_message_at,
               CASE WHEN quiet THEN last_message_at + interval '30 minutes' END,
               CASE WHEN quiet THEN 'quiet' END
        FROM entries
    )
    UPDATE journal_messages AS message
    SET entry_id = entries.id
    FROM numbered
    JOIN entries USING (user_id, entry_number)
    WHERE message.id = numbered.id
"""
"""Messages journaled before entries existed are grouped by the same rule as new ones: a gap of 30
minutes starts the next entry (FR-CAP-4). Entries quiet for 30 minutes are closed, as the tick
would close them; a user's latest entry stays open if it's more recent. One statement, as asyncpg
takes one command at a time."""


def upgrade() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "closed_by IN ('quiet', 'done')", name=op.f("ck_journal_entries_closed_by")
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL) = (closed_by IS NULL)", name=op.f("ck_journal_entries_closed")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_journal_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entries")),
    )
    op.create_index(
        op.f("ix_journal_entries_user_id"), "journal_entries", ["user_id"], unique=False
    )
    op.create_index(
        "uq_journal_entries_one_open",
        "journal_entries",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.add_column("journal_messages", sa.Column("entry_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_journal_messages_entry_id"), "journal_messages", ["entry_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_journal_messages_entry_id_journal_entries"),
        "journal_messages",
        "journal_entries",
        ["entry_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(GROUP_EARLIER_MESSAGES)


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_journal_messages_entry_id_journal_entries"), "journal_messages", type_="foreignkey"
    )
    op.drop_index(op.f("ix_journal_messages_entry_id"), table_name="journal_messages")
    op.drop_column("journal_messages", "entry_id")
    op.drop_index(
        "uq_journal_entries_one_open",
        table_name="journal_entries",
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.drop_index(op.f("ix_journal_entries_user_id"), table_name="journal_entries")
    op.drop_table("journal_entries")
