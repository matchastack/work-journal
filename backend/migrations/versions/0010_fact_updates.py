"""Fact updates

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12 09:12:31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Facts saved before this column existed haven't changed since.
    op.execute("UPDATE facts SET updated_at = created_at")


def downgrade() -> None:
    op.drop_column("facts", "updated_at")
