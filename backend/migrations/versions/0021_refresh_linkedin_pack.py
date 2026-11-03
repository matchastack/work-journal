"""Refreshes write the LinkedIn pack

Revision ID: 0021
Revises: 0012
Create Date: 2026-11-03 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING_PACKS = """
UPDATE refreshes SET linkedin_pack = 'pending', linkedin_pack_at = outputs_at
WHERE outputs = 'pending'
"""
"""The outputs that still wait to be updated get the LinkedIn pack too, so it shows the version
they're to show. Outputs already updated, failed or superseded keep no pack status: the next
refresh writes the pack."""


def upgrade() -> None:
    op.add_column("refreshes", sa.Column("linkedin_pack", sa.String(length=16), nullable=True))
    op.add_column(
        "refreshes", sa.Column("linkedin_pack_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_refreshes_linkedin_pack"),
        "refreshes",
        "linkedin_pack IN ('pending', 'updated', 'failed', 'superseded')",
    )
    op.create_check_constraint(
        op.f("ck_refreshes_linkedin_pack_version"),
        "refreshes",
        "linkedin_pack IS NULL OR output_version IS NOT NULL",
    )
    op.execute(PENDING_PACKS)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_refreshes_linkedin_pack_version"), "refreshes", type_="check")
    op.drop_constraint(op.f("ck_refreshes_linkedin_pack"), "refreshes", type_="check")
    op.drop_column("refreshes", "linkedin_pack_at")
    op.drop_column("refreshes", "linkedin_pack")
