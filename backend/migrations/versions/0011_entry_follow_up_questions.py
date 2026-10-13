"""Entry follow-up questions

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13 11:02:47
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "journal_entries", sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "journal_entries", sa.Column("follow_up_question_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "journal_entries",
        sa.Column(
            "awaiting_follow_up", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_index(
        "uq_journal_entries_one_awaiting_follow_up",
        "journal_entries",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("awaiting_follow_up"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_journal_entries_one_awaiting_follow_up",
        table_name="journal_entries",
        postgresql_where=sa.text("awaiting_follow_up"),
    )
    op.drop_column("journal_entries", "awaiting_follow_up")
    op.drop_column("journal_entries", "follow_up_question_id")
    op.drop_column("journal_entries", "follow_up_at")
