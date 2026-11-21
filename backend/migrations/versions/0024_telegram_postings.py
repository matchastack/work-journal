"""Job postings sent to the bot

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-16 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telegram_links", sa.Column("posting", sa.LargeBinary(), nullable=True))
    op.add_column(
        "telegram_links", sa.Column("posting_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tailor_drafts",
        sa.Column("from_bot", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tailor_drafts", "from_bot")
    op.drop_column("telegram_links", "posting_at")
    op.drop_column("telegram_links", "posting")
