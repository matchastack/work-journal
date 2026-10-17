"""Entry open questions

Revision ID: 0015
Revises: 0012
Create Date: 2026-10-17 08:22:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "journal_entries", sa.Column("open_question_id", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("journal_entries", "open_question_id")
