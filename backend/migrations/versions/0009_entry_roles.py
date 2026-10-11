"""Entry roles

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-11 14:09:12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("journal_entries", sa.Column("role_id", sa.String(length=64), nullable=True))
    op.add_column("journal_entries", sa.Column("new_role", sa.LargeBinary(), nullable=True))
    op.add_column(
        "journal_entries", sa.Column("role_asked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("journal_entries", sa.Column("role_question_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "journal_entries",
        sa.Column("naming_role", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_journal_entries_one_role"),
        "journal_entries",
        "role_id IS NULL OR new_role IS NULL",
    )
    op.create_index(
        "uq_journal_entries_one_naming_role",
        "journal_entries",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("naming_role"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_journal_entries_one_naming_role",
        table_name="journal_entries",
        postgresql_where=sa.text("naming_role"),
    )
    op.drop_constraint(op.f("ck_journal_entries_one_role"), "journal_entries", type_="check")
    op.drop_column("journal_entries", "naming_role")
    op.drop_column("journal_entries", "role_question_id")
    op.drop_column("journal_entries", "role_asked_at")
    op.drop_column("journal_entries", "new_role")
    op.drop_column("journal_entries", "role_id")
