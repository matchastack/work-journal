"""Renders

Revision ID: 0016
Revises: 0012
Create Date: 2026-10-30 11:40:03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "renders",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("cuts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("pages > 0", name=op.f("ck_renders_pages")),
        sa.ForeignKeyConstraint(
            ["user_id", "pdf_sha256"],
            ["artifacts.user_id", "artifacts.sha256"],
            name=op.f("fk_renders_user_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_renders_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_renders")),
    )
    op.create_index(op.f("ix_renders_user_id"), "renders", ["user_id", "pdf_sha256"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_renders_user_id"), table_name="renders")
    op.drop_table("renders")
