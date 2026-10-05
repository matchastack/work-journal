"""Entry processing

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05 13:51:42
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    "triage": "triage IN ('work', 'other')",
    "processed": "(triage IS NULL) = (processed_at IS NULL)",
    "processed_closed": "processed_at IS NULL OR closed_at IS NOT NULL",
    "processed_or_failed": "processed_at IS NULL OR failed_at IS NULL",
}


def upgrade() -> None:
    op.add_column("journal_entries", sa.Column("triage", sa.String(length=8), nullable=True))
    op.add_column(
        "journal_entries", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "journal_entries", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True)
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_journal_entries_{name}"), "journal_entries", condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_journal_entries_{name}"), "journal_entries", type_="check")
    op.drop_column("journal_entries", "failed_at")
    op.drop_column("journal_entries", "processed_at")
    op.drop_column("journal_entries", "triage")
