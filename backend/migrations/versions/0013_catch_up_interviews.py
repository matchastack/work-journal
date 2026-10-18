"""Catch-up interviews

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16 12:39:47
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catch_ups",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="thinking", nullable=False),
        sa.Column("transcript", sa.LargeBinary(), nullable=False),
        sa.Column("question", sa.LargeBinary(), nullable=True),
        sa.Column("question_id", sa.BigInteger(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'finished') = (finished_at IS NOT NULL)", name=op.f("ck_catch_ups_finished")
        ),
        sa.CheckConstraint(
            "status <> 'asking' OR (question IS NOT NULL AND asked_at IS NOT NULL)",
            name=op.f("ck_catch_ups_asking"),
        ),
        sa.CheckConstraint(
            "status IN ('thinking', 'asking', 'paused', 'finished')",
            name=op.f("ck_catch_ups_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_catch_ups_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catch_ups")),
    )
    op.create_index(op.f("ix_catch_ups_user_id"), "catch_ups", ["user_id"], unique=False)
    op.create_index(
        "uq_catch_ups_one_unfinished",
        "catch_ups",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'finished'"),
    )
    op.add_column("journal_entries", sa.Column("question", sa.LargeBinary(), nullable=True))
    op.drop_constraint(op.f("ck_journal_entries_closed_by"), "journal_entries", type_="check")
    op.create_check_constraint(
        op.f("ck_journal_entries_closed_by"),
        "journal_entries",
        "closed_by IN ('quiet', 'done', 'catchup')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_journal_entries_closed_by"), "journal_entries", type_="check")
    op.execute("UPDATE journal_entries SET closed_by = 'done' WHERE closed_by = 'catchup'")
    op.create_check_constraint(
        op.f("ck_journal_entries_closed_by"), "journal_entries", "closed_by IN ('quiet', 'done')"
    )
    op.drop_column("journal_entries", "question")
    op.drop_index(
        "uq_catch_ups_one_unfinished",
        table_name="catch_ups",
        postgresql_where=sa.text("status <> 'finished'"),
    )
    op.drop_index(op.f("ix_catch_ups_user_id"), table_name="catch_ups")
    op.drop_table("catch_ups")
